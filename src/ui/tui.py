import curses
import io
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Callable, Dict, List, Optional, Tuple, Any

from modules.sqlite_store import DBManager


class TUIApplication:
    """Enhanced K9s-style TUI for Any2PG."""

    # Color pair IDs
    COLOR_HEADER = 1
    COLOR_SELECTED = 2
    COLOR_SUCCESS = 3
    COLOR_WARNING = 4
    COLOR_ERROR = 5
    COLOR_MUTED = 6
    COLOR_HIGHLIGHT = 7

    def __init__(self, config: dict, actions: Optional[Dict[str, Callable]] = None) -> None:
        self.config = config
        self.actions = actions or {}
        project = config.get("project", {})
        self.project_name = project.get("name", "default")
        self.project_version = project.get("version", "dev")
        self.db = DBManager(project.get("db_file"), project_name=self.project_name)
        self.db.init_db()
        self.stdscr = None
        
        # UI State
        self.selected_idx = 0
        self.list_offset = 0
        self.active_tab = 0  # 0: Info, 1: SQL, 2: Logs
        self.tabs = ["Info", "SQL", "Logs"]
        self.assets = []
        self.show_help = False

    def run(self) -> None:
        try:
            curses.wrapper(self._run)
        except KeyboardInterrupt:
            pass

    def _run(self, stdscr) -> None:
        self.stdscr = stdscr
        self._init_colors()
        curses.curs_set(0)
        self.stdscr.nodelay(False)
        self.stdscr.keypad(True)

        self._refresh_assets()

        while True:
            self._draw_screen()
            key = self.stdscr.getch()
            if not self._handle_input(key):
                break

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(self.COLOR_HEADER, curses.COLOR_CYAN, -1)
        curses.init_pair(self.COLOR_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(self.COLOR_SUCCESS, curses.COLOR_GREEN, -1)
        curses.init_pair(self.COLOR_WARNING, curses.COLOR_YELLOW, -1)
        curses.init_pair(self.COLOR_ERROR, curses.COLOR_RED, -1)
        curses.init_pair(self.COLOR_MUTED, curses.COLOR_WHITE, -1) # adjust as needed
        curses.init_pair(self.COLOR_HIGHLIGHT, curses.COLOR_MAGENTA, -1)

    def _refresh_assets(self):
        self.assets = self.db.list_source_assets()
        # Ensure selection is valid
        if self.selected_idx >= len(self.assets):
            self.selected_idx = max(0, len(self.assets) - 1)

    def _draw_screen(self):
        self.stdscr.clear()
        h, w = self.stdscr.getmaxyx()

        # Header
        header = f" Any2PG v{self.project_version} | Project: {self.project_name} "
        self.stdscr.attron(curses.color_pair(self.COLOR_HEADER) | curses.A_BOLD)
        self.stdscr.addstr(0, 0, header.center(w)[:w])
        self.stdscr.attroff(curses.color_pair(self.COLOR_HEADER) | curses.A_BOLD)

        # Footer
        footer = " q:Quit | m:Metadata | p:Port | a:Apply | e:Export | SPC:Select | ENTER:View "
        try:
            self.stdscr.addstr(h - 1, 0, footer.ljust(w), curses.color_pair(self.COLOR_SELECTED))
        except curses.error:
            pass

        # Split Pane
        mid_x = w // 3
        # Vertical Line
        for y in range(1, h - 1):
            try:
                self.stdscr.addch(y, mid_x, curses.ACS_VLINE)
            except curses.error:
                pass

        self._draw_asset_list(1, 0, h - 2, mid_x)
        self._draw_detail_pane(1, mid_x + 1, h - 2, w - mid_x - 1)

        self.stdscr.refresh()

    def _draw_asset_list(self, y, x, h, w):
        title = " Assets "
        self.stdscr.addstr(y, x + 1, title, curses.A_BOLD)
        
        list_h = h - 2
        visible_assets = self.assets[self.list_offset : self.list_offset + list_h]
        
        for i, asset in enumerate(visible_assets):
            abs_idx = self.list_offset + i
            is_selected = (abs_idx == self.selected_idx)
            is_checked = asset["selected_for_port"] == 1
            
            check_mark = "[x]" if is_checked else "[ ]"
            status = asset["last_status"] or "PENDING"
            
            # Status icon/color
            status_char = " "
            status_attr = curses.A_NORMAL
            if status == "DONE":
                status_char = "✔"
                status_attr = curses.color_pair(self.COLOR_SUCCESS)
            elif status in ("FAILED", "VERIFY_FAIL"):
                status_char = "✖"
                status_attr = curses.color_pair(self.COLOR_ERROR)
            
            line_str = f"{check_mark} {asset['file_name'][:w-10]} {status_char}"
            
            attr = curses.A_NORMAL
            if is_selected:
                attr = curses.color_pair(self.COLOR_SELECTED)
            
            try:
                self.stdscr.addstr(y + 1 + i, x + 1, line_str[:w-2].ljust(w-2), attr)
                # Overwrite status char with color if possible
                # (Simple overwrite, logic could be more complex for exact positioning)
            except curses.error:
                pass

    def _draw_detail_pane(self, y, x, h, w):
        if not self.assets:
            self.stdscr.addstr(y + 2, x + 2, "No assets found.")
            return

        current = self.assets[self.selected_idx]
        
        # Draw Tabs
        tab_x = x + 1
        for i, tab in enumerate(self.tabs):
            attr = curses.A_NORMAL
            if i == self.active_tab:
                attr = curses.color_pair(self.COLOR_HIGHLIGHT) | curses.A_BOLD
            label = f" {tab} "
            try:
                self.stdscr.addstr(y, tab_x, label, attr)
                tab_x += len(label) + 1
            except curses.error:
                pass
        
        # Content box
        content_y = y + 2
        content_h = h - 3
        try:
             # Just a visual separator line
             self.stdscr.hline(y+1, x, curses.ACS_HLINE, w)
        except curses.error:
            pass

        if self.active_tab == 0: # Info
            self._draw_info_tab(content_y, x + 1, content_h, w - 2, current)
        elif self.active_tab == 1: # SQL
            self._draw_sql_tab(content_y, x + 1, content_h, w - 2, current)
        elif self.active_tab == 2: # Logs
            self._draw_logs_tab(content_y, x + 1, content_h, w - 2, current)

    def _draw_info_tab(self, y, x, h, w, asset):
        info = [
            f"File: {asset['file_path']}",
            f"Status: {asset['last_status'] or 'PENDING'}",
            f"Updated: {asset['updated_at']}",
            f"Selected: {bool(asset['selected_for_port'])}",
            "",
            "[Source Analysis]",
            f"Parsed Schemas: {asset.get('parsed_schemas') or 'N/A'}",
            "",
            "[Verification]",
            f"Verified: {bool(asset.get('verified'))}",
            f"Last Error: {asset.get('last_error') or 'None'}"
        ]
        
        for i, line in enumerate(info):
            if i >= h: break
            try:
                self.stdscr.addstr(y + i, x, line[:w])
            except curses.error:
                pass

    def _draw_sql_tab(self, y, x, h, w, asset):
        # Prefer rendered SQL, fallback to source
        rendered = self.db.fetch_rendered_sql([asset['file_name']])
        sql_text = ""
        header = "Source SQL"
        if rendered and rendered[0]['sql_text']:
            sql_text = rendered[0]['sql_text']
            header = f"Rendered SQL (Status: {rendered[0]['status']})"
        else:
            sql_text = asset['sql_text']
        
        try:
            self.stdscr.addstr(y, x, f"--- {header} ---", curses.color_pair(self.COLOR_MUTED))
        except curses.error:
            pass
            
        lines = sql_text.splitlines()
        for i in range(h - 1):
            if i >= len(lines): break
            try:
                self.stdscr.addstr(y + 1 + i, x, lines[i][:w])
            except curses.error:
                pass

    def _draw_logs_tab(self, y, x, h, w, asset):
        # We don't have per-asset logs easily indexed unless we query execution_logs with filter
        # For now, show project logs
        logs = self.db.fetch_execution_logs(limit=20)
        try:
             self.stdscr.addstr(y, x, "--- Recent System Logs ---", curses.color_pair(self.COLOR_MUTED))
        except curses.error:
            pass
            
        for i, log in enumerate(logs):
            if i >= h - 1: break
            line = f"[{log['level']}] {log['event']} {log['detail'] or ''}"
            attr = curses.A_NORMAL
            if log["level"] == "ERROR": attr = curses.color_pair(self.COLOR_ERROR)
            try:
                self.stdscr.addstr(y + 1 + i, x, line[:w], attr)
            except curses.error:
                pass

    def _handle_input(self, key):
        if key in (ord('q'), 27): # q or ESC
            return False
            
        if key in (curses.KEY_UP, ord('k')):
            if self.selected_idx > 0:
                self.selected_idx -= 1
                if self.selected_idx < self.list_offset:
                    self.list_offset = self.selected_idx
        elif key in (curses.KEY_DOWN, ord('j')):
            if self.assets and self.selected_idx < len(self.assets) - 1:
                self.selected_idx += 1
                h, _ = self.stdscr.getmaxyx()
                list_h = h - 2
                if self.selected_idx >= self.list_offset + list_h:
                    self.list_offset = self.selected_idx - list_h + 1
        
        elif key in (curses.KEY_LEFT, ord('h')):
            self.active_tab = (self.active_tab - 1) % len(self.tabs)
        elif key in (curses.KEY_RIGHT, ord('l')):
            self.active_tab = (self.active_tab + 1) % len(self.tabs)
            
        elif key == ord(' '): # Space to toggle selection
            if self.assets:
                current = self.assets[self.selected_idx]
                new_val = not bool(current["selected_for_port"])
                self.db.set_selection([current["file_name"]], new_val)
                self._refresh_assets()
        
        elif key == ord('m'): # Metadata
            self._run_action("metadata")
            self._refresh_assets()
            
        elif key == ord('p'): # Port
            self._run_action("port")
            self._refresh_assets()
            
        elif key == ord('a'): # Apply
            self._run_action("apply")
            self._refresh_assets()
            
        elif key == ord('e'): # Export
            self._run_action("export")
            
        elif key == ord('r'): # Refresh
            self._refresh_assets()

        return True

    def _run_action(self, name):
        action = self.actions.get(name)
        if not action:
            self._show_modal("Error", f"No handler for {name}")
            return
            
        self._show_modal("Running...", f"Executing {name} task.\nPlease wait...")
        # Capture logic could be added here similar to previous implementation
        try:
            # We must temporarily leave curses mode or redirect stdout carefully
            # But essentially we just run it and catch exceptions
            # For strict TUI, better to redirect stdout/stderr to a buffer and display it
            buffer = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(buffer):
                action(self.config)
            
            output = buffer.getvalue()
            self._show_modal("Result", output[-1000:] if len(output) > 1000 else output) # Show tail
        except Exception as e:
            self._show_modal("Error", f"Task failed:\n{traceback.format_exc()}")
            
    def _show_modal(self, title, message):
        h, w = self.stdscr.getmaxyx()
        box_h, box_w = h // 2, w // 2
        y, x = h // 4, w // 4
        
        win = curses.newwin(box_h, box_w, y, x)
        win.box()
        win.addstr(0, 2, f" {title} ", curses.A_BOLD)
        
        lines = message.splitlines()
        for i, line in enumerate(lines):
            if i >= box_h - 2: break
            try:
                win.addstr(i + 1, 2, line[:box_w-4])
            except: pass
            
        win.addstr(box_h - 1, 2, " Press any key to close ")
        win.refresh()
        win.getch()
        del win
        self.stdscr.touchwin()
        self.stdscr.refresh()
