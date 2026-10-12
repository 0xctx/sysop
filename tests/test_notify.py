import json
import os

from helpers import TempRepoCase


class NotifyTest(TempRepoCase):
    def notifier(self) -> str:
        """A notifier command that appends its arguments to a file."""
        log = self.tmp / "notified"
        script = self.tmp / "bin" / "ping"
        script.parent.mkdir(exist_ok=True)
        script.write_text(f'#!/bin/sh\nprintf "%s|%s\\n" "$1" "$2" >> {log}\n')
        script.chmod(0o755)
        self.env["PATH"] = f"{script.parent}{os.pathsep}{self.env['PATH']}"
        return str(log)

    def test_off_by_default_and_never_fails(self):
        out = self.sysop_json("notify", "hello")
        self.assertFalse(out["sent"])
        self.sysop("config", "set", "notify", "no-such-notifier-xyz")
        self.assertFalse(self.sysop_json("notify", "hello")["sent"])  # a missing notifier is not an error

    def test_command_notifier_and_run_end(self):
        log = self.notifier()
        self.sysop("config", "set", "notify", "ping")
        self.assertTrue(self.sysop_json("notify", "built t1", "--title", "sysop fleet")["sent"])
        self.sysop("run", "start", "ship-x", "--session", "s")
        ended = self.sysop_json("run", "end", "ship-x", "--outcome", "landed")
        self.assertTrue(ended["notified"])
        lines = open(log).read().splitlines()
        self.assertEqual(lines[0], "sysop fleet|built t1")
        self.assertEqual(lines[1].split("|")[0], "sysop: ship-x landed")

    def test_ntfy_target_posts_to_the_topic(self):
        import http.server
        import threading
        got = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                got["title"] = self.headers["Title"]
                got["body"] = self.rfile.read(int(self.headers["Content-Length"])).decode()
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.handle_request, daemon=True).start()
        self.sysop("config", "set", "notify", f"ntfy:http://127.0.0.1:{server.server_port}/mytopic")
        self.assertTrue(self.sysop_json("notify", "fleet done: 3 landed")["sent"])
        self.assertEqual(got, {"title": "sysop", "body": "fleet done: 3 landed"})
