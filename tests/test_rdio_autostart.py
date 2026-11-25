# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""Starting Rdio Scanner with the Monitor (v1.1.90).

Finding it (services and programs), starting it only when it doesn't answer and only on this PC, and
stopping on exit only a program the Monitor started. One test starts a stand-in rdio-scanner program
(a small Python web server in a throw-away folder) for real and stops it again.
"""
import os
import socket
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import mon, monitor


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalTests(unittest.TestCase):
    def test_local_addresses(self):
        for url in ("", "127.0.0.1:3000", "http://localhost:3000", "http://127.0.1.1:3000/", "[::1]:3000",
                    socket.gethostname() + ":3000"):
            self.assertTrue(mon.rdio_server_is_local(url), url)
        self.assertFalse(mon.rdio_server_is_local("https://example.com"))
        self.assertFalse(mon.rdio_server_is_local("http://192.0.2.10:3000"))     # TEST-NET, never this PC


class DetectTests(unittest.TestCase):
    def test_services_from_systemctl(self):
        def run(args):
            if "--user" in args:
                return "rdio-scanner.service enabled enabled\n"
            return "rdio-scanner.service disabled enabled\nrdio-scanner-test.service static -\n"
        found = mon.rdio_service_candidates(run)
        self.assertEqual(found, [("user", "rdio-scanner.service"), ("system", "rdio-scanner.service"),
                                 ("system", "rdio-scanner-test.service")])

    def test_programs_in_usual_folders(self):
        with tempfile.TemporaryDirectory() as home:
            home = Path(home)
            prog = home / "rdio-scanner-linux-amd64-v6.6.3" / "rdio-scanner"
            prog.parent.mkdir()
            prog.write_text("#!/bin/sh\n")
            prog.chmod(0o755)
            not_exec = home / "rdio-scanner" / "rdio-scanner"
            not_exec.parent.mkdir()
            not_exec.write_text("")
            found = mon.rdio_program_candidates(home, which=lambda n: None)
            self.assertEqual(found, [prog])

    def test_detect_puts_services_first(self):
        with tempfile.TemporaryDirectory() as home:
            prog = Path(home) / "bin" / "rdio-scanner"
            prog.parent.mkdir()
            prog.write_text("#!/bin/sh\n")
            prog.chmod(0o755)
            ways = mon.rdio_detect(home, which=lambda n: None,
                                   run=lambda a: "rdio-scanner.service enabled\n" if "--user" not in a else "")
        self.assertEqual([w["kind"] for w in ways], ["system", "program"])
        self.assertIn("password", ways[0]["label"])

    def test_start_commands(self):
        self.assertEqual(mon.rdio_start_command({"kind": "user", "target": "rdio-scanner.service"}),
                         ("systemctl", ["--user", "start", "rdio-scanner.service"]))
        self.assertEqual(mon.rdio_start_command({"kind": "system", "target": "rdio-scanner.service"}),
                         ("pkexec", ["systemctl", "start", "rdio-scanner.service"]))


class StartTests(unittest.TestCase):
    def setUp(self):
        self.w = monitor(fresh=True)
        self.s = self.w.settings
        self.s.setValue("rdio/url", "127.0.0.1:3000")
        self.answers = []
        self.launched = []

    def server(self, answers, ways=({"kind": "user", "target": "rdio-scanner.service", "label": "User service"},),
               launch_ok=True):
        self.answers = list(answers)

        def reachable():
            return self.answers.pop(0) if self.answers else True

        def launch(way):
            self.launched.append(way)
            return launch_ok, None, "Started it." if launch_ok else "Couldn't start it: no."
        srv = mon.RdioServer(self.w, reachable=reachable, launch=launch, detect=lambda: list(ways))
        srv.statusBar = None
        return srv

    def test_already_running_starts_nothing(self):
        ok, text = self.server([True]).start_if_needed(sleep=lambda s: None)
        self.assertTrue(ok)
        self.assertEqual(self.launched, [])
        self.assertIn("already running", text)

    def test_started_and_answers(self):
        ok, text = self.server([False, False, False, True]).start_if_needed(sleep=lambda s: None)
        self.assertTrue(ok, text)
        self.assertEqual(self.launched[0]["kind"], "user")
        self.assertIn("answered after 3 s", text)

    def test_never_answers(self):
        srv = self.server([False] * 50)
        ok, text = srv.start_if_needed(wait=5, sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertIn("didn't answer within 5 s", text)

    def test_other_computer_is_not_started(self):
        self.s.setValue("rdio/url", "http://192.0.2.10:3000")
        ok, text = self.server([False]).start_if_needed(sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertEqual(self.launched, [])
        self.assertIn("another computer", text)

    def test_not_found(self):
        ok, text = self.server([False], ways=()).start_if_needed(sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertIn("wasn't found", text)

    def test_chosen_program_beats_detection(self):
        self.s.setValue("rdio/start_kind", "program")
        self.s.setValue("rdio/start_target", "/opt/rdio/rdio-scanner")
        srv = self.server([False, True])
        srv.start_if_needed(sleep=lambda s: None)
        self.assertEqual(self.launched[0], {"kind": "program", "target": "/opt/rdio/rdio-scanner",
                                            "label": "Program /opt/rdio/rdio-scanner"})

    def test_launch_failure_is_reported(self):
        ok, text = self.server([False], launch_ok=False).start_if_needed(sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertIn("Couldn't start", text)

    def test_startup_only_when_ticked(self):
        srv = self.server([True])
        calls = []
        srv.start_in_background = lambda: calls.append(1)
        srv.startup()
        self.assertEqual(calls, [])            # off by default (QTimer is a stand-in, so nothing would run)


class RealProgramTests(unittest.TestCase):
    """A stand-in rdio-scanner (a Python web server) started for real, then stopped on exit."""

    def test_start_and_stop_a_program(self):
        w = monitor(fresh=True)
        port = free_port()
        with tempfile.TemporaryDirectory() as folder:
            prog = Path(folder) / "rdio-scanner"
            prog.write_text(f"#!{sys.executable}\nimport http.server, os\n"
                            "open('started-here', 'w').write(os.getcwd())\n"
                            f"http.server.ThreadingHTTPServer(('127.0.0.1', {port}), "
                            "http.server.SimpleHTTPRequestHandler).serve_forever()\n")
            prog.chmod(prog.stat().st_mode | stat.S_IXUSR)
            s = w.settings
            s.setValue("rdio/url", f"127.0.0.1:{port}")
            s.setValue("rdio/start_kind", "program")
            s.setValue("rdio/start_target", str(prog))
            s.setValue("rdio/stop_on_exit", "true")
            srv = mon.RdioServer(w)
            ok, text = srv.start_if_needed(wait=15)
            try:
                self.assertTrue(ok, text)
                self.assertTrue((Path(folder) / "started-here").exists())     # runs in its own folder
                self.assertIsNotNone(srv.started_pid)
                pid = srv.started_pid
                ok2, text2 = srv.start_if_needed()
                self.assertIn("already running", text2)                        # not started twice
                srv.started_path = "rdio-scanner"
                # The process is Python running the script, so /proc/<pid>/exe is python: shutdown()
                # rightly refuses (it only stops what is really rdio-scanner) ...
                self.assertFalse(srv.shutdown())
                # ... and with the program's name matching, it stops it.
                srv.started_path = os.readlink(f"/proc/{pid}/exe")
                self.assertTrue(srv.shutdown())
                for _ in range(50):
                    try:
                        os.kill(pid, 0)
                        os.waitpid(pid, os.WNOHANG)
                    except (ProcessLookupError, ChildProcessError):
                        break
                    time.sleep(0.1)
                self.assertFalse(srv.check_reachable(timeout=1))
            finally:
                try:
                    os.kill(srv.started_pid or pid, 9)
                except (OSError, UnboundLocalError, TypeError):
                    pass

    def test_no_stop_unless_asked(self):
        w = monitor(fresh=True)
        srv = mon.RdioServer(w)
        srv.started_pid, srv.started_path = os.getpid(), sys.executable
        w.settings.setValue("rdio/stop_on_exit", "false")
        self.assertFalse(srv.shutdown())                 # never kills anything unless ticked


if __name__ == "__main__":
    unittest.main()
