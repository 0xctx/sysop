"""The plan reserve: near a Claude plan limit, delegated work stops routing to claude: (the mod records usage)."""
import json
import time

from helpers import TempRepoCase


class PlanReserveTest(TempRepoCase):
    def setUp(self):
        super().setUp()
        self.use_fakebin()
        self.pin_tiers()

    def usage(self, percent, kind="five_hour", age=0):
        path = self.home / ".cache" / "sysop" / "claude-usage.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"rateLimits": [{"kind": kind, "percentUsed": percent},
                                                   {"kind": "seven_day", "percentUsed": 6}],
                                    "at": time.time() - age}))

    def route(self, name="large"):  # the standard tier: claude:sonnet first
        return json.loads(self.sysop("route", name).stdout)

    def test_a_fresh_reading_past_the_reserve_rests_claude_with_the_reason(self):
        self.usage(85)
        pick = self.route()
        self.assertNotEqual(pick.get("worker"), "claude")
        reasons = [s["reason"] for s in pick["skipped"] if s["entry"].startswith("claude:")]
        self.assertTrue(reasons)
        self.assertEqual(reasons[0], "your Claude plan is at 85% (5-hour); kept for you")
        status = self.sysop("status").stdout
        self.assertIn("Claude plan: 5-hour 85%, weekly 6%; delegated work goes elsewhere", status)

    def test_below_the_reserve_stale_or_absent_nothing_is_held(self):
        for setup in (lambda: self.usage(70), lambda: self.usage(95, age=16 * 60), lambda: None):
            (self.home / ".cache" / "sysop" / "claude-usage.json").unlink(missing_ok=True)
            setup()
            self.assertFalse(any("kept for you" in s["reason"] for s in self.route()["skipped"]))

    def test_the_weekly_limit_counts_and_a_zero_reserve_spends_it_all(self):
        self.usage(81, kind="seven_day")
        self.assertIn("(weekly)", json.dumps(self.route()))
        self.sysop("config", "set", "claude_reserve", "0")
        self.assertNotIn("kept for you", json.dumps(self.route()))
