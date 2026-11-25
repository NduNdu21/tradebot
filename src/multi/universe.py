"""Instruments, classes and cost/financing constants (strategy §1, §3, §5, §6)."""
from src.trend.run import PAIRS as FX_PAIRS

CLASSES = {
    "equity": ["SPX500_USD", "DE30_EUR", "UK100_GBP", "JP225_USD", "AU200_AUD", "EU50_EUR"],
    "bond": ["USB10Y_USD", "USB30Y_USD", "DE10YB_EUR", "UK10YB_GBP"],
    "metal": ["XAU_USD", "XAG_USD"],
    "fx": list(FX_PAIRS),
}
CLASS_OF = {i: c for c, members in CLASSES.items() for i in members}
INSTRUMENTS = [i for members in CLASSES.values() for i in members]
CLASS_RISK_GBP = 25.0   # per ATR per class, split equally across members

# Measured from OANDA's 2026-11-25 rates as -(long + short) / 2.
MARKUP = {**{i: 0.025 for i in CLASSES["equity"] + CLASSES["bond"]},
          "XAU_USD": 0.011, "XAG_USD": 0.014, **{p: 0.01 for p in FX_PAIRS}}
# Fixed long-run dividend yields; DE30 is a total-return index.
DIVIDEND = {"SPX500_USD": 0.020, "DE30_EUR": 0.0, "UK100_GBP": 0.037, "JP225_USD": 0.018,
            "AU200_AUD": 0.043, "EU50_EUR": 0.033}
# Bond yield series files in data/rates/ (FRED), used from the month after.
BOND_YIELD = {"USB10Y_USD": "IRLTLT01USM156N", "USB30Y_USD": "GS30",
              "DE10YB_EUR": "IRLTLT01DEM156N", "UK10YB_GBP": "IRLTLT01GBM156N"}
# OANDA trade-unit precision (decimal places), fetched 2026-11-25.
PRECISION = {"SPX500_USD": 2, "DE30_EUR": 2, "JP225_USD": 2, "UK100_GBP": 1, "AU200_AUD": 1, "EU50_EUR": 1,
             "XAU_USD": 1, **{i: 0 for i in CLASSES["bond"] + ["XAG_USD"] + list(FX_PAIRS)}}
# GBP per one unit of the quote currency = converter close, or its inverse.
CONVERTERS = {"USD": ("GBP_USD", True), "EUR": ("EUR_GBP", False), "AUD": ("GBP_AUD", True),
              "JPY": ("GBP_JPY", True), "CHF": ("GBP_CHF", True), "CAD": ("GBP_CAD", True)}


def quote_currency(instrument):
    return instrument.split("_")[1]
