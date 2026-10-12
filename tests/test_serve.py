import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from helpers import ROOT, TempRepoCase

sys.path.insert(0, str(ROOT / "scripts"))
import kaggle_jobs  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServeTemplateTest(unittest.TestCase):
    """Runs the real serve notebook offline: fake upstream, no tunnel (no domain)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "kaggle/working").mkdir(parents=True)
        (self.tmp / "tmp").mkdir()
        self.port = free_port()

    def start(self, **job) -> subprocess.Popen:
        spec = {"id": "gemma-4-26b-a4b", "key": "k-123", "port": self.port, "fake_upstream": True,
                "accelerator": "cpu", "idle_minutes": 5, "max_hours": 1, **job}
        (self.tmp / "run.py").write_text(kaggle_jobs.notebook("serve.py", spec))
        env = {**os.environ, "SYSOP_KAGGLE_ROOT": str(self.tmp / "kaggle"), "SYSOP_TMP": str(self.tmp / "tmp")}
        proc = subprocess.Popen([sys.executable, str(self.tmp / "run.py")], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        for _ in range(100):
            if self.call("/health")[0] == 200:
                return proc
            time.sleep(0.1)
        self.fail("serve notebook never got healthy: " + (proc.stdout.read() if proc.poll() is not None else ""))

    def call(self, path: str, key: str | None = None, body: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {key}"} if key else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")
        except OSError:
            return 0, {}

    def metrics(self) -> dict:
        return json.loads((self.tmp / "kaggle/working/metrics.json").read_text())

    def test_keyed_proxy_answers_and_down_ends_the_session(self):
        (self.tmp / "tmp" / "server.log").write_text("main: --api-key k-123 --port 8081\n")
        proc = self.start()
        self.assertEqual(self.call("/v1/models")[0], 401)  # no key: refused
        self.assertEqual(self.call("/v1/models", key="wrong")[0], 401)
        code, out = self.call("/v1/chat/completions", key="k-123",
                              body={"messages": [{"role": "user", "content": "hello"}]})
        self.assertEqual((code, out["choices"][0]["message"]["content"]), (200, "echo: hello"))
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", method="POST",
                                     data=iter([b'{"messages": []}']),
                                     headers={"Authorization": "Bearer k-123", "Transfer-Encoding": "chunked"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(caught.exception.code, 411)
        code, st = self.call("/sysop/status", key="k-123")
        self.assertEqual((code, st["ready"], st["requests"]), (200, True, 1))
        self.assertEqual(self.call("/sysop/down", key="k-123", body={})[0], 200)
        self.assertEqual(proc.wait(timeout=15), 0)
        self.assertEqual((self.metrics()["exit"], self.metrics()["requests"]), ("down", 1))
        saved = (self.tmp / "kaggle/working/server.log").read_text()
        self.assertIn("<session key>", saved)
        self.assertNotIn("k-123", saved)  # an engine that logs its --api-key never leaks it into outputs

    def test_idle_session_ends_by_itself(self):
        proc = self.start(idle_minutes=0.05)  # 3 s
        self.assertEqual(proc.wait(timeout=20), 0)
        self.assertEqual(self.metrics()["exit"], "idle")

    def test_no_key_no_endpoint(self):
        spec = {"key": "", "port": self.port, "fake_upstream": True}
        (self.tmp / "run.py").write_text(kaggle_jobs.notebook("serve.py", spec))
        env = {**os.environ, "SYSOP_KAGGLE_ROOT": str(self.tmp / "kaggle"), "SYSOP_TMP": str(self.tmp / "tmp")}
        proc = subprocess.run([sys.executable, str(self.tmp / "run.py")], env=env, capture_output=True, text=True,
                              timeout=20)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unkeyed endpoint is never opened", self.metrics()["error"])


class ServeCommandTest(TempRepoCase):
    """`sysop.py serve …` against the fake kaggle CLI, which really runs the pushed notebook locally."""

    def setUp(self):
        super().setUp()
        self.use_fakebin(only=("agy", "claude", "kaggle"))
        self.state = self.tmp / "fake-kaggle"
        self.port = free_port()
        self.env.update(FAKE_KAGGLE_DIR=str(self.state), FAKE_KAGGLE_RUN=str(self.port),
                        SYSOP_SERVE_URL=f"http://127.0.0.1:{self.port}", SYSOP_SERVE_POLL="0.2",
                        SYSOP_SERVE_DOWN_POLLS="50")

    def tearDown(self):
        self.kill_notebook()  # before the temp dir (and its pid file) goes
        super().tearDown()

    def kill_notebook(self):
        pid = self.state / "kernels" / "sysop-serve" / "pid"
        if pid.is_file():
            try:
                os.kill(int(pid.read_text()), 9)
            except ProcessLookupError:
                pass

    def ready(self):
        self.assertEqual(self.sysop("config", "set", "serve.domain", "my-name.ngrok-free.dev",
                                    "--scope", "user").returncode, 0)
        self.env["NGROK_AUTHTOKEN"] = "tok-secret-123"
        out = self.sysop("serve", "token")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("tok-secret-123", out.stdout + out.stderr)  # never printed
        del self.env["NGROK_AUTHTOKEN"]

    def pi_models(self) -> dict:
        path = self.home / ".pi" / "agent" / "models.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def test_up_needs_a_domain_and_a_token(self):
        self.assertIn("serve.domain is not set", self.sysop("serve", "up").stderr)
        self.sysop("config", "set", "serve.domain", "my-name.ngrok-free.dev", "--scope", "user")
        self.assertIn("serve token", self.sysop("serve", "up").stderr)
        self.assertIn("NGROK_AUTHTOKEN is not set", self.sysop("serve", "token").stderr)
        self.assertIn("bare host name", self.sysop("config", "set", "serve.domain", "https://x.dev/").stderr)

    @unittest.skipUnless(sys.platform.startswith("linux"), "runs the Kaggle serve notebook (a Linux script) locally")
    def test_up_status_down_round_trip(self):
        self.ready()
        (self.home / ".pi" / "agent").mkdir(parents=True)
        (self.home / ".pi" / "agent" / "models.json").write_text(json.dumps({"providers": {"mine": {"baseUrl": "x"}}}))
        up = self.sysop_json("serve", "up")
        self.assertEqual((up["status"], up["pi"]), ("up", "pi:kaggle/qwen3.6-35b-a3b"))
        token = self.state / "datasets" / "sysop-tunnel" / "ngrok.token"
        self.assertEqual(token.read_text(), "tok-secret-123")
        meta = json.loads((self.state / "kernels" / "sysop-serve" / "kernel-metadata.json").read_text())
        self.assertTrue(meta["is_private"])
        self.assertIn("tester/sysop-tunnel", meta["dataset_sources"])
        key = (self.home / ".cache" / "sysop" / "serve.key").read_text()
        provider = self.pi_models()["providers"]["kaggle"]
        self.assertEqual((provider["apiKey"], provider["models"][0]["id"]), (key, "qwen3.6-35b-a3b"))
        self.assertIn("mine", self.pi_models()["providers"])  # the operator's own providers are kept
        self.assertNotIn(key, json.dumps(up))  # the key is never printed
        self.assertTrue(self.sysop_json("serve", "status")["ready"])
        self.assertTrue(self.sysop_json("serve", "up")["already_up"])  # no second session
        down = self.sysop_json("serve", "down")
        self.assertEqual(down["kernel_state"], "complete")
        self.assertNotIn("kaggle", self.pi_models()["providers"])
        self.assertFalse((self.home / ".cache" / "sysop" / "serve.key").exists())
        self.assertEqual(self.sysop_json("serve", "status")["status"], "down")

    def test_an_unreadable_pi_models_file_is_never_overwritten(self):
        self.ready()
        path = self.home / ".pi" / "agent" / "models.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"providers": {broken')
        self.assertIn("not valid JSON", self.sysop("serve", "up").stderr)
        self.assertEqual(path.read_text(), '{"providers": {broken')
        self.assertFalse((self.state / "kernels" / "sysop-serve").exists())  # nothing was pushed

    @unittest.skipUnless(sys.platform.startswith("linux"), "runs the Kaggle serve notebook (a Linux script) locally")
    def test_the_run_that_started_a_session_ends_it(self):
        self.ready()
        self.env["CLAUDE_CODE_SESSION_ID"] = "s1"  # the session's own run, as in a real fleet
        self.sysop("run", "start", "fleet-x")
        self.assertEqual(self.sysop_json("serve", "up")["run"], "fleet-x")
        ended = self.sysop_json("run", "end", "fleet-x", "--outcome", "done")
        self.assertEqual(ended["serve_down"]["kernel_state"], "complete")
        self.assertFalse((self.home / ".cache" / "sysop" / "serve.json").exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "runs the Kaggle serve notebook (a Linux script) locally")
    def test_a_session_that_ended_by_itself_is_cleared_by_status(self):
        self.ready()
        self.assertEqual(self.sysop("config", "set", "serve.idle_minutes", "1", "--scope", "user").returncode, 0)
        self.sysop_json("serve", "up")
        self.kill_notebook()  # as if it reached its idle limit
        time.sleep(0.5)
        st = self.sysop_json("serve", "status")
        self.assertEqual((st["status"], st["ended"]), ("down", "complete"))
        self.assertNotIn("kaggle", self.pi_models().get("providers", {}))

    def test_add_picks_a_fitting_4bit_gguf(self):
        api = self.tmp / "hf"
        self.env["SYSOP_HF_API"] = f"file://{api}"

        def repo(name, files):
            base = api / "models" / "org" / name
            (base / "tree").mkdir(parents=True)
            (base / "revision").mkdir()
            (base / "tree" / "main").write_text(json.dumps([{"path": p, "size": s} for p, s in files]))
            (base / "revision" / "main").write_text(json.dumps({"cardData": {"license": "apache-2.0"}}))

        repo("Good-GGUF", [("Good-Q8_0.gguf", 30e9), ("Good-Q4_K_M.gguf", 18e9), ("README.md", 10)])
        repo("Huge-GGUF", [("Huge-Q4_K_M.gguf", 60e9)])
        out = self.sysop_json("serve", "add", "org/Good-GGUF")
        self.assertEqual((out["id"], out["file"], out["size_gb"]), ("good", "Good-Q4_K_M.gguf", 18.0))
        self.assertIn("org/Good-GGUF/Good-Q4_K_M.gguf", self.sysop("serve", "list").stdout)
        self.assertIn("not measured", self.sysop("serve", "list").stdout)
        self.assertIn("fits about 24 GB", self.sysop("serve", "add", "org/Huge-GGUF").stderr)
        self.assertIn("want a Hugging Face repo id", self.sysop("serve", "add", "not a repo").stderr)
