import curses
import io
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Callable, Dict, List, Optional, Tuple

from modules.sqlite_store import DBManager


class TUIApplication:
    """Enhanced curses-driven TUI with color support and dashboard layout."""

    def __init__(
        self,
        config: dict,
        actions: Optional[Dict[str, Callable]] = None,
    ) -> None:
        self.BACK = -1
        self.config = config
        self.actions = actions or {}
        project = config.get("project", {})
        self.project_name = project.get("name", "default")
        self.project_version = project.get("version", "dev")
        self.db = DBManager(project.get("db_file"), project_name=self.project_name)
        self.db.init_db()
        self.stdscr = None

        # Colors
        self.COLOR_HEADER = 1
        self.COLOR_SELECTED = 2
        self.COLOR_SUCCESS = 3
        self.COLOR_WARNING = 4
        self.COLOR_ERROR = 5
        self.COLOR_MUTED = 6

    def run(self) -> None:
        try:
            curses.wrapper(self._run)
        except KeyboardInterrupt:
            pass

    def _init_colors(self):
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(self.COLOR_HEADER, curses.COLOR_CYAN, -1)
            curses.init_pair(self.COLOR_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(self.COLOR_SUCCESS, curses.COLOR_GREEN, -1)
            curses.init_pair(self.COLOR_WARNING, curses.COLOR_YELLOW, -1)
            curses.init_pair(self.COLOR_ERROR, curses.COLOR_RED, -1)
            curses.init_pair(self.COLOR_MUTED, curses.COLOR_WHITE, -1)

    def _run(self, stdscr) -> None:
        self.stdscr = stdscr
        self._init_colors()
        curses.curs_set(0)
        self.stdscr.nodelay(False)

        while True:
            choice = self._menu(
                "Main Menu",
                [
                    ("Collect metadata", "Connect to DB and extract schema info"),
                    ("Run/Resume porting", "Start migration workflow (Fast/Full)"),
                    ("View status", "Monitor progress, logs, and outputs"),
                    ("Export or apply", "Save to disk or apply to target DB"),
                    ("Quit", "Exit the application"),
                ],
                show_kpi=True
            )
            if choice is None:
                break
            if choice == 0:
                self._handle_metadata_collection()
            elif choice == 1:
                self._handle_porting()
            elif choice == 2:
                self._handle_status_and_browse()
            elif choice == 3:
                self._handle_export()
            elif choice == 4 or choice == self.BACK:
                break

    # --- Drawing Helpers ------------------------------------------------

    def _draw_box(self, win, y, x, h, w, title=""):
        try:
            win.attron(curses.color_pair(self.COLOR_MUTED))
            # Draw borders manually or use win.box() if subwin
            # Top
            win.addstr(y, x, "┌" + "─" * (w - 2) + "┐")
            # Sides
            for i in range(1, h - 1):
                win.addstr(y + i, x, "│")
                win.addstr(y + i, x + w - 1, "│")
            # Bottom
            win.addstr(y + h - 1, x, "└" + "─" * (w - 2) + "┘")
            
            if title:
                win.addstr(y, x + 2, f" {title} ", curses.color_pair(self.COLOR_HEADER) | curses.A_BOLD)
            win.attroff(curses.color_pair(self.COLOR_MUTED))
        except curses.error:
            pass

    def _draw_header(self):
        h, w = self.stdscr.getmaxyx()
        title = f" Any2PG v{self.project_version} | Project: {self.project_name} "
        self.stdscr.attron(curses.color_pair(self.COLOR_SELECTED))
        self.stdscr.addstr(0, 0, title.center(w))
        self.stdscr.attroff(curses.color_pair(self.COLOR_SELECTED))

    def _draw_footer(self, text=""):
        h, w = self.stdscr.getmaxyx()
        try:
            self.stdscr.addstr(h - 1, 0, text.center(w)[:w], curses.color_pair(self.COLOR_MUTED))
        except curses.error:
            pass

    def _menu(self, title: str, options: List[Tuple[str, str]], allow_quit: bool = True, show_kpi: bool = False) -> Optional[int]:
        idx = 0
        while True:
            self.stdscr.clear()
            self._draw_header()
            h, w = self.stdscr.getmaxyx()

            # Layout: Menu on left (1/3), Info on right (2/3) or centered if simple
            menu_w = min(40, w - 4)
            menu_x = 2
            menu_y = 3
            item_count = len(options)
            
            # Draw KPI box if requested (e.g., Main Menu)
            if show_kpi:
                self._draw_kpi_dashboard(menu_y, menu_x + menu_w + 2, h - 5, w - (menu_x + menu_w + 4))

            # Draw Menu Box
            self._draw_box(self.stdscr, menu_y, menu_x, item_count + 4, menu_w, title)
            
            for i, (opt_label, opt_desc) in enumerate(options):
                style = curses.A_NORMAL
                marker = "  "
                if i == idx:
                    style = curses.color_pair(self.COLOR_SELECTED) | curses.A_BOLD
                    marker = "▶ "
                
                try:
                    self.stdscr.addstr(menu_y + 2 + i, menu_x + 2, f"{marker}{opt_label:<28}", style)
                    if i == idx and not show_kpi:
                        # Show description in footer instead of KPI panel
                        self._draw_footer(f"INFO: {opt_desc}")
                except curses.error:
                    pass
            
            if show_kpi:
                 self._draw_footer(f"Select: Enter/→  |  Nav: ↑/↓  |  Quit: q/ESC")
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

    def _draw_kpi_dashboard(self, y, x, h, w):
        if w < 20 or h < 5:
            return
            
        progress = self.db.summarize_migration()
        # count by status
        counts = {p["status"]: p["count"] for p in progress}
        
        total = sum(counts.values())
        done = counts.get("DONE", 0)
        failed = counts.get("FAILED", 0) + counts.get("VERIFY_FAIL", 0) + counts.get("REVIEW_FAIL", 0)
        pending = counts.get("PENDING", 0)
        
        self._draw_box(self.stdscr, y, x, h, w, "Project Dashboard")
        
        # Simple stats
        try:
            self.stdscr.addstr(y + 2, x + 2, f"Total Assets: {total}")
            
            self.stdscr.addstr(y + 4, x + 2, "Completed: ")
            self.stdscr.addstr(f"{done}", curses.color_pair(self.COLOR_SUCCESS) | curses.A_BOLD)
            
            self.stdscr.addstr(y + 5, x + 2, "Issues:    ")
            self.stdscr.addstr(f"{failed}", curses.color_pair(self.COLOR_ERROR) | curses.A_BOLD)
            
            self.stdscr.addstr(y + 6, x + 2, "Pending:   ")
            self.stdscr.addstr(f"{pending}", curses.color_pair(self.COLOR_WARNING))

            # Recent activity logs
            logs = self.db.fetch_execution_logs(limit=5)
            self.stdscr.addstr(y + 9, x + 2, "[Recent Logs]", curses.A_UNDERLINE)
            for i, log in enumerate(logs):
                if y + 10 + i >= y + h - 1: break
                lvl_color = self.COLOR_MUTED
                if log['level'] == 'ERROR': lvl_color = self.COLOR_ERROR
                elif log['level'] == 'WARNING': lvl_color = self.COLOR_WARNING
                
                msg = f"{log['created_at'][11:19]} {log['event']}"
                self.stdscr.addstr(y + 10 + i, x + 2, msg[:w-4], curses.color_pair(lvl_color))

        except curses.error:
            pass

    def _prompt(self, prompt: str, default: str = "") -> str:
        curses.echo()
        curses.curs_set(1)
        self.stdscr.clear()
        self._draw_header()
        
        h, w = self.stdscr.getmaxyx()
        box_y, box_x = h//2 - 4, max(2, w//2 - 30)
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

    def _show_text(
        self, title: str, text: str, footer: Optional[str] = None
    ) -> None:
        lines = text.splitlines() or ["<empty>"]
        offset = 0
        footer = footer or "q: Back | ↑/↓: Scroll"
        
        while True:
            self.stdscr.clear()
            self._draw_header()
            h, w = self.stdscr.getmaxyx()
            
            # Content Box
            box_y = 2
            box_h = h - 4
            self._draw_box(self.stdscr, box_y, 0, box_h, w, title)
            
            # Calculate viewing area
            view_h = box_h - 2
            view_w = w - 4
            
            # Draw content
            for i in range(view_h):
                line_idx = offset + i
                if line_idx >= len(lines):
                    break
                line = lines[line_idx]
                
                # Colorize logic for status keywords
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
            
            # Scrollbar indicator
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
            elif key in (curses.KEY_PPAGE,): # PageUp
                offset = max(0, offset - view_h)
            elif key in (curses.KEY_NPAGE,): # PageDown
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
        self._draw_box(self.stdscr, center_y - box_h//2, 4, box_h, w - 8, title)
        
        for i, line in enumerate(body):
            try:
                self.stdscr.addstr(center_y - box_h//2 + 2 + i, 6, line, curses.color_pair(self.COLOR_HEADER))
            except curses.error:
                pass
                
        self._draw_footer("Processing... Please wait.")
        self.stdscr.refresh()

    # --- Actions --------------------------------------------------------

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
                show_kpi=True
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

    def _handle_metadata_collection(self) -> None:
        action = self.actions.get("metadata")
        if not action:
            self._show_text("Error", "No metadata handler is configured.")
            return
        self._show_status_banner(
            "Metadata Collection",
            [
                "Connecting to source database...",
                "Extracting schema information...",
                "This may take a moment depending on schema size."
            ],
        )
        output = self._capture_output(action, self.config)
        self._show_text(
            "Collection Complete", output or "Metadata successfully updated.", footer="Press q to return."
        )

    def _show_metadata_browser(self) -> None:
        while True:
            schemas = [row["schema_name"] for row in self.db.list_schemas()]
            if not schemas:
                self._show_text("Empty", "No schemas found. Run metadata collection first.")
                return

            schema_opts = [(s, "Schema") for s in schemas] + [("Back", "Return")]
            schema_idx = self._menu("Select Schema", schema_opts)
            if schema_idx in (self.BACK, None) or schema_idx == len(schemas):
                return

            schema = schemas[schema_idx]
            objects = self.db.list_schema_objects(schema)
            if not objects:
                self._show_text("Empty", f"No objects found in {schema}")
                continue

            # Object Browser
            obj_opts = [(f"[{row['obj_type'][:4]}] {row['obj_name']}", row['obj_type']) for row in objects]
            obj_opts.append(("Back", "Return"))
            
            while True:
                obj_idx = self._menu(f"Browsing: {schema}", obj_opts)
                if obj_idx in (self.BACK, None) or obj_idx == len(objects):
                    break

                target = objects[obj_idx]
                detail = self.db.get_object_detail(schema, target["obj_name"], target["obj_type"])
                
                content = [
                    f"Name: {target['obj_name']}",
                    f"Type: {target['obj_type']}",
                    f"Extracted: {target['extracted_at']}",
                    "-" * 40
                ]
                if detail['ddl_script']:
                    content.append("[DDL]")
                    content.append(detail['ddl_script'])
                if detail['source_code']:
                    content.append("[Source Code]")
                    content.append(detail['source_code'])
                    
                self._show_text(f"Object: {target['obj_name']}", "\n".join(content))

    def _handle_porting(self) -> None:
        mode_choice = self._menu(
            "Select Porting Mode",
            [
                ("FAST Mode", "Rules-based conversion (sqlglot only). Fast, no LLM cost."),
                ("FULL Mode", "AI-powered conversion (LLM + RAG). Slower, higher quality."),
                ("Back", "Return to menu")
            ]
        )
        if mode_choice in (self.BACK, None) or mode_choice == 2:
            return
            
        self.config.setdefault("llm", {})["mode"] = "fast" if mode_choice == 0 else "full"
        action = self.actions.get("port")
        
        # Simple/Advanced toggle
        filters = False
        if self._prompt_yes_no("Configure advanced filters? (Select files, etc)", False):
             filters = True
             
        only_selected = self._prompt_yes_no("Process selected assets only?", True) if filters else False
        changed_only = self._prompt_yes_no("Process changed assets only?", False) if filters else False
        
        self._show_status_banner(
            "Migration in Progress", 
            ["Initialization...", "Processing assets... check logs for details."]
        )
        
        output = self._capture_output(action, self.config, only_selected=only_selected, changed_only=changed_only)
        self._show_text("Migration Summary", output)

    def _show_progress(self) -> None:
        progress = self.db.summarize_migration()
        text = ["=== Migration Statistics ===\n"]
        if not progress:
            text.append("No data available.")
        else:
            for p in progress:
                text.append(f"{p['status']:<15} : {p['count']}")
        
        text.append("\n=== Latest Outputs ===")
        outputs = self.db.list_rendered_outputs(limit=15)
        for out in outputs:
            verified = "✅" if out['verified'] else "❌"
            text.append(f"[{verified}] {out['file_name']} ({out['status']})")
            
        self._show_text("Project Status", "\n".join(text))

    def _show_converted_outputs(self) -> None:
        outputs = self.db.list_rendered_outputs(limit=50) # Just get names first
        if not outputs:
            self._show_text("Empty", "No outputs found.")
            return
            
        opts = [(f"{o['file_name']}", f"{o['status']}") for o in outputs] + [("Back", "Return")]
        
        while True:
            idx = self._menu("Select File to View", opts)
            if idx in (self.BACK, None) or idx == len(outputs):
                return
            
            # Re-fetch full content
            full_data = self.db.fetch_rendered_sql([outputs[idx]['file_name']])
            if full_data:
                row = full_data[0]
                content = f"-- Status: {row['status']}\n-- Verified: {row['verified']}\n\n{row['sql_text']}"
                self._show_text(row['file_name'], content)

    def _show_logs(self) -> None:
        logs = self.db.fetch_execution_logs(limit=100)
        lines = []
        for log in logs:
            lines.append(f"[{log['created_at']}] [{log['level']}] {log['event']} {log['detail'] or ''}")
        self._show_text("System Logs", "\n".join(lines))

    def _handle_export(self) -> None:
        # Simplified export flow
        action = self.actions.get("export")
        output = self._capture_output(action, self.config)
        self._show_text("Export Result", output)

    def _handle_quality(self) -> None:
        action = self.actions.get("quality")
        output = self._capture_output(action, self.config)
        self._show_text("Quality Report", output)
