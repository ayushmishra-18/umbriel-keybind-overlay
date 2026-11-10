"""Tests for scripts/run-nested.sh launcher behavior and bounded cleanup.

Uses fake umbriel and overlay processes on PATH to test process supervision,
environment targeting, config generation, error handling, signal trapping,
and escalation from SIGTERM to SIGKILL without requiring a live Wayland compositor.

No GTK imports are used in this file.
"""

import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAUNCHER_SCRIPT = os.path.join(PROJECT_DIR, "scripts", "run-nested.sh")

# Python 3.11+ has tomllib in stdlib; fallback to tomli if present
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


class RunNestedBehaviorTests(unittest.TestCase):
    """Behavior tests for scripts/run-nested.sh with fake processes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = self.temp_dir.name

        # Runtime and state dirs
        self.runtime_dir = os.path.join(self.test_root, "runtime")
        os.makedirs(self.runtime_dir, exist_ok=True)

        self.fake_bin_dir = os.path.join(self.test_root, "bin")
        os.makedirs(self.fake_bin_dir, exist_ok=True)

        self.state_dir = os.path.join(self.test_root, "state")
        os.makedirs(self.state_dir, exist_ok=True)

        # Create isolated repo tree copy
        self.repo_copy = os.path.join(self.test_root, "repo")
        self._setup_repo_copy(self.repo_copy)

        # Setup fake umbriel executable on PATH
        self.fake_umbriel = os.path.join(self.fake_bin_dir, "umbriel")
        self._write_fake_umbriel()

        # Track scratch dirs created by script runs for cleanup
        self.created_scratch_dirs = []

    def tearDown(self):
        for sdir in self.created_scratch_dirs:
            if os.path.exists(sdir):
                shutil.rmtree(sdir, ignore_errors=True)
        self.temp_dir.cleanup()

    def _setup_repo_copy(self, dest_dir, overlay_exit_code=0, overlay_sleep=0.0):
        os.makedirs(dest_dir, exist_ok=True)
        scripts_dir = os.path.join(dest_dir, "scripts")
        examples_dir = os.path.join(dest_dir, "examples")
        os.makedirs(scripts_dir, exist_ok=True)
        os.makedirs(examples_dir, exist_ok=True)

        # Copy launcher script preserving executable mode
        shutil.copy2(LAUNCHER_SCRIPT, os.path.join(scripts_dir, "run-nested.sh"))

        # Copy real example config if present, or generate minimal representative TOML
        real_example = os.path.join(PROJECT_DIR, "examples", "typical-desktop.toml")
        if os.path.exists(real_example):
            shutil.copy2(real_example, os.path.join(examples_dir, "typical-desktop.toml"))
        else:
            with open(os.path.join(examples_dir, "typical-desktop.toml"), "w") as f:
                f.write('# Minimal example\n"Mod+Return" = { action = "spawn alacritty" }\n')

        # Create fake overlay.py that logs invocation and exits
        fake_overlay = os.path.join(dest_dir, "overlay.py")
        with open(fake_overlay, "w") as f:
            f.write(f"""#!/usr/bin/env python3
import sys, os, time

state_dir = os.environ.get("FAKE_UMBRIEL_DIR", "")
if state_dir:
    with open(os.path.join(state_dir, "overlay.pid"), "w") as f:
        f.write(str(os.getpid()))
    log_path = os.path.join(state_dir, "overlay_invocations.log")
    with open(log_path, "a") as f:
        f.write(f"ARGS: {{sys.argv}}\\n")
        f.write(f"WAYLAND_DISPLAY={{os.environ.get('WAYLAND_DISPLAY')}}\\n")
        f.write(f"UMBRIEL_SOCKET={{os.environ.get('UMBRIEL_SOCKET')}}\\n")

sleep_sec = {overlay_sleep}
if sleep_sec > 0:
    time.sleep(sleep_sec)

sys.exit({overlay_exit_code})
""")
        os.chmod(fake_overlay, 0o755)

    def _write_fake_umbriel(self):
        script = """#!/usr/bin/env python3
import sys, os, time, socket, signal

state_dir = os.environ.get("FAKE_UMBRIEL_DIR", "")
calls_log = os.path.join(state_dir, "umbriel_calls.log") if state_dir else None

def log_call():
    if calls_log:
        with open(calls_log, "a") as f:
            f.write(f"ARGS: {sys.argv}\\n")
            f.write(f"CWD={os.getcwd()}\\n")
            f.write(f"WLR_BACKENDS={os.environ.get('WLR_BACKENDS')}\\n")
            f.write(f"XDG_CONFIG_HOME={os.environ.get('XDG_CONFIG_HOME')}\\n")
            f.write(f"XDG_CACHE_HOME={os.environ.get('XDG_CACHE_HOME')}\\n")
            f.write(f"UMBRIEL_SOCKET={os.environ.get('UMBRIEL_SOCKET')}\\n")
            f.write(f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')}\\n")

log_call()

mode_file = os.path.join(state_dir, "mode.txt") if state_dir else ""
mode = ""
if mode_file and os.path.exists(mode_file):
    with open(mode_file, "r") as f:
        mode = f.read().strip()

# Subcommand: umbriel msg cheatsheet-close
if len(sys.argv) > 1 and sys.argv[1] == "msg":
    if len(sys.argv) > 2 and sys.argv[2] == "cheatsheet-close":
        if mode == "fail_cheatsheet":
            sys.stderr.write("fake-umbriel: failed to close cheatsheet\\n")
            sys.stderr.flush()
            sys.exit(1)
        sys.exit(0)
    sys.exit(0)

# Compositor invocation: umbriel -c <scratch_config>
if mode == "exit_immediately":
    sys.stderr.write("fake-umbriel: immediate fatal crash\\n")
    sys.stderr.flush()
    sys.exit(1)

display_name = "wayland-77"
if state_dir:
    with open(os.path.join(state_dir, "compositor.pid"), "w") as f:
        f.write(str(os.getpid()))

# Create Unix socket for cheatsheet close check
xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
sock_path = os.path.join(xdg_runtime, f"umbriel-{display_name}.sock")
try:
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(5)
except Exception as e:
    sys.stderr.write(f"Socket bind failed: {e}\\n")

if mode == "no_display":
    sys.stderr.write("fake-umbriel: started up without reporting display\\n")
    sys.stderr.flush()
else:
    sys.stderr.write(f"running on WAYLAND_DISPLAY={display_name}\\n")
    sys.stderr.flush()

if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

while True:
    try:
        time.sleep(0.5)
    except Exception:
        pass
"""
        with open(self.fake_umbriel, "w") as f:
            f.write(script)
        os.chmod(self.fake_umbriel, 0o755)

    def _set_mode(self, mode_str):
        with open(os.path.join(self.state_dir, "mode.txt"), "w") as f:
            f.write(mode_str)

    def _get_env(self, extra_env=None):
        env = os.environ.copy()
        env["PATH"] = self.fake_bin_dir + os.path.pathsep + env.get("PATH", "")
        env["XDG_RUNTIME_DIR"] = self.runtime_dir
        env["FAKE_UMBRIEL_DIR"] = self.state_dir
        env["UMBRIEL_CLEANUP_GRACE_SEC"] = "0.5"
        env["UMBRIEL_DISPLAY_TIMEOUT_STEPS"] = "5"
        env["UMBRIEL_SOCKET_TIMEOUT_STEPS"] = "5"
        if extra_env:
            env.update(extra_env)
        return env

    def _extract_scratch_dir(self, stdout_text):
        m = re.search(r"Created scratch environment:\s*(\S+)", stdout_text)
        if m:
            sdir = m.group(1)
            self.created_scratch_dirs.append(sdir)
            return sdir
        return None

    def test_01_normal_flow(self):
        """1. Normal flow: overlay exits 0, script prints notice, compositor kept until Enter, exits 0."""
        launcher = os.path.join(self.repo_copy, "scripts", "run-nested.sh")
        proc = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            cwd=self.repo_copy,
        )

        # Wait until prompt for Enter is reached
        stdout_acc = ""
        while "Press Enter to stop the test session..." not in stdout_acc:
            line = proc.stdout.readline()
            if not line:
                break
            stdout_acc += line

        self.assertIn("Overlay closed normally.", stdout_acc)
        self.assertIn("Press Enter to stop the test session...", stdout_acc)
        scratch_dir = self._extract_scratch_dir(stdout_acc)
        self.assertIsNotNone(scratch_dir)

        # Check compositor PID is still running
        pid_file = os.path.join(self.state_dir, "compositor.pid")
        self.assertTrue(os.path.exists(pid_file))
        with open(pid_file) as f:
            comp_pid = int(f.read().strip())
        # kill -0 should succeed
        try:
            os.kill(comp_pid, 0)
            compositor_alive = True
        except OSError:
            compositor_alive = False
        self.assertTrue(compositor_alive, "Compositor must stay alive until Enter is pressed")

        # Press Enter to stop
        stdout_rem, stderr_rem = proc.communicate(input="\n", timeout=5)
        stdout_acc += stdout_rem
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Test session finished.", stdout_acc)

        # Compositor must have received TERM and terminated
        time.sleep(0.1)
        with self.assertRaises(OSError):
            os.kill(comp_pid, 0)

    def test_02_overlay_exits_nonzero(self):
        """2. Overlay exits non-zero: warning with code to stderr, session kept until Enter, cleanup runs."""
        # Re-setup repo copy with overlay exit code 42
        self._setup_repo_copy(self.repo_copy, overlay_exit_code=42)
        launcher = os.path.join(self.repo_copy, "scripts", "run-nested.sh")
        proc = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            cwd=self.repo_copy,
        )

        stdout_acc = ""
        while "Press Enter to stop the test session..." not in stdout_acc:
            line = proc.stdout.readline()
            if not line:
                break
            stdout_acc += line

        self._extract_scratch_dir(stdout_acc)
        stdout_rem, stderr_out = proc.communicate(input="\n", timeout=5)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Warning: Overlay exited with non-zero exit code 42.", stderr_out)

    def test_03_exact_targeting(self):
        """3. Exact targeting: fake umbriel sees -c SCRATCH_CONFIG, scratch XDG dirs, WLR_BACKENDS=wayland, overlay sees display & socket."""
        launcher = os.path.join(self.repo_copy, "scripts", "run-nested.sh")
        proc = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            cwd=self.repo_copy,
        )
        stdout_out, stderr_out = proc.communicate(input="\n", timeout=5)
        self.assertEqual(proc.returncode, 0)
        scratch_dir = self._extract_scratch_dir(stdout_out)

        # Check umbriel calls log
        calls_log = os.path.join(self.state_dir, "umbriel_calls.log")
        self.assertTrue(os.path.exists(calls_log))
        with open(calls_log) as f:
            calls_text = f.read()

        self.assertIn("WLR_BACKENDS=wayland", calls_text)
        self.assertIn(f"XDG_CONFIG_HOME={scratch_dir}/config", calls_text)
        self.assertIn(f"XDG_CACHE_HOME={scratch_dir}/cache", calls_text)
        self.assertIn(f"-c', '{scratch_dir}/config.toml'", calls_text)

        # Check cheatsheet-close saw nested socket
        expected_socket = os.path.join(self.runtime_dir, "umbriel-wayland-77.sock")
        self.assertIn(f"UMBRIEL_SOCKET={expected_socket}", calls_text)

        # Check overlay saw exact display and socket
        overlay_log = os.path.join(self.state_dir, "overlay_invocations.log")
        self.assertTrue(os.path.exists(overlay_log))
        with open(overlay_log) as f:
            overlay_text = f.read()
        self.assertIn("WAYLAND_DISPLAY=wayland-77", overlay_text)
        self.assertIn(f"UMBRIEL_SOCKET={expected_socket}", overlay_text)
        self.assertIn(f"-c', '{scratch_dir}/config.toml'", overlay_text)

    def test_04_generated_config(self):
        """4. Generated config: parse scratch config with tomllib, check Alt+Shift+Space, repeat=false, [input.cursor] values."""
        if tomllib is None:
            self.skipTest("tomllib / tomli not installed in environment")

        launcher = os.path.join(self.repo_copy, "scripts", "run-nested.sh")
        proc = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            cwd=self.repo_copy,
        )
        stdout_out, _ = proc.communicate(input="\n", timeout=5)
        scratch_dir = self._extract_scratch_dir(stdout_out)
        cfg_path = os.path.join(scratch_dir, "config.toml")
        self.assertTrue(os.path.exists(cfg_path))

        with open(cfg_path, "rb") as f:
            parsed = tomllib.load(f)

        keybinds = parsed.get("keybinds", parsed)
        self.assertIn("Alt+Shift+Space", keybinds)
        reopen_binding = keybinds["Alt+Shift+Space"]
        self.assertFalse(reopen_binding.get("repeat", True))
        self.assertIn("spawn", reopen_binding.get("action", ""))

        self.assertIn("input", parsed)
        self.assertIn("cursor", parsed["input"])
        cursor_cfg = parsed["input"]["cursor"]
        self.assertEqual(cursor_cfg.get("theme"), "Adwaita")
        self.assertEqual(cursor_cfg.get("size"), 24)
        self.assertFalse(cursor_cfg.get("hardware_cursor", True))

    def test_05_reopen_wrapper(self):
        """5. Reopen wrapper: executable, exact display, socket, overlay path, scratch config."""
        launcher = os.path.join(self.repo_copy, "scripts", "run-nested.sh")
        proc = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            cwd=self.repo_copy,
        )
        stdout_out, _ = proc.communicate(input="\n", timeout=5)
        scratch_dir = self._extract_scratch_dir(stdout_out)
        wrapper_path = os.path.join(scratch_dir, "open-overlay.sh")
        self.assertTrue(os.path.exists(wrapper_path))
        self.assertTrue(os.access(wrapper_path, os.X_OK))

        with open(wrapper_path) as f:
            wrapper_content = f.read()

        expected_socket = os.path.join(self.runtime_dir, "umbriel-wayland-77.sock")
        self.assertIn("export WAYLAND_DISPLAY=wayland-77", wrapper_content)
        self.assertIn(f"export UMBRIEL_SOCKET={expected_socket}", wrapper_content)
        self.assertIn(f"{self.repo_copy}/overlay.py", wrapper_content)
        self.assertIn(f"{scratch_dir}/config.toml", wrapper_content)

        # Clear overlay log and execute wrapper directly
        overlay_log = os.path.join(self.state_dir, "overlay_invocations.log")
        if os.path.exists(overlay_log):
            os.unlink(overlay_log)

        run_res = subprocess.run([wrapper_path], env=self._get_env(), capture_output=True, text=True)
        self.assertEqual(run_res.returncode, 0)
        with open(overlay_log) as f:
            log_data = f.read()
        self.assertIn("WAYLAND_DISPLAY=wayland-77", log_data)
        self.assertIn(f"UMBRIEL_SOCKET={expected_socket}", log_data)

    def test_06_quoting_spaces_and_apostrophes(self):
        """6. Quoting: run whole flow from repo copy with space and apostrophe (e.g. "my repo's copy")."""
        fancy_repo = os.path.join(self.test_root, "tester's fancy repo with spaces")
        self._setup_repo_copy(fancy_repo)
        launcher = os.path.join(fancy_repo, "scripts", "run-nested.sh")

        proc = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            cwd=fancy_repo,
        )
        stdout_out, stderr_out = proc.communicate(input="\n", timeout=5)
        self.assertEqual(proc.returncode, 0, f"Failed with stderr: {stderr_out}")
        scratch_dir = self._extract_scratch_dir(stdout_out)

        # Check TOML action decodes cleanly and points to open-overlay.sh
        if tomllib:
            cfg_path = os.path.join(scratch_dir, "config.toml")
            with open(cfg_path, "rb") as f:
                parsed = tomllib.load(f)
            keybinds = parsed.get("keybinds", parsed)
            action = keybinds["Alt+Shift+Space"]["action"]
            parts = shlex.split(action, posix=True)
            self.assertEqual(parts[0], "spawn")
            self.assertEqual(parts[1], os.path.join(scratch_dir, "open-overlay.sh"))

    def test_07_failure_paths(self):
        """7. Failure paths: compositor exits immediately, never reports display, missing files, cheatsheet fails."""
        launcher = os.path.join(self.repo_copy, "scripts", "run-nested.sh")

        # 7a: Compositor exits immediately
        self._set_mode("exit_immediately")
        proc_a = subprocess.run([launcher], env=self._get_env(), capture_output=True, text=True, cwd=self.repo_copy)
        self.assertNotEqual(proc_a.returncode, 0)
        self.assertIn("Nested compositor exited unexpectedly", proc_a.stderr)
        self.assertIn("fake-umbriel: immediate fatal crash", proc_a.stderr)
        self._extract_scratch_dir(proc_a.stdout)

        # 7b: Compositor never reports display
        self._set_mode("no_display")
        proc_b = subprocess.run([launcher], env=self._get_env(), capture_output=True, text=True, cwd=self.repo_copy)
        self.assertNotEqual(proc_b.returncode, 0)
        self.assertIn("Nested compositor did not report a WAYLAND_DISPLAY within timeout", proc_b.stderr)
        self._extract_scratch_dir(proc_b.stdout)

        # 7c: Missing overlay.py or config
        self._set_mode("")
        bad_repo = os.path.join(self.test_root, "bad_repo")
        os.makedirs(os.path.join(bad_repo, "scripts"), exist_ok=True)
        shutil.copy2(LAUNCHER_SCRIPT, os.path.join(bad_repo, "scripts", "run-nested.sh"))
        proc_c = subprocess.run(
            [os.path.join(bad_repo, "scripts", "run-nested.sh")],
            env=self._get_env(),
            capture_output=True,
            text=True,
            cwd=bad_repo,
        )
        self.assertEqual(proc_c.returncode, 1)
        self.assertIn("Cannot locate overlay.py", proc_c.stderr)

        # 7d: umbriel msg cheatsheet-close fails
        self._set_mode("fail_cheatsheet")
        proc_d = subprocess.run([launcher], env=self._get_env(), capture_output=True, text=True, cwd=self.repo_copy)
        self.assertNotEqual(proc_d.returncode, 0)
        self.assertIn("Error: Failed to close nested cheatsheet", proc_d.stderr)
        self._extract_scratch_dir(proc_d.stdout)

    def test_08_signal_handling(self):
        """8. Signal handling: SIGTERM/SIGINT while waiting on read (and while overlay is running) terminates compositor promptly."""
        launcher = os.path.join(self.repo_copy, "scripts", "run-nested.sh")

        # 8a: SIGTERM while waiting on read
        proc = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            cwd=self.repo_copy,
        )
        stdout_acc = ""
        while "Press Enter to stop the test session..." not in stdout_acc:
            line = proc.stdout.readline()
            if not line:
                break
            stdout_acc += line
        self._extract_scratch_dir(stdout_acc)

        pid_file = os.path.join(self.state_dir, "compositor.pid")
        with open(pid_file) as f:
            comp_pid = int(f.read().strip())

        # Send SIGTERM to the launcher process
        proc.terminate()
        proc.wait(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
        proc.stdin.close()

        # Compositor must have been terminated promptly
        time.sleep(0.1)
        with self.assertRaises(OSError):
            os.kill(comp_pid, 0)

        # 8b: SIGTERM while overlay is running (Option 2b(i): asserts both compositor and overlay are gone)
        self._setup_repo_copy(self.repo_copy, overlay_sleep=10.0)
        proc_b = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            cwd=self.repo_copy,
        )
        # Wait until overlay starts
        overlay_pid_file = os.path.join(self.state_dir, "overlay.pid")
        for _ in range(50):
            if os.path.exists(overlay_pid_file):
                break
            time.sleep(0.1)

        with open(pid_file) as f:
            comp_pid_b = int(f.read().strip())
        with open(overlay_pid_file) as f:
            overlay_pid_b = int(f.read().strip())

        proc_b.terminate()
        proc_b.wait(timeout=5)
        proc_b.stdout.close()
        proc_b.stderr.close()
        proc_b.stdin.close()
        time.sleep(0.1)
        with self.assertRaises(OSError):
            os.kill(comp_pid_b, 0)
        with self.assertRaises(OSError):
            os.kill(overlay_pid_b, 0)

    def test_09_term_resistant_child_escalates_to_kill(self):
        """9. TERM-resistant child: fake compositor that ignores TERM; script escalates to KILL within bounded time."""
        self._set_mode("ignore_term")
        launcher = os.path.join(self.repo_copy, "scripts", "run-nested.sh")
        proc = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env({"UMBRIEL_CLEANUP_GRACE_SEC": "0.3"}),
            cwd=self.repo_copy,
        )

        stdout_acc = ""
        while "Press Enter to stop the test session..." not in stdout_acc:
            line = proc.stdout.readline()
            if not line:
                break
            stdout_acc += line
        self._extract_scratch_dir(stdout_acc)

        pid_file = os.path.join(self.state_dir, "compositor.pid")
        with open(pid_file) as f:
            comp_pid = int(f.read().strip())

        start_time = time.time()
        stdout_rem, stderr_rem = proc.communicate(input="\n", timeout=5)
        elapsed = time.time() - start_time

        # Must have completed promptly (around grace sec ~0.3s)
        self.assertLess(elapsed, 4.0)
        self.assertEqual(proc.returncode, 0)

        # Must have logged warning about SIGKILL
        self.assertIn("did not terminate on SIGTERM; sent SIGKILL", stderr_rem)

        # Compositor must be dead
        with self.assertRaises(OSError):
            os.kill(comp_pid, 0)

    def test_10_compositor_cwd_is_scratch_dir(self):
        """10. Compositor cwd: verify nested compositor is started with cwd = scratch dir so relative outputs stay in scratch."""
        launcher = os.path.join(self.repo_copy, "scripts", "run-nested.sh")
        proc = subprocess.Popen(
            [launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._get_env(),
            cwd=self.repo_copy,
        )

        stdout_acc = ""
        while "Press Enter to stop the test session..." not in stdout_acc:
            line = proc.stdout.readline()
            if not line:
                break
            stdout_acc += line
        scratch_dir = self._extract_scratch_dir(stdout_acc)

        proc.communicate(input="\n", timeout=5)

        calls_file = os.path.join(self.state_dir, "umbriel_calls.log")
        with open(calls_file) as f:
            calls_content = f.read()

        cwd_match = re.search(r"^CWD=(.+)$", calls_content, re.MULTILINE)
        self.assertIsNotNone(cwd_match, "umbriel_calls.log must contain CWD entry")
        logged_cwd = cwd_match.group(1).strip()
        self.assertEqual(logged_cwd, scratch_dir, f"Compositor cwd ({logged_cwd}) must match scratch dir ({scratch_dir})")
        self.assertNotEqual(logged_cwd, self.repo_copy, "Compositor cwd must not be inherited repo cwd")


if __name__ == "__main__":
    unittest.main()
