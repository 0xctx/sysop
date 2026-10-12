import json
import time

from helpers import TempRepoCase


class PiWorkerTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin(only=("agy", "claude", "pi"))

    def set_tier(self, *entries):
        self.assertEqual(self.sysop("models", "set", "fast", *entries).returncode, 0)

    def test_roster_lists_provider_models_without_ansi_or_header(self):
        pi = self.sysop_json("roster")["workers"]["pi"]
        self.assertTrue(pi["available"])
        self.assertEqual(pi["models"], ["cline/google/gemma-4-31b-it:free",
                                        "cline/nvidia/nemotron-3-super-120b-a12b:free", "kaggle/qwen3-coder-30b",
                                        "google/gemini-3.5-flash"])
        self.assertEqual(pi["meta"]["kaggle/qwen3-coder-30b"], {"ctx": 64000, "thinking": False})
        self.assertEqual(pi["meta"]["google/gemini-3.5-flash"]["ctx"], 1000000)

    def test_signed_in_nowhere_offers_nothing_and_says_how_to_fix(self):
        self.env["FAKE_PI_NOAUTH"] = "1"
        pi = self.sysop_json("roster")["workers"]["pi"]
        self.assertEqual(pi["models"], [])
        self.assertIn("/login", pi["error"])
        self.set_tier("pi:cline/google/gemma-4-31b-it:free", "claude:haiku")
        pick = self.sysop_json("route", "small")
        self.assertEqual(pick["worker"], "claude")
        self.assertEqual(pick["skipped"][0]["reason"], "model not offered by pi")

    def test_build_and_review_commands(self):
        self.set_tier("pi:cline/google/gemma-4-31b-it:free@max")
        pick = self.sysop_json("route", "small", "--prompt-file", "/tmp/b.md")
        self.assertEqual((pick["worker"], pick["family"]), ("pi", "google"))
        self.assertIn("--model cline/google/gemma-4-31b-it:free", pick["cmd"])
        self.assertIn("--no-session -na", pick["cmd"])
        self.assertTrue(pick["cmd"].endswith("--thinking max"))
        self.assertNotIn("--tools", pick["cmd"])
        import sys
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import advice, store, workers
        review = workers.worker_cmd(store.load_settings(self.repo), "pi", "review", "kaggle/qwen3-coder-30b",
                                  "/tmp/b.md")
        self.assertIn("--tools read,grep,find,ls --no-mcp", review)

    def test_settings_cannot_redefine_pi(self):
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "sysop.json").write_text(json.dumps(
            {"workers": {"pi": {"build": "pi --yolo {prompt_file}"}}}))
        proc = self.sysop("settings", "--key", "workers")
        self.assertEqual(json.loads(proc.stdout), {})
        self.assertIn("cannot be redefined", proc.stderr)

    def test_worker_runs_with_pi_free_fallback_off(self):
        envlog = self.tmp / "pi-env"
        self.env["FAKE_PI_ENV"] = str(envlog)
        self.set_tier("pi:kaggle/qwen3-coder-30b")
        self.assertEqual(self.sysop("models", "check", "fast", timeout=60).returncode, 0)
        self.assertEqual(set(envlog.read_text().split()), {"AUTO_FALLBACK=false"})  # listing and probe

    def test_kaggle_entries_route_only_while_their_session_is_up(self):
        self.set_tier("pi:kaggle/qwen3-coder-30b", "claude:haiku")
        pick = self.sysop_json("route", "small")
        self.assertEqual(pick["worker"], "claude")
        self.assertIn("serve up", pick["skipped"][0]["reason"])
        self.serve_up("other-model")  # a different model is up: still down for this one
        self.assertEqual(self.sysop_json("route", "small")["worker"], "claude")
        self.serve_up("qwen3-coder-30b", cap_at=time.time() - 1)  # past the hard cap
        self.assertEqual(self.sysop_json("route", "small")["worker"], "claude")
        self.serve_up("qwen3-coder-30b")
        self.assertEqual(self.sysop_json("route", "small")["model"], "kaggle/qwen3-coder-30b")

    def test_provider_cooldown_covers_every_model_of_that_provider(self):
        self.serve_up("qwen3-coder-30b")
        self.set_tier("pi:cline/google/gemma-4-31b-it:free", "pi:cline/nvidia/nemotron-3-super-120b-a12b:free",
                      "pi:kaggle/qwen3-coder-30b")
        self.sysop("cooldown", "add", "pi:cline/", "--seconds", "600", "--reason", "not signed in")
        pick = self.sysop_json("route", "small")
        self.assertEqual(pick["model"], "kaggle/qwen3-coder-30b")
        self.assertEqual(len(pick["skipped"]), 2)

    def test_preflight_turns_a_missing_login_into_a_provider_cooldown(self):
        self.env["FAKE_PI_NOKEY"] = "cline"
        self.set_tier("pi:cline/google/gemma-4-31b-it:free")
        proc = self.sysop("models", "check", "fast", timeout=60)
        self.assertEqual(proc.returncode, 1)
        cool = self.sysop_json("cooldown", "list")
        self.assertIn("pi:cline/", cool)
        self.assertIn("/login cline", cool["pi:cline/"]["reason"])
        self.assertNotIn("pi:cline/google/gemma-4-31b-it:free", cool)

    def test_fallback_reads_the_log_and_cools_the_provider(self):
        self.set_tier("pi:cline/google/gemma-4-31b-it:free", "pi:cline/nvidia/nemotron-3-super-120b-a12b:free",
                      "claude:haiku")
        run = self.repo / ".sysop" / "runs" / "t1" / "1"
        run.mkdir(parents=True)
        (run / "log").write_text("\x1b[31mNo API key for provider: cline\x1b[0m\n" + "retrying...\n" * 2000)
        pick = self.sysop_json("fallback", "t1", "--from", "pi:cline/google/gemma-4-31b-it:free",
                               "--reason", "exit 1 at startup", "--size", "small")
        self.assertEqual(pick["worker"], "claude")  # the other cline model is skipped too
        self.assertIn("pi:cline/", self.sysop_json("cooldown", "list"))

    def test_free_vendors_are_their_own_families(self):
        import sys
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import advice, workers
        for model, vendor in {"cline/poolside/laguna-s-2.1:free": "poolside",
                              "cline/cohere/north-mini-code:free": "cohere",
                              "cline/liquid/lfm-2.5-2.6b:free": "liquid",
                              "cline/nvidia/nemotron-3-ultra-550b-a55b:free": "nvidia",
                              "cline/inclusionai/ling-3.0-flash-sante:free": "inclusionai",
                              # "inkling-small" holds "ling-": markers count only where a name part starts
                              "cline/thinkingmachines/inkling-small:free": "thinkingmachines",
                              "openrouter/sarvam/sarvam-105b:free": "sarvam",
                              "kilo/stepfun/step-5-preview-free": "stepfun",
                              "groq/openai/gpt-oss-120b": "openai",
                              "cline/dots-studio/dots-3-note-preview:free": "rednote"}.items():
            self.assertEqual(workers.family(model), vendor, model)

    def test_claude_on_a_login_is_plan_cost_and_on_an_api_key_paid(self):
        import os
        import sys
        from unittest import mock
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import advice, store, workers
        settings = store.load_settings(self.repo)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.assertEqual(advice.load_catalog(settings)["claude:sonnet"]["cost"], "plan")
            os.environ["ANTHROPIC_API_KEY"] = "sk-test"
            self.assertEqual(advice.load_catalog(settings)["claude:sonnet"]["cost"], "paid")
        self.assertEqual(advice.load_catalog(settings)["agy:claude-sonnet-4-6"]["cost"], "plan")

    def test_a_served_kaggle_model_is_free_and_scored_from_the_serve_list(self):
        import sys
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import advice, store, workers
        settings = store.load_settings(self.repo)
        roster = {"workers": {"pi": {"available": True, "models": ["kaggle/qwen3.6-35b-a3b"]}}}
        info = advice.load_catalog(settings, roster, {"models": {}, "latency": {}})["pi:kaggle/qwen3.6-35b-a3b"]
        self.assertEqual((info["cost"], info["skills"]["code"], info["skills"]["review"]), ("free", 4, 4))
        self.assertIn("111.1 tok/s", info["source"])

    def test_one_model_many_doors_identity_and_order(self):
        import sys
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import intel
        import advice, workers
        same = ["pi:google/gemini-3.8-flash", "agy:gemini-3.8-flash-high", "pi:openrouter/google/gemini-3.8-flash",
                "pi:cline/google/gemini-3.8-flash:free", "pi:google/gemini-3.8-flash@high"]
        self.assertEqual({intel.model_key(e) for e in same}, {"gemini-3-8-flash"})
        self.assertNotEqual(intel.model_key("pi:google/gemini-3.8-flash-lite"), "gemini-3-8-flash")
        self.assertNotEqual(intel.model_key("pi:google/gemini-3.1-pro-preview"), intel.model_key("agy:gemini-3.1-pro-high"))
        catalog = {"pi:google/gemini-3.8-flash": {"cost": "free"}, "pi:cline/google/gemini-3.8-flash:free": {"cost": "free"},
                   "agy:gemini-3.8-flash-high": {"cost": "plan"}, "pi:openrouter/google/gemini-3.8-flash": {"cost": "paid"}}
        self.assertEqual(sorted(catalog, key=lambda e: advice.door_rank(e, catalog)),
                         ["pi:google/gemini-3.8-flash",            # free, Google's own API
                          "pi:cline/google/gemini-3.8-flash:free",  # free, a resold pool
                          "agy:gemini-3.8-flash-high",             # the operator's plan
                          "pi:openrouter/google/gemini-3.8-flash"])  # per token

    def test_a_rate_limited_door_falls_back_to_the_same_model_first(self):
        self.env["FAKE_PI_EXTRA"] = ("google      gemini-3.8-flash    1M  65K  yes  yes\n"
                                     "openrouter  google/gemini-3.8-flash  1M  65K  yes  yes")
        self.sysop("roster", "--refresh")
        self.set_tier("agy:gemini-3.8-flash-high@high", "claude:haiku")
        self.sysop("cooldown", "add", "agy", "--seconds", "600", "--reason", "429 individual quota")
        pick = self.sysop_json("route", "small")
        self.assertEqual((pick["entry"], pick["door_for"]), ("pi:google/gemini-3.8-flash@high",
                                                             "agy:gemini-3.8-flash-high@high"))
        self.assertIn("cooling down", pick["skipped"][0]["reason"])
        self.sysop("cooldown", "add", "pi:google/", "--seconds", "600", "--reason", "429 per-minute")
        self.assertEqual(self.sysop_json("route", "small")["entry"], "pi:openrouter/google/gemini-3.8-flash@high")
        self.sysop("config", "set", "doors", "false")
        self.assertEqual(self.sysop_json("route", "small")["entry"], "claude:haiku")

    def test_stated_reset_sets_the_cooldown_length(self):
        import sys
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import advice, workers
        self.assertEqual(workers.stated_reset("RESOURCE_EXHAUSTED. Resets in 150h56m49s."), 543409)
        self.assertEqual(workers.stated_reset("429; retry after 3600s"), 3600)
        self.assertIsNone(workers.stated_reset("503 no capacity"))
        self.assertEqual(workers.stated_reset("quota; resets in 1.5h"), 5400)
        self.assertEqual(workers.stated_reset("retry after 99999999"), 7 * 86400)
        free_pro = ("Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, model: gemini-3.1-pro"
                    "\nPlease retry in 16h43m36s. \"retryDelay\": \"60216s\"")
        self.assertEqual(workers.stated_reset(free_pro), 7 * 86400)  # a zero quota never resets: park it
        self.assertEqual(workers.stated_reset("limit: 10, retryDelay: 30s"), 30)
        run = self.repo / ".sysop" / "runs" / "t2" / "1"
        run.mkdir(parents=True)
        (run / "log").write_text("Error: quota reached. Resets in 2h0m0s.\n")
        self.set_tier("agy:gemini-3.8-flash-high", "claude:haiku")
        self.sysop_json("fallback", "t2", "--from", "agy:gemini-3.8-flash-high", "--reason", "quota",
                        "--size", "small")
        until = self.sysop_json("cooldown", "list")["agy:gemini-3.8-flash-high"]["until"]
        import time
        self.assertAlmostEqual(until - time.time(), 7200, delta=60)


    def test_launch_builds_the_worker_command_itself(self):
        self.serve_up("qwen3-coder-30b")
        self.set_tier("pi:kaggle/qwen3-coder-30b@high", "claude:haiku@low")
        brief = self.repo / "brief.md"
        brief.write_text("do it")
        pick = self.sysop_json("route", "small")
        self.assertEqual(pick["entry"], "pi:kaggle/qwen3-coder-30b")  # effort dropped: no reasoning
        rec = self.sysop_json("launch", "t1", str(self.repo), "--worker", "claude:haiku@low",
                              "--prompt-file", "brief.md")
        run = self.repo / ".sysop" / "runs" / "t1" / "1"
        cmd = (run / "cmd").read_text()
        self.assertTrue(cmd.startswith("claude -p"))
        self.assertIn("--effort low", cmd)
        self.assertIn(str(brief), cmd)
        self.assertEqual((run / "worker").read_text().strip(), "claude:haiku")  # the ledger's form
        self.assertEqual(rec["attempt"], 1)
        bad = self.sysop("launch", "t2", str(self.repo), "echo hi", "--worker", "claude:haiku",
                         "--prompt-file", "brief.md")
        self.assertEqual(bad.returncode, 1)  # one source of truth for the command
        self.assertEqual(self.sysop("launch", "t3", str(self.repo), "--worker", "nope:x",
                                    "--prompt-file", "brief.md").returncode, 1)

    def test_kilo_is_a_door_to_the_same_model_ahead_of_pi_free(self):
        import sys
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import advice, intel
        twins = ["pi:openrouter/nvidia/nemotron-3-super-120b-a12b:free", "pi:kilo/nvidia/nemotron-3-super-120b-a12b:free",
                 "pi:cline/nvidia/nemotron-3-super-120b-a12b:free"]
        self.assertEqual(len({intel.model_key(e) for e in twins}), 1)
        # unknown to the catalog, a `:free` id still ranks free; a legitimate gateway before pi-free's pools
        self.assertEqual(sorted(twins[::-1], key=lambda e: advice.door_rank(e, {}))[-1], twins[2])
        self.assertEqual(advice.door_rank("pi:kilo/kilo-auto/free", {})[0], 0)
        # the e2e case: a listed Kilo model the catalog does not know is inferred free, so it beats Cline's pool
        import store
        settings = store.load_settings(self.repo)
        roster = {"workers": {"pi": {"available": True, "models": [m[3:] for m in twins]}}}
        catalog = advice.load_catalog(settings, roster, {"models": {}, "latency": {}})
        self.assertEqual(catalog[twins[1]]["cost"], "free")
        self.assertEqual(advice.doors(twins[0], roster, {}, catalog, {"tiers": {"fast": twins}})[0], twins[1])

    def test_a_refused_model_or_key_rests_with_its_fix(self):
        self.env["FAKE_PI_EXTRA"] = ("groq  llama-3.3-70b-versatile  131K  32K  no  no\n"
                                     "groq  openai/gpt-oss-120b  131K  65K  yes  no")
        self.sysop("roster", "--refresh")
        self.sysop("config", "set", "doors", "false")  # this test is about the classes, not the doors
        self.set_tier("pi:groq/llama-3.3-70b-versatile", "pi:groq/openai/gpt-oss-120b", "claude:haiku")
        run = self.repo / ".sysop" / "runs" / "t3" / "1"
        run.mkdir(parents=True)
        (run / "log").write_text("404 The model `llama-3.3-70b-versatile` does not exist or you do not have access to it.\n")
        pick = self.sysop_json("fallback", "t3", "--from", "pi:groq/llama-3.3-70b-versatile", "--reason", "exit 1",
                               "--size", "small")
        self.assertEqual(pick["entry"], "pi:groq/openai/gpt-oss-120b")  # the provider still serves its other models
        cool = self.sysop_json("cooldown", "list")["pi:groq/llama-3.3-70b-versatile"]
        self.assertIn("not enabled for your account", cool["reason"])
        self.assertAlmostEqual(cool["until"] - time.time(), 86400, delta=60)
        (run / "log").write_text("Error: 401 Unauthorized: Invalid API Key\n")
        pick = self.sysop_json("fallback", "t3", "--from", "pi:groq/openai/gpt-oss-120b", "--reason", "exit 1",
                               "--size", "small")
        self.assertEqual(pick["entry"], "claude:haiku")
        self.assertIn("fix the key in pi", self.sysop_json("cooldown", "list")["pi:groq/"]["reason"])

    def test_a_test_about_http_401_is_not_a_refused_key(self):
        import sys
        sys.path.insert(0, str(__import__("helpers").ROOT / "scripts"))
        import workers
        self.assertIsNone(workers.KEY_REFUSED_RE.search("FAILED test_auth.py::test_status - assert 403 == 401"))

    def test_a_whole_worker_fallback_from_pi_rests_only_that_provider(self):
        self.env["FAKE_PI_EXTRA"] = "kilo  nvidia/nemotron-3.5-lightning:free  128K  8K  yes  no"
        self.sysop("roster", "--refresh")
        self.set_tier("pi:cline/nvidia/nemotron-3-super-120b-a12b:free", "pi:kilo/nvidia/nemotron-3.5-lightning:free")
        pick = self.sysop_json("fallback", "t9", "--from", "pi:cline/nvidia/nemotron-3-super-120b-a12b:free",
                               "--reason", "429 free-models-per-day", "--size", "small", "--whole-worker")
        self.assertEqual(pick["entry"], "pi:kilo/nvidia/nemotron-3.5-lightning:free")
        cool = self.sysop_json("cooldown", "list")
        self.assertIn("pi:cline/", cool)
        self.assertNotIn("pi", cool)

