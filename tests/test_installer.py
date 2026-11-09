"""Headless unit test suite for user-local install.sh and uninstall.sh."""

import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INSTALL_SH = os.path.join(REPO_ROOT, "scripts", "install.sh")
UNINSTALL_SH = os.path.join(REPO_ROOT, "scripts", "uninstall.sh")
INSTALL_CMD = ["bash", INSTALL_SH]
UNINSTALL_CMD = ["bash", UNINSTALL_SH]


class TestInstaller(unittest.TestCase):
    """Test suite covering install.sh and uninstall.sh behavior and safety."""

    def create_stub(self, code_str):
        stub_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, stub_dir)
        stub_path = os.path.join(stub_dir, "python3")
        import sys
        with open(stub_path, "w") as f:
            f.write(f"#!{sys.executable}\n" + code_str)
        os.chmod(stub_path, 0o755)
        return stub_dir

    def run_cmd(self, args, env, input_text=None):
        return subprocess.run(
            args,
            cwd=REPO_ROOT,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
        )

    def test_happy_path(self):
        """(a) Happy path: four managed files + manifest installed, launcher executable, snippet printed."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            res = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("Warning: Skipping dependency check", res.stdout)
            self.assertIn("[keybinds]", res.stdout)

            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            for fn in ["overlay.py", "umbriel_keys.py", "style.css", "uninstall.sh", ".install-manifest"]:
                p = os.path.join(data_dir, fn)
                self.assertTrue(os.path.exists(p), f"Missing managed file {fn}")

            launcher = os.path.join(fake_home, ".local", "bin", "umbriel-keybind-overlay")
            self.assertTrue(os.path.exists(launcher))
            self.assertTrue(os.access(launcher, os.X_OK))

            with open(launcher) as f:
                l_content = f.read()
            self.assertIn("# umbriel-keybind-overlay launcher (managed)", l_content)
            self.assertIn(f"exec python3 '{data_dir}/overlay.py' \"$@\"", l_content)

            with open(os.path.join(data_dir, ".install-manifest")) as f:
                m_content = f.read()
            self.assertIn("@LAUNCHER@", m_content)
            self.assertIn("overlay.py", m_content)
            self.assertIn("umbriel_keys.py", m_content)
            self.assertIn("style.css", m_content)
            self.assertIn("uninstall.sh", m_content)

    def test_idempotency(self):
        """(b) Idempotency: second run succeeds cleanly, same file set, manifest refreshed."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            res1 = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertEqual(res1.returncode, 0, res1.stderr)

            manifest_path = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay", ".install-manifest")
            with open(manifest_path) as f:
                m1 = f.read()

            res2 = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertEqual(res2.returncode, 0, res2.stderr)
            with open(manifest_path) as f:
                m2 = f.read()
            self.assertEqual(m1, m2)

    def test_dependency_refusal(self):
        """(c) Dependency refusal: failing stub, no flag -> non-zero exit, per-distro guidance, nothing created."""
        stub_code = """import sys
if "--version" in sys.argv:
    print("Python 3.11.0")
    sys.exit(0)
if len(sys.argv) > 1 and sys.argv[1] == "-c":
    sys.exit(1)
sys.exit(0)
"""
        stub_dir = self.create_stub(stub_code)
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": f"{stub_dir}:{os.environ['PATH']}",
            }
            res = self.run_cmd(INSTALL_CMD, env)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("dnf", res.stderr)
            self.assertIn("apt", res.stderr)
            self.assertIn("pacman", res.stderr)

            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            launcher = os.path.join(fake_home, ".local", "bin", "umbriel-keybind-overlay")
            self.assertFalse(os.path.exists(data_dir))
            self.assertFalse(os.path.exists(launcher))

    def test_uninstall_happy_path(self):
        """(d) Uninstall happy path: owned files removed, dir rmdir'd, compositor config untouched, reminder printed."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            res = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertEqual(res.returncode, 0, res.stderr)

            umbriel_conf_dir = os.path.join(fake_home, ".config", "umbriel")
            os.makedirs(umbriel_conf_dir)
            sentinel_path = os.path.join(umbriel_conf_dir, "config.toml")
            with open(sentinel_path, "w") as f:
                f.write("# sentinel\n")

            res_un = self.run_cmd(UNINSTALL_CMD + ["--yes"], env)
            self.assertEqual(res_un.returncode, 0, res_un.stderr)
            self.assertIn("Reminder", res_un.stdout)
            self.assertIn("~/.config/umbriel/config.toml", res_un.stdout)

            self.assertTrue(os.path.exists(sentinel_path))
            with open(sentinel_path) as f:
                self.assertEqual(f.read(), "# sentinel\n")

            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            launcher = os.path.join(fake_home, ".local", "bin", "umbriel-keybind-overlay")
            self.assertFalse(os.path.exists(data_dir))
            self.assertFalse(os.path.exists(launcher))

    def test_uninstall_cancellation(self):
        """(e) Uninstall cancellation: prompt answered 'n' -> nothing removed."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            launcher = os.path.join(fake_home, ".local", "bin", "umbriel-keybind-overlay")

            res = self.run_cmd(UNINSTALL_CMD, env, input_text="n\n")
            self.assertEqual(res.returncode, 0)
            self.assertIn("cancelled", res.stdout)
            self.assertTrue(os.path.exists(data_dir))
            self.assertTrue(os.path.exists(launcher))
            self.assertTrue(os.path.exists(os.path.join(data_dir, "overlay.py")))

    def test_uninstall_preserves_modified_and_foreign_files(self):
        """(f) Uninstall preserves a user-modified managed file and a user-added foreign file."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")

            style_path = os.path.join(data_dir, "style.css")
            with open(style_path, "a") as f:
                f.write("/* user custom css */\n")

            foreign_path = os.path.join(data_dir, "my_custom_theme.txt")
            with open(foreign_path, "w") as f:
                f.write("foreign\n")

            res = self.run_cmd(UNINSTALL_CMD + ["--yes"], env)
            self.assertEqual(res.returncode, 0)
            self.assertIn("Warning: File", res.stdout)
            self.assertIn("modified", res.stdout)
            self.assertTrue(os.path.exists(style_path))
            self.assertTrue(os.path.exists(foreign_path))
            self.assertTrue(os.path.exists(data_dir))
            self.assertFalse(os.path.exists(os.path.join(data_dir, "overlay.py")))

    def test_install_refusal_unmanifested_managed_file(self):
        """(g) Install refusal: managed-name file present without manifest -> refuse; --force -> succeeds."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            os.makedirs(data_dir)
            with open(os.path.join(data_dir, "overlay.py"), "w") as f:
                f.write("# rogue overlay\n")

            res_refuse = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertNotEqual(res_refuse.returncode, 0)
            self.assertIn("--force", res_refuse.stderr)

            res_force = self.run_cmd(INSTALL_CMD + ["--skip-deps-check", "--force"], env)
            self.assertEqual(res_force.returncode, 0, res_force.stderr)
            self.assertIn("Notice: Overriding", res_force.stdout)
            with open(os.path.join(data_dir, "overlay.py")) as f:
                self.assertNotEqual(f.read(), "# rogue overlay\n")

    def test_install_refusal_modified_managed_file(self):
        """(h) Install refusal: manifest-recorded file modified by user -> refuse; --force -> updates it."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            with open(os.path.join(data_dir, "style.css"), "w") as f:
                f.write("/* modified */\n")

            res_refuse = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertNotEqual(res_refuse.returncode, 0)
            self.assertIn("--force", res_refuse.stderr)

            res_force = self.run_cmd(INSTALL_CMD + ["--skip-deps-check", "--force"], env)
            self.assertEqual(res_force.returncode, 0, res_force.stderr)
            self.assertIn("Notice: Overriding", res_force.stdout)

    def test_install_refusal_foreign_launcher(self):
        """(i) Foreign launcher (no marker) -> refuse; --force -> overwrite; uninstall never force-removes."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            launcher_dir = os.path.join(fake_home, ".local", "bin")
            os.makedirs(launcher_dir)
            launcher = os.path.join(launcher_dir, "umbriel-keybind-overlay")
            with open(launcher, "w") as f:
                f.write("#!/bin/sh\necho foreign launcher\n")

            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            res_refuse = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertNotEqual(res_refuse.returncode, 0)
            self.assertIn("--force", res_refuse.stderr)

            res_force = self.run_cmd(INSTALL_CMD + ["--skip-deps-check", "--force"], env)
            self.assertEqual(res_force.returncode, 0, res_force.stderr)
            self.assertIn("Notice: Overriding", res_force.stdout)
            with open(launcher) as f:
                self.assertIn("# umbriel-keybind-overlay launcher (managed)", f.read())

            # Replace launcher with markerless foreign script after install
            with open(launcher, "w") as f:
                f.write("#!/bin/sh\necho foreign again\n")

            # Uninstall with --force must still preserve foreign launcher and require manual cleanup
            res_u = self.run_cmd(UNINSTALL_CMD + ["--yes", "--force"], env)
            self.assertEqual(res_u.returncode, 0)
            self.assertTrue(os.path.exists(launcher))
            self.assertIn("manual", res_u.stdout)
            self.assertNotIn("re-run with --force", res_u.stdout)

    def test_quoting_spaces_and_apostrophe_in_home(self):
        """(j) Quoting: HOME containing spaces and apostrophe -> sh -n clean and passes exact path."""
        stub_code = """import sys
if "--version" in sys.argv:
    print("Python 3.11.0")
    sys.exit(0)
print("RECORDED_ARG:", sys.argv[1] if len(sys.argv) > 1 else "")
"""
        stub_dir = self.create_stub(stub_code)
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "weird 'user' name")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": f"{stub_dir}:{os.environ['PATH']}",
            }
            res = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertEqual(res.returncode, 0, res.stderr)

            launcher = os.path.join(fake_home, ".local", "bin", "umbriel-keybind-overlay")
            self.assertTrue(os.path.exists(launcher))

            sh_chk = subprocess.run(["sh", "-n", launcher], capture_output=True, text=True)
            self.assertEqual(sh_chk.returncode, 0, sh_chk.stderr)

            run_res = subprocess.run([launcher], env=env, capture_output=True, text=True)
            self.assertEqual(run_res.returncode, 0, run_res.stderr)
            expected_target = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay", "overlay.py")
            self.assertIn(f"RECORDED_ARG: {expected_target}", run_res.stdout)

    def test_relative_xdg_data_home_refusal(self):
        """(k) Relative XDG_DATA_HOME -> refusal, nothing created."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "XDG_DATA_HOME": "relative/data",
                "PATH": os.environ["PATH"],
            }
            res = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("XDG_DATA_HOME must be absolute", res.stderr)
            self.assertFalse(os.path.exists(os.path.join(td, "relative")))

    def test_path_variants(self):
        """(l) PATH variants: ~/.local/bin off PATH -> warning + absolute snippet; on PATH -> short snippet."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            launcher_dir = os.path.join(fake_home, ".local", "bin")

            env_off = {
                "HOME": fake_home,
                "PATH": "/usr/bin:/bin",
            }
            res_off = self.run_cmd(INSTALL_CMD + ["--skip-deps-check", "--force"], env_off)
            self.assertEqual(res_off.returncode, 0)
            self.assertIn(f"Warning: '{launcher_dir}' is not currently in your PATH.", res_off.stdout)
            self.assertIn(f'spawn {launcher_dir}/umbriel-keybind-overlay"', res_off.stdout)

            env_on = {
                "HOME": fake_home,
                "PATH": f"{launcher_dir}:/usr/bin:/bin",
            }
            res_on = self.run_cmd(INSTALL_CMD + ["--skip-deps-check", "--force"], env_on)
            self.assertEqual(res_on.returncode, 0)
            self.assertIn('action = "spawn umbriel-keybind-overlay"', res_on.stdout)

    def test_forged_manifest_refusal(self):
        """(m) FORGED MANIFEST: traversal, absolute paths, unknown basenames, duplicates, malformed lines -> refuse."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            manifest_path = os.path.join(data_dir, ".install-manifest")

            evil_target = os.path.join(td, "evil_file.txt")
            with open(evil_target, "w") as f:
                f.write("survive\n")

            forged_manifest_cases = [
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  ../evil_file.txt\n",
                f"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  {evil_target}\n",
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  unknown.txt\n",
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  overlay.py\ne3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  overlay.py\n",
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 overlay.py\n",
            ]

            for forged_content in forged_manifest_cases:
                with open(manifest_path, "w") as f:
                    f.write(forged_content)
                res = self.run_cmd(UNINSTALL_CMD + ["--yes"], env)
                self.assertNotEqual(res.returncode, 0, f"Failed on case: {forged_content}")
                self.assertTrue(os.path.exists(evil_target))
                self.assertTrue(os.path.exists(os.path.join(data_dir, "overlay.py")))

    def test_symlinks_refusal(self):
        """(n) Symlinks: symlinked data dir, manifest, and managed file refused by install and uninstall."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            # 1. Symlinked data dir
            outside_dir = os.path.join(td, "outside_data")
            os.makedirs(outside_dir)
            share_dir = os.path.join(fake_home, ".local", "share")
            os.makedirs(share_dir)
            data_link = os.path.join(share_dir, "umbriel-keybind-overlay")
            os.symlink(outside_dir, data_link)

            res_i = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertNotEqual(res_i.returncode, 0)
            self.assertIn("symlink", res_i.stderr)

            res_u = self.run_cmd(UNINSTALL_CMD + ["--yes"], env)
            self.assertNotEqual(res_u.returncode, 0)
            self.assertIn("symlink", res_u.stderr)

            os.unlink(data_link)
            os.makedirs(data_link)

            # 2. Symlinked manifest
            outside_manifest = os.path.join(td, "outside_manifest")
            with open(outside_manifest, "w") as f:
                f.write("dummy\n")
            manifest_link = os.path.join(data_link, ".install-manifest")
            os.symlink(outside_manifest, manifest_link)

            res_i2 = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertNotEqual(res_i2.returncode, 0)
            self.assertIn("symlink", res_i2.stderr)

            res_u2 = self.run_cmd(UNINSTALL_CMD + ["--yes"], env)
            self.assertNotEqual(res_u2.returncode, 0)
            self.assertIn("symlink", res_u2.stderr)

            os.unlink(manifest_link)

            # 3. Symlinked managed file
            outside_file = os.path.join(td, "outside_file")
            with open(outside_file, "w") as f:
                f.write("dummy\n")
            overlay_link = os.path.join(data_link, "overlay.py")
            os.symlink(outside_file, overlay_link)

            res_i3 = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertNotEqual(res_i3.returncode, 0)
            self.assertIn("symlink", res_i3.stderr)

            res_u3 = self.run_cmd(UNINSTALL_CMD + ["--yes"], env)
            self.assertNotEqual(res_u3.returncode, 0)
            self.assertIn("symlink", res_u3.stderr)

    def test_modified_launcher(self):
        """(o) Modified launcher (marker intact, content changed) -> uninstall keeps with warning; --force removes; install refuses."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            launcher = os.path.join(fake_home, ".local", "bin", "umbriel-keybind-overlay")
            with open(launcher, "a") as f:
                f.write("# user tweak\n")

            res_i = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertNotEqual(res_i.returncode, 0)
            self.assertIn("--force", res_i.stderr)

            res_u = self.run_cmd(UNINSTALL_CMD + ["--yes"], env)
            self.assertEqual(res_u.returncode, 0)
            self.assertIn("modified", res_u.stdout)
            self.assertTrue(os.path.exists(launcher))

            # Since launcher is force-eligible, manifest is retained on the first run,
            # allowing immediate force removal on the re-run without reinstalling
            res_u_force = self.run_cmd(UNINSTALL_CMD + ["--yes", "--force"], env)
            self.assertEqual(res_u_force.returncode, 0)
            self.assertFalse(os.path.exists(launcher))

    def test_quoting_dollar_and_backticks_in_home(self):
        """(p) Quoting with space, apostrophe, $, backtick in HOME -> launcher executes exact path; printed advice is paste-safe."""
        stub_code = """import sys
if "--version" in sys.argv:
    print("Python 3.11.0")
    sys.exit(0)
print("RECORDED_ARG:", sys.argv[1] if len(sys.argv) > 1 else "")
"""
        stub_dir = self.create_stub(stub_code)
        with tempfile.TemporaryDirectory() as td:
            sentinel = os.path.join(td, "sentinel.txt")
            fake_home = os.path.join(td, "user d'or $NAME `touch sentinel.txt`")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": f"{stub_dir}:{os.environ['PATH']}",
                "NAME": "expanded_variable",
            }
            res = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertEqual(res.returncode, 0, res.stderr)

            # (e) Extract install summary's printed pointers and assert single-quoted
            uninstall_ptr = None
            lines = res.stdout.splitlines()
            for i, line in enumerate(lines):
                if "To uninstall at any time, run:" in line and i + 1 < len(lines):
                    uninstall_ptr = lines[i + 1].strip()
                if "Installed data files to:" in line and i + 1 < len(lines):
                    self.assertTrue(lines[i + 1].strip().startswith("'") and lines[i + 1].strip().endswith("'"))
                if "Installed launcher script to:" in line and i + 1 < len(lines):
                    self.assertTrue(lines[i + 1].strip().startswith("'") and lines[i + 1].strip().endswith("'"))

            self.assertIsNotNone(uninstall_ptr)
            self.assertTrue(uninstall_ptr.startswith("'") and uninstall_ptr.endswith("'"))

            # Launcher execution check from original test
            launcher = os.path.join(fake_home, ".local", "bin", "umbriel-keybind-overlay")
            self.assertTrue(os.path.exists(launcher))

            sh_chk = subprocess.run(["sh", "-n", launcher], capture_output=True, text=True)
            self.assertEqual(sh_chk.returncode, 0, sh_chk.stderr)

            run_res = subprocess.run([launcher], env=env, capture_output=True, text=True)
            self.assertEqual(run_res.returncode, 0, run_res.stderr)
            expected_target = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay", "overlay.py")
            self.assertIn(f"RECORDED_ARG: {expected_target}", run_res.stdout)

            # (b) Modify one managed file, run INSTALLED uninstall.sh --yes, capture stdout
            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            installed_uninstaller = os.path.join(data_dir, "uninstall.sh")
            manifest_path = os.path.join(data_dir, ".install-manifest")
            style_css = os.path.join(data_dir, "style.css")
            with open(style_css, "a") as f:
                f.write("/* modified */\n")

            res_u = subprocess.run(
                [installed_uninstaller, "--yes"],
                cwd=td,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_u.returncode, 0, res_u.stderr)

            # (c) Extract the printed rerun command line, then execute it verbatim through bash -c
            rerun_cmd = None
            for line in res_u.stdout.splitlines():
                if "--yes --force" in line:
                    rerun_cmd = line.strip()
                    break
            self.assertIsNotNone(rerun_cmd)
            self.assertTrue(rerun_cmd.startswith("'"))

            p_paste = subprocess.run(
                ["bash", "-c", rerun_cmd],
                cwd=td,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(p_paste.returncode, 0, p_paste.stderr)

            # (d) Assert: sentinel file NOT created, rerun succeeded, cleanup finished
            self.assertFalse(os.path.exists(sentinel), "Command substitution was executed on paste!")
            self.assertFalse(os.path.exists(launcher))
            self.assertFalse(os.path.exists(manifest_path))
            self.assertFalse(os.path.exists(data_dir))

            # (e cont.) Reinstall and verify executing uninstall_ptr verbatim works for complete-cleanup
            res_reinstall = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertEqual(res_reinstall.returncode, 0, res_reinstall.stderr)
            self.assertTrue(os.path.exists(launcher))
            self.assertTrue(os.path.exists(data_dir))

            p_clean = subprocess.run(
                ["bash", "-c", f"{uninstall_ptr} --yes"],
                cwd=td,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(p_clean.returncode, 0, p_clean.stderr)
            self.assertFalse(os.path.exists(sentinel))
            self.assertFalse(os.path.exists(launcher))
            self.assertFalse(os.path.exists(data_dir))

    def test_uninstall_installed_copy_sequential_flow_with_modification(self):
        """Sequential flow via installed copy: modified file retains manifest & uninstaller; --force rerun removes all."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            res_i = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertEqual(res_i.returncode, 0, res_i.stderr)

            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            installed_uninstaller = os.path.join(data_dir, "uninstall.sh")
            manifest_path = os.path.join(data_dir, ".install-manifest")
            style_path = os.path.join(data_dir, "style.css")

            self.assertTrue(os.path.exists(installed_uninstaller))
            self.assertTrue(os.path.exists(manifest_path))

            # Modify a managed file
            with open(style_path, "a") as f:
                f.write("/* user custom modification */\n")

            # Run the INSTALLED uninstall.sh --yes
            res_u1 = subprocess.run(
                [installed_uninstaller, "--yes"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_u1.returncode, 0, res_u1.stderr)
            self.assertIn("Retaining manifest and uninstaller copy", res_u1.stdout)
            self.assertIn("--yes --force", res_u1.stdout)

            # Assert modified file, manifest, and installed uninstaller are all KEPT
            self.assertTrue(os.path.exists(style_path))
            self.assertTrue(os.path.exists(manifest_path))
            self.assertTrue(os.path.exists(installed_uninstaller))

            # Re-run the SAME installed uninstall.sh with --yes --force
            res_u2 = subprocess.run(
                [installed_uninstaller, "--yes", "--force"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_u2.returncode, 0, res_u2.stderr)

            # Assert everything removed and data dir is gone
            launcher = os.path.join(fake_home, ".local", "bin", "umbriel-keybind-overlay")
            self.assertFalse(os.path.exists(launcher))
            self.assertFalse(os.path.exists(style_path))
            self.assertFalse(os.path.exists(manifest_path))
            self.assertFalse(os.path.exists(installed_uninstaller))
            self.assertFalse(os.path.exists(data_dir))

    def test_uninstall_installed_copy_complete_cleanup(self):
        """Complete cleanup via installed copy: unmodified install -> installed uninstall.sh removes everything."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = os.path.join(td, "home")
            os.makedirs(fake_home)
            env = {
                "HOME": fake_home,
                "PATH": os.environ["PATH"],
            }
            res_i = self.run_cmd(INSTALL_CMD + ["--skip-deps-check"], env)
            self.assertEqual(res_i.returncode, 0, res_i.stderr)

            data_dir = os.path.join(fake_home, ".local", "share", "umbriel-keybind-overlay")
            installed_uninstaller = os.path.join(data_dir, "uninstall.sh")
            launcher = os.path.join(fake_home, ".local", "bin", "umbriel-keybind-overlay")

            self.assertTrue(os.path.exists(installed_uninstaller))
            self.assertTrue(os.path.exists(launcher))

            # Run installed uninstall.sh --yes directly
            res_u = subprocess.run(
                [installed_uninstaller, "--yes"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_u.returncode, 0, res_u.stderr)
            self.assertIn("Uninstall complete", res_u.stdout)

            # Everything must be gone
            self.assertFalse(os.path.exists(launcher))
            self.assertFalse(os.path.exists(data_dir))


if __name__ == "__main__":
    unittest.main()
