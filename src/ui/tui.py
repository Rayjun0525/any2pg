import curses
import io
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Callable, Dict, List, Optional, Tuple

from modules.sqlite_store import DBManager


class TUIApplication:
    """Enhanced curses-driven TUI covering all CLI capabilities.

    Features:
    * Dashboard with project stats and recent logs
    * Full menu: metadata, porting, status, export, apply, select/deselect, settings, quit
    * Settings editor for log level, DB file, LLM mode/provider
    * Asset selection toggling
    * Apply operation (calls provided ``apply`` action)
    * Progress bar visualization
    * Log viewer with level filter
    """

    BACK = -1

    # Color pair IDs
    COLOR_HEADER = 1
    COLOR_SELECTED = 2
    COLOR_SUCCESS = 3
    COLOR_WARNING = 4
    COLOR_ERROR = 5
    COLOR_MUTED = 6

    def __init__(self, config: dict, actions: Optional[Dict[str, Callable]] = None) -> None:
        self.config = config
        self.actions = actions or {}
        project = config.get("project", {})
        self.project_name = project.get("name", "default")
        self.project_version = project.get("version", "dev")
        self.db = DBManager(project.get("db_file"), project_name=self.project_name)
        self.db.init_db()
        self.stdscr = None
        # UI state
        self.current_filter_level: Optional[str] = None

    # ---------------------------------------------------------------------
    # Entry point
    # ---------------------------------------------------------------------
    def run(self) -> None:
        try:
            curses.wrapper(self._run)
        except KeyboardInterrupt:
            pass

    # ---------------------------------------------------------------------
        try:
            win.attron(curses.color_pair(self.COLOR_MUTED))
            win.addstr(y, x, "┌" + "─" * (w - 2) + "┐")
            for i in range(1, h - 1):
                win.addstr(y + i, x, "│")
                win.addstr(y + i, x + w - 1, "│")
            win.addstr(y + h - 1, x, "└" + "─" * (w - 2) + "┘")
            if title:
                win.addstr(y, x + 2, f" {title} ", curses.color_pair(self.COLOR_HEADER) | curses.A_BOLD)
            win.attroff(curses.color_pair(self.COLOR_MUTED))
        except curses.error:
            pass

    def _draw_header(self) -> None:
        h, w = self.stdscr.getmaxyx()
        title = f" Any2PG v{self.project_version} | Project: {self.project_name} "
        self.stdscr.attron(curses.color_pair(self.COLOR_SELECTED))
        self.stdscr.addstr(0, 0, title.center(w)[:w])
        self.stdscr.attroff(curses.color_pair(self.COLOR_SELECTED))

    def _draw_footer(self, text: str = "") -> None:
        h, w = self.stdscr.getmaxyx()
        try:
            self.stdscr.addstr(h - 1, 0, text.center(w)[:w], curses.color_pair(self.COLOR_MUTED))
        except curses.error:
            pass

    def _menu(
        self,
        title: str,
        options: List[Tuple[str, str]],
        allow_quit: bool = True,
        show_kpi: bool = False,
    ) -> Optional[int]:
        idx = 0
        while True:
            self.stdscr.clear()
            self._draw_header()
            h, w = self.stdscr.getmaxyx()
            menu_w = min(40, w - 4)
            menu_x = 2
            menu_y = 3
            if show_kpi:
                self._draw_kpi_dashboard(menu_y, menu_x + menu_w + 2, h - 5, w - (menu_x + menu_w + 4))
            self._draw_box(self.stdscr, menu_y, menu_x, len(options) + 4, menu_w, title)
            for i, (label, desc) in enumerate(options):
                style = curses.A_NORMAL
                marker = "  "
                if i == idx:
                    style = curses.color_pair(self.COLOR_SELECTED) | curses.A_BOLD
                    marker = "▶ "
                try:
                    self.stdscr.addstr(menu_y + 2 + i, menu_x + 2, f"{marker}{label:<28}", style)
                    if i == idx and not show_kpi:
                        self._draw_footer(f"INFO: {desc}")
                except curses.error:
                    pass
            if show_kpi:
                self._draw_footer("Select: Enter/→  |  Nav: ↑/↓  |  Quit: q/ESC")
            else:
                self._draw_footer("Select: Enter  |  Back: q/←")
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key in (curses.KEY_UP, ord('k')):
                idx = (idx - 1) % len(options)
            elif key in (curses.KEY_DOWN, ord('j')):
                idx = (idx + 1) % len(options)
            elif key in (curses.KEY_LEFT, ord('h'), ord('q'), 27):
                if allow_quit:
                    return self.BACK
                return None
            elif key in (curses.KEY_RIGHT, ord('l'), curses.KEY_ENTER, 10, 13):
                return idx

    def _draw_kpi_dashboard(self, y, x, h, w) -> None:
        if w < 20 or h < 5:
            return
        progress = self.db.summarize_migration()
        counts = {p["status"]: p["count"] for p in progress}
        total = sum(counts.values())
        done = counts.get("DONE", 0)
        failed = sum(counts.get(s, 0) for s in ("FAILED", "VERIFY_FAIL", "REVIEW_FAIL"))
        pending = counts.get("PENDING", 0)
        self._draw_box(self.stdscr, y, x, h, w, "Project Dashboard")
        try:
            self.stdscr.addstr(y + 2, x + 2, f"Total Assets: {total}")
            self.stdscr.addstr(y + 4, x + 2, "Completed: ")
            self.stdscr.addstr(f"{done}", curses.color_pair(self.COLOR_SUCCESS) | curses.A_BOLD)
            self.stdscr.addstr(y + 5, x + 2, "Issues:    ")
            self.stdscr.addstr(f"{failed}", curses.color_pair(self.COLOR_ERROR) | curses.A_BOLD)
            self.stdscr.addstr(y + 6, x + 2, "Pending:   ")
            self.stdscr.addstr(f"{pending}", curses.color_pair(self.COLOR_WARNING))
            logs = self.db.fetch_execution_logs(limit=5)
            self.stdscr.addstr(y + 8, x + 2, "[Recent Logs]", curses.A_UNDERLINE)
            for i, log in enumerate(logs):
                if y + 9 + i >= y + h - 1:
                    break
                lvl = log["level"]
                col = self.COLOR_MUTED
                if lvl == "ERROR":
                    col = self.COLOR_ERROR
                elif lvl == "WARNING":
                    col = self.COLOR_WARNING
                msg = f"{log['created_at'][11:19]} {log['event']}"
                self.stdscr.addstr(y + 9 + i, x + 2, msg[: w - 4], curses.color_pair(col))
        except curses.error:
            pass

    # ---------------------------------------------------------------------
    # Prompt helpers
    # ---------------------------------------------------------------------
    def _prompt(self, prompt: str, default: str = "") -> str:
        curses.echo()
        curses.curs_set(1)
        self.stdscr.clear()
        self._draw_header()
        h, w = self.stdscr.getmaxyx()
        box_y, box_x = h // 2 - 4, max(2, w // 2 - 30)
        box_w = min(60, w - 4)
        self._draw_box(self.stdscr, box_y, box_x, 8, box_w, "Input Required")
        self.stdscr.addstr(box_y + 2, box_x + 2, prompt)
        if default:
            self.stdscr.addstr(box_y + 3, box_x + 2, f"Default: {default}", curses.color_pair(self.COLOR_MUTED))
        self.stdscr.addstr(box_y + 5, box_x + 2, "> ")
        self.stdscr.refresh()
        value = self.stdscr.getstr(box_y + 5, box_x + 4, box_w - 6).decode("utf-8")
        curses.noecho()
        curses.curs_set(0)
        return value.strip() or default

    def _prompt_yes_no(self, prompt: str, default: bool = False) -> bool:
        choice = self._prompt(f"{prompt} (y/n)", "y" if default else "n").lower()
        return choice.startswith("y")

    # ---------------------------------------------------------------------
    # Text display helpers
    # ---------------------------------------------------------------------
    def _show_text(self, title: str, text: str, footer: Optional[str] = None) -> None:
        lines = text.splitlines() or ["<empty>"]
        offset = 0
        footer = footer or "q: Back | ↑/↓: Scroll"
        while True:
            self.stdscr.clear()
            self._draw_header()
            h, w = self.stdscr.getmaxyx()
            box_y = 2
            box_h = h - 4
            self._draw_box(self.stdscr, box_y, 0, box_h, w, title)
            view_h = box_h - 2
            view_w = w - 4
            for i in range(view_h):
                line_idx = offset + i
                if line_idx >= len(lines):
                    break
                line = lines[line_idx]
                attr = curses.A_NORMAL
                if " DONE " in line or "VERIFIED" in line:
                    attr = curses.color_pair(self.COLOR_SUCCESS)
                elif "FAIL" in line or "ERROR" in line:
                    attr = curses.color_pair(self.COLOR_ERROR)
                elif "WARN" in line:
                    attr = curses.color_pair(self.COLOR_WARNING)
                try:
                    self.stdscr.addstr(box_y + 1 + i, 2, line[:view_w], attr)
                except curses.error:
                    pass
            if len(lines) > view_h:
                try:
                    scroll_pct = offset / (len(lines) - view_h)
                    bar_y = box_y + 1 + int(scroll_pct * (view_h - 1))
                    self.stdscr.addch(bar_y, w - 1, '█', curses.color_pair(self.COLOR_SELECTED))
                except curses.error:
                    pass
            self._draw_footer(f"{footer} | Lines {offset+1}-{min(offset+view_h, len(lines))} of {len(lines)}")
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key in (curses.KEY_UP, ord('k')):
                offset = max(0, offset - 1)
            elif key in (curses.KEY_DOWN, ord('j')):
                if offset + view_h < len(lines):
                    offset += 1
            elif key in (curses.KEY_PPAGE,):
                offset = max(0, offset - view_h)
            elif key in (curses.KEY_NPAGE,):
                if offset + view_h < len(lines):
                    offset = min(len(lines) - view_h, offset + view_h)
            elif key in (ord('q'), 27, curses.KEY_LEFT):
                break

    def _capture_output(self, func: Callable, *args, **kwargs) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(buffer):
            try:
                func(*args, **kwargs)
            except Exception:
                buffer.write("\n[ERROR] Task failed with an exception.\n")
                buffer.write(traceback.format_exc())
        return buffer.getvalue().strip()

    def _show_status_banner(self, title: str, body: List[str]) -> None:
        self.stdscr.clear()
        self._draw_header()
        h, w = self.stdscr.getmaxyx()
        center_y = h // 2
        box_h = len(body) + 4
        self._draw_box(self.stdscr, center_y - box_h // 2, 4, box_h, w - 8, title)
        for i, line in enumerate(body):
            try:
                self.stdscr.addstr(center_y - box_h // 2 + 2 + i, 6, line, curses.color_pair(self.COLOR_HEADER))
            except curses.error:
                pass
        self._draw_footer("Processing... Please wait.")
        self.stdscr.refresh()

    # ---------------------------------------------------------------------
    # Handlers for each menu action
    # ---------------------------------------------------------------------
    def _handle_metadata_collection(self) -> None:
        action = self.actions.get("metadata")
        if not action:
            self._show_text("Error", "No metadata handler is configured.")
            return
        self._show_status_banner("Metadata Collection", [
            "Connecting to source database...",
            "Extracting schema information...",
            "This may take a moment depending on schema size.",
        ])
        output = self._capture_output(action, self.config)
        self._show_text("Collection Complete", output or "Metadata successfully updated.", footer="Press q to return.")

    def _handle_porting(self) -> None:
        mode_choice = self._menu(
            "Select Porting Mode",
            [
                ("FAST Mode", "Rules‑based conversion (sqlglot only). Fast, no LLM cost."),
                ("FULL Mode", "AI‑powered conversion (LLM + RAG). Slower, higher quality."),
                ("Back", "Return to menu"),
            ],
        )
        if mode_choice in (self.BACK, None) or mode_choice == 2:
            return
        self.config.setdefault("llm", {})["mode"] = "fast" if mode_choice == 0 else "full"
        action = self.actions.get("port")
        if not action:
            self._show_text("Error", "No porting handler is configured.")
            return
        filters = self._prompt_yes_no("Configure advanced filters? (Select files, etc)", False)
        only_selected = self._prompt_yes_no("Process selected assets only?", True) if filters else False
        changed_only = self._prompt_yes_no("Process changed assets only?", False) if filters else False
        self._show_status_banner("Migration in Progress", ["Initialization...", "Processing assets... check logs for details."])
        output = self._capture_output(
            action,
            self.config,
            only_selected=only_selected,
            changed_only=changed_only,
        )
        self._show_text("Migration Summary", output)

    def _handle_status_and_browse(self) -> None:
        while True:
            choice = self._menu(
                "Status & Monitoring",
                [
                    ("Metadata overview", "Browse captured source schemas and objects"),
                    ("Porting status", "Summary of conversion progress and queues"),
                    ("Converted SQL preview", "Inspect generated SQL outputs"),
                    ("Execution logs", "View system logs and debug info"),
                    ("Quality checks", "Run automated health and safety metrics"),
                    ("Back", "Return to main menu"),
                ],
                show_kpi=True,
            )
            if choice in (self.BACK, None) or choice == 5:
                return
            if choice == 0:
                self._show_metadata_browser()
            elif choice == 1:
                self._show_progress()
            elif choice == 2:
                self._show_converted_outputs()
            elif choice == 3:
                self._show_logs()
            elif choice == 4:
                self._handle_quality()

    def _handle_export(self) -> None:
        action = self.actions.get("export")
        if not action:
            self._show_text("Error", "No export handler is configured.")
            return
        export_dir = self._prompt(
            "Export directory (default=target_dir)",
            self.config.get("project", {}).get("target_dir", "output"),
        )
        only_selected = self._prompt_yes_no("Export only selected assets?", True)
        changed_only = self._prompt_yes_no("Export only assets marked as changed?", False)
        output = self._capture_output(
            action,
            self.config,
            export_dir=export_dir,
            only_selected=only_selected,
            changed_only=changed_only,
        )
        self._show_text("Export result", output or "Done")

    def _handle_apply(self) -> None:
        action = self.actions.get("apply")
        if not action:
            self._show_text("Error", "No apply handler is configured.")
            return
        only_selected = self._prompt_yes_no("Apply only selected assets?", True)
        changed_only = self._prompt_yes_no("Apply only assets marked as changed?", False)
        self._show_status_banner("Applying to target DB", ["Running apply operation...", "Check logs for any errors."])
        output = self._capture_output(
            action,
            self.config,
            only_selected=only_selected,
            changed_only=changed_only,
        )
        self._show_text("Apply result", output or "Done")

    def _handle_select_deselect(self) -> None:
        assets = self.db.list_source_assets()
        if not assets:
            self._show_text("Select/Deselect", "No assets available. Run metadata collection first.")
            return
        while True:
            options = []
            for row in assets:
                flag = "[X]" if row["selected_for_port"] else "[ ]"
                options.append((f"{flag} {row['file_name']}", ""))
            options.append(("Done", ""))
            idx = self._menu("Select/Deselect Assets", options, allow_quit=False)
            if idx is None or idx == len(options) - 1:
                break
            asset = assets[idx]
            new_state = not bool(asset["selected_for_port"])
            self.db.set_selection([asset["file_name"]], new_state)
            assets = self.db.list_source_assets()
        self._show_text("Select/Deselect", "Selection updated.")

    def _handle_settings(self) -> None:
        while True:
            choice = self._menu(
                "Settings",
                [
                    ("Log level", f"Current: {self.config.get('logging', {}).get('level', 'INFO')}"),
                    ("DB file", f"Current: {self.config.get('project', {}).get('db_file', 'project.db')}"),
                    ("LLM mode", f"Current: {self.config.get('llm', {}).get('mode', 'fast')}"),
                    ("LLM provider", f"Current: {self.config.get('llm', {}).get('provider', 'ollama')}"),
                    ("Back", "Return to main menu"),
                ],
                allow_quit=False,
            )
            if choice is None or choice == 4:
                break
            if choice == 0:
                new = self._prompt("Set log level (DEBUG/INFO/WARNING/ERROR)", self.config.get('logging', {}).get('level', 'INFO'))
                self.config.setdefault('logging', {})['level'] = new.upper()
            elif choice == 1:
                new = self._prompt("Set project DB file path", self.config.get('project', {}).get('db_file', 'project.db'))
                self.config.setdefault('project', {})['db_file'] = new
                self.db = DBManager(new, project_name=self.project_name)
                self.db.init_db()
            elif choice == 2:
                new = self._prompt_yes_no("Use fast mode?", self.config.get('llm', {}).get('mode', 'fast') == 'fast')
                self.config.setdefault('llm', {})['mode'] = 'fast' if new else 'full'
            elif choice == 3:
                new = self._prompt("Set LLM provider (ollama/openai/...)", self.config.get('llm', {}).get('provider', 'ollama'))
                self.config.setdefault('llm', {})['provider'] = new
            self._show_text("Settings", f"{['Log level','DB file','LLM mode','LLM provider'][choice]} updated.")

    # ---------------------------------------------------------------------
    # Detailed viewers
    # ---------------------------------------------------------------------
    def _show_progress(self) -> None:
        progress = self.db.summarize_migration()
        total = sum(p["count"] for p in progress) if progress else 0
        done = sum(p["count"] for p in progress if p["status"] == "DONE")
        failed = sum(p["count"] for p in progress if p["status"] in ("FAILED", "VERIFY_FAIL", "REVIEW_FAIL"))
        pending = sum(p["count"] for p in progress if p["status"] == "PENDING")
        bar_len = 30
        done_ratio = int(bar_len * done / total) if total else 0
        bar = "[" + "#" * done_ratio + "-" * (bar_len - done_ratio) + "]"
        lines = [
            f"Project: {self.project_name} (v{self.project_version})",
            f"Total assets: {total}",
            f"Completed: {done} {bar}",
            f"Pending: {pending}",
            f"Failed: {failed}",
            "",
            "Recent outputs (last 10):",
        ]
        outputs = self.db.list_rendered_outputs(limit=10)
        for out in outputs:
            status = out["status"]
            color = self.COLOR_SUCCESS if status == "DONE" else (self.COLOR_ERROR if status in ("FAILED", "VERIFY_FAIL", "REVIEW_FAIL") else self.COLOR_MUTED)
            lines.append(f"{out['file_name']} :: {status}")
        self._show_text("Porting status", "\n".join(lines))

    def _show_logs(self) -> None:
        level = self._prompt("Filter by level (INFO/WARNING/ERROR) or leave empty for all", "")
        level = level.upper() if level else None
        logs = self.db.fetch_execution_logs(limit=200)
        lines = []
        for log in logs:
            if level and log["level"] != level:
                continue
            lines.append(f"[{log['created_at']}] [{log['level']}] {log['event']} {log['detail'] or ''}")
        self._show_text("Execution logs", "\n".join(lines) or "No logs.")

    def _show_converted_outputs(self) -> None:
        outputs = self.db.list_rendered_outputs(limit=50)
        if not outputs:
            self._show_text("Empty", "No outputs found.")
            return
        opts = [(f"{o['file_name']}", o['status']) for o in outputs] + [("Back", "")]
        while True:
            idx = self._menu("Select File to View", opts)
            if idx is None or idx == len(outputs):
                return
            full = self.db.fetch_rendered_sql([outputs[idx]['file_name']])
            if full:
                row = full[0]
                content = f"-- Status: {row['status']}\n-- Verified: {row['verified']}\n\n{row['sql_text']}"
                self._show_text(row['file_name'], content)

    def _show_metadata_browser(self) -> None:
        while True:
            schemas = [row["schema_name"] for row in self.db.list_schemas()]
            if not schemas:
                self._show_text("Empty", "No schemas found. Run metadata collection first.")
                return
            schema_opts = [(s, "Schema") for s in schemas] + [("Back", "Return")]
            s_idx = self._menu("Select Schema", schema_opts)
            if s_idx is None or s_idx == len(schemas):
                return
            schema = schemas[s_idx]
            objects = self.db.list_schema_objects(schema)
            if not objects:
                self._show_text("Empty", f"No objects found in {schema}")
                continue
            obj_opts = [(f"[{row['obj_type'][:4]}] {row['obj_name']}", row['obj_type']) for row in objects] + [("Back", "Return")]
            while True:
                o_idx = self._menu(f"Browsing: {schema}", obj_opts)
                if o_idx is None or o_idx == len(objects):
                    break
                target = objects[o_idx]
                detail = self.db.get_object_detail(schema, target["obj_name"], target["obj_type"])
                content = [
                    f"Name: {target['obj_name']}",
                    f"Type: {target['obj_type']}",
                    f"Extracted: {target['extracted_at']}",
                    "-" * 40,
                ]
                if detail.get('ddl_script'):
                    content.append("[DDL]")
                    content.append(detail['ddl_script'])
                if detail.get('source_code'):
                    content.append("[Source Code]")
                    content.append(detail['source_code'])
                self._show_text(f"Object: {target['obj_name']}", "\n".join(content))

    def _handle_quality(self) -> None:
        action = self.actions.get("quality")
        if not action:
            self._show_text("Error", "No quality handler is configured.")
            return
        output = self._capture_output(action, self.config)
        self._show_text("Quality Report", output)
