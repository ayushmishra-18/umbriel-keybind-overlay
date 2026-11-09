#!/usr/bin/env python3
"""Umbriel Keybind Overlay.

A launcher-quality, searchable keyboard-shortcut overlay for the
Umbriel Wayland compositor.
"""

import argparse
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False

from gi.repository import Gtk, Gdk, GLib, Pango

# Import delivered config reader
import umbriel_keys

FORBIDDEN_FAMILIES = (
    "session-",
    "config-",
    "output-",
    "input-capture-",
    "dpms-",
)


def split_action(action: str) -> list[str] | None:
    """Safely split action string into argv list using POSIX rules.
    Returns None if action syntax is malformed (e.g. unclosed quotes).
    """
    if not action or not action.strip():
        return []
    try:
        return shlex.split(action, posix=True)
    except ValueError:
        return None


def is_executable_action(action: str) -> bool:
    """Returns False if the action belongs to a forbidden family or has malformed syntax."""
    if not action or not action.strip():
        return False
    parts = action.strip().split(None, 1)
    head = parts[0]
    if any(head.startswith(fam) for fam in FORBIDDEN_FAMILIES):
        return False
    if head == "spawn":
        return True
    words = split_action(action)
    if not words:
        return False
    return not any(words[0].startswith(fam) for fam in FORBIDDEN_FAMILIES)


def format_action_title(action: str, default_label: str = "") -> str:
    """Provides a polished human-readable title for an action."""
    if default_label and default_label != action:
        label = default_label
    else:
        label = umbriel_keys.clean_label(action)

    custom_map = {
        "window-toggle-maximize": "Toggle maximize",
        "window-toggle-pinned": "Toggle pinned",
        "session-quit skip-confirmation": "Quit session (skip confirmation)",
        "session-quit": "Quit session",
        "submap reset": "Reset submap",
    }
    if action in custom_map:
        return custom_map[action]
    if action.startswith("submap "):
        sub_name = action[7:].strip()
        return f"Enter submap: {sub_name}"
    if action.startswith("spawn "):
        return f"Run: {action[6:].strip()}"
    return label


def resolve_compositor_config(config_arg: str, proc_cwd: str | None = None) -> str | None:
    """Resolves an explicit -c config argument from a compositor's command line.

    If config_arg is absolute, resolves symlinks via os.path.realpath without
    reading or requiring proc_cwd.
    If config_arg is relative, resolves it against the compositor process's working
    directory (proc_cwd). If proc_cwd is missing, empty, or not an absolute path,
    resolution fails and returns None.

    Note on cwd staleness: Resolution uses the compositor's current working directory
    at check time (/proc/<pid>/cwd), which may differ from its launch-time directory
    if the compositor process or a wrapper changed directory after spawn. Upstream
    Umbriel source inspection (commit c6ad18d) shows it does not call chdir/current_path;
    nevertheless, resolving a relative -c path against /proc/<pid>/cwd is on a best-effort
    basis and does not guarantee recovering the exact launch-time directory.
    """
    if not config_arg or not isinstance(config_arg, str) or not config_arg.strip():
        return None

    if os.path.isabs(config_arg):
        return os.path.realpath(config_arg)

    if not proc_cwd or not isinstance(proc_cwd, str) or not proc_cwd.strip():
        return None

    if not os.path.isabs(proc_cwd):
        return None

    return os.path.realpath(os.path.join(proc_cwd, config_arg))


def verify_safety_gate(config_path: str, scratch_config: str = None, scratch_cache: str = None) -> tuple[bool, str]:
    """Verifies that:
    1. A single unambiguous compositor process is running that matches the target config,
       either via explicit -c <path> or via default launch ($XDG_CONFIG_HOME/umbriel/config.toml,
       else $HOME/.config/umbriel/config.toml) resolved from that process's environment.
    2. The compositor's own log confirms running on WAYLAND_DISPLAY.
    3. The overlay's WAYLAND_DISPLAY matches the compositor log.
    4. UMBRIEL_SOCKET canonical path matches the log-verified display socket in XDG_RUNTIME_DIR.
    5. The socket endpoint inode in /proc/net/unix is actively owned by an open fd of the
       matched compositor process.
    """
    target_config = os.path.realpath(scratch_config if scratch_config else config_path)
    if not os.path.exists(target_config):
        return False, f"Safety gate: target config does not exist: {target_config}"

    # 1. Discover all candidate umbriel compositor processes
    candidate_pids = set()
    res = subprocess.run(["pgrep", "-f", "umbriel"], capture_output=True, text=True)
    if res.returncode == 0:
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid == os.getpid():
                    continue
                comm_file = f"/proc/{pid}/comm"
                cmdline_file = f"/proc/{pid}/cmdline"
                is_umbriel = False
                if os.path.exists(comm_file):
                    try:
                        with open(comm_file, "r") as f:
                            comm = f.read().strip()
                        if comm in ("umbriel", "umbriel-nightly"):
                            is_umbriel = True
                    except Exception:
                        pass
                if not is_umbriel and os.path.exists(cmdline_file):
                    try:
                        with open(cmdline_file, "rb") as f:
                            raw = f.read().split(b"\x00")
                        if raw and os.path.basename(raw[0].decode("utf-8", "ignore")) in ("umbriel", "umbriel-nightly"):
                            is_umbriel = True
                    except Exception:
                        pass
                if is_umbriel:
                    candidate_pids.add(pid)

    if not candidate_pids:
        return False, f"Safety gate: no running compositor found for {os.path.basename(target_config)}"

    # Match each candidate process to target_config (via -c or default launch)
    matching_pids = []
    for pid in sorted(list(candidate_pids)):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = [a.decode("utf-8", "ignore") for a in f.read().split(b"\x00") if a]
        except Exception as e:
            return False, f"Safety gate: unreadable /proc/{pid}/cmdline: {e}"

        cmdline_config = None
        i = 1
        while i < len(args):
            arg = args[i]
            if arg == "-c" and i + 1 < len(args):
                cmdline_config = args[i + 1]
                break
            elif arg.startswith("-c") and len(arg) > 2:
                cmdline_config = arg[2:]
                break
            i += 1

        if cmdline_config is not None:
            if os.path.isabs(cmdline_config):
                proc_config = resolve_compositor_config(cmdline_config)
            else:
                proc_cwd = None
                try:
                    proc_cwd = os.readlink(f"/proc/{pid}/cwd")
                except Exception:
                    proc_cwd = None
                proc_config = resolve_compositor_config(cmdline_config, proc_cwd)

            if proc_config is None:
                continue
        else:
            # Deterministic default launch resolution from process's own environment
            proc_env = {}
            try:
                with open(f"/proc/{pid}/environ", "rb") as f:
                    for entry in f.read().split(b"\x00"):
                        if b"=" in entry:
                            k, v = entry.split(b"=", 1)
                            proc_env[k.decode("utf-8", "ignore")] = v.decode("utf-8", "ignore")
            except Exception as e:
                return False, f"Safety gate: unreadable /proc/{pid}/environ: {e}"

            if proc_env.get("XDG_CONFIG_HOME"):
                default_path = os.path.join(proc_env["XDG_CONFIG_HOME"], "umbriel", "config.toml")
            elif proc_env.get("HOME"):
                default_path = os.path.join(proc_env["HOME"], ".config", "umbriel", "config.toml")
            else:
                return False, f"Safety gate: cannot determine config for PID {pid}: neither XDG_CONFIG_HOME nor HOME set"
            proc_config = os.path.realpath(default_path)

        if proc_config == target_config:
            matching_pids.append(pid)

    if len(matching_pids) == 0:
        return False, f"Safety gate: no running compositor found for {os.path.basename(target_config)}"
    if len(matching_pids) > 1:
        return False, f"Safety gate: ambiguous matching compositor instances found ({len(matching_pids)} processes: {matching_pids})"

    matched_pid = matching_pids[0]

    # 2. Determine log path from scratch_cache, /proc/<matched_pid>/environ, or scratch layout
    log_path = None
    if scratch_cache:
        cand = os.path.join(scratch_cache, "umbriel", "umbriel.log")
        if os.path.exists(cand) or os.path.exists(os.path.join(scratch_cache, "umbriel", "umbriel-stderr.log")):
            log_path = cand

    if not log_path:
        proc_env = {}
        try:
            with open(f"/proc/{matched_pid}/environ", "rb") as ef:
                for entry in ef.read().split(b"\x00"):
                    if b"=" in entry:
                        k, v = entry.split(b"=", 1)
                        proc_env[k.decode("utf-8", "ignore")] = v.decode("utf-8", "ignore")
        except Exception as e:
            return False, f"Safety gate: unreadable /proc/{matched_pid}/environ: {e}"

        if proc_env.get("XDG_CACHE_HOME"):
            cand = os.path.join(proc_env["XDG_CACHE_HOME"], "umbriel", "umbriel.log")
            if os.path.exists(cand) or os.path.exists(os.path.join(proc_env["XDG_CACHE_HOME"], "umbriel", "umbriel-stderr.log")):
                log_path = cand
        elif proc_env.get("HOME"):
            cand = os.path.join(proc_env["HOME"], ".cache", "umbriel", "umbriel.log")
            if os.path.exists(cand) or os.path.exists(os.path.join(proc_env["HOME"], ".cache", "umbriel", "umbriel-stderr.log")):
                log_path = cand

    if not log_path:
        parent = os.path.dirname(os.path.dirname(target_config))
        cand = os.path.join(parent, "cache", "umbriel", "umbriel.log")
        if os.path.exists(cand) or os.path.exists(os.path.join(parent, "cache", "umbriel", "umbriel-stderr.log")):
            log_path = cand

    if not log_path:
        return False, f"Safety gate: compositor log not found for PID {matched_pid}"

    # 3. Read latest WAYLAND_DISPLAY from compositor log
    expected_display = None
    log_candidates = []
    if log_path:
        log_candidates.append(log_path)
        stderr_candidate = os.path.join(os.path.dirname(log_path), "umbriel-stderr.log")
        if os.path.exists(stderr_candidate):
            log_candidates.append(stderr_candidate)

    # Also inspect process's own stdout/stderr file targets from /proc/<pid>/fd/
    for fd_num in (1, 2):
        fd_path = f"/proc/{matched_pid}/fd/{fd_num}"
        if os.path.exists(fd_path):
            try:
                target = os.readlink(fd_path)
                if os.path.isfile(target) and target not in log_candidates:
                    log_candidates.append(target)
            except Exception:
                pass

    for cand in log_candidates:
        try:
            with open(cand, "r", encoding="utf-8", errors="ignore") as lf:
                for line in lf:
                    if "running on WAYLAND_DISPLAY=" in line:
                        match = re.search(r"running on WAYLAND_DISPLAY=(wayland-\d+)", line)
                        if match:
                            expected_display = match.group(1)
        except Exception:
            pass
        if expected_display:
            break

    if not expected_display:
        return False, f"Safety gate: could not extract WAYLAND_DISPLAY from compositor log for PID {matched_pid}"

    # 4. Verify overlay environment matches display
    env_display = os.environ.get("WAYLAND_DISPLAY")
    if env_display != expected_display:
        return False, f"Safety gate: WAYLAND_DISPLAY mismatch (env='{env_display}', compositor='{expected_display}')"

    # 5. Endpoint-Process Association:
    # (a) Full canonical path check
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    canonical_expected_sock = os.path.realpath(os.path.join(runtime_dir, f"umbriel-{expected_display}.sock"))

    env_socket = os.environ.get("UMBRIEL_SOCKET")
    if env_socket:
        resolved_env_sock = os.path.realpath(env_socket)
    else:
        resolved_env_sock = canonical_expected_sock

    if resolved_env_sock != canonical_expected_sock:
        return False, f"Safety gate: UMBRIEL_SOCKET path mismatch (env='{env_socket}', expected='{canonical_expected_sock}')"

    if not os.path.exists(canonical_expected_sock):
        return False, f"Safety gate: expected socket '{canonical_expected_sock}' does not exist"

    # (b) Inode ownership check via /proc/net/unix and /proc/<matched_pid>/fd/
    socket_inodes = set()
    try:
        with open("/proc/net/unix", "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 8:
                    sock_path = " ".join(parts[7:])
                    if os.path.realpath(sock_path) == canonical_expected_sock:
                        socket_inodes.add(parts[6])
    except Exception as e:
        return False, f"Safety gate: unreadable /proc/net/unix: {e}"

    if not socket_inodes:
        return False, f"Safety gate: socket '{canonical_expected_sock}' not found in /proc/net/unix"

    fd_dir = f"/proc/{matched_pid}/fd"
    if not os.path.isdir(fd_dir):
        return False, f"Safety gate: cannot access {fd_dir}"

    fd_matched = False
    try:
        for fd_entry in os.listdir(fd_dir):
            try:
                link_target = os.readlink(os.path.join(fd_dir, fd_entry))
                if link_target.startswith("socket:["):
                    ino = link_target[8:-1]
                    if ino in socket_inodes:
                        fd_matched = True
                        break
            except Exception:
                pass
    except Exception as e:
        return False, f"Safety gate: unreadable {fd_dir}: {e}"

    if not fd_matched:
        return False, f"Safety gate: socket '{canonical_expected_sock}' is not owned by compositor PID {matched_pid}"

    return True, f"Verified instance PID {matched_pid} on {expected_display}"


def compute_target_width(default_width: int = 620, margin: int = 40) -> int:
    """Clamp the overlay width so it never overflows small monitor outputs."""
    try:
        display = Gdk.Display.get_default()
        if display and display.get_n_monitors() > 0:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            if monitor:
                geom = monitor.get_geometry()
                if geom.width > 0:
                    max_w = max(320, geom.width - margin)
                    return min(default_width, max_w)
    except Exception:
        pass
    return default_width


def ensure_valid_cursor_theme():
    """Ensure GTK and Wayland use an installed, physically present cursor theme."""
    settings = Gtk.Settings.get_default()
    if not settings:
        return

    current = settings.get_property("gtk-cursor-theme-name")

    def theme_exists(name):
        if not name:
            return False
        for base in ["/usr/share/icons", os.path.expanduser("~/.icons"), os.path.expanduser("~/.local/share/icons")]:
            if os.path.isdir(os.path.join(base, name)):
                return True
        return False

    if not theme_exists(current):
        for candidate in ["Adwaita", "breeze", "Bibata-Modern-Classic", "default"]:
            if theme_exists(candidate):
                settings.set_property("gtk-cursor-theme-name", candidate)
                if not os.environ.get("XCURSOR_THEME") or not theme_exists(os.environ.get("XCURSOR_THEME")):
                    os.environ["XCURSOR_THEME"] = candidate
                break


def split_chord(chord_str: str) -> list[str]:
    """Splits a key chord string (e.g. 'Mod+B', 'Mod++', '+') into individual key parts."""
    if not chord_str:
        return []
    if chord_str == "+":
        return ["+"]
    if chord_str.endswith("++"):
        prefix = chord_str[:-2]
        return (prefix.split("+") if prefix else []) + ["+"]
    return chord_str.split("+")


class KeybindOverlay(Gtk.Window):
    def __init__(self, config_path: str, scratch_config: str = None, scratch_cache: str = None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.config_path = os.path.abspath(config_path)
        self.scratch_config = os.path.abspath(scratch_config) if scratch_config else None
        self.scratch_cache = os.path.abspath(scratch_cache) if scratch_cache else None
        self.binds_data = []
        self.active_submap = None
        self.submap_listener_proc = None
        self.current_filtered_binds = []

        ensure_valid_cursor_theme()

        # Configure Layer-Shell if available
        if HAS_LAYER_SHELL and GtkLayerShell.is_supported():
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_namespace(self, "umbriel-keybind-overlay")
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
            # Center on screen (all 4 edges unanchored)
            for edge in [GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT]:
                GtkLayerShell.set_anchor(self, edge, False)
        else:
            self.set_position(Gtk.WindowPosition.CENTER)

        # Content-responsive sizing: modest width clamped to output, dynamic height
        self.target_width = compute_target_width(620)
        self.set_size_request(self.target_width, -1)
        self.set_decorated(False)
        self.set_app_paintable(True)

        self.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("realize", self._on_window_realize)

        self._executing = False
        self._destroyed = False
        self._cancel_event = threading.Event()
        self._pending_proc = None
        self._proc_lock = threading.Lock()

        self._load_css()
        self._build_ui()
        self._load_config()
        self._start_submap_listener()

        # Connect window events
        self.connect("key-press-event", self._on_window_key_press)
        self.connect("destroy", self._on_destroy)

    def _load_css(self):
        css_provider = Gtk.CssProvider()
        css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")
        if os.path.exists(css_path):
            try:
                css_provider.load_from_path(css_path)
                screen = Gdk.Screen.get_default()
                if screen:
                    Gtk.StyleContext.add_provider_for_screen(
                        screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                    )
            except GLib.Error as e:
                sys.stderr.write(f"Warning: Failed to load style.css: {e}\n")

    def _build_ui(self):
        # Outer container (content-responsive sizing)
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.main_box.set_size_request(self.target_width, -1)
        self.main_box.get_style_context().add_class("main-container")
        self.add(self.main_box)

        # Search bar box
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_box.get_style_context().add_class("search-box")
        self.main_box.pack_start(search_box, False, False, 0)

        search_icon = Gtk.Label(label="🔍")
        search_box.pack_start(search_icon, False, False, 0)

        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("Search keybindings, actions, submaps...")
        self.search_entry.get_style_context().add_class("search-entry")
        self.search_entry.connect("changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_entry_activate)
        self.search_entry.connect("realize", self._on_search_entry_realize)
        search_box.pack_start(self.search_entry, True, True, 0)

        # Active Submap Banner
        self.submap_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.submap_banner.get_style_context().add_class("submap-banner")
        self.submap_banner_label = Gtk.Label()
        self.submap_banner_label.set_xalign(0.0)
        self.submap_banner_label.get_style_context().add_class("submap-banner-label")
        self.submap_banner.pack_start(self.submap_banner_label, False, False, 0)

        submap_hint = Gtk.Label(label="(submap bindings prioritized)")
        submap_hint.get_style_context().add_class("submap-banner-hint")
        self.submap_banner.pack_start(submap_hint, False, False, 0)
        self.main_box.pack_start(self.submap_banner, False, False, 0)
        self.submap_banner.set_no_show_all(True)
        self.submap_banner.hide()

        # Results Scrolled Window (shrink-to-fit with maximum height cap)
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.get_style_context().add_class("results-scroll")
        self.scrolled.set_propagate_natural_height(True)
        self.scrolled.set_max_content_height(320)
        self.main_box.pack_start(self.scrolled, True, True, 0)

        # ListBox
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.get_style_context().add_class("results-list")
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.listbox.connect("motion-notify-event", self._on_listbox_motion)
        self.listbox.connect("realize", self._on_listbox_realize)
        self.scrolled.add(self.listbox)

        # Footer Status Bar
        self.footer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.footer_box.get_style_context().add_class("footer-bar")
        self.main_box.pack_start(self.footer_box, False, False, 0)

        self.status_label = Gtk.Label()
        self.status_label.set_xalign(0.0)
        self.status_label.set_yalign(0.5)
        self.status_label.set_line_wrap(True)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.status_label.get_style_context().add_class("footer-text")
        self.footer_box.pack_start(self.status_label, True, True, 0)

        hints_label = Gtk.Label()
        hints_label.set_markup(
            '<span font_family="monospace" foreground="#a6adc8"><b>↵</b></span> run   '
            '<span font_family="monospace" foreground="#a6adc8"><b>Esc</b></span> close   '
            '<span font_family="monospace" foreground="#a6adc8"><b>^R</b></span> reload'
        )
        hints_label.get_style_context().add_class("footer-text")
        self.footer_box.pack_end(hints_label, False, False, 0)

    def _load_config(self):
        """Loads or reloads keybindings from the config file using umbriel_keys."""
        try:
            data = umbriel_keys.build(self.config_path)
            errors = data.get("errors", [])
            if errors:
                self.binds_data = data.get("binds", [])
                self.set_status(f"Config error: {errors[0]}", "error")
                self._filter_and_render()
                return

            self.binds_data = data.get("binds", [])
            warnings = data.get("warnings", [])
            if warnings:
                self.set_status(f"Loaded {len(self.binds_data)} bindings ({warnings[0]})", "info")
            else:
                self.set_status(f"Loaded {len(self.binds_data)} bindings from {os.path.basename(self.config_path)}", "info")
            self._filter_and_render()
        except Exception as e:
            self.set_status(f"Failed to load config: {e}", "error")

    def set_status(self, text: str, status_type: str = "info"):
        if getattr(self, "_destroyed", False):
            return
        ctx = self.status_label.get_style_context()
        ctx.remove_class("status-info")
        ctx.remove_class("status-ok")
        ctx.remove_class("status-hint")

        if status_type == "error":
            ctx.add_class("status-hint")
            self.status_label.set_markup(f"<b>⚠ {GLib.markup_escape_text(text)}</b>")
        elif status_type == "ok":
            ctx.add_class("status-ok")
            self.status_label.set_text(text)
        else:
            ctx.add_class("status-info")
            self.status_label.set_text(text)

    def set_active_submap(self, submap_name: str):
        """Updates active submap state from IPC."""
        if submap_name in ("", "default", "null", None):
            self.active_submap = None
            self.submap_banner.set_no_show_all(True)
            self.submap_banner.hide()
        else:
            self.active_submap = str(submap_name)
            self.submap_banner_label.set_text(f"ACTIVE SUBMAP: {self.active_submap.upper()}")
            self.submap_banner.set_no_show_all(False)
            self.submap_banner.show_all()

        # Reset footer status on submap change
        self.set_status(f"Loaded {len(self.binds_data)} bindings from {os.path.basename(self.config_path)}", "info")
        self._filter_and_render()

    def _start_submap_listener(self):
        """Starts a background thread listening to `umbriel subscribe submap`."""
        def listener():
            env = os.environ.copy()
            try:
                res = subprocess.run(
                    ["umbriel", "submap", "--json"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=2
                )
                if res.returncode == 0:
                    data = json.loads(res.stdout.strip())
                    initial = data.get("ok") if isinstance(data, dict) and "ok" in data else data
                    GLib.idle_add(self.set_active_submap, initial)
            except Exception:
                pass

            try:
                proc = subprocess.Popen(
                    ["umbriel", "subscribe", "submap"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    env=env
                )
                self.submap_listener_proc = proc
                for line in proc.stdout:
                    if not line:
                        break
                    try:
                        ev = json.loads(line.strip())
                        if ev.get("event") == "submap":
                            sub = ev.get("data")
                            GLib.idle_add(self.set_active_submap, sub)
                    except Exception:
                        continue
            except Exception:
                pass

        t = threading.Thread(target=listener, daemon=True)
        t.start()

    def _render_chord(self, chord_str: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.get_style_context().add_class("keycap-container")
        parts = split_chord(chord_str)

        for i, part in enumerate(parts):
            if i > 0:
                plus = Gtk.Label(label="+")
                plus.get_style_context().add_class("chord-plus")
                box.pack_start(plus, False, False, 0)
            keycap = Gtk.Label(label=part)
            keycap.get_style_context().add_class("keycap")
            box.pack_start(keycap, False, False, 0)
        return box

    def _create_row_widget(self, bind: dict) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("result-row")
        row.bind_data = bind

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.add(hbox)

        # Left side: Keycap chord (fixed width box for alignment)
        chord_widget = self._render_chord(bind.get("key", ""))
        chord_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        chord_box.set_size_request(100, -1)
        chord_box.pack_start(chord_widget, False, False, 0)
        hbox.pack_start(chord_box, False, False, 0)

        # Middle: Title and raw action
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = format_action_title(bind.get("action", ""), bind.get("label", ""))
        title_label = Gtk.Label(label=title)
        title_label.set_xalign(0.0)
        title_label.set_yalign(0.5)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.get_style_context().add_class("action-title")
        vbox.pack_start(title_label, False, False, 0)

        raw_label = Gtk.Label(label=bind.get("action", ""))
        raw_label.set_xalign(0.0)
        raw_label.set_yalign(0.5)
        raw_label.set_ellipsize(Pango.EllipsizeMode.END)
        raw_label.get_style_context().add_class("action-raw")
        vbox.pack_start(raw_label, False, False, 0)
        hbox.pack_start(vbox, True, True, 0)

        # Right side: Badges
        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hbox.pack_end(badge_box, False, False, 0)

        category = bind.get("category", "")
        if category:
            cat_badge = Gtk.Label(label=category)
            cat_badge.get_style_context().add_class("badge")
            cat_badge.get_style_context().add_class("badge-category")
            badge_box.pack_start(cat_badge, False, False, 0)

        context = bind.get("context", "default")
        if context and context != "default":
            sub_badge = Gtk.Label(label=context)
            sub_badge.get_style_context().add_class("badge")
            sub_badge.get_style_context().add_class("badge-submap")
            badge_box.pack_start(sub_badge, False, False, 0)

        action = bind.get("action", "")
        if not is_executable_action(action):
            danger_badge = Gtk.Label(label="not executable")
            danger_badge.get_style_context().add_class("badge")
            danger_badge.get_style_context().add_class("badge-danger")
            badge_box.pack_start(danger_badge, False, False, 0)

        return row

    def _filter_and_render(self):
        query = self.search_entry.get_text().strip().lower()
        query_words = query.split()

        # Clear existing rows
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        matched = []
        for b in self.binds_data:
            key = b.get("key", "").lower()
            action = b.get("action", "").lower()
            label = b.get("label", "").lower()
            context = b.get("context", "default").lower()
            category = b.get("category", "").lower()
            searchable_text = f"{key} {action} {label} {context} {category}"

            if all(w in searchable_text for w in query_words):
                score = 0
                if self.active_submap and context == self.active_submap.lower():
                    score -= 100
                if query:
                    if key.startswith(query):
                        score -= 50
                    elif action.startswith(query):
                        score -= 40
                matched.append((score, b))

        matched.sort(key=lambda x: (x[0], x[1].get("order", 0), x[1].get("key", "")))
        self.current_filtered_binds = [item[1] for item in matched]

        if not self.current_filtered_binds:
            empty_row = Gtk.ListBoxRow()
            empty_row.set_selectable(False)
            empty_row.set_activatable(False)
            lbl = Gtk.Label(label=f"No keybindings found matching '{self.search_entry.get_text()}'")
            lbl.get_style_context().add_class("empty-state")
            empty_row.add(lbl)
            self.listbox.add(empty_row)
        else:
            for b in self.current_filtered_binds:
                row = self._create_row_widget(b)
                self.listbox.add(row)

        self.listbox.show_all()

        # Select first row
        if self.current_filtered_binds:
            row0 = self.listbox.get_row_at_index(0)
            if row0:
                self.listbox.select_row(row0)

        # Content-responsive sizing: derive height from measured row geometry
        num_rows = len(self.current_filtered_binds)
        if num_rows == 0:
            scrolled_h = 60
        else:
            # Measure actual natural height of rows in listbox
            rows = self.listbox.get_children()
            measured_rows_h = sum(r.get_preferred_height()[1] for r in rows)
            list_nat_h = self.listbox.get_preferred_height()[1]
            measured_h = max(measured_rows_h, list_nat_h)
            if measured_h <= 0 and rows:
                r0_h = rows[0].get_preferred_height()[1]
                measured_h = r0_h * num_rows if r0_h > 0 else num_rows * 52
            elif measured_h <= 0:
                measured_h = num_rows * 52

            scrolled_h = min(320, measured_h)

        self.scrolled.set_min_content_height(scrolled_h)
        self.scrolled.set_max_content_height(320)

        # Shrink-to-fit to match content rows
        self.queue_resize()
        self.resize(self.target_width, 1)

    def _on_window_realize(self, widget):
        d = Gdk.Display.get_default()
        if d:
            cursor = Gdk.Cursor.new_from_name(d, "default")
            if not cursor:
                cursor = Gdk.Cursor.new_for_display(d, Gdk.CursorType.ARROW)
            widget.get_window().set_cursor(cursor)

    def _on_search_entry_realize(self, widget):
        d = Gdk.Display.get_default()
        if d:
            cursor = Gdk.Cursor.new_from_name(d, "text")
            if not cursor:
                cursor = Gdk.Cursor.new_for_display(d, Gdk.CursorType.XTERM)
            widget.get_window().set_cursor(cursor)

    def _on_listbox_realize(self, widget):
        d = Gdk.Display.get_default()
        if d:
            cursor = Gdk.Cursor.new_from_name(d, "default")
            if not cursor:
                cursor = Gdk.Cursor.new_for_display(d, Gdk.CursorType.ARROW)
            widget.get_window().set_cursor(cursor)

    def _on_listbox_motion(self, widget, event):
        row = self.listbox.get_row_at_y(int(event.y))
        if row and hasattr(row, "bind_data") and row.get_selectable():
            if self.listbox.get_selected_row() != row:
                self.listbox.select_row(row)
                if not self.search_entry.has_focus():
                    self.search_entry.grab_focus()

    def _on_search_changed(self, entry):
        # Clear any prior refusal or error status on new search input
        self.set_status(f"Loaded {len(self.binds_data)} bindings from {os.path.basename(self.config_path)}", "info")
        self._filter_and_render()

    def _on_entry_activate(self, entry):
        row = self.listbox.get_selected_row()
        if row and hasattr(row, "bind_data"):
            self._execute_bind(row.bind_data)

    def _on_row_activated(self, listbox, row):
        if row and hasattr(row, "bind_data"):
            self._execute_bind(row.bind_data)
            if not self.search_entry.has_focus():
                self.search_entry.grab_focus()

    def _execute_bind(self, bind: dict):
        if (
            getattr(self, "_executing", False)
            or getattr(self, "_destroyed", False)
            or (hasattr(self, "_cancel_event") and self._cancel_event.is_set())
        ):
            return

        action = bind.get("action", "")
        if not action or not action.strip():
            return

        lstripped = action.lstrip()
        parts = lstripped.split(None, 1)
        head = parts[0]
        remainder = parts[1] if len(parts) > 1 else None

        # 1. Hard safety exclusion check on head
        if any(head.startswith(fam) for fam in FORBIDDEN_FAMILIES):
            self.set_status(
                f"Action '{head}' is excluded from overlay execution for safety",
                "error"
            )
            return

        # 2. Argument preparation: spawn passes raw tail; others split words
        if head == "spawn":
            if remainder is not None and remainder != "":
                parsed_argv = ["spawn", remainder]
            else:
                parsed_argv = ["spawn"]
        else:
            action_argv = split_action(action)
            if action_argv is None:
                self.set_status(f"Invalid action syntax: unclosed quotation in '{action}'", "error")
                return

            if not action_argv:
                return

            first_word = action_argv[0]
            if any(first_word.startswith(fam) for fam in FORBIDDEN_FAMILIES):
                self.set_status(
                    f"Action '{first_word}' is excluded from overlay execution for safety",
                    "error"
                )
                return

            parsed_argv = list(action_argv)

        # 3. Non-blocking off-thread execution
        self._executing = True
        config_path = str(self.config_path) if self.config_path else ""
        scratch_config = str(self.scratch_config) if self.scratch_config else None
        scratch_cache = str(self.scratch_cache) if self.scratch_cache else None
        flags = bind.get("flags") or {}
        submap_after = flags.get("submap")
        submap_after_str = str(submap_after) if submap_after else None

        # Cancellation and Dispatch Semantics:
        # 1. Cancellation checks: the worker explicitly polls self._cancel_event before running the
        #    safety gate, before launching the primary umbriel msg command, upon registering the
        #    spawned process under self._proc_lock, before launching any follow-up submap command,
        #    and upon registering the submap process.
        # 2. Child tracking: while a CLI child is in-flight, it is registered in self._pending_proc under
        #    self._proc_lock. If cancellation occurs during or prior to registration, the process is
        #    terminated immediately. If the overlay is destroyed while in-flight, _on_destroy terminates
        #    the pending process immediately under self._proc_lock so follow-up submaps never execute.
        # 3. Honest limitation: once umbriel accepts a command or spawns an external process (such as a
        #    terminal or browser), that action runs independently in the compositor/session and cannot be
        #    undone or killed by the overlay.
        # 4. Silent exit: if cancelled at any point, the worker exits silently without touching the UI
        #    or raising an exception.
        def worker():
            def finish(callback):
                def idle_cb():
                    self._executing = False
                    cancel_ev = getattr(self, "_cancel_event", None)
                    if not getattr(self, "_destroyed", False) and not (cancel_ev and cancel_ev.is_set()):
                        callback()
                    return False
                GLib.idle_add(idle_cb)

            try:
                cancel_ev = getattr(self, "_cancel_event", None)
                if (cancel_ev and cancel_ev.is_set()) or getattr(self, "_destroyed", False):
                    self._executing = False
                    return

                # Strict Safety Gate check: verify compositor is alive AND targets exact instance
                safe, msg = verify_safety_gate(
                    config_path,
                    scratch_config=scratch_config,
                    scratch_cache=scratch_cache
                )
                cancel_ev = getattr(self, "_cancel_event", None)
                if (cancel_ev and cancel_ev.is_set()) or getattr(self, "_destroyed", False):
                    self._executing = False
                    return

                if not safe:
                    finish(lambda: self.set_status(msg, "error"))
                    return

                # Argument execution with umbriel msg
                cmd = ["umbriel", "msg"] + parsed_argv
                if (cancel_ev and cancel_ev.is_set()) or getattr(self, "_destroyed", False):
                    self._executing = False
                    return

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                cancelled = False
                proc_lock = getattr(self, "_proc_lock", None)
                if proc_lock:
                    with proc_lock:
                        cancel_ev = getattr(self, "_cancel_event", None)
                        if getattr(self, "_destroyed", False) or (cancel_ev and cancel_ev.is_set()):
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                            cancelled = True
                        else:
                            self._pending_proc = proc
                else:
                    cancel_ev = getattr(self, "_cancel_event", None)
                    if getattr(self, "_destroyed", False) or (cancel_ev and cancel_ev.is_set()):
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        cancelled = True
                    else:
                        self._pending_proc = proc

                if cancelled:
                    self._executing = False
                    return

                try:
                    stdout, stderr = proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    if proc_lock:
                        with proc_lock:
                            if self._pending_proc is proc:
                                self._pending_proc = None
                    cancel_ev = getattr(self, "_cancel_event", None)
                    if not (cancel_ev and cancel_ev.is_set()) and not getattr(self, "_destroyed", False):
                        finish(lambda: self.set_status("Execution timed out after 3 seconds", "error"))
                    return
                finally:
                    if proc_lock:
                        with proc_lock:
                            if self._pending_proc is proc:
                                self._pending_proc = None

                cancel_ev = getattr(self, "_cancel_event", None)
                if (cancel_ev and cancel_ev.is_set()) or getattr(self, "_destroyed", False):
                    self._executing = False
                    return

                stdout = (stdout or "").strip()
                stderr = (stderr or "").strip()
                raw_out = stdout or stderr
                if raw_out:
                    try:
                        resp_json = json.loads(raw_out)
                        if isinstance(resp_json, dict) and "err" in resp_json and resp_json["err"]:
                            err_msg = resp_json["err"]
                            finish(lambda: self.set_status(f"Error: {err_msg}", "error"))
                            return
                    except Exception:
                        pass

                if proc.returncode != 0:
                    err_msg = raw_out or f"Exit code {proc.returncode}"
                    finish(lambda: self.set_status(f"Compositor error: {err_msg}", "error"))
                    return

                # Handle submap-after: if bind has flags.submap, apply it now
                if submap_after_str:
                    cancel_ev = getattr(self, "_cancel_event", None)
                    if (cancel_ev and cancel_ev.is_set()) or getattr(self, "_destroyed", False):
                        self._executing = False
                        return

                    sub_cmd = ["umbriel", "msg", "submap", submap_after_str]
                    sub_proc = subprocess.Popen(
                        sub_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    sub_cancelled = False
                    if proc_lock:
                        with proc_lock:
                            cancel_ev = getattr(self, "_cancel_event", None)
                            if getattr(self, "_destroyed", False) or (cancel_ev and cancel_ev.is_set()):
                                try:
                                    sub_proc.terminate()
                                except Exception:
                                    pass
                                sub_cancelled = True
                            else:
                                self._pending_proc = sub_proc
                    else:
                        cancel_ev = getattr(self, "_cancel_event", None)
                        if getattr(self, "_destroyed", False) or (cancel_ev and cancel_ev.is_set()):
                            try:
                                sub_proc.terminate()
                            except Exception:
                                pass
                            sub_cancelled = True
                        else:
                            self._pending_proc = sub_proc

                    if sub_cancelled:
                        self._executing = False
                        return

                    try:
                        sub_out, sub_err = sub_proc.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        sub_proc.kill()
                        sub_out, sub_err = sub_proc.communicate()
                        if proc_lock:
                            with proc_lock:
                                if self._pending_proc is sub_proc:
                                    self._pending_proc = None
                        cancel_ev = getattr(self, "_cancel_event", None)
                        if not (cancel_ev and cancel_ev.is_set()) and not getattr(self, "_destroyed", False):
                            finish(lambda: self.set_status(f"Submap command timed out after 3 seconds", "error"))
                        return
                    finally:
                        if proc_lock:
                            with proc_lock:
                                if self._pending_proc is sub_proc:
                                    self._pending_proc = None

                    cancel_ev = getattr(self, "_cancel_event", None)
                    if (cancel_ev and cancel_ev.is_set()) or getattr(self, "_destroyed", False):
                        self._executing = False
                        return

                    if sub_proc.returncode != 0:
                        finish(lambda: self.set_status(f"Failed to apply submap {submap_after_str}", "error"))
                        return

                # Clean success: close overlay
                finish(lambda: Gtk.main_quit())

            except Exception as e:
                cancel_ev = getattr(self, "_cancel_event", None)
                if not (cancel_ev and cancel_ev.is_set()) and not getattr(self, "_destroyed", False):
                    err_text = str(e)
                    finish(lambda: self.set_status(f"Execution failed: {err_text}", "error"))
                else:
                    self._executing = False

        t = threading.Thread(target=worker, daemon=True)
        self._worker_thread = t
        t.start()

    def _on_window_key_press(self, widget, event):
        keyval = event.keyval
        state = event.state

        # Esc closes overlay
        if keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True

        # Ctrl+R reloads config
        if (state & Gdk.ModifierType.CONTROL_MASK) and (keyval in (Gdk.KEY_r, Gdk.KEY_R)):
            self._load_config()
            return True

        # Up / Down / Ctrl+K / Ctrl+J list navigation
        is_up = keyval == Gdk.KEY_Up or ((state & Gdk.ModifierType.CONTROL_MASK) and keyval == Gdk.KEY_k)
        is_down = keyval == Gdk.KEY_Down or ((state & Gdk.ModifierType.CONTROL_MASK) and keyval == Gdk.KEY_j)

        if is_up or is_down:
            row = self.listbox.get_selected_row()
            total = len(self.current_filtered_binds)
            if total > 0:
                if row:
                    idx = row.get_index()
                    new_idx = max(0, min(total - 1, idx - 1 if is_up else idx + 1))
                else:
                    new_idx = 0
                target_row = self.listbox.get_row_at_index(new_idx)
                if target_row:
                    self.listbox.select_row(target_row)
                    adj = self.scrolled.get_vadjustment()
                    alloc = target_row.get_allocation()
                    adj.clamp_page(alloc.y, alloc.y + alloc.height)
            return True

        # Forward typing to search entry if focus wandered
        if not self.search_entry.has_focus() and keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.search_entry.grab_focus()

        return False

    def _on_destroy(self, widget):
        self._destroyed = True
        if hasattr(self, "_cancel_event"):
            self._cancel_event.set()
        self._executing = False
        if hasattr(self, "_proc_lock"):
            with self._proc_lock:
                if self._pending_proc and self._pending_proc.poll() is None:
                    try:
                        self._pending_proc.terminate()
                    except Exception:
                        pass
        if self.submap_listener_proc:
            try:
                self.submap_listener_proc.terminate()
                self.submap_listener_proc.wait(timeout=0.5)
            except Exception:
                pass
            if self.submap_listener_proc.stdout:
                try:
                    self.submap_listener_proc.stdout.close()
                except Exception:
                    pass


def acquire_single_instance_lock(display: str):
    """Ensures single instance per Wayland display using an advisory file lock."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    lock_file_path = os.path.join(runtime_dir, f"umbriel-overlay-{display}.lock")
    try:
        lock_fd = open(lock_file_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep file descriptor open for lifetime of process
        return lock_fd
    except (BlockingIOError, OSError):
        sys.stderr.write(f"Notice: Another instance of umbriel-overlay is already running on {display}. Exiting.\n")
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Umbriel Keybind Overlay")
    parser.add_argument("-c", "--config", default=None, help="Path to umbriel config.toml")
    parser.add_argument("--scratch-config", default=None, help="Enforce safety gate verification against this scratch config")
    parser.add_argument("--scratch-cache", default=None, help="Cache directory for the scratch instance")
    args = parser.parse_args()

    # Resolution order: -c flag, else $XDG_CONFIG_HOME/umbriel/config.toml, else ~/.config/umbriel/config.toml
    config_path = args.config
    if not config_path:
        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        candidate = os.path.join(xdg_config, "umbriel", "config.toml")
        if os.path.exists(candidate):
            config_path = candidate
        else:
            std_fallback = os.path.expanduser("~/.config/umbriel/config.toml")
            if os.path.exists(std_fallback):
                config_path = std_fallback

    if not config_path or not os.path.exists(config_path):
        sys.stderr.write(
            f"Error: Umbriel config file not found: {config_path or 'no config specified and default ~/.config/umbriel/config.toml does not exist'}\n"
        )
        sys.exit(3)

    # Check Wayland display
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    if not wayland_display:
        sys.stderr.write("Error: WAYLAND_DISPLAY environment variable is not set.\n")
        sys.exit(2)

    # Single-instance enforcement per display
    _lock_handle = acquire_single_instance_lock(wayland_display)

    # Initialize GTK
    ok, _ = Gtk.init_check()
    if not ok:
        sys.stderr.write(f"Error: Failed to connect to Wayland display '{wayland_display}'.\n")
        sys.exit(2)

    overlay = KeybindOverlay(
        config_path,
        scratch_config=args.scratch_config,
        scratch_cache=args.scratch_cache
    )
    overlay.show_all()
    try:
        Gtk.main()
    finally:
        overlay.destroy()


if __name__ == "__main__":
    main()
