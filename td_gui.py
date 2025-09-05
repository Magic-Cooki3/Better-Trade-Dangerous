#!/usr/bin/env python3
import sys
import os
import threading
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    # Ensure local package import works when run from this file
    sys.path.insert(0, os.path.dirname(__file__))
    from tradedangerous import commands as td_commands
except Exception as e:
    raise SystemExit(f"Failed to import tradedangerous: {e}")

# --- GUI toolkit ---
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText
from tkinter import filedialog, messagebox


# ---------- Introspection models ----------

@dataclass(eq=False)
class OptionSpec:
    args: Tuple[str, ...]
    kwargs: Dict[str, Any]
    group_id: Optional[int] = None

    @property
    def long_flag(self) -> str:
        # Prefer a long option (starts with --), else the first
        longs = [a for a in self.args if a.startswith("--")]
        return longs[0] if longs else self.args[0]

    @property
    def display_name(self) -> str:
        return self.long_flag

    @property
    def help(self) -> str:
        return self.kwargs.get("help", "")

    @property
    def action(self) -> Optional[str]:
        return self.kwargs.get("action")

    @property
    def is_flag(self) -> bool:
        return self.action == "store_true"

    @property
    def is_positional(self) -> bool:
        # Positional args in our CLI have names without leading dashes
        try:
            first = self.args[0]
        except Exception:
            return False
        return not (isinstance(first, str) and first.startswith("-"))

    @property
    def key(self) -> str:
        # Stable identifier used for saving/restoring state
        try:
            return self.kwargs.get("dest") or (self.args[0] if self.args else self.long_flag.lstrip('-'))
        except Exception:
            return self.long_flag.lstrip('-')

    @property
    def metavar(self) -> Optional[str]:
        return self.kwargs.get("metavar")

    @property
    def default(self) -> Any:
        return self.kwargs.get("default")

    @property
    def choices(self) -> Optional[List[str]]:
        return self.kwargs.get("choices")

    @property
    def dest(self) -> Optional[str]:
        return self.kwargs.get("dest")

    @property
    def multiple(self) -> bool:
        return self.action == "append"


@dataclass
class CommandMeta:
    name: str
    help: str
    arguments: List[OptionSpec] = field(default_factory=list)
    switches: List[OptionSpec] = field(default_factory=list)
    # When provided, these args are used verbatim instead of building from options
    fixed_args: Optional[List[str]] = None


def _flatten_args(items: List[Any]) -> List[OptionSpec]:
    flat: List[OptionSpec] = []
    for item in items or []:
        # MutuallyExclusiveGroup has 'arguments'
        if hasattr(item, "arguments"):
            gid = id(item)
            for sub in getattr(item, "arguments", []):
                flat.append(OptionSpec(tuple(sub.args), dict(sub.kwargs), group_id=gid))
        else:
            flat.append(OptionSpec(tuple(item.args), dict(item.kwargs)))
    return flat


def load_commands() -> Dict[str, CommandMeta]:
    metas: Dict[str, CommandMeta] = {}
    for cmd_name, module in td_commands.commandIndex.items():
        help_text = getattr(module, "help", cmd_name)
        arguments = _flatten_args(getattr(module, "arguments", []))
        switches = _flatten_args(getattr(module, "switches", []))
        metas[cmd_name] = CommandMeta(cmd_name, help_text, arguments, switches)
    # Add a convenience action for updating/rebuilding the DB via eddblink plugin
    metas["Update/Rebuild DB"] = CommandMeta(
        name="import",
        help="Convenience: import with eddblink (clean/all/skipvend/force)",
        arguments=[],
        switches=[],
        fixed_args=["import", "-P", "eddblink", "-O", "clean,all,skipvend,force"],
    )
    # (Removed preset: Rebuild Cache (-i -f))
    # Update only live listings via eddblink
    metas["Update Live Listings"] = CommandMeta(
        name="import",
        help="Convenience: import eddblink live market listings",
        arguments=[],
        switches=[],
        fixed_args=["import", "-P", "eddblink", "-O", "listings_live"],
    )

    # Start EDDN Live (carriers only, public access)
    metas["EDDN Live (Carriers)"] = CommandMeta(
        name="import",
        help="Start EDDN live updates for public Fleet Carriers",
        arguments=[],
        switches=[],
        fixed_args=["import", "-P", "eddn", "-O", "carrier_only,public_only"],
    )

    # Start EDDN Live (all markets)
    metas["EDDN Live (All Markets)"] = CommandMeta(
        name="import",
        help="Start EDDN live updates for all markets",
        arguments=[],
        switches=[],
        fixed_args=["import", "-P", "eddn"],
    )

    # Spansh galaxy import (seed/update systems, stations, services)
    metas["Import Spansh Galaxy"] = CommandMeta(
        name="import",
        help="Import galaxy data (systems/stations/services) from Spansh",
        arguments=[],
        switches=[],
        fixed_args=["import", "-P", "spansh"],
    )
    return metas


# ---------- GUI ----------

class TdGuiApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trade Dangerous – New GUI")
        self.geometry("1100x720")
        self.minsize(900, 560)
        # Suspend preference writes during first-time UI construction
        self._suspend_save = True

        # Theme colors (Dracula-like base + your palette)
        self.colors: Dict[str, str] = {
            "bg": "#282a36",           # Dracula background
            "panel": "#1f2029",        # Slightly darker panel surface
            "surface": "#1c1e26",      # Inputs / text areas
            "line": "#44475a",         # Lines / selection
            "fg": "#f8f8f2",           # Primary foreground
            "muted": "#6272a4",        # Subtle/help text
            "primary": "#7849bf",      # Provided primary
            "primaryActive": "#8a5ad7",# Active/hover primary
            "secondary": "#49a2bf",    # Provided secondary
            "secondaryActive": "#5cb3c9",
            "success": "#49bf60",      # Provided 3rd
        }

        # Apply custom dark theme styling first
        self._apply_theme()

        # Data
        self.cmd_metas = load_commands()
        self.current_meta: Optional[CommandMeta] = None
        self.widget_vars: Dict[OptionSpec, Dict[str, Any]] = {}
        # Foreground subprocess control (Output tab)
        self._proc = None
        self._stop_requested = False
        # Background sessions (e.g., EDDN live) keyed by tab widget name
        self._bg_sessions: Dict[str, Dict[str, Any]] = {}
        self._bg_counter = 0

        # Paths
        self.repo_dir = os.path.dirname(__file__)
        self.trade_py = os.path.join(self.repo_dir, "trade.py")

        # Build UI
        self._build_topbar()
        self._build_preview_row()
        self._build_main_area()
        self._build_global_options()

        # Make all scrollable areas respond to mouse wheel
        self._install_global_mousewheel()

        # Load last-used paths (CWD/DB)
        self._load_prefs()

        # Ensure save on close
        try:
            self.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

        # Initialize with the first command (custom ordering)
        all_cmds = self._ordered_command_labels()
        if all_cmds:
            # Use restored command if available
            restore_cmd = getattr(self, "_restore_cmd", None)
            if restore_cmd in self.cmd_metas:
                self.cmd_var.set(restore_cmd)
            else:
                self.cmd_var.set(all_cmds[0])
            self._on_command_change()
            # Apply saved option states if present
            self._apply_saved_state_for_current()
        # Now allow preference writes and save the fully restored state once
        self._suspend_save = False
        try:
            self._save_prefs()
        except Exception:
            pass

    def _ordered_command_labels(self) -> List[str]:
        """Return command labels with preferred presets first.
        Order: Update/Rebuild DB, Update Live Listings, EDDN Live presets, Spansh import, then the rest sorted.
        """
        keys = list(self.cmd_metas.keys())
        preferred = [
            "Update/Rebuild DB",
            "Update Live Listings",
            "EDDN Live (Carriers)",
            "EDDN Live (All Markets)",
            "Import Spansh Galaxy",
        ]
        head = [k for k in preferred if k in keys]
        tail = sorted([k for k in keys if k not in head])
        return head + tail

    # ----- Top bar -----
    def _build_topbar(self):
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        top.columnconfigure(2, weight=1)

        ttk.Label(top, text="Command:").grid(row=0, column=0, sticky="w", padx=(0,6))
        self.cmd_var = tk.StringVar()
        self.cmd_combo = ttk.Combobox(
            top,
            textvariable=self.cmd_var,
            values=self._ordered_command_labels(),
            state="readonly",
            width=20,
        )
        self.cmd_combo.grid(row=0, column=1, sticky="w")
        self.cmd_combo.bind("<<ComboboxSelected>>", lambda e: self._on_command_change())
        # Spacer stretches
        ttk.Label(top, text="").grid(row=0, column=2, sticky="ew")

    # ----- Command preview -----
    def _build_preview_row(self):
        prev = ttk.Frame(self)
        prev.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 6))
        prev.columnconfigure(1, weight=1)
        # Make Copy/Run/Reset columns equal width without forcing fixed sizes
        prev.columnconfigure(2, uniform='btn')
        prev.columnconfigure(3, uniform='btn')
        prev.columnconfigure(4, uniform='btn')
        ttk.Label(prev, text="Preview:").grid(row=0, column=0, sticky="w")
        self.preview_var = tk.StringVar()
        self.preview_entry = ttk.Entry(prev, textvariable=self.preview_var)
        self.preview_entry.grid(row=0, column=1, sticky="ew", padx=(6,6))
        # Make insertion cursor white in the preview box
        try:
            self.preview_entry.configure(insertbackground=self.colors["fg"])
        except Exception:
            pass
        copy_btn = ttk.Button(prev, text="Copy", command=self._copy_preview, style="Secondary.TButton")
        copy_btn.grid(row=0, column=2, sticky="ew", padx=(0,6))
        run_btn = ttk.Button(prev, text="Run", command=self._run, style="Accent.TButton")
        run_btn.grid(row=0, column=3, sticky="ew", padx=(0,6))
        reset_btn = ttk.Button(prev, text="Reset", command=self._reset_defaults)
        reset_btn.grid(row=0, column=4, sticky="ew")

    # ----- Forms (top) + Output (bottom) -----
    def _build_main_area(self):
        # Horizontal split: left option selector, right editor+output
        self.main_pane = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        self.main_pane.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 4))
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)

        # Left: Option Selector (scrollable)
        left_container = ttk.Frame(self.main_pane)
        left_container.rowconfigure(0, weight=1)
        left_container.columnconfigure(0, weight=1)
        self.main_pane.add(left_container, weight=1)

        self.selector_canvas = tk.Canvas(left_container, highlightthickness=0, bg=self.colors["bg"], bd=0)
        self.selector_scroll = ttk.Scrollbar(left_container, orient=tk.VERTICAL, command=self.selector_canvas.yview)
        self.selector_frame = ttk.Frame(self.selector_canvas)
        self.selector_frame.bind(
            "<Configure>",
            lambda e: self.selector_canvas.configure(scrollregion=self.selector_canvas.bbox("all"))
        )
        self.selector_canvas.create_window((0,0), window=self.selector_frame, anchor="nw")
        self.selector_canvas.configure(yscrollcommand=self.selector_scroll.set)
        self.selector_canvas.grid(row=0, column=0, sticky="nsew")
        self.selector_scroll.grid(row=0, column=1, sticky="ns")
        # Wheel on selector area
        self._bind_mousewheel_target(self.selector_canvas)
        self._bind_mousewheel_target(self.selector_frame, target=self.selector_canvas)

        # Right: editor (top) + notebook (bottom)
        right_container = ttk.Frame(self.main_pane)
        right_container.rowconfigure(0, weight=1)
        right_container.columnconfigure(0, weight=1)
        self.main_pane.add(right_container, weight=3)

        # Vertical splitter between selected options (top) and output/help (bottom)
        self.right_split = ttk.Panedwindow(right_container, orient=tk.VERTICAL, style="RightSplit.TPanedwindow")
        self.right_split.grid(row=0, column=0, sticky="nsew")
        try:
            self.right_split.configure(sashwidth=6)
        except Exception:
            pass

        self.selected_frame = ttk.LabelFrame(self.right_split, text="Selected Options")
        self.selected_frame.columnconfigure(1, weight=1)
        self.selected_frame.rowconfigure(0, weight=1)

        # Make selected options scrollable as well
        self.sel_canvas = tk.Canvas(self.selected_frame, highlightthickness=0, height=200, bg=self.colors["bg"], bd=0)
        self.sel_scroll = ttk.Scrollbar(self.selected_frame, orient=tk.VERTICAL, command=self.sel_canvas.yview)
        self.sel_inner = ttk.Frame(self.sel_canvas)
        # Track help labels for dynamic wrap updates
        self._help_labels: List[ttk.Label] = []
        # Update scrollregion and help label wrap lengths on size changes
        self.sel_inner.bind("<Configure>", self._on_sel_inner_configure)
        self.sel_canvas.create_window((0,0), window=self.sel_inner, anchor="nw")
        try:
            self.sel_canvas.configure(yscrollcommand=self._on_sel_yview)
        except Exception:
            self.sel_canvas.configure(yscrollcommand=self.sel_scroll.set)
        self.sel_canvas.grid(row=0, column=0, sticky="nsew")
        self.sel_scroll.grid(row=0, column=1, sticky="ns")
        # Wheel on selected options area
        self._bind_mousewheel_target(self.sel_canvas)
        self._bind_mousewheel_target(self.sel_inner, target=self.sel_canvas)

        # Output/Help tabs
        self.tabs = ttk.Notebook(self.right_split)
        # Track hover state for tab-close affordance
        self._hover_close_tab_id: Optional[str] = None
        # Bind mouse events to manage hover 'x' and closing
        try:
            self.tabs.bind("<Motion>", self._on_tab_motion, add=True)
            self.tabs.bind("<Leave>", self._on_tab_leave, add=True)
            self.tabs.bind("<Button-1>", self._on_tab_click_close, add=True)
        except Exception:
            pass
        # Output tab (foreground runs)
        out_tab = ttk.Frame(self.tabs)
        # Rows: 0 status, 1 splitter
        out_tab.rowconfigure(0, weight=0)
        out_tab.rowconfigure(1, weight=1)
        out_tab.columnconfigure(0, weight=1)
        # Status bar container: keeps layout stable when Stop hides/shows
        self.status_row = ttk.Frame(out_tab)
        self.status_row.grid(row=0, column=0, sticky="ew")
        self.status_row.columnconfigure(0, weight=0)
        self.status_row.columnconfigure(1, weight=1)
        # Status line above output
        self.run_status_var = tk.StringVar(value="")
        # Stop button (red with white text) to the left of the timer
        self.stop_btn = ttk.Button(self.status_row, text="Stop", command=self._stop_run, style="Stop.TButton")
        # Hidden by default until a run starts
        try:
            self.stop_btn.state(["disabled"])
        except Exception:
            pass
        # Will be gridded dynamically while a command is running
        self.run_status = ttk.Label(self.status_row, textvariable=self.run_status_var)
        self.run_status.grid(row=0, column=1, sticky="w", padx=4, pady=(2,2))
        # Vertical splitter exactly between route cards and console output
        self.out_split = ttk.Panedwindow(out_tab, orient=tk.VERTICAL, style="OutSplit.TPanedwindow")
        self.out_split.grid(row=1, column=0, sticky="nsew")
        try:
            self.out_split.configure(sashwidth=6)
        except Exception:
            pass
        # Top: route cards container (populated when parsing 'run' output)
        routes_panel = ttk.Frame(self.out_split)
        routes_panel.columnconfigure(0, weight=1)
        self.routes_frame = ttk.Frame(routes_panel)
        self.routes_frame.grid(row=0, column=0, sticky="ew", padx=4, pady=(2,4))
        self.routes_frame.columnconfigure(0, weight=1)
        self._route_cards: List[Dict[str, Any]] = []
        try:
            self.out_split.add(routes_panel, weight=1)
        except Exception:
            self.out_split.add(routes_panel)
        # Bottom: console output
        self.output = ScrolledText(self.out_split, wrap="word")
        try:
            self.out_split.add(self.output, weight=2)
        except Exception:
            self.out_split.add(self.output)
        self._style_scrolled_text(self.output)
        # Enable explicit Ctrl/Cmd+C copy for output
        self._enable_copy_shortcuts(self.output)
        # Track output scroll for sticky sessions (also forward to internal vbar)
        try:
            self.output.configure(yscrollcommand=self._on_output_yview)
        except Exception:
            pass
        self._bind_mousewheel_target(self.output)
        # Help tab
        self.help_tab = ttk.Frame(self.tabs)
        self.help_tab.rowconfigure(0, weight=1)
        self.help_tab.columnconfigure(0, weight=1)
        self.help_text = ScrolledText(self.help_tab, wrap="word", height=12)
        self.help_text.grid(row=0, column=0, sticky="nsew")
        self._style_scrolled_text(self.help_text)
        # Also allow copying from Help
        self._enable_copy_shortcuts(self.help_text)
        self._bind_mousewheel_target(self.help_text)
        self.tabs.add(out_tab, text="Output")
        self.tabs.add(self.help_tab, text="Help")
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Add top/bottom panes to the vertical splitter with weights
        try:
            self.right_split.add(self.selected_frame, weight=1)
            self.right_split.add(self.tabs, weight=2)
        except Exception:
            # Fallback if weights unsupported
            self.right_split.add(self.selected_frame)
            self.right_split.add(self.tabs)

    def _build_global_options(self):
        bottom = ttk.LabelFrame(self, text="Global Options")
        bottom.grid(row=3, column=0, sticky="ew", padx=8, pady=(0,8))
        for i in range(9):
            bottom.columnconfigure(i, weight=1 if i in (1,3,5) else 0)

        # CWD
        ttk.Label(bottom, text="CWD:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.cwd_var = tk.StringVar()
        _cwd_entry = ttk.Entry(bottom, textvariable=self.cwd_var)
        _cwd_entry.grid(row=0, column=1, sticky="ew", padx=(0,6))
        try:
            _cwd_entry.configure(insertbackground=self.colors["fg"])
        except Exception:
            pass
        ttk.Button(bottom, text="Browse...", command=self._browse_cwd).grid(row=0, column=2, sticky="e")
        # DB
        ttk.Label(bottom, text="DB:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.db_var = tk.StringVar()
        _db_entry = ttk.Entry(bottom, textvariable=self.db_var)
        _db_entry.grid(row=1, column=1, sticky="ew", padx=(0,6))
        try:
            _db_entry.configure(insertbackground=self.colors["fg"])
        except Exception:
            pass
        ttk.Button(bottom, text="Browse...", command=self._browse_db).grid(row=1, column=2, sticky="e")
        # Link-Ly
        ttk.Label(bottom, text="Link-Ly:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.linkly_var = tk.StringVar()
        _ll_entry = ttk.Entry(bottom, textvariable=self.linkly_var)
        _ll_entry.grid(row=2, column=1, sticky="ew", padx=(0,6))
        try:
            _ll_entry.configure(insertbackground=self.colors["fg"])
        except Exception:
            pass

        # Detail / Quiet / Debug counters on the right
        ttk.Label(bottom, text="Detail:").grid(row=0, column=6, sticky="e")
        self.detail_var = tk.IntVar(value=0)
        ttk.Spinbox(bottom, from_=0, to=5, textvariable=self.detail_var, width=3, command=self._update_preview).grid(row=0, column=7, sticky="e", padx=(0,6))
        ttk.Label(bottom, text="Quiet:").grid(row=1, column=6, sticky="e")
        self.quiet_var = tk.IntVar(value=0)
        ttk.Spinbox(bottom, from_=0, to=5, textvariable=self.quiet_var, width=3, command=self._update_preview).grid(row=1, column=7, sticky="e", padx=(0,6))
        ttk.Label(bottom, text="Debug:").grid(row=2, column=6, sticky="e")
        self.debug_var = tk.IntVar(value=0)
        ttk.Spinbox(bottom, from_=0, to=5, textvariable=self.debug_var, width=3, command=self._update_preview).grid(row=2, column=7, sticky="e", padx=(0,6))
        # Export button next to Debug
        ttk.Button(bottom, text="Export", command=self._export_output).grid(row=2, column=8, sticky="e", padx=(6,6))
        # Export/Import settings buttons
        ttk.Button(bottom, text="Export Settings", command=self._export_settings).grid(row=2, column=9, sticky="e", padx=(0,6))
        ttk.Button(bottom, text="Import Settings", command=self._import_settings).grid(row=2, column=10, sticky="e")

        # Trace to update preview when globals change
        self.cwd_var.trace_add("write", lambda *_: self._update_preview())
        self.db_var.trace_add("write", lambda *_: self._update_preview())
        self.linkly_var.trace_add("write", lambda *_: self._update_preview())

        # Also persist on change
        self.cwd_var.trace_add("write", lambda *_: self._save_prefs())
        self.db_var.trace_add("write", lambda *_: self._save_prefs())
        self.linkly_var.trace_add("write", lambda *_: self._save_prefs())
        self.detail_var.trace_add("write", lambda *_: self._save_prefs())
        self.quiet_var.trace_add("write", lambda *_: self._save_prefs())
        self.debug_var.trace_add("write", lambda *_: self._save_prefs())

    def _clear_option_frames(self):
        # Clear selector and selected entries
        for child in self.selector_frame.winfo_children():
            child.destroy()
        for child in self.sel_inner.winfo_children():
            child.destroy()
        self.widget_vars.clear()
        # Also keep a selection map
        self._selected: Dict[OptionSpec, Dict[str, Any]] = {}

    # ----- Populate dynamic forms -----
    def _on_command_change(self):
        # Before switching, capture current command state (if any)
        try:
            prev_label = getattr(self, "_current_cmd_label", None)
            if prev_label and self.current_meta:
                self._save_state_for_label(prev_label)
        except Exception:
            pass
        name = self.cmd_var.get()
        self.current_meta = self.cmd_metas.get(name)
        self._clear_option_frames()
        if not self.current_meta:
            return

        # Build left selector groups and pre-select required args
        groups = self._categorize_current()
        row = 0
        for group_name, specs in groups:
            lf = ttk.LabelFrame(self.selector_frame, text=group_name)
            lf.grid(row=row, column=0, sticky="ew", padx=4, pady=4)
            lf.columnconfigure(1, weight=1)
            r = 0
            for spec in specs:
                # Required args are in current_meta.arguments
                is_required = spec in self.current_meta.arguments
                var = tk.BooleanVar(value=is_required and not spec.is_flag or is_required)
                cb = ttk.Checkbutton(lf, variable=var)
                cb.grid(row=r, column=0, sticky="w")
                if is_required:
                    cb.state(["disabled"])  # Always selected
                ttk.Label(lf, text=spec.display_name).grid(row=r, column=1, sticky="w")
                # Keep ref
                self.widget_vars.setdefault(spec, {})["selected"] = var
                # Bind
                def make_cb(s=spec, v=var):
                    return lambda *_: (self._on_toggle_option(s, v.get()), self._save_prefs())
                var.trace_add("write", make_cb())
                # If pre-selected (required), add to editor panel
                if var.get():
                    self._ensure_selected_row(spec)
                r += 1
            row += 1

        # For buildcache, default -i and -f to selected (user can uncheck)
        try:
            if self.current_meta.name == 'buildcache':
                for spec, vars_ in list(self.widget_vars.items()):
                    if not isinstance(spec, OptionSpec):
                        continue
                    longflag = spec.long_flag
                    if longflag in ('--ignore-unknown', '--force'):
                        sv = vars_.get('selected')
                        if isinstance(sv, tk.BooleanVar) and not sv.get():
                            sv.set(True)
        except Exception:
            pass

        # Apply saved values for this command, if any
        self._apply_saved_state_for_current()
        # Preserve saved preview if available; otherwise compute fresh preview
        try:
            data = getattr(self, '_prefs', {}) or {}
            saved = (data.get('commands', {}) or {}).get(name, {})
            pv = saved.get('preview')
            if isinstance(pv, str) and pv:
                self.preview_var.set(pv)
            else:
                self._update_preview()
        except Exception:
            self._update_preview()
        self._save_prefs()
        # Track which command the UI currently represents
        self._current_cmd_label = name

    def _on_toggle_option(self, spec: OptionSpec, selected: bool):
        # enforce mutual exclusion if needed
        if selected and spec.group_id is not None:
            # Unselect other specs from the same group
            for other, vars_ in list(self.widget_vars.items()):
                if other is spec:
                    continue
                if isinstance(other, OptionSpec) and other.group_id == spec.group_id:
                    sv = vars_.get("selected")
                    if isinstance(sv, tk.BooleanVar) and sv.get():
                        sv.set(False)
                        # row will be removed in recursive call
        # Show/remove from editor
        if selected:
            self._ensure_selected_row(spec)
        else:
            self._remove_selected_row(spec)
        self._update_preview()

    def _ensure_selected_row(self, spec: OptionSpec):
        if spec in self._selected:
            return
        row = len(self._selected)
        lbl = ttk.Label(self.sel_inner, text=spec.display_name + ":")
        lbl.grid(row=row*2, column=0, sticky="w", padx=6, pady=(6,0))
        # For flags, show a checked indicator but no input
        if spec.is_flag:
            val_var = tk.BooleanVar(value=True)
            chk = ttk.Checkbutton(self.sel_inner, variable=val_var)
            chk.grid(row=row*2, column=1, sticky="w", padx=6, pady=(6,0))
            val_var.trace_add("write", lambda *_: (self._update_preview(), self._save_prefs()))
            help_lbl = None
            widgets = {"flag": val_var, "row": row}
        else:
            val_var = tk.StringVar()
            entry = ttk.Entry(self.sel_inner, textvariable=val_var)
            entry.grid(row=row*2, column=1, sticky="ew", padx=6, pady=(6,0))
            try:
                entry.configure(insertbackground=self.colors["fg"])
            except Exception:
                pass
            self.sel_inner.columnconfigure(1, weight=1)
            if spec.default not in (None, False):
                entry.insert(0, str(spec.default))
            val_var.trace_add("write", lambda *_: (self._update_preview(), self._save_prefs()))
            help_lbl = None
            if spec.help:
                help_lbl = ttk.Label(
                    self.sel_inner,
                    text=spec.help,
                    foreground=self.colors["muted"],
                    justify="left",
                    wraplength=max(300, self.sel_canvas.winfo_width() - 20 if self.sel_canvas.winfo_width() else 600),
                )
                help_lbl.grid(row=row*2+1, column=0, columnspan=2, sticky="ew", padx=6)
                self._help_labels.append(help_lbl)
            widgets = {"value": val_var, "row": row, "help": help_lbl}
        self._selected[spec] = widgets

    def _remove_selected_row(self, spec: OptionSpec):
        widgets = self._selected.pop(spec, None)
        if not widgets:
            return
        # Destroy row widgets: find widgets in the row (labels/entries)
        for w in list(self.sel_inner.grid_slaves()):
            info = w.grid_info()
            # Each row occupies two grid rows: row*2 and row*2+1
            if info.get("row") in (widgets.get("row", -1)*2, widgets.get("row", -1)*2 + 1):
                w.destroy()
        # Re-pack remaining rows
        for i, (s, wd) in enumerate(list(self._selected.items())):
            # Move to new row index i
            target_r0 = i*2
            # Find row of label of s
            for w in self.sel_inner.grid_slaves():
                inf = w.grid_info()
                if inf.get("row") == wd.get("row")*2:
                    w.grid(row=target_r0, column=inf.get("column"))
                elif inf.get("row") == wd.get("row")*2 + 1:
                    w.grid(row=target_r0+1, column=inf.get("column"))
            wd["row"] = i
        # Also purge from help label tracker
        try:
            hl = widgets.get("help")
            if hl in self._help_labels:
                self._help_labels.remove(hl)
        except Exception:
            pass
        self._save_prefs()

    # ----- Build args and preview -----
    def _build_args(self) -> List[str]:
        if not self.current_meta:
            return []
        # If this meta defines fixed args, use them verbatim
        if getattr(self.current_meta, "fixed_args", None):
            parts: List[str] = list(self.current_meta.fixed_args)  # includes subcommand
        else:
            parts: List[str] = [self.current_meta.name]

        # Selected options (right panel)
        for spec, wd in self._selected.items():
            if spec.is_flag:
                if wd.get("flag").get():
                    parts.append(spec.display_name)
            else:
                val = wd.get("value").get().strip()
                if val != "":
                    # For positional required arguments, emit only the value
                    if spec.is_positional:
                        parts.append(val)
                    # Support comma-separated values for append-type options
                    elif spec.multiple and "," in val:
                        for v in [x.strip() for x in val.split(',') if x.strip()]:
                            parts.extend([spec.display_name, v])
                    else:
                        parts.extend([spec.display_name, val])

        # Global/common switches
        # cwd (-C)
        if self.cwd_var.get().strip():
            parts.extend(["-C", self.cwd_var.get().strip()])
        # db
        if self.db_var.get().strip():
            parts.extend(["--db", self.db_var.get().strip()])
        # link-ly (-L)
        if self.linkly_var.get().strip():
            parts.extend(["-L", self.linkly_var.get().strip()])
        # detail (-v), quiet (-q), debug (-w)
        parts.extend(["-v"] * int(self.detail_var.get()))
        parts.extend(["-q"] * int(self.quiet_var.get()))
        parts.extend(["-w"] * int(self.debug_var.get()))

        return parts

    def _update_preview(self):
        args = self._build_args()
        # Render a shell-like preview
        cmd = [sys.executable, self.trade_py] + args
        def quote_double(s: str) -> str:
            s = str(s)
            if s is None:
                s = ""
            # For preview: trim leading/trailing whitespace
            s = s.strip()
            needs_quotes = (s == "" or any(ch.isspace() for ch in s) or "/" in s)
            if needs_quotes:
                return '"' + s.replace('"', '\\"') + '"'
            return s
        self.preview_var.set(" ".join(quote_double(p) for p in cmd))

    # ----- Layout helpers -----
    def _on_sel_inner_configure(self, event=None):
        # Maintain scrollregion and re-wrap help labels to available width
        try:
            self.sel_canvas.configure(scrollregion=self.sel_canvas.bbox("all"))
        except Exception:
            pass
        try:
            wrap = max(300, self.sel_canvas.winfo_width() - 20)
            for lbl in list(self._help_labels):
                try:
                    lbl.configure(wraplength=wrap)
                except Exception:
                    pass
        except Exception:
            pass

    # ----- Running the command -----
    def _run(self):
        self.output.delete("1.0", tk.END)
        self._start_timer()
        self._clear_routes()
        self._stop_requested = False
        args = [sys.executable, self.trade_py] + self._build_args()

        # Safeguard: pause/stop background writer tabs before DB‑exclusive tasks
        preempted = []
        try:
            if self._requires_db_exclusive(args):
                preempted = self._preempt_background_sessions()
        except Exception:
            preempted = []

        # If this is an import we treat as background, start it in its own tab
        bg_title = self._background_title_for_args(args)
        if bg_title:
            self._run_background(args, bg_title, resume_after=preempted)
            return

        def reader(preempted_sessions=preempted):
            try:
                # Start child in its own process group so we can signal it
                popen_kwargs = dict(
                    cwd=self.repo_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    bufsize=1,
                    env={**os.environ, "PYTHONIOENCODING": "UTF-8"},
                )
                if sys.platform.startswith('win'):
                    try:
                        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                    except Exception:
                        pass
                else:
                    try:
                        import os as _os
                        popen_kwargs["preexec_fn"] = _os.setsid
                    except Exception:
                        pass

                proc = subprocess.Popen(
                    args,
                    **popen_kwargs,
                )
                # Expose handle for Stop button
                self._proc = proc
            except Exception as e:
                self._append_output(f"Failed to start: {e}\n")
                self.after(0, self._finish_timer)
                return

            with proc.stdout:
                try:
                    for line in iter(proc.stdout.readline, ''):
                        self._append_output(line)
                except Exception:
                    pass
            rc = proc.wait()
            # Clear proc handle
            self._proc = None
            self.after(0, self._finish_timer)
            # Post-process output to extract routes (on main thread)
            self.after(0, self._process_routes_from_output)
            # Persist the latest output for this command so it restores on tab switch
            self.after(0, self._save_prefs)
            # Resume any preempted background sessions
            if preempted_sessions:
                self.after(0, lambda lst=preempted_sessions: self._resume_preempted_list(lst))

        threading.Thread(target=reader, daemon=True).start()

    def _stop_run(self):
        # Send an interrupt signal to the running process and stop quickly
        proc = getattr(self, "_proc", None)
        if not proc:
            return
        self._stop_requested = True
        try:
            self.run_status_var.set("Stopping...")
        except Exception:
            pass
        try:
            import signal
            if sys.platform.startswith('win'):
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                except Exception:
                    try:
                        proc.send_signal(signal.SIGINT)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
        except Exception:
            pass
        # Fallback: if still running after a short delay, force kill
        def _ensure_kill(p=proc):
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass
        try:
            self.after(3000, _ensure_kill)
        except Exception:
            pass

    # ----- Background sessions (EDDN Live) -----
    def _background_title_for_args(self, full_args: List[str]) -> Optional[str]:
        try:
            if len(full_args) < 4:
                return None
            sub = full_args[2]
            if sub != 'import':
                return None
            argv = full_args[3:]
            plug = None
            options_blob = " ".join(argv)
            for i, a in enumerate(argv):
                if a in ('-P', '--plug') and i + 1 < len(argv):
                    plug = argv[i+1].lower()
                    break
            if plug == 'eddn':
                if 'carrier_only' in options_blob or 'public_only' in options_blob:
                    return 'EDDN Live (Carriers)'
                return 'EDDN Live (All)'
            if plug == 'spansh':
                return 'Import Spansh Galaxy'
            if plug == 'eddblink':
                if 'listings_live' in options_blob:
                    return 'EDDB Link (Live Listings)'
                return 'EDDB Link Import'
            return None
        except Exception:
            return None

    def _run_background(self, args: List[str], title_hint: str, resume_after: Optional[List[Dict[str, Any]]] = None):
        # Build session tab
        self._bg_counter += 1

        tab = ttk.Frame(self.tabs)
        tab.rowconfigure(0, weight=0)
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        # Status row with Stop + timer
        status_row = ttk.Frame(tab)
        status_row.grid(row=0, column=0, sticky="ew")
        status_row.columnconfigure(0, weight=0)
        status_row.columnconfigure(1, weight=1)
        status_var = tk.StringVar(value="Running (00:00:00)")
        stop_btn = ttk.Button(status_row, text="Stop", style="Stop.TButton")
        stop_btn.grid(row=0, column=0, sticky="w", padx=(4,6), pady=(2,2))
        ttk.Label(status_row, textvariable=status_var).grid(row=0, column=1, sticky="w", padx=4, pady=(2,2))

        # Output area tails a log file
        output = ScrolledText(tab, wrap="word")
        self._style_scrolled_text(output)
        self._enable_copy_shortcuts(output)
        self._bind_mousewheel_target(output)
        output.grid(row=1, column=0, sticky="nsew")

        # Prepare log path
        try:
            logs_dir = os.path.join(self.repo_dir, 'logs')
            os.makedirs(logs_dir, exist_ok=True)
        except Exception:
            logs_dir = self.repo_dir
        logfile = os.path.join(logs_dir, f"eddn_live_{time.strftime('%Y%m%d_%H%M%S')}_{self._bg_counter}.log")

        # Start background process writing to log
        env = {**os.environ, "PYTHONIOENCODING": "UTF-8", "PYTHONUNBUFFERED": "1"}
        _stdout_file = open(logfile, 'w', encoding='utf-8', buffering=1)
        popen_kwargs = dict(cwd=self.repo_dir, stdout=_stdout_file, stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1, env=env)
        if sys.platform.startswith('win'):
            try:
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            except Exception:
                pass
        else:
            try:
                import os as _os
                popen_kwargs["preexec_fn"] = _os.setsid
            except Exception:
                pass
        try:
            proc = subprocess.Popen(args, **popen_kwargs)
        except Exception as e:
            self._append_output(f"Failed to start background task: {e}\n")
            return
        finally:
            try:
                _stdout_file.close()
            except Exception:
                pass

        # Tail the logfile in a thread
        stop_flag = {"stopped": False}

        def tailer():
            try:
                with open(logfile, 'r', encoding='utf-8', errors='replace') as fh:
                    fh.seek(0, os.SEEK_END)
                    while proc.poll() is None and not stop_flag["stopped"]:
                        line = fh.readline()
                        if not line:
                            time.sleep(0.25)
                            continue
                        try:
                            output.insert(tk.END, line)
                            output.see(tk.END)
                        except Exception:
                            pass
            except Exception:
                pass
            # Process finished
            try:
                stop_btn.state(["disabled"])
            except Exception:
                pass
            # Update status as finished
            try:
                status_var.set("Finished (see log)")
            except Exception:
                pass
            # Resume any preempted background sessions after finish
            if resume_after:
                self.after(0, lambda lst=resume_after: self._resume_preempted_list(lst))

        threading.Thread(target=tailer, daemon=True).start()

        # Timer for this bg session
        timer = {"start": time.monotonic(), "job": None}

        def tick():
            if proc.poll() is not None or stop_flag["stopped"]:
                return
            elapsed = time.monotonic() - timer["start"]
            try:
                status_var.set(f"Running ({self._format_elapsed(elapsed)})")
            except Exception:
                pass
            timer["job"] = self.after(1000, tick)

        timer["job"] = self.after(1000, tick)

        # Stop handler for this session
        def stop_bg():
            try:
                import signal
                if sys.platform.startswith('win'):
                    try:
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    except Exception:
                        proc.terminate()
                else:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                    except Exception:
                        try:
                            proc.send_signal(signal.SIGINT)
                        except Exception:
                            proc.terminate()
            except Exception:
                pass
            # Ensure kill if needed after delay
            def _ensure():
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass
                stop_flag["stopped"] = True
                try:
                    stop_btn.state(["disabled"])
                except Exception:
                    pass
            self.after(3000, _ensure)

        stop_btn.configure(command=stop_bg)

        # Register session and add tab before Help
        tab_id = str(tab)
        self._bg_sessions[tab_id] = {
            'proc': proc,
            'output': output,
            'status_var': status_var,
            'stop_btn': stop_btn,
            'start': timer["start"],
            'logfile': logfile,
            'title': title_hint,
            'stop_flag': stop_flag,
            'args': list(args),
        }
        # Insert this tab just before Help
        try:
            help_index = self.tabs.index(self.help_tab)
        except Exception:
            help_index = 'end'
        self.tabs.insert(help_index, tab, text=title_hint)
        # Focus new tab
        try:
            self.tabs.select(tab)
        except Exception:
            pass

    # ----- DB exclusive safeguard helpers -----
    def _requires_db_exclusive(self, full_args: List[str]) -> bool:
        try:
            if len(full_args) < 3:
                return False
            sub = full_args[2]
            if sub == 'buildcache':
                return True
            if sub != 'import':
                return False
            argv = full_args[3:]
            plug = None
            options_blob = " ".join(argv)
            for i, a in enumerate(argv):
                if a in ('-P', '--plug') and i + 1 < len(argv):
                    plug = argv[i+1].lower()
                    break
            if plug == 'eddblink':
                # Heaviest when doing clean/all (full schema/data rebuild)
                if 'clean' in options_blob or 'all' in options_blob:
                    return True
            if plug == 'spansh':
                # Large write import
                return True
            return False
        except Exception:
            return False

    def _preempt_background_sessions(self) -> List[Dict[str, Any]]:
        """Stop all running background sessions and return a list of sessions to resume later."""
        to_resume: List[Dict[str, Any]] = []
        # Collect current sessions to resume
        for tab_id, sess in list(self._bg_sessions.items()):
            try:
                proc = sess.get('proc')
                args = sess.get('args')
                title = sess.get('title')
                if proc and proc.poll() is None and args:
                    to_resume.append({'args': list(args), 'title': title})
                    # Close/stop the tab
                    self._close_bg_tab(tab_id)
            except Exception:
                continue
        return to_resume

    def _resume_preempted_list(self, resume_list: List[Dict[str, Any]]):
        try:
            for ent in resume_list or []:
                args = ent.get('args')
                title = ent.get('title') or self._background_title_for_args(args or []) or 'Background Task'
                if args:
                    self._run_background(args, title)
        except Exception:
            pass

    # ----- Tab close UX -----
    def _tab_under_pointer(self, x: int, y: int) -> Optional[int]:
        try:
            return self.tabs.index(f"@{x},{y}")
        except Exception:
            return None

    def _tab_id_from_index(self, idx: int) -> Optional[str]:
        try:
            tab_widget = self.tabs.tabs()[idx]
            return tab_widget
        except Exception:
            return None

    def _is_closable_tab(self, tab_id: str) -> bool:
        return tab_id in self._bg_sessions

    def _restore_tab_title(self, tab_id: str):
        try:
            if tab_id in self._bg_sessions:
                title = self._bg_sessions[tab_id].get('title') or ''
                self.tabs.tab(tab_id, text=title)
        except Exception:
            pass

    def _show_tab_close_glyph(self, tab_id: str):
        try:
            if tab_id in self._bg_sessions:
                base = self._bg_sessions[tab_id].get('title') or ''
                # Append a small space and the multiplication sign
                self.tabs.tab(tab_id, text=f"{base}  ×")
        except Exception:
            pass

    def _on_tab_motion(self, event=None):
        try:
            x, y = event.x, event.y
        except Exception:
            return
        idx = self._tab_under_pointer(x, y)
        if idx is None:
            # Not over a tab; restore any previous
            if self._hover_close_tab_id:
                self._restore_tab_title(self._hover_close_tab_id)
                self._hover_close_tab_id = None
            return
        tab_id = self._tab_id_from_index(idx)
        if not tab_id:
            return
        if self._is_closable_tab(tab_id):
            # If hovering a new tab, restore old and show on new
            if self._hover_close_tab_id and self._hover_close_tab_id != tab_id:
                self._restore_tab_title(self._hover_close_tab_id)
            self._show_tab_close_glyph(tab_id)
            self._hover_close_tab_id = tab_id
        else:
            # Not closable; ensure previous closable restored
            if self._hover_close_tab_id:
                self._restore_tab_title(self._hover_close_tab_id)
                self._hover_close_tab_id = None

    def _on_tab_leave(self, event=None):
        if self._hover_close_tab_id:
            self._restore_tab_title(self._hover_close_tab_id)
            self._hover_close_tab_id = None

    def _on_tab_click_close(self, event=None):
        try:
            x, y = event.x, event.y
        except Exception:
            return
        idx = self._tab_under_pointer(x, y)
        if idx is None:
            return
        tab_id = self._tab_id_from_index(idx)
        if not tab_id or not self._is_closable_tab(tab_id):
            return
        # Determine if click was on the rightmost 16px of the tab (close hitbox)
        try:
            bx, by, bw, bh = self.tabs.bbox(idx)
        except Exception:
            bx = by = bw = bh = 0
        # Hitbox
        hx0 = bx + max(bw - 16, 0)
        hy0 = by
        hx1 = bx + bw
        hy1 = by + bh
        # If we're hovering a closable tab, allow click anywhere on the tab header to close
        if self._hover_close_tab_id == tab_id or (x >= hx0 and x <= hx1 and y >= hy0 and y <= hy1):
            # Close the tab (and stop any running process)
            self._close_bg_tab(tab_id)
            # Prevent default tab selection
            return "break"

    def _close_bg_tab(self, tab_id: str):
        sess = self._bg_sessions.get(tab_id)
        if not sess:
            # Not a background tab; nothing to do
            try:
                self.tabs.forget(tab_id)
            except Exception:
                pass
            return
        # Signal stop to running process if any
        try:
            proc = sess.get('proc')
            stop_flag = sess.get('stop_flag')
            if isinstance(stop_flag, dict):
                stop_flag['stopped'] = True
            if proc and proc.poll() is None:
                import signal
                if sys.platform.startswith('win'):
                    try:
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                else:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                    except Exception:
                        try:
                            proc.send_signal(signal.SIGINT)
                        except Exception:
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                # Ensure kill after a grace period if still running
                def _ensure_kill(p=proc):
                    try:
                        if p.poll() is None:
                            p.kill()
                    except Exception:
                        pass
                try:
                    self.after(3000, _ensure_kill)
                except Exception:
                    pass
        except Exception:
            pass
        # Remove tab from notebook
        try:
            self.tabs.forget(tab_id)
        except Exception:
            pass
        # Drop session entry
        try:
            self._bg_sessions.pop(tab_id, None)
        except Exception:
            pass

    def _append_output(self, text: str):
        def _append():
            self.output.insert(tk.END, text)
            self.output.see(tk.END)
        self.after(0, _append)

    def _clear_output(self):
        self.output.delete("1.0", tk.END)
    
    # ----- Routes parsing and UI -----
    def _clear_routes(self):
        for child in list(self.routes_frame.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self._route_cards = []
    
    def _strip_ansi(self, s: str) -> str:
        import re
        return re.sub(r"\x1b\[[0-9;]*m", "", s)
    
    def _parse_routes(self, text: str) -> List[Dict[str, str]]:
        import re
        # Normalize text
        text = self._strip_ansi(text)
        lines = text.splitlines()
        routes: List[Dict[str, str]] = []
        current: List[str] = []
        start_pat = re.compile(r"^\s*(.+?)\s*->\s*(.+?)(?:\s*\(score:.*)?\s*$")
        for ln in lines:
            if start_pat.match(ln):
                if current:
                    block = "\n".join(current).strip()
                    if block:
                        # Extract destination from the first line of block
                        m = start_pat.match(current[0])
                        dest_line = m.group(2).strip() if m else ""
                        routes.append({"block": block, "dest": dest_line})
                    current = []
            if ln.strip() == "":
                # keep blank lines to preserve block text, but don't accumulate leading empties
                if current:
                    current.append(ln)
                continue
            current.append(ln)
        if current:
            block = "\n".join(current).strip()
            if block:
                m = start_pat.match(current[0])
                dest_line = m.group(2).strip() if m else ""
                routes.append({"block": block, "dest": dest_line})
        return routes
    
    def _process_routes_from_output(self):
        try:
            if not self.current_meta or self.current_meta.name != 'run':
                self._clear_routes()
                return
            text = self.output.get("1.0", tk.END)
            routes = self._parse_routes(text)
            if not routes:
                self._clear_routes()
                return
            self._build_route_cards(routes)
            # Save now that routes exist so selection state is persisted
            self._schedule_save()
        except Exception:
            # Don't let UI crash because of parsing issues
            self._clear_routes()
            return
    
    def _build_route_cards(self, routes: List[Dict[str, str]]):
        self._clear_routes()
        # Style for route cards
        try:
            style = ttk.Style(self)
            style.configure("RouteCard.TFrame", background=self.colors["panel"], bordercolor=self.colors["line"], relief="groove")
            style.configure("RouteCardSelected.TFrame", background=self.colors["surface"], bordercolor=self.colors["primary"], relief="solid")
            style.configure("RouteTitle.TLabel", background=self.colors["panel"], foreground=self.colors["fg"])
            style.configure("RouteBody.TLabel", background=self.colors["panel"], foreground=self.colors["muted"], wraplength=900, justify="left")
        except Exception:
            pass
        # Build cards
        for idx, rt in enumerate(routes):
            card = ttk.Frame(self.routes_frame, style="RouteCard.TFrame")
            card.grid(row=idx, column=0, sticky="ew", padx=2, pady=2)
            card.columnconfigure(0, weight=1)
            # Title (first line)
            title = rt["block"].splitlines()[0]
            lbl_title = ttk.Label(card, text=title, style="RouteTitle.TLabel")
            lbl_title.grid(row=0, column=0, sticky="w", padx=8, pady=(6,2))
            # Body (optional: show a short preview of next lines)
            body_lines = rt["block"].splitlines()[1:6]
            if body_lines:
                lbl_body = ttk.Label(card, text="\n".join(body_lines), style="RouteBody.TLabel")
                lbl_body.grid(row=1, column=0, sticky="ew", padx=8)
            # Actions row
            btn_row = ttk.Frame(card, style="RouteCard.TFrame")
            btn_row.grid(row=2, column=0, sticky="ew", padx=8, pady=(4,8))
            btn_row.columnconfigure(0, weight=1)
            btn_row.columnconfigure(1, weight=0)
            btn_row.columnconfigure(2, weight=0)
            dest = rt.get("dest", "").strip()
            btn_copy = ttk.Button(btn_row, text="Copy Dest", command=lambda d=dest: self._copy_text(d), style="Secondary.TButton")
            btn_copy.grid(row=0, column=1, sticky="e", padx=(6,0))
            btn_swap = ttk.Button(btn_row, text="Swap to From", command=lambda d=dest: self._swap_from_to_dest(d))
            btn_swap.grid(row=0, column=2, sticky="e", padx=(6,0))
            # Click to select highlight
            def on_select(event=None, i=idx):
                self._select_route_card(i)
            for w in (card, lbl_title, btn_row):
                try:
                    w.bind("<Button-1>", on_select)
                except Exception:
                    pass
            self._route_cards.append({"frame": card, "dest": dest, "title": title})
        # Default select first
        if self._route_cards:
            self._select_route_card(0)
    
    def _select_route_card(self, index: int):
        for i, rc in enumerate(self._route_cards):
            try:
                rc["frame"].configure(style="RouteCardSelected.TFrame" if i == index else "RouteCard.TFrame")
            except Exception:
                pass
        self._selected_route_index = index
        # Persist route selection
        try:
            label = self.cmd_var.get()
            data = dict(getattr(self, '_prefs', {}) or {})
            cmd_state = data.setdefault('commands', {}).setdefault(label, {})
            cmd_state.setdefault('route', {})['selected_index'] = int(index)
            self._prefs = data
        except Exception:
            pass
        self._schedule_save()
    
    def _copy_text(self, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass
    
    def _swap_from_to_dest(self, dest: str):
        # Ensure we're on the 'run' command
        if not self.current_meta or self.current_meta.name != 'run':
            # Switch to run command if available
            if 'run' in self.cmd_metas:
                self.cmd_var.set('run')
                self._on_command_change()
            else:
                return
        # Find the '--from' spec (dest 'starting')
        spec = None
        for s in self.current_meta.arguments + self.current_meta.switches:
            if (s.dest == 'starting') or (s.long_flag.startswith('--from')):
                spec = s
                break
        if not spec:
            return
        # Ensure selected and set value
        sel_var = self.widget_vars.get(spec, {}).get('selected')
        if sel_var is not None and not sel_var.get():
            sel_var.set(True)
            self._ensure_selected_row(spec)
        row = self._selected.get(spec)
        if row and row.get('value') is not None:
            try:
                row['value'].set(dest)
            except Exception:
                pass
        self._update_preview()
        self._save_prefs()

    # ----- Export helpers -----
    def _default_export_filename(self) -> str:
        return time.strftime("TD_%Y%m%d_%H%M%S")

    def _export_output(self):
        text = self.output.get("1.0", tk.END)
        if not text.strip():
            messagebox.showinfo("Export", "There is no output to export yet.")
            return
        # Ask for destination path and format via a standard Save dialog
        initial = self._default_export_filename()
        path = filedialog.asksaveasfilename(
            title="Export Output",
            defaultextension=".txt",
            initialfile=initial,
            filetypes=[
                ("PDF", "*.pdf"),
                ("CSV", "*.csv"),
                ("Text", "*.txt"),
                ("Flat (one line per route)", "*.flat;*.flat.txt"),
                ("Raw text", "*.raw;*.raw.txt"),
                ("All Files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            lower = path.lower()
            if lower.endswith(".pdf"):
                self._export_pdf(self._strip_ansi(text), path)
            elif lower.endswith(".csv"):
                routes = self._parse_routes(text)
                self._export_csv(routes, path)
            elif lower.endswith(".flat") or lower.endswith(".flat.txt"):
                routes = self._parse_routes(text)
                self._export_flat(routes, path)
            elif lower.endswith(".raw") or lower.endswith(".raw.txt"):
                self._export_txt(text, path, pretty=False)
            else:
                # default to pretty text
                self._export_txt(self._strip_ansi(text), path, pretty=True)
            messagebox.showinfo("Export", f"Exported to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Failed", str(e))

    # ----- Export/Import settings -----
    def _globals_snapshot(self) -> Dict[str, Any]:
        try:
            return {
                'cwd': self.cwd_var.get().strip(),
                'db': self.db_var.get().strip(),
                'linkly': self.linkly_var.get().strip(),
                'detail': int(self.detail_var.get()),
                'quiet': int(self.quiet_var.get()),
                'debug': int(self.debug_var.get()),
            }
        except Exception:
            return {}

    def _export_settings(self):
        """Export the current command settings (including globals) to a JSON file.
        Includes all options, even unselected or blank values, so importing can
        reconstruct the exact state.
        """
        try:
            import json
            label = self.cmd_var.get()
            if not label or not self.current_meta:
                messagebox.showinfo("Export Settings", "No command selected to export.")
                return
            snap = self._capture_session(label)
            payload = {
                'version': 1,
                'exported_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'selected_command': label,
                'globals': self._globals_snapshot(),
                'commands': {label: snap},
            }
            initial = f"TD_SETTINGS_{self._default_export_filename()}"
            path = filedialog.asksaveasfilename(
                title="Export Settings",
                defaultextension=".json",
                initialfile=initial,
                filetypes=[
                    ("JSON", "*.json"),
                    ("All Files", "*.*"),
                ],
            )
            if not path:
                return
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
            messagebox.showinfo("Export Settings", f"Exported settings to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Settings Failed", str(e))

    def _import_settings(self):
        """Import settings JSON and restore UI: command, options, and globals."""
        try:
            import json
            path = filedialog.askopenfilename(
                title="Import Settings",
                filetypes=[
                    ("JSON", "*.json"),
                    ("All Files", "*.*"),
                ],
            )
            if not path:
                return
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Determine command label and snapshot
            label = None
            snap: Dict[str, Any] = {}

            if isinstance(data, dict):
                # Preferred format
                if isinstance(data.get('commands'), dict):
                    label = data.get('selected_command')
                    if not label:
                        # fall back to first command present
                        try:
                            label = next(iter(data['commands'].keys()))
                        except Exception:
                            label = None
                    if label:
                        snap = data['commands'].get(label) or {}
                # Raw snapshot fallback
                if not snap and any(k in data for k in ('options', 'preview', 'output')):
                    snap = data
                    # Allow explicit command name in raw snapshot
                    label = data.get('command') or data.get('name') or self.cmd_var.get()

                # Apply globals if provided
                gl = data.get('globals') if isinstance(data.get('globals'), dict) else None
                if gl:
                    try:
                        if 'cwd' in gl:
                            self.cwd_var.set(gl.get('cwd') or '')
                        if 'db' in gl:
                            self.db_var.set(gl.get('db') or '')
                        if 'linkly' in gl:
                            self.linkly_var.set(gl.get('linkly') or '')
                        if 'detail' in gl:
                            self.detail_var.set(int(gl.get('detail') or 0))
                        if 'quiet' in gl:
                            self.quiet_var.set(int(gl.get('quiet') or 0))
                        if 'debug' in gl:
                            self.debug_var.set(int(gl.get('debug') or 0))
                    except Exception:
                        # Ignore malformed globals
                        pass

            if not label or not snap:
                messagebox.showerror("Import Settings", "Could not find command settings in the file.")
                return

            if label not in self.cmd_metas:
                messagebox.showerror(
                    "Import Settings",
                    f"Command '{label}' is not available in this build.")
                return

            # Merge imported snapshot into in-memory prefs and switch UI to it
            pref_data = dict(getattr(self, '_prefs', {}) or {})
            cmds = pref_data.setdefault('commands', {})
            # Keep any pre-existing keys for that command but override with imported
            base = dict(cmds.get(label, {}) or {})
            base.update(snap)
            cmds[label] = base
            pref_data['selected_command'] = label

            # Temporarily suspend auto-saves to avoid overwriting the just-imported
            # state when switching commands (especially if importing into the same
            # command currently shown).
            old_susp = getattr(self, '_suspend_save', False)
            try:
                self._suspend_save = True
                self._prefs = pref_data
                # Switch command; _on_command_change will apply saved state
                self.cmd_var.set(label)
                self._on_command_change()
            finally:
                self._suspend_save = old_susp
            # Now persist to disk
            self._save_prefs()
            messagebox.showinfo("Import Settings", f"Imported settings for command: {label}")
        except Exception as e:
            messagebox.showerror("Import Settings Failed", str(e))

    def _export_txt(self, text: str, path: str, pretty: bool = True):
        header = []
        if pretty:
            header.append("Trade Dangerous GUI Export")
            header.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            header.append(f"Command: {self.preview_var.get()}")
            header.append("-" * 80)
        with open(path, "w", encoding="utf-8") as f:
            if header:
                f.write("\n".join(header) + "\n\n")
            f.write(text.rstrip() + "\n")

    def _export_flat(self, routes: List[Dict[str, str]], path: str):
        lines: List[str] = []
        lines.append(f"Trade Dangerous GUI Flat Export ({time.strftime('%Y-%m-%d %H:%M:%S')})")
        lines.append(f"Command: {self.preview_var.get()}")
        lines.append("-" * 80)
        for i, r in enumerate(routes, 1):
            title = self._strip_ansi(r.get("block", "").splitlines()[0])
            lines.append(f"{i:02d}. {title}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _export_csv(self, routes: List[Dict[str, str]], path: str):
        import csv
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["index", "origin", "destination", "route_title", "detail_preview"]) 
            for i, r in enumerate(routes, 1):
                title = self._strip_ansi(r.get("block", "").splitlines()[0])
                parts = [p.strip() for p in title.split("->", 1)]
                origin = parts[0] if parts else ""
                dest = parts[1] if len(parts) > 1 else r.get("dest", "")
                body_lines = r.get("block", "").splitlines()[1:6]
                preview = " | ".join(self._strip_ansi(bl).strip() for bl in body_lines)
                w.writerow([i, origin, dest, title, preview])

    def _export_pdf(self, text: str, path: str):
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import inch
            from reportlab.pdfbase.pdfmetrics import stringWidth
        except Exception:
            raise RuntimeError("PDF export requires 'reportlab'. Install with: pip install reportlab")
        page_size = letter
        c = canvas.Canvas(path, pagesize=page_size)
        width, height = page_size
        left = 0.75 * inch
        right = width - 0.75 * inch
        top = height - 0.75 * inch
        y = top
        font_name = "Courier"
        font_size = 10
        line_h = 12
        c.setFont(font_name, font_size)
        # Header
        header = [
            "Trade Dangerous GUI Export",
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Command: {self.preview_var.get()}",
            "-" * 80,
        ]
        lines = header + text.splitlines()
        def wrap_line(s: str) -> List[str]:
            s = s.replace('\t', '    ')
            maxw = right - left
            if stringWidth(s, font_name, font_size) <= maxw:
                return [s]
            # word wrap
            out: List[str] = []
            cur = ""
            for word in s.split(" "):
                trial = (cur + (" " if cur else "") + word)
                if stringWidth(trial, font_name, font_size) <= maxw:
                    cur = trial
                else:
                    if cur:
                        out.append(cur)
                    cur = word
            if cur:
                out.append(cur)
            return out
        for raw in lines:
            for ln in wrap_line(raw):
                if y - line_h < 0.75 * inch:
                    c.showPage()
                    c.setFont(font_name, font_size)
                    y = top
                c.drawString(left, y, ln)
                y -= line_h
        c.save()

    def _show_help(self):
        # Show CLI help for the selected command into the Help tab
        if not self.current_meta:
            return
        args = [sys.executable, self.trade_py, self.current_meta.name, "-h"]

        def run_help():
            try:
                out = subprocess.check_output(
                    args,
                    cwd=self.repo_dir,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    env={**os.environ, "PYTHONIOENCODING": "UTF-8"},
                )
            except subprocess.CalledProcessError as e:
                out = e.output
            def write():
                self.help_text.delete("1.0", tk.END)
                self.help_text.insert(tk.END, out)
                self.tabs.select(1)
            self.after(0, write)

        threading.Thread(target=run_help, daemon=True).start()

    def _on_tab_changed(self, event=None):
        # Load help when Help tab is selected
        try:
            sel = self.tabs.select()
            widget = self.tabs.nametowidget(sel)
        except Exception:
            return
        if widget is self.help_tab:
            self._show_help()
        # Persist selected tab index for the current command (foreground UX only)
        try:
            current = self.tabs.index(sel)
            label = self.cmd_var.get()
            data = dict(getattr(self, '_prefs', {}) or {})
            cmd_state = data.setdefault('commands', {}).setdefault(label, {})
            cmd_state['tab_index'] = int(current)
            self._prefs = data
        except Exception:
            pass
        self._schedule_save()

    # ----- Run timer helpers -----
    def _format_elapsed(self, seconds: float) -> str:
        sec = max(0, int(seconds))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _tick_timer(self):
        if not getattr(self, "_timer_running", False):
            return
        elapsed = time.monotonic() - getattr(self, "_timer_start", time.monotonic())
        self.run_status_var.set(f"Running ({self._format_elapsed(elapsed)})")
        # Schedule next tick
        self._timer_job = self.after(1000, self._tick_timer)

    def _start_timer(self):
        self._timer_start = time.monotonic()
        self._timer_running = True
        # Show and enable the Stop button while running
        try:
            self._show_stop_btn()
        except Exception:
            pass
        # Cancel any previous scheduled job
        try:
            if getattr(self, "_timer_job", None):
                self.after_cancel(self._timer_job)
        except Exception:
            pass
        self.run_status_var.set("Running (00:00:00)")
        self._timer_job = self.after(1000, self._tick_timer)

    def _finish_timer(self):
        # Stop ticking and show final elapsed
        start = getattr(self, "_timer_start", None)
        if start is None:
            # Nothing ran; clear status
            self.run_status_var.set("Finished (00:00:00)")
            return
        self._timer_running = False
        try:
            if getattr(self, "_timer_job", None):
                self.after_cancel(self._timer_job)
        except Exception:
            pass
        elapsed = time.monotonic() - start
        try:
            if getattr(self, "_stop_requested", False):
                self.run_status_var.set(f"Stopped ({self._format_elapsed(elapsed)})")
            else:
                self.run_status_var.set(f"Finished ({self._format_elapsed(elapsed)})")
        except Exception:
            self.run_status_var.set(f"Finished ({self._format_elapsed(elapsed)})")
        # Hide the Stop button when no command is running
        try:
            self._hide_stop_btn()
        except Exception:
            pass

    def _show_stop_btn(self):
        try:
            self.stop_btn.grid(row=0, column=0, sticky="w", padx=(4,6), pady=(2,2))
            self.stop_btn.state(["!disabled"])
        except Exception:
            pass

    def _hide_stop_btn(self):
        try:
            self.stop_btn.state(["disabled"])
            self.stop_btn.grid_remove()
        except Exception:
            pass

    def _copy_preview(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.preview_var.get())
        except Exception:
            pass

    # ----- Preferences (persist CWD/DB) -----
    def _config_dir(self) -> str:
        if sys.platform.startswith('win'):
            base = os.getenv('APPDATA') or os.path.expanduser('~')
            return os.path.join(base, 'TradeDangerous')
        elif sys.platform == 'darwin':
            base = os.path.expanduser('~/Library/Application Support')
            return os.path.join(base, 'TradeDangerous')
        else:
            base = os.path.join(os.path.expanduser('~'), '.config')
            return os.path.join(base, 'TradeDangerous')

    def _prefs_path(self) -> str:
        return os.path.join(self._config_dir(), 'td_gui_prefs.json')

    def _load_prefs(self):
        try:
            p = self._prefs_path()
            if os.path.isfile(p):
                import json
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # Keep raw prefs for later saves
                self._prefs = data if isinstance(data, dict) else {}
                cwd = self._prefs.get('cwd')
                db = self._prefs.get('db')
                linkly = self._prefs.get('linkly')
                detail = self._prefs.get('detail')
                quiet = self._prefs.get('quiet')
                debug = self._prefs.get('debug')
                self._restore_cmd = self._prefs.get('selected_command')
                if isinstance(cwd, str):
                    self.cwd_var.set(cwd)
                if isinstance(db, str):
                    self.db_var.set(db)
                if isinstance(linkly, str):
                    self.linkly_var.set(linkly)
                if isinstance(detail, int):
                    self.detail_var.set(detail)
                if isinstance(quiet, int):
                    self.quiet_var.set(quiet)
                if isinstance(debug, int):
                    self.debug_var.set(debug)
        except Exception:
            # Ignore preference loading errors silently
            self._prefs = {}

    def _save_prefs(self):
        if getattr(self, '_suspend_save', False):
            return
        try:
            d = self._config_dir()
            os.makedirs(d, exist_ok=True)
            import json
            # Start with previous prefs to preserve per-command states
            data = dict(getattr(self, '_prefs', {}) or {})
            # Capture current command snapshot (options/output/preview/tab/scroll/route)
            try:
                current_label = self.cmd_var.get()
                if current_label and self.current_meta:
                    snap = self._capture_session(current_label)
                    data.setdefault('commands', {})[current_label] = {
                        **data.get('commands', {}).get(current_label, {}),
                        **snap,
                    }
            except Exception:
                pass
            # Update globals
            data.update({
                'cwd': self.cwd_var.get().strip(),
                'db': self.db_var.get().strip(),
                'linkly': self.linkly_var.get().strip(),
                'detail': int(self.detail_var.get()),
                'quiet': int(self.quiet_var.get()),
                'debug': int(self.debug_var.get()),
                'selected_command': self.cmd_var.get(),
            })
            # Legacy path handled by snapshot above; nothing further needed for current command
            self._prefs = data
            with open(self._prefs_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            # Ignore preference saving errors silently
            pass

    def _on_close(self):
        try:
            self._save_prefs()
        except Exception:
            pass
        self.destroy()

    def _apply_saved_state_for_current(self):
        """Apply saved selection and values for the current command, if available."""
        try:
            if not self.current_meta:
                return
            data = getattr(self, '_prefs', {}) or {}
            cmds = data.get('commands', {})
            state = cmds.get(self.cmd_var.get(), {})
            options = state.get('options', {})
            # Update selection states and row values
            for group_name, specs in self._categorize_current():
                for spec in specs:
                    # Be liberal in what we accept: try several possible keys
                    opt = (
                        options.get(spec.key)
                        or (spec.dest and options.get(spec.dest))
                        or options.get(spec.long_flag)
                        or options.get(spec.long_flag.lstrip('-'))
                        or (spec.args and options.get(spec.args[0]))
                        or None
                    )
                    if not opt:
                        continue
                    is_required = spec in self.current_meta.arguments
                    target_sel = True if is_required else bool(opt.get('selected', False))
                    sel_var = self.widget_vars.get(spec, {}).get('selected')
                    if sel_var is not None:
                        try:
                            sel_var.set(target_sel)
                        except Exception:
                            pass
                    if target_sel:
                        self._ensure_selected_row(spec)
                        row = self._selected.get(spec)
                        if spec.is_flag:
                            try:
                                row.get('flag').set(bool(opt.get('flag', True)))
                            except Exception:
                                pass
                        else:
                            if 'value' in opt and row and row.get('value') is not None:
                                try:
                                    row.get('value').set(str(opt.get('value') or ''))
                                except Exception:
                                    pass
            # Restore saved terminal output for this command, if available
            out = state.get('output')
            if isinstance(out, str):
                try:
                    self.output.delete("1.0", tk.END)
                    self.output.insert(tk.END, out)
                    self.output.see(tk.END)
                except Exception:
                    pass
                # Rebuild route cards if this is 'run'
                if self.current_meta.name == 'run':
                    try:
                        self._process_routes_from_output()
                    except Exception:
                        pass
            # Restore saved notebook tab index
            try:
                ti = state.get('tab_index')
                if isinstance(ti, int) and ti in (0, 1):
                    self.tabs.select(ti)
            except Exception:
                pass
            # Restore preview last if saved
            try:
                pv = state.get('preview')
                if isinstance(pv, str) and pv:
                    self.preview_var.set(pv)
            except Exception:
                pass
            # Restore scroll positions (defer to ensure widgets laid out)
            try:
                scr = state.get('scroll') or {}
                oy = scr.get('output_yview')
                sy = scr.get('selected_yview')
                if isinstance(oy, (list, tuple)) and len(oy) >= 1:
                    first = float(oy[0])
                    self.after(0, lambda f=first: self._safe_yview_moveto(self.output, f))
                if isinstance(sy, (list, tuple)) and len(sy) >= 1:
                    first = float(sy[0])
                    self.after(0, lambda f=first: self._safe_yview_moveto(self.sel_canvas, f))
            except Exception:
                pass
            # Restore selected route index if any
            try:
                rt = state.get('route') or {}
                idx = rt.get('selected_index')
                if isinstance(idx, int) and self.current_meta and self.current_meta.name == 'run':
                    if getattr(self, '_route_cards', None):
                        if 0 <= idx < len(self._route_cards):
                            self._select_route_card(idx)
            except Exception:
                pass
        except Exception:
            pass

    def _save_state_for_label(self, label: str):
        """Save the current on-screen command state under the given label without
        changing the selected command in preferences. Used when switching commands
        during the session so each command restores exactly as left.
        """
        if getattr(self, '_suspend_save', False):
            return
        if not label or not self.current_meta:
            return
        try:
            import json
            d = self._config_dir()
            os.makedirs(d, exist_ok=True)
            # Start from existing prefs and capture snapshot
            data = dict(getattr(self, '_prefs', {}) or {})
            snap = self._capture_session(label)
            data.setdefault('commands', {})[label] = {
                **data.get('commands', {}).get(label, {}),
                **snap,
            }
            # Keep globals and selected_command untouched here; _save_prefs will handle them
            self._prefs = data
            with open(self._prefs_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ----- Session snapshot API -----
    def _capture_session(self, label: str) -> Dict[str, Any]:
        """Capture current on-screen state for the given label into a snapshot dict
        and update in-memory prefs for that label. Returns the snapshot.
        """
        snap: Dict[str, Any] = {}
        try:
            if not label or not self.current_meta:
                return snap
            data = dict(getattr(self, '_prefs', {}) or {})
            cmd_state = data.setdefault('commands', {}).setdefault(label, {})
            # Options (selection/value/flag)
            options = cmd_state.setdefault('options', {})
            for _grp, specs in self._categorize_current():
                for spec in specs:
                    key = spec.key
                    sel_var = self.widget_vars.get(spec, {}).get('selected')
                    selected = bool(sel_var.get()) if sel_var is not None else False
                    rec = options.setdefault(key, {})
                    rec['selected'] = selected
                    if spec.is_flag:
                        wd = self._selected.get(spec)
                        rec['flag'] = bool(wd.get('flag').get()) if wd and wd.get('flag') else False
                    else:
                        wd = self._selected.get(spec)
                        rec['value'] = str(wd.get('value').get()) if wd and wd.get('value') else ''
            snap['options'] = options
            # Output text
            try:
                snap['output'] = self.output.get("1.0", tk.END)
            except Exception:
                pass
            # Preview
            try:
                snap['preview'] = self.preview_var.get()
            except Exception:
                pass
            # Tab index
            try:
                ti = self.tabs.index(self.tabs.select())
                snap['tab_index'] = int(ti)
            except Exception:
                pass
            # Scroll positions
            scr: Dict[str, Any] = cmd_state.setdefault('scroll', {})
            try:
                oy = self.output.yview()
                scr['output_yview'] = [float(oy[0]), float(oy[1])]
            except Exception:
                pass
            try:
                sy = self.sel_canvas.yview()
                scr['selected_yview'] = [float(sy[0]), float(sy[1])]
            except Exception:
                pass
            if scr:
                snap['scroll'] = scr
            # Route selected index
            try:
                idx = int(getattr(self, '_selected_route_index', 0))
                snap['route'] = {'selected_index': idx}
            except Exception:
                pass
            # Update in-memory prefs
            for k, v in snap.items():
                cmd_state[k] = v
            self._prefs = data
        except Exception:
            pass
        return snap

    def _restore_session(self, label: str, snapshot: Dict[str, Any]) -> None:
        """Restore visual aspects (preview/tab/scroll/route) from snapshot for label.
        Options and output are already restored by _apply_saved_state_for_current.
        """
        try:
            if not snapshot:
                return
            # Preview (optional)
            pv = snapshot.get('preview')
            if isinstance(pv, str) and pv:
                try:
                    self.preview_var.set(pv)
                except Exception:
                    pass
            # Tab index
            ti = snapshot.get('tab_index')
            if isinstance(ti, int) and ti in (0, 1):
                try:
                    self.tabs.select(ti)
                except Exception:
                    pass
            # Scrolls
            scr = snapshot.get('scroll') or {}
            oy = scr.get('output_yview')
            sy = scr.get('selected_yview')
            if isinstance(oy, (list, tuple)) and len(oy) >= 1:
                try:
                    first = float(oy[0])
                    self.after(0, lambda f=first: self._safe_yview_moveto(self.output, f))
                except Exception:
                    pass
            if isinstance(sy, (list, tuple)) and len(sy) >= 1:
                try:
                    first = float(sy[0])
                    self.after(0, lambda f=first: self._safe_yview_moveto(self.sel_canvas, f))
                except Exception:
                    pass
            # Route selection
            rt = snapshot.get('route') or {}
            idx = rt.get('selected_index')
            if isinstance(idx, int) and self.current_meta and self.current_meta.name == 'run':
                try:
                    if getattr(self, '_route_cards', None) and 0 <= idx < len(self._route_cards):
                        self._select_route_card(idx)
                except Exception:
                    pass
        except Exception:
            pass

    def _reset_defaults(self):
        """Reset all settings to initial defaults and clear saved preferences."""
        try:
            # Remove prefs on disk
            p = self._prefs_path()
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
            self._prefs = {}
            self._restore_cmd = None
            # Reset globals
            self.cwd_var.set("")
            self.db_var.set("")
            self.linkly_var.set("")
            self.detail_var.set(0)
            self.quiet_var.set(0)
            self.debug_var.set(0)
            # Reset to initial command ordering
            all_cmds = self._ordered_command_labels()
            if all_cmds:
                self.cmd_var.set(all_cmds[0])
                self._on_command_change()
            self._update_preview()
            self._save_prefs()
        except Exception:
            pass

    # ----- Mouse wheel helpers -----
    def _install_global_mousewheel(self):
        # Fallback: route wheel events to nearest scrollable ancestor anywhere in the app
        self.bind_all("<MouseWheel>", self._on_global_mousewheel, add=True)
        self.bind_all("<Button-4>", self._on_global_mousewheel, add=True)  # X11 up
        self.bind_all("<Button-5>", self._on_global_mousewheel, add=True)  # X11 down

    def _bind_mousewheel_target(self, widget, target=None):
        # Bind wheel directly to a scrollable widget or route to a target (e.g., inner frame -> canvas)
        tgt = target or widget

        def _on_local_wheel(ev):
            return self._scroll_target(tgt, ev)

        widget.bind("<MouseWheel>", _on_local_wheel, add=True)
        widget.bind("<Button-4>", _on_local_wheel, add=True)
        widget.bind("<Button-5>", _on_local_wheel, add=True)

    def _on_global_mousewheel(self, ev):
        # Try to find a scrollable ancestor under the cursor and scroll it
        w = ev.widget
        # Walk up to find a widget that supports yview_scroll (Canvas/Text/Listbox/Treeview)
        visited = 0
        while w is not None and visited < 10:
            if hasattr(w, 'yview_scroll'):
                return self._scroll_target(w, ev)
            try:
                parent_path = w.winfo_parent()
                if not parent_path:
                    break
                w = w._nametowidget(parent_path)
            except Exception:
                break
            visited += 1
        return None

    def _scroll_target(self, target, ev):
        # Compute scroll direction
        delta = 0
        if hasattr(ev, 'num') and ev.num in (4, 5):
            # X11 button scroll
            delta = -1 if ev.num == 4 else 1
        else:
            # Windows and macOS: use sign of delta
            try:
                d = int(ev.delta)
            except Exception:
                d = 0
            if d != 0:
                delta = -1 if d > 0 else 1
        # Only scroll if there is overflow (content doesn't fully fit)
        try:
            if hasattr(target, 'yview'):
                first, last = target.yview()
                # If the fraction span covers the whole content, don't intercept
                if (last - first) >= 0.999:
                    return None
        except Exception:
            # If we can't determine, fall through to try scrolling
            pass

        if delta != 0:
            try:
                target.yview_scroll(delta, 'units')
                return "break"
            except Exception:
                pass
        return None

    # ----- Theming helpers -----
    def _apply_theme(self):
        c = self.colors
        # Base window and default font
        self.configure(background=c["bg"])
        try:
            default_font = tkfont.nametofont("TkDefaultFont")
            default_font.configure(size=10)
        except Exception:
            pass

        style = ttk.Style(self)
        # Use a theme that respects color configs
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Global style tweaks
        style.configure(
            ".",
            foreground=c["fg"],
            background=c["bg"],
            fieldbackground=c["surface"],
            bordercolor=c["line"],
            lightcolor=c["bg"],
            darkcolor=c["bg"],
            focuscolor=c["primary"],
        )

        # Containers / text
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabelframe", background=c["bg"], foreground=c["fg"], bordercolor=c["line"], relief="groove")
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["fg"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])

        # Inputs
        style.configure("TEntry", fieldbackground=c["surface"], foreground=c["fg"], bordercolor=c["line"]) 
        try:
            style.configure("TEntry", insertcolor=c["fg"])
        except Exception:
            pass
        style.map("TEntry",
                  fieldbackground=[('focus', c["surface"])],
                  bordercolor=[('focus', c["primary"])])

        style.configure("TCombobox", fieldbackground=c["surface"], foreground=c["fg"], bordercolor=c["line"], arrowsize=12)
        try:
            style.configure("TCombobox", insertcolor=c["fg"])
        except Exception:
            pass
        style.map("TCombobox",
                  fieldbackground=[('readonly', c["surface"])],
                  bordercolor=[('focus', c["primary"])],
                  foreground=[('disabled', c["muted"])])

        style.configure("TSpinbox", fieldbackground=c["surface"], foreground=c["fg"], bordercolor=c["line"]) 
        style.map("TSpinbox", bordercolor=[('focus', c["primary"])])

        # Buttons
        style.configure("TButton", background=c["panel"], foreground=c["fg"], bordercolor=c["line"], focusthickness=2, focuscolor=c["primary"]) 
        style.map("TButton",
                  background=[('active', c["line"])],
                  bordercolor=[('focus', c["primary"])])

        style.configure("Accent.TButton", background=c["primary"], foreground=c["fg"], bordercolor=c["primary"], relief="flat")
        style.map("Accent.TButton", background=[('active', c["primaryActive"])])

        style.configure("Secondary.TButton", background=c["secondary"], foreground=c["fg"], bordercolor=c["secondary"], relief="flat")
        style.map("Secondary.TButton", background=[('active', c["secondaryActive"])])

        # Stop button style: red background with white text
        try:
            style.configure("Stop.TButton", background="#d9534f", foreground="#ffffff", bordercolor="#d9534f", relief="flat")
            style.map("Stop.TButton", background=[('active', "#c9302c")], foreground=[('active', "#ffffff")])
        except Exception:
            pass

        # Notebook
        style.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(6, 4, 6, 0))
        style.configure("TNotebook.Tab", background=c["panel"], foreground=c["fg"], padding=(12, 6), bordercolor=c["line"])
        style.map("TNotebook.Tab",
                  background=[('selected', c["surface"]), ('active', c["panel"])],
                  foreground=[('selected', c["fg"])])

        # Route card styles
        try:
            style.configure("RouteCard.TFrame", background=c["panel"], bordercolor=c["line"], relief="groove")
            style.configure("RouteCardSelected.TFrame", background=c["surface"], bordercolor=c["primary"], relief="solid")
            style.configure("RouteTitle.TLabel", background=c["panel"], foreground=c["fg"]) 
            style.configure("RouteBody.TLabel", background=c["panel"], foreground=c["muted"]) 
        except Exception:
            pass

        # Paned window / scrollbars
        style.configure("TPanedwindow", background=c["bg"], sashrelief="flat")
        # Make the right-side vertical split (Selected Options vs Output) clearly resizable
        try:
            style.configure("RightSplit.TPanedwindow", background=c["bg"], sashrelief="raised")
        except Exception:
            pass
        # Make the Output tab's internal splitter (Routes vs Console) clearly resizable
        try:
            style.configure("OutSplit.TPanedwindow", background=c["bg"], sashrelief="raised")
        except Exception:
            pass
        style.configure("Vertical.TScrollbar", background=c["panel"], troughcolor=c["bg"], arrowcolor=c["fg"])
        style.configure("Horizontal.TScrollbar", background=c["panel"], troughcolor=c["bg"], arrowcolor=c["fg"])

        # Tk widgets option db (Text/Listbox/Scrollbar popups)
        self.option_add('*Text.background', c["surface"]) 
        self.option_add('*Text.foreground', c["fg"]) 
        self.option_add('*Text.insertBackground', c["fg"]) 
        # Entry insertion cursor color (for classic Tk widgets and some ttk themes)
        self.option_add('*Entry.insertBackground', c["fg"]) 
        self.option_add('*Text.selectBackground', c["line"]) 
        self.option_add('*Text.selectForeground', c["fg"]) 
        self.option_add('*Listbox.background', c["surface"]) 
        self.option_add('*Listbox.foreground', c["fg"]) 
        self.option_add('*Listbox.selectBackground', c["line"]) 
        self.option_add('*Listbox.selectForeground', c["fg"]) 
        self.option_add('*Scrollbar.background', c["panel"]) 
        self.option_add('*Scrollbar.activeBackground', c["panel"]) 
        self.option_add('*Scrollbar.troughColor', c["bg"]) 
        self.option_add('*Scrollbar.arrowColor', c["fg"]) 

    def _style_scrolled_text(self, widget: ScrolledText):
        c = self.colors
        try:
            widget.configure(
                bg=c["surface"],
                fg=c["fg"],
                insertbackground=c["fg"],
                highlightthickness=0,
                selectbackground=c["line"],
                selectforeground=c["fg"],
                borderwidth=0,
                relief="flat",
            )
        except Exception:
            pass

    def _enable_copy_shortcuts(self, widget):
        # Bind Ctrl/Cmd+C to copy selected text in the widget to clipboard
        def _copy_sel(event=None):
            try:
                text = widget.get("sel.first", "sel.last")
            except Exception:
                # No selection; keep clipboard unchanged
                return "break"
            try:
                self.clipboard_clear()
                self.clipboard_append(text)
            except Exception:
                pass
            return "break"

        for seq in ("<Control-c>", "<Control-C>", "<Command-c>", "<Command-C>"):
            try:
                widget.bind(seq, _copy_sel, add=True)
            except Exception:
                pass

    def _browse_cwd(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.cwd_var.get() or os.getcwd())
        if d:
            self.cwd_var.set(d)

    def _browse_db(self):
        from tkinter import filedialog
        f = filedialog.askopenfilename(initialdir=os.path.expanduser("~"), filetypes=[("DB", "*.db"), ("All", "*.*")])
        if f:
            self.db_var.set(f)

    # ----- Sticky-session helpers -----
    def _schedule_save(self, delay_ms: int = 250):
        try:
            job = getattr(self, "_save_job", None)
            if job:
                self.after_cancel(job)
        except Exception:
            pass
        try:
            self._save_job = self.after(delay_ms, self._save_prefs)
        except Exception:
            pass

    def _on_sel_yview(self, first: str, last: str):
        # Forward to the scrollbar and remember scroll position
        try:
            self.sel_scroll.set(first, last)
        except Exception:
            pass
        try:
            label = self.cmd_var.get()
            data = dict(getattr(self, '_prefs', {}) or {})
            cmd_state = data.setdefault('commands', {}).setdefault(label, {})
            scroll = cmd_state.setdefault('scroll', {})
            scroll['selected_yview'] = [float(first), float(last)]
            self._prefs = data
        except Exception:
            pass
        self._schedule_save()
        return None

    def _on_output_yview(self, first: str, last: str):
        # Forward to ScrolledText's internal vbar if present and remember scroll
        try:
            vbar = getattr(self.output, 'vbar', None)
            if vbar is not None:
                vbar.set(first, last)
        except Exception:
            pass
        try:
            label = self.cmd_var.get()
            data = dict(getattr(self, '_prefs', {}) or {})
            cmd_state = data.setdefault('commands', {}).setdefault(label, {})
            scroll = cmd_state.setdefault('scroll', {})
            scroll['output_yview'] = [float(first), float(last)]
            self._prefs = data
        except Exception:
            pass
        self._schedule_save()
        return None

    def _safe_yview_moveto(self, widget, first: float):
        try:
            widget.yview_moveto(first)
        except Exception:
            pass

    # ----- Categorization -----
    def _categorize_current(self) -> List[Tuple[str, List[OptionSpec]]]:
        if not self.current_meta:
            return []
        # Fallback: simple Required/Other
        required = list(self.current_meta.arguments)
        other = list(self.current_meta.switches)

        # Special layout and defaults for 'buildcache'
        if self.current_meta.name == 'buildcache':
            # Group frequently used flags prominently
            by_dest = {}
            for s in required + other:
                key1 = s.dest or s.long_flag.lstrip('-')
                key2 = s.long_flag.lstrip('-')
                by_dest[key1] = s
                by_dest.setdefault(key2, s)
            force = by_dest.get('force')
            ign   = by_dest.get('ignoreUnknown')
            sql   = by_dest.get('sqlFilename') or by_dest.get('--sql')
            prices= by_dest.get('pricesFilename') or by_dest.get('--prices')
            groups: List[Tuple[str, List[OptionSpec]]] = []
            primary: List[OptionSpec] = [s for s in (force, ign) if s]
            secondary: List[OptionSpec] = [s for s in (sql, prices) if s]
            if primary:
                groups.append(("Rebuild", primary))
            if secondary:
                groups.append(("Other", secondary))
            # Ensure any remaining switches also appear
            used = set(primary + secondary)
            remaining = [s for s in (required + other) if s not in used]
            if remaining:
                groups.append(("Misc", remaining))
            return groups

        # Special layout for 'run'
        if self.current_meta.name == 'run':
            by_dest = {}
            for s in required + other:
                key1 = s.dest or s.long_flag.lstrip('-')
                key2 = s.long_flag.lstrip('-')
                by_dest[key1] = s
                by_dest.setdefault(key2, s)
            sects: List[Tuple[str, List[str]]] = [
                ("Required", ["capacity", "credits"]),
                ("Other", ["starting", "ending", "via", "limit", "blackMarket", "unique", "pruneScores", "shorten", "routes", "maxRoutes", "pruneHops"]),
                ("Travel", ["goalSystem", "loop", "direct", "hops", "maxJumpsPer", "maxLyPer", "emptyLyPer", "startJumps", "endJumps", "showJumps", "supply", "demand"]),
                ("Filters", ["avoid", "maxAge", "lsPenalty", "demand", "supply"]),
                ("Constraints", ["padSize", "noPlanet", "planetary", "fleet", "odyssey", "maxLs"]),
                ("Economy", ["minGainPerTon", "maxGainPerTon", "margin", "insurance"]),
                ("Display", ["checklist", "x52pro", "progress", "summary"]),
            ]
            used = set()
            result: List[Tuple[str, List[OptionSpec]]] = []
            for title, names in sects:
                specs: List[OptionSpec] = []
                for n in names:
                    s = by_dest.get(n)
                    if s and s not in used:
                        specs.append(s)
                        used.add(s)
                if specs:
                    result.append((title, specs))
            # Any remaining not categorized
            remaining = [s for s in (required + other) if s not in used]
            if remaining:
                result.append(("Misc", remaining))
            return result

        # Generic fallback
        return [("Required", required), ("Other", other)]


def main():
    app = TdGuiApp()
    app.mainloop()


if __name__ == "__main__":
    main()
