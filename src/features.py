"""
Step 3 - Feature engineering
============================
Turns raw monthly counts into *behaviour* signals, one row per shop per year.

Why ratios instead of raw totals? A shop with 2,000 cards will always sell
more rice than a shop with 200. If we cluster on raw totals, we just rediscover
"big shops vs small shops". Ratios such as transactions-per-card describe HOW a
shop behaves regardless of its size, which is what reveals anomalies.

Features
--------
avg_cards           mean ration cards attached to the shop (size, log-scaled for modelling)
utilization_ratio   transactions / cards. ~1 means every card collected once a month.
                    Way above 1 can mean duplicate/ghost draws; far below 1 means low uptake.
portability_share   share of transactions by cards from OTHER shops (One Nation One Ration Card).
rice_per_trans      kg of rice per transaction. Unusually high values hint at diversion.
wheat_share         wheat / (rice + wheat). Captures commodity mix (rice-to-wheat intensity).
aay_share           share of Antyodaya (poorest) cards. A proxy for the shop's catchment poverty.
trans_cv            coefficient of variation (std / mean) of monthly transactions = volatility.
active_month_share  share of months in which the shop recorded any transaction.
"""
import numpy as np
import pandas as pd

from data_prep import SHOP_KEY

MODEL_FEATURES = [
    "log_avg_cards",
    "utilization_ratio",
    "portability_share",
    "rice_per_trans",
    "wheat_share",
    "aay_share",
    "trans_cv",
]
# active_month_share is computed for reporting but left out of the model: in
# 2023-2025 almost every shop transacted every month, so it has ~zero variance
# and would add nothing but noise to the distances.


def _safe_div(a, b):
    return np.where(b > 0, a / np.where(b > 0, b, 1), np.nan)


def shop_year_features(master: pd.DataFrame) -> pd.DataFrame:
    g = master.groupby(SHOP_KEY + ["year"])
    feats = g.agg(
        distName=("distName", "first"),
        officeName=("officeName", "first"),
        area_type=("area_type", "first"),
        latitude=("latitude", "first"),
        longitude=("longitude", "first"),
        months=("month", "nunique"),
        total_trans=("noOfTrans", "sum"),
        total_cards=("totalRcs", "sum"),
        avg_cards=("totalRcs", "mean"),
        avg_monthly_trans=("noOfTrans", "mean"),
        std_monthly_trans=("noOfTrans", "std"),
        active_months=("noOfTrans", lambda s: (s > 0).sum()),
        portability_trans=("otherShopTransCnt", "sum"),
        rice_kg=("rice", "sum"),
        wheat_kg=("wheat", "sum"),
        aay_cards=("rcNfsaAay", "sum"),
        amount=("totalAmount", "sum"),
    ).reset_index()

    feats["utilization_ratio"] = _safe_div(feats["total_trans"], feats["total_cards"])
    feats["portability_share"] = _safe_div(feats["portability_trans"], feats["total_trans"])
    feats["rice_per_trans"] = _safe_div(feats["rice_kg"], feats["total_trans"])
    feats["wheat_share"] = _safe_div(feats["wheat_kg"], feats["rice_kg"] + feats["wheat_kg"])
    feats["rice_to_wheat"] = _safe_div(feats["rice_kg"], feats["wheat_kg"])
    feats["aay_share"] = _safe_div(feats["aay_cards"], feats["total_cards"])
    feats["trans_cv"] = _safe_div(feats["std_monthly_trans"], feats["avg_monthly_trans"])
    feats["active_month_share"] = feats["active_months"] / feats["months"]
    feats["log_avg_cards"] = np.log1p(feats["avg_cards"])
    return feats


def model_ready(feats: pd.DataFrame, min_cards: int = 20) -> pd.DataFrame:
    """Keep shop-years that can be modelled: has cards, has some activity, no missing features.

    Shops with almost no cards (closed or newly opened) would dominate the
    ratios with noise, so they are left out of clustering and reported separately.
    """
    ok = (feats["avg_cards"] >= min_cards) & (feats["total_trans"] > 0)
    out = feats[ok].dropna(subset=MODEL_FEATURES).copy()
    # Clip only the most extreme 0.1% so a handful of typos can't stretch the
    # scale. The original values stay available for anomaly reporting.
    for c in MODEL_FEATURES:
        lo, hi = out[c].quantile([0.001, 0.999])
        out[c + "_clip"] = out[c].clip(lo, hi)
    return out
