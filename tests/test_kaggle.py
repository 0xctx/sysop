import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import ROOT, TempRepoCase

sys.path.insert(0, str(ROOT / "scripts"))
import kaggle_jobs  # noqa: E402


class KaggleJobsTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin(only=("agy", "claude", "kaggle"))
        self.state = self.tmp / "fake-kaggle"
        self.env["FAKE_KAGGLE_DIR"] = str(self.state)
        self.env["FAKE_KAGGLE_STEPS"] = "3"

    def spec(self, **over):
        rows = [{"id": i, "prompt": f"summarise chapter {i}"} for i in range(3)]
        (self.repo / "prompts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        spec = {"name": "chapters", "kind": "infer", "model": "gemma-4-26b-a4b", "inputs": "prompts.jsonl",
                "max_minutes": 60, "params": {"max_tokens": 200}, **over}
        (self.repo / "job.json").write_text(json.dumps(spec))
        return "job.json"

    def test_models_lists_the_batch_catalog(self):
        self.assertIn("gemma-4-26b-a4b", self.sysop("kaggle", "models").stdout)

    def test_run_pushes_a_private_finite_gpu_notebook(self):
        rec = self.sysop_json("kaggle", "run", self.spec())
        self.assertEqual((rec["kernel"], rec["dataset"], rec["rows"]),
                         ("tester/sysop-job-chapters", "tester/sysop-in-chapters", 3))
        kernel = self.state / "kernels" / "sysop-job-chapters"
        meta = json.loads((kernel / "kernel-metadata.json").read_text())
        self.assertTrue(meta["is_private"])
        self.assertEqual((meta["enable_gpu"], meta["machine_shape"]), (True, "NvidiaTeslaT4"))
        self.assertEqual(meta["dataset_sources"], ["tester/sysop-in-chapters"])
        code = (kernel / "run.py").read_text()
        self.assertIn('"repo": "google/gemma-4-26B-A4B-it-qat-q4_0-gguf"', code)
        self.assertIn('"max_minutes": 60', code)
        self.assertIn('"--host", "127.0.0.1"', code)  # the server never listens beyond the session
        self.assertIn('"accelerator": "gpu"', code)  # a GPU job stops when the model misses the GPU
        self.assertNotIn('"LD_LIBRARY_PATH"', code)  # replacing it hid Kaggle's libcuda (e2e runs 2-3 on CPU)
        self.assertTrue((self.state / "datasets" / "sysop-in-chapters" / "prompts.jsonl").is_file())
        self.assertTrue((self.repo / ".sysop" / "kaggle" / "chapters" / "1" / "job.json").is_file())

    def test_gpu_evidence_reads_memory_not_log_lines(self):
        tree = ast.parse((ROOT / "scripts" / "kaggle" / "common.py").read_text())
        [fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "gpu_warning"]
        scope: dict = {}
        exec(compile(ast.Module([fn], []), "common.py", "exec"), scope)
        warn = scope["gpu_warning"]
        self.assertIsNone(warn(["Tesla T4, 7145 MiB", "Tesla T4, 7059 MiB"], "llama_server: model loaded"))  # e2e run 4
        self.assertIn("0 MiB", warn(["Tesla T4, 0 MiB", "Tesla T4, 0 MiB"], ""))  # e2e runs 2-3
        self.assertIn("driver version", warn(["Tesla T4, 300 MiB"], "E ggml_cuda_init: failed to initialize CUDA: "
                                                                     "CUDA driver version is insufficient"))
        self.assertIsNotNone(warn([], ""))  # no nvidia-smi at all

    def test_rendered_notebooks_compile_with_json_literals(self):
        for template in ("infer.py", "serve.py"):
            code = kaggle_jobs.notebook(template, {"params": {"reasoning": True, "system": None}, "key": "x'\""})
            compile(code, template, "exec")  # json.dumps alone wrote `true`, a NameError on Kaggle

    def test_cached_engine_is_attached_when_present(self):
        (self.state / "datasets" / "sysop-llama-cuda").mkdir(parents=True)
        rec = self.sysop_json("kaggle", "run", self.spec())
        self.assertTrue(rec["engine_cached"])

    def test_engine_build_skips_the_cache_and_pull_seeds_it(self):
        (self.state / "datasets" / "sysop-llama-cuda").mkdir(parents=True)
        rec = self.sysop_json("kaggle", "run", self.spec(engine="build"))
        self.assertFalse(rec["engine_cached"])
        code = (self.state / "kernels" / "sysop-job-chapters" / "run.py").read_text()
        self.assertIn('"engine": "build"', code)
        self.env["FAKE_KAGGLE_ENGINE"] = "unproven"  # built, then killed before it served: no proof
        self.assertNotIn("engine_cached", self.sysop_json("kaggle", "pull", "chapters"))
        self.assertFalse((self.state / "datasets" / "sysop-llama-cuda" / "llama-server").exists())
        self.env["FAKE_KAGGLE_ENGINE"] = "1"
        out = self.sysop_json("kaggle", "pull", "chapters")
        self.assertEqual(out["engine_cached"], "tester/sysop-llama-cuda")
        self.assertTrue((self.state / "datasets" / "sysop-llama-cuda" / "llama-server").is_file())

    def test_bad_specs_are_refused_before_any_upload(self):
        self.assertEqual(self.sysop("kaggle", "run", self.spec(name="Bad Name")).returncode, 1)
        self.assertEqual(self.sysop("kaggle", "run", self.spec(model="gpt-9")).returncode, 1)
        self.assertEqual(self.sysop("kaggle", "run", self.spec(max_minutes=900)).returncode, 1)
        self.assertEqual(self.sysop("kaggle", "run", self.spec(engine="vllm")).returncode, 1)
        spec = self.spec()
        (self.repo / "prompts.jsonl").write_text('{"id": 1, "prompt": "a"}\n{"id": 1, "prompt": "b"}\n')
        proc = self.sysop("kaggle", "run", spec)
        self.assertIn("unique ids", proc.stderr)
        (self.repo / "prompts.jsonl").write_text('{"id": 1, "prompt": "a"}\nnot json\n')
        self.assertIn("line 2: not JSON", self.sysop("kaggle", "run", spec).stderr)
        self.assertFalse((self.state / "datasets").exists())

    def test_status_wait_and_pull(self):
        self.sysop_json("kaggle", "run", self.spec())
        self.assertEqual(self.sysop_json("kaggle", "status", "chapters")["state"], "queued")
        done = self.sysop_json("kaggle", "wait", "chapters", "--timeout", "30", "--poll", "0.1")
        self.assertEqual(done["state"], "complete")
        out = self.sysop_json("kaggle", "pull", "chapters")
        self.assertEqual((out["results"], out["failed"], out["expected"]), (3, 0, 3))
        self.assertIn("results.jsonl", out["files"])

    def test_a_rerun_waits_for_the_running_attempt(self):
        self.env["FAKE_KAGGLE_STEPS"] = "100"
        self.sysop_json("kaggle", "run", self.spec())
        proc = self.sysop("kaggle", "run", self.spec())
        self.assertIn("is still queued", proc.stderr)
        self.assertFalse((self.repo / ".sysop" / "kaggle" / "chapters" / "2").exists())
        self.env["FAKE_KAGGLE_STEPS"] = "1"
        self.assertEqual(self.sysop_json("kaggle", "run", self.spec())["attempt"], 2)

    def test_wait_times_out_with_124_and_errors_are_reported(self):
        self.env["FAKE_KAGGLE_STEPS"] = "100"
        self.sysop_json("kaggle", "run", self.spec())
        self.assertEqual(self.sysop("kaggle", "wait", "chapters", "--timeout", "0.3", "--poll", "0.1").returncode, 124)
        self.env["FAKE_KAGGLE_STEPS"], self.env["FAKE_KAGGLE_FAIL"] = "1", "1"
        self.assertEqual(self.sysop_json("kaggle", "status", "chapters")["state"], "error")

    def test_script_job_runs_the_operators_ml_script_on_cpu(self):
        (self.repo / "train.py").write_text("print('training')\n")
        (self.repo / "data").mkdir()
        (self.repo / "data" / "x.csv").write_text("a,b\n1,2\n")
        spec = {"name": "embed-index", "kind": "script", "script": "train.py", "inputs": ["data"],
                "max_minutes": 30}
        (self.repo / "s.json").write_text(json.dumps(spec))
        rec = self.sysop_json("kaggle", "run", "s.json")
        kernel = self.state / "kernels" / "sysop-job-embed-index"
        meta = json.loads((kernel / "kernel-metadata.json").read_text())
        self.assertEqual((rec["accelerator"], meta["enable_gpu"]), ("cpu", False))
        self.assertNotIn("machine_shape", meta)
        self.assertIn("LIMIT = 1710", (kernel / "run.py").read_text())  # 30 min less a margin
        self.assertTrue((self.state / "datasets" / "sysop-in-embed-index" / "data" / "x.csv").is_file())

    def test_unknown_job_and_missing_cli(self):
        self.assertIn("no job named", self.sysop("kaggle", "status", "nope").stderr)
        self.use_fakebin(only=("agy", "claude"))
        (self.tmp / "bin" / "kaggle").unlink()
        self.assertIn("kaggle CLI is not installed", self.sysop("kaggle", "run", self.spec()).stderr)


FAKE_LLAMA = r'''#!/usr/bin/env python3
import http.server, json, os, sys, time
tmp, mode = os.environ["SYSOP_TMP"], os.environ.get("FAKE_LLAMA_MODE", "gpu")
open(f"{tmp}/server.pid", "w").write(str(os.getpid()))
if mode == "cpu":
    print("E ggml_cuda_init: failed to initialize CUDA: CUDA driver version is insufficient", flush=True)
if mode == "hang":
    time.sleep(3600)


class H(http.server.BaseHTTPRequestHandler):
    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply({"status": "ok"})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.reply({"choices": [{"message": {"content": "a summary"}}], "usage": {"completion_tokens": 3}})

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[sys.argv.index("--port") + 1])), H).serve_forever()
'''


class InferTemplateTest(unittest.TestCase):
    """Runs the real notebook template offline: fake llama-server, nvidia-smi and huggingface_hub."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for d in ("kaggle/input/engine", "kaggle/input/data", "kaggle/working", "tmp", "bin", "py/huggingface_hub"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "kaggle/input/engine/llama-server").write_text(FAKE_LLAMA)
        smi = self.tmp / "bin" / "nvidia-smi"
        smi.write_text('#!/bin/sh\necho "Tesla T4, ${FAKE_GPU_MIB} MiB"\necho "Tesla T4, ${FAKE_GPU_MIB} MiB"\n')
        smi.chmod(0o755)
        (self.tmp / "py/huggingface_hub/__init__.py").write_text(
            "def hf_hub_download(repo, file, local_dir):\n    return local_dir + '/' + file\n")
        (self.tmp / "kaggle/input/data/prompts.jsonl").write_text(
            "".join(json.dumps({"id": i, "prompt": f"p{i}"}) + "\n" for i in range(3)))

    def run_template(self, mode: str, gpu_mib: int, **job) -> tuple[subprocess.CompletedProcess, dict]:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        spec = {"repo": "r", "file": "m.gguf", "name": "t", "max_minutes": 30, "accelerator": "gpu",
                "port": port, "parallel": 2, **job}
        code = kaggle_jobs.notebook("infer.py", spec)
        (self.tmp / "run.py").write_text(code)
        env = {**os.environ, "SYSOP_KAGGLE_ROOT": str(self.tmp / "kaggle"), "SYSOP_TMP": str(self.tmp / "tmp"),
               "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "PYTHONPATH": str(self.tmp / "py"),
               "FAKE_LLAMA_MODE": mode, "FAKE_GPU_MIB": str(gpu_mib)}
        proc = subprocess.run([sys.executable, str(self.tmp / "run.py")], env=env, capture_output=True,
                              text=True, timeout=60)
        return proc, json.loads((self.tmp / "kaggle/working/metrics.json").read_text())

    def assert_server_stopped(self):
        pid = int((self.tmp / "tmp/server.pid").read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        os.kill(pid, 9)
        self.fail("llama-server outlived the job")

    def test_gpu_job_answers_every_row_and_stops_the_server(self):
        proc, m = self.run_template("gpu", 7000)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        rows = (self.tmp / "kaggle/working/results.jsonl").read_text().splitlines()
        self.assertEqual((len(rows), m["ok"], m.get("warning")), (3, 3, None))
        self.assert_server_stopped()

    def test_gpu_job_on_cpu_stops_before_the_batch(self):
        proc, m = self.run_template("cpu", 0)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("driver version", m["warning"])
        self.assertIn("did not reach the GPU", m["error"])
        self.assertFalse((self.tmp / "kaggle/working/results.jsonl").exists())
        self.assert_server_stopped()

    def test_cpu_job_carries_no_gpu_warning(self):
        proc, m = self.run_template("gpu", 0, accelerator="cpu")
        self.assertEqual((proc.returncode, m.get("warning")), (0, None), proc.stderr[-500:])

    def test_a_server_that_never_gets_healthy_is_given_up_on(self):
        proc, m = self.run_template("hang", 7000, load_limit=2)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not healthy after 2 s", m["error"])
        self.assert_server_stopped()
