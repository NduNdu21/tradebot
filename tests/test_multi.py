"""One test per strategy 4 rule (docs/strategy-multi-asset-trend.md)."""
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from src.multi import run as R
from src.multi import universe as U


def rates_const(**vals):
    idx = pd.DatetimeIndex(["2000-01-01"])
    return {c: pd.Series([vals.get(c, 0.0)], index=idx) for c in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")}


DAYS = pd.DatetimeIndex(["2020-01-06"])


class TestUniverse(unittest.TestCase):
    def test_four_classes_and_no_energy_or_farm(self):
        self.assertEqual(set(U.CLASSES), {"equity", "bond", "metal", "fx"})
        for excluded in ("WTICO_USD", "BCO_USD", "NATGAS_USD", "CORN_USD", "WHEAT_USD", "SOYBN_USD", "SUGAR_USD",
                         "XCU_USD"):
            self.assertNotIn(excluded, U.INSTRUMENTS)
        self.assertEqual(len(U.INSTRUMENTS), 24)

    def test_class_budget_split_equally(self):
        self.assertAlmostEqual(R.unit_risk_gbp("SPX500_USD"), 25 / 6)
        self.assertAlmostEqual(R.unit_risk_gbp("USB10Y_USD"), 25 / 4)
        self.assertAlmostEqual(R.unit_risk_gbp("XAU_USD"), 12.5)
        self.assertAlmostEqual(R.unit_risk_gbp("EUR_USD"), 25 / 12)


class TestFinancing(unittest.TestCase):
    def test_index_long_pays_cash_plus_markup_and_earns_dividend(self):
        long, short = R.financing_rates("SPX500_USD", DAYS, rates_const(USD=0.04), {})
        self.assertAlmostEqual(long[0], 0.02 - 0.04 - 0.025)
        self.assertAlmostEqual(short[0], 0.04 - 0.02 - 0.025)

    def test_jp225_usd_is_funded_in_usd(self):
        long, _ = R.financing_rates("JP225_USD", DAYS, rates_const(USD=0.05, JPY=0.0), {})
        self.assertAlmostEqual(long[0], 0.018 - 0.05 - 0.025)

    def test_dax_has_no_dividend_and_dividends_can_be_switched_off(self):
        long, _ = R.financing_rates("DE30_EUR", DAYS, rates_const(EUR=0.01), {})
        self.assertAlmostEqual(long[0], -0.01 - 0.025)
        long, _ = R.financing_rates("UK100_GBP", DAYS, rates_const(GBP=0.01), {}, dividends=False)
        self.assertAlmostEqual(long[0], -0.01 - 0.025)

    def test_bond_long_earns_yield_minus_cash_or_markup_only(self):
        y = {"USB10Y_USD": pd.Series([0.045], index=pd.DatetimeIndex(["2019-01-01"]))}
        long, short = R.financing_rates("USB10Y_USD", DAYS, rates_const(USD=0.02), y)
        self.assertAlmostEqual(long[0], 0.025 - 0.025)
        self.assertAlmostEqual(short[0], -0.025 - 0.025)
        long, _ = R.financing_rates("USB10Y_USD", DAYS, rates_const(USD=0.02), y, bond_carry=False)
        self.assertAlmostEqual(long[0], -0.025)

    def test_metal_has_zero_yield(self):
        long, short = R.financing_rates("XAU_USD", DAYS, rates_const(USD=0.03), {})
        self.assertAlmostEqual(long[0], -0.03 - 0.011)
        self.assertAlmostEqual(short[0], 0.03 - 0.011)

    def test_fx_markup_one_percent(self):
        long, _ = R.financing_rates("AUD_USD", DAYS, rates_const(AUD=0.05, USD=0.02), {})
        self.assertAlmostEqual(long[0], 0.03 - 0.01)

    def test_bond_yield_used_from_month_after(self):
        with tempfile.TemporaryDirectory() as d:
            for code in U.BOND_YIELD.values():
                pd.DataFrame({"date": ["2020-01-01"], "v": [3.0]}).to_csv(os.path.join(d, f"{code}.csv"), index=False)
            y = R.load_yields(d)["USB10Y_USD"]
            self.assertEqual(y.index[0], pd.Timestamp("2020-02-01"))
            self.assertAlmostEqual(y.iloc[0], 0.03)


class TestSizing(unittest.TestCase):
    def test_quote_conversion(self):
        idx = pd.DatetimeIndex(["2020-01-06"])
        bars = {"GBP_USD": pd.DataFrame({"c": [1.25]}, index=idx), "EUR_GBP": pd.DataFrame({"c": [0.85]}, index=idx)}
        self.assertAlmostEqual(R.quote_to_gbp("SPX500_USD", idx, bars).iloc[0], 0.8)
        self.assertAlmostEqual(R.quote_to_gbp("DE30_EUR", idx, bars).iloc[0], 0.85)
        self.assertAlmostEqual(R.quote_to_gbp("UK100_GBP", idx, bars).iloc[0], 1.0)

    def test_target_rounds_to_oanda_precision(self):
        b = pd.DataFrame({"trend": [1 / 3, 1.0, np.nan], "unit": [0.14, 0.14, 0.14]})
        self.assertEqual(list(R.target(b, "UK100_GBP")), [0.0, 0.1, 0.0])
        self.assertEqual(list(R.target(b, "SPX500_USD")), [0.05, 0.14, 0.0])
        self.assertAlmostEqual(R.target(b, "UK100_GBP", rounded=False).iloc[0], 0.14 / 3)

    def test_slippage_is_half_median_dev_spread(self):
        idx = pd.DatetimeIndex(["2010-01-04", "2010-01-05", "2010-01-06", "2025-01-06"])
        bars = pd.DataFrame({"exec_bid": [1.0, 1.0, 1.0, 1.0], "exec_ask": [1.2, 1.4, 1.6, 9.0]}, index=idx)
        self.assertAlmostEqual(R.slippage_from_spreads(bars), 0.2)


if __name__ == "__main__":
    unittest.main()
