import curses
import io
from contextlib import redirect_stdout
from typing import Callable, Dict, List, Optional

from modules.sqlite_store import DBManager


class TUIApplication:
    """Minimal curses-driven TUI for interactive workflows."""

    def __init__(
        self,
        config: dict,
        actions: Optional[Dict[str, Callable]] = None,
    ) -> None:
        self.config = config
        self.actions = actions or {}
        project = config.get("project", {})
        self.project_name = project.get("name", "default")
        self.project_version = project.get("version", "dev")
        self.db = DBManager(project.get("db_file"), project_name=self.project_name)
        self.db.init_db()

    def run(self) -> None:
        curses.wrapper(self._run)

    # --- UI helpers -----------------------------------------------------

    def _run(self, stdscr) -> None:
        curses.curs_set(0)
        stdscr.nodelay(False)
        self.stdscr = stdscr

        while True:
            choice = self._menu(
                "Any2PG | Database Migration Assistant",
                [
                    "Collect metadata",
                    "Browse metadata",
                    "Run/Resume porting",
                    "Progress & logs",
                    "Export rendered SQL",
                    "Apply rendered SQL",
                    "Quality checks",
                    "Exit",
                ],
            )
            if choice is None or choice == 7:
                break
            if choice == 0:
                self._handle_metadata_collection()
            elif choice == 1:
                self._handle_metadata_browser()
            elif choice == 2:
                self._handle_porting()
            elif choice == 3:
                self._handle_progress_and_logs()
            elif choice == 4:
                self._handle_export()
            elif choice == 5:
                self._handle_apply()
            elif choice == 6:
                self._handle_quality()

    def _menu(self, title: str, options: List[str]) -> Optional[int]:
        idx = 0
        while True:
            self.stdscr.clear()
            self.stdscr.addstr(1, 2, "╔════════════════════════════════════════╗")
            header = f"{title} (v{self.project_version})"
            self.stdscr.addstr(2, 2, f"║ {header:<36.36} ║")
            self.stdscr.addstr(3, 2, "╚════════════════════════════════════════╝")
            for i, opt in enumerate(options):
                prefix = "▶" if i == idx else " "
                self.stdscr.addstr(5 + i, 4, f"{prefix} {opt}")
            self.stdscr.addstr(5 + len(options) + 1, 4, "Use ↑/↓ to move, Enter to select, ESC to exit")
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key in (curses.KEY_UP, ord('k')):
                idx = (idx - 1) % len(options)
            elif key in (curses.KEY_DOWN, ord('j')):
                idx = (idx + 1) % len(options)
            elif key in (curses.KEY_ENTER, 10, 13):
                return idx
            elif key in (27,):
                return None

    def _prompt(self, prompt: str, default: str = "") -> str:
        curses.echo()
        self.stdscr.clear()
        self.stdscr.addstr(2, 2, prompt)
        if default:
            self.stdscr.addstr(3, 2, f"Default: {default}")
        self.stdscr.refresh()
        value = self.stdscr.getstr(5, 2, 80).decode("utf-8")
        curses.noecho()
        return value or default

    def _prompt_yes_no(self, prompt: str, default: bool = False) -> bool:
        choice = self._prompt(f"{prompt} (y/n)", "y" if default else "n").lower()
        return choice.startswith("y")

    def _show_text(self, title: str, text: str) -> None:
        lines = text.splitlines() or ["<empty>"]
        offset = 0
        while True:
            self.stdscr.clear()
            height, width = self.stdscr.getmaxyx()
            max_lines = height - 4
            window = lines[offset : offset + max_lines]
            self.stdscr.addstr(1, 2, title[: width - 4])
            for idx, line in enumerate(window):
                self.stdscr.addstr(3 + idx, 2, line[: width - 4])
            self.stdscr.addstr(height - 1, 2, "Use ↑/↓ to scroll, q to close")
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key in (curses.KEY_UP, ord('k')) and offset > 0:
                offset -= 1
            elif key in (curses.KEY_DOWN, ord('j')) and offset + max_lines < len(lines):
                offset += 1
            elif key in (ord('q'), 27):
                break

    def _capture_output(self, func: Callable, *args, **kwargs) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            func(*args, **kwargs)
        return buffer.getvalue()

    # --- Actions --------------------------------------------------------

    def _handle_metadata_collection(self) -> None:
        action = self.actions.get("metadata")
        if not action:
            self._show_text("Metadata collection", "No metadata handler is configured.")
            return
        self._show_text("Running", "Collecting metadata from the source database...")
        output = self._capture_output(action, self.config)
        self._show_text("Metadata collection finished", output or "Done")

    def _handle_metadata_browser(self) -> None:
        schemas = [row["schema_name"] for row in self.db.list_schemas()]
        if not schemas:
            self._show_text("Browse metadata", "No schemas are stored yet. Run metadata collection first.")
            return
        schema_idx = self._menu("Select a schema", schemas)
        if schema_idx is None:
            return
        schema = schemas[schema_idx]
        objects = self.db.list_schema_objects(schema)
        if not objects:
            self._show_text("Browse metadata", "No objects are stored under this schema.")
            return
        labels = [f"{row['obj_type']}: {row['obj_name']}" for row in objects]
        obj_idx = self._menu(f"{schema} objects", labels)
        if obj_idx is None:
            return
        detail = self.db.get_object_detail(schema, objects[obj_idx]["obj_name"], objects[obj_idx]["obj_type"])
        if not detail:
            self._show_text("Object detail", "Unable to find object detail.")
            return
        body_parts = [f"Schema: {schema}", f"Name: {detail['obj_name']}", f"Type: {detail['obj_type']}"]
        ddl = detail["ddl_script"]
        source_code = detail["source_code"]
        if ddl:
            body_parts.append("\n[DDL]\n" + ddl)
        if source_code:
            body_parts.append("\n[Source]\n" + source_code)
        self._show_text("Object detail", "\n".join(body_parts))

    def _handle_porting(self) -> None:
        mode_choice = self._menu("Choose a porting mode", ["FAST (sqlglot)", "FULL (LLM+RAG)", "Cancel"])
        if mode_choice is None or mode_choice == 2:
            return
        self.config.setdefault("llm", {})["mode"] = "fast" if mode_choice == 0 else "full"
        only_selected = self._prompt_yes_no("Process only assets marked as selected?", True)
        changed_only = self._prompt_yes_no("Process only assets flagged as changed?", False)
        names_raw = self._prompt("Specific file names (comma-separated, leave empty for all)", "").strip()
        asset_names = {n.strip() for n in names_raw.split(',') if n.strip()} if names_raw else None
        action = self.actions.get("port")
        if not action:
            self._show_text("Run porting", "No porting handler is configured.")
            return
        self._show_text("Run porting", "Running migration with the selected options...")
        output = self._capture_output(
            action,
            self.config,
            only_selected=only_selected,
            changed_only=changed_only,
            asset_names=asset_names,
        )
        summary_lines = ["Porting finished"]
        progress = self.db.summarize_migration()
        if progress:
            summary_lines.append("\nStatus:")
            for row in progress:
                summary_lines.append(f"- {row['status']}: {row['count']}")
        if output.strip():
            summary_lines.append("\n" + output)
        self._show_text("Porting summary", "\n".join(summary_lines))

    def _handle_progress_and_logs(self) -> None:
        progress = self.db.summarize_migration()
        lines = [f"Project: {self.project_name}", "Version: {self.project_version}", ""]
        if progress:
            lines.append("[Conversion status]")
            lines.extend([f"- {row['status']}: {row['count']}" for row in progress])
            lines.append("")
        logs = self.db.fetch_execution_logs(limit=50)
        lines.append("[Recent execution logs]")
        if not logs:
            lines.append("No logs recorded yet.")
        else:
            for row in logs:
                detail = (row["detail"] or "").replace("\n", " ")
                lines.append(f"{row['created_at']} [{row['level']}] {row['event']} :: {detail}")
        self._show_text("Status / Logs", "\n".join(lines))

    def _handle_export(self) -> None:
        action = self.actions.get("export")
        if not action:
            self._show_text("Export rendered SQL", "No export handler is configured.")
            return
        export_dir = self._prompt("Export directory (default=target_dir)", self.config["project"].get("target_dir") or "output")
        output = self._capture_output(
            action,
            self.config,
            export_dir=export_dir,
            only_selected=self._prompt_yes_no("Export only selected assets?", True),
            changed_only=self._prompt_yes_no("Export only assets marked as changed?", False),
        )
        self._show_text("Export rendered SQL", output or "Done")

    def _handle_apply(self) -> None:
        action = self.actions.get("apply")
        if not action:
            self._show_text("Apply rendered SQL", "No apply handler is configured.")
            return
        output = self._capture_output(
            action,
            self.config,
            only_selected=self._prompt_yes_no("Apply only selected assets?", True),
            changed_only=self._prompt_yes_no("Apply only assets marked as changed?", False),
        )
        self._show_text("Apply rendered SQL", output or "Done")

    def _handle_quality(self) -> None:
        action = self.actions.get("quality")
        if not action:
            self._show_text("Quality checks", "No quality-check handler is configured.")
            return
        output = self._capture_output(action, self.config)
        self._show_text("Quality check results", output or "Done")
