"""Tests for src.ict.evaluate: the pre-registered pass bar (§10) and the verdict."""
import unittest

from src.ict.evaluate import passes, render, verdict


def stats(n=250, mean=0.15, se=0.04, nas=0.2, spx=0.1):
    return {"n": n, "mean_r": mean, "se": se,
            "by_instrument": {"NAS100_USD": {"mean_r": nas}, "SPX500_USD": {"mean_r": spx}}}


class PassesTest(unittest.TestCase):
    def test_passes_all(self):
        self.assertEqual(passes(stats(), 0.05, 1), (True, []))

    def test_fails_on_too_few_trades(self):
        ok, failed = passes(stats(n=199), 0.05, 1)
        self.assertFalse(ok)
        self.assertEqual(failed, ["n >= 200"])

    def test_fails_on_small_mean(self):
        ok, failed = passes(stats(mean=0.09, se=0.01), 0.05, 1)
        self.assertFalse(ok)
        self.assertEqual(failed, ["mean R >= 0.1"])

    def test_rescue_needs_two_se(self):
        s = stats(mean=0.12, se=0.07)
        self.assertEqual(passes(s, 0.05, 1), (True, []))
        ok, failed = passes(s, 0.05, 2)
        self.assertFalse(ok)
        self.assertEqual(failed, ["mean R - 2 SE > 0"])

    def test_fails_on_one_instrument_negative(self):
        ok, failed = passes(stats(spx=-0.01), 0.05, 1)
        self.assertFalse(ok)
        self.assertEqual(failed, ["mean R > 0 on each instrument"])

    def test_fails_when_control_not_beaten(self):
        ok, failed = passes(stats(mean=0.15), 0.15, 1)   # equal is not "beats"
        self.assertFalse(ok)
        self.assertEqual(failed, ["mean R > control 95th percentile"])


class VerdictTest(unittest.TestCase):
    def test_verdict(self):
        self.assertEqual(verdict(True, None), "primary to hold-out")
        self.assertEqual(verdict(False, True), "rescue to hold-out")
        self.assertEqual(verdict(False, False), "fail")
        self.assertEqual(verdict(False, None), "fail")


if __name__ == "__main__":
    unittest.main()


class RenderTest(unittest.TestCase):
    def variant(self, mean, se, p95, skips):
        s = stats(mean=mean, se=se)
        return {"stats": s, "se_multiple": 1, "control_p95": p95, "control_mean": -0.3,
                "control_dropped": 0, "max_drawdown_pct": 5.0, "passed": False, "failed": ["x"],
                "skips": skips}

    def test_text_derived_from_data(self):
        grp = {"2020": {"n": 5, "mean_r": 0.1, "se": 0.1}}
        v = self.variant(0.15, 0.04, 0.5, {"NAS100_USD": {"missing_m5": 3}, "SPX500_USD": {"missing_m5": 7}})
        v["stats"].update(by_side=grp, by_year=grp, stop_first=0, rearmed=0)
        v["stats"]["by_instrument"] = {k: {"n": 1, "mean_r": g["mean_r"], "se": 0.1}
                                       for k, g in v["stats"]["by_instrument"].items()}
        sens = {k: {"n": 1, "mean_r": 0.0, "se": 0.1} for k in ("wick_5min", "close_1min", "close_2min")}
        out = {"verdict": "FAIL", "primary": v, "rescue": None, "sensitivity": sens,
               "truncation_checks": {"A_close": True, "B_close": False}}
        text = render(out)
        self.assertIn("Truncation check FAILED for: B_close", text)
        self.assertIn("NAS100_USD 3, SPX500_USD 7", text)
        self.assertIn("not met: mean R vs control 95th percentile", text)
        self.assertNotIn("206", text)
