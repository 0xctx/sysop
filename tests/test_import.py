"""`ticket import`: slices from the idea path become fleet tickets, idempotently, skeleton first."""
import json

from helpers import TempRepoCase


def slice_(sid, depends=(), size="small", **extra):
    return {"id": sid, "title": f"slice {sid}", "size": size, "kind": "feature", "depends": list(depends),
            "request": f"the PRD's words for {sid}", "contract": f"contract {sid}",
            "acceptance": [f"{sid} works"], "check": f"a visitor sees {sid}", **extra}


class ImportTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.sysop("store")
        self.store = self.repo / ".sysop"

    def write(self, slices, name="slices.json"):
        path = self.tmp / name
        path.write_text(json.dumps({"slices": slices}))
        return path

    def imp(self, slices, *flags, code=0):
        proc = self.sysop("ticket", "import", str(self.write(slices)), *flags)
        self.assertEqual(proc.returncode, code, proc.stderr)
        return proc

    def tickets(self):
        return json.loads((self.store / "tickets.json").read_text())["tickets"]

    def test_reimport_updates_and_never_duplicates(self):
        self.sysop("config", "set", "check", "exit 0")
        self.imp([slice_("s1"), slice_("s2", ["s1"])])
        self.sysop("ticket", "set", "s1", "--status", "running")
        changed = slice_("s1", size="large")
        changed["title"] = "slice s1, sharper"
        self.imp([changed, slice_("s2", ["s1"]), slice_("s3")])
        t = self.tickets()
        self.assertEqual(sorted(t), ["s1", "s2", "s3"])
        self.assertEqual((t["s1"]["title"], t["s1"]["size"], t["s1"]["status"]), ("slice s1, sharper", "large", "running"))
        self.assertTrue(any("updated from slices" in line for line in t["s1"]["log"]))
        self.assertEqual(t["s2"]["check"], "a visitor sees s2")
        self.assertEqual(t["s3"]["acceptance"], ["s3 works"])

    def test_bad_graphs_and_records_change_nothing(self):
        self.sysop("config", "set", "check", "exit 0")
        self.imp([slice_("s1")])
        before = (self.store / "tickets.json").read_text()
        for slices, msg in (([slice_("a", ["b"]), slice_("b", ["a"])], "cycle"),
                            ([slice_("a", ["nope"])], "unknown dependency"),
                            ([slice_("a"), slice_("a")], "duplicate"),
                            ([{"id": "a", "title": "x"}], "missing"),
                            ([slice_("a", size="huge")], "size")):
            with self.subTest(msg=msg):
                proc = self.imp(slices, code=1)
                self.assertIn(msg, proc.stderr)
                self.assertEqual((self.store / "tickets.json").read_text(), before)

    def test_a_repo_without_a_check_gets_a_walking_skeleton_first(self):
        proc = self.imp([slice_("s1"), slice_("s2", ["s1"])])
        t = self.tickets()
        self.assertEqual(list(t)[0], "t0-skeleton")
        self.assertEqual(t["t0-skeleton"]["kind"], "skeleton")
        self.assertIn("health", t["t0-skeleton"]["contract"])
        self.assertEqual(t["s1"]["depends"], ["t0-skeleton"])  # roots hang off the skeleton
        self.assertEqual(t["s2"]["depends"], ["s1"])
        self.assertIn("t0-skeleton", proc.stdout.splitlines()[0])  # the tree starts at the skeleton
        self.assertRegex(proc.stdout, r"\n\s+s1")
        self.assertRegex(proc.stdout, r"\n\s{4,}s2")
        self.imp([slice_("s1"), slice_("s2", ["s1"])])  # the same file again: roots stay on the skeleton
        self.assertEqual(self.tickets()["s1"]["depends"], ["t0-skeleton"])
        self.assertEqual(len(self.tickets()), 3)

    def test_no_skeleton_when_checked_declined_or_present(self):
        self.imp([slice_("s1")], "--skeleton", "never")
        self.assertNotIn("t0-skeleton", self.tickets())
        self.imp([slice_("boot", kind="skeleton"), slice_("s2", ["boot"])])
        self.assertNotIn("t0-skeleton", self.tickets())
        self.sysop("config", "set", "check", "exit 0")
        self.imp([slice_("s3")])
        self.assertNotIn("t0-skeleton", self.tickets())

    def test_base_and_authority_flags(self):
        self.sysop("config", "set", "check", "exit 0")
        self.imp([slice_("s1")], "--base", "main", "--authority", "--land given")
        data = json.loads((self.store / "tickets.json").read_text())
        self.assertEqual((data["base"], data["authority"]), ("main", "--land given"))
        self.assertEqual(self.sysop_json("next")["actions"][0]["do"], "launch")  # straight into the engine
