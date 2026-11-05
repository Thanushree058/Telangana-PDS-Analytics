"""
Step 2 - Data integration
=========================
Combines the monthly CSVs into master DataFrames and joins the three sources.

Concepts
--------
* Stacking (pd.concat): every monthly file has the same columns, so we read
  them in a loop and stack them vertically into one "long" table.
* Keys: a shop is identified by (distCode, shopNo). A shop-month row is
  identified by (distCode, shopNo, year, month). Joins must use the full key,
  otherwise rows get duplicated.
* Left join: we keep every transaction row and attach card counts and
  location where they exist, so no transactions are silently dropped.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from config import PROCESSED_DIR, RAW_DIR

SHOP_KEY = ["distCode", "shopNo"]
MONTH_KEY = SHOP_KEY + ["year", "month"]

# Districts that sit inside the Hyderabad metropolitan area. Used as the
# "urban" label for policy analysis and for cluster-purity evaluation.
URBAN_DISTRICTS = {"Hyderabad", "Medchal", "Ranga Reddy"}


def _read_folder(folder: Path, usecols=None) -> pd.DataFrame:
    frames = []
    for f in sorted(folder.glob("*.csv")):
        df = pd.read_csv(f, usecols=usecols, low_memory=False)
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No CSVs in {folder}. Run src/download_data.py first.")
    return pd.concat(frames, ignore_index=True)


def load_transactions() -> pd.DataFrame:
    df = _read_folder(RAW_DIR / "transactions")
    num_cols = df.columns.drop(["distName", "officeName"])
    df[num_cols] = df[num_cols].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=MONTH_KEY)
    df[MONTH_KEY] = df[MONTH_KEY].astype("int64")
    df = df.fillna(0)
    # Total rice across the three schemes (Antyodaya / Food Security / Annapurna)
    df["rice"] = df["riceAfsc"] + df["riceFsc"] + df["riceAap"]
    return df.drop_duplicates(MONTH_KEY)


def load_card_status() -> pd.DataFrame:
    cols = MONTH_KEY + ["rcNfsaAay", "totalRcNfsa", "totalRcState", "totalRcs", "totalUnits"]
    df = _read_folder(RAW_DIR / "card_status", usecols=cols)
    df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=MONTH_KEY)
    df[MONTH_KEY] = df[MONTH_KEY].astype("int64")
    return df.fillna(0).drop_duplicates(MONTH_KEY)


def load_locations() -> pd.DataFrame:
    df = pd.read_csv(RAW_DIR / "locations" / "fps_locations.csv")
    df = df[SHOP_KEY + ["address", "latitude", "longitude", "fpsStatus", "fpsType"]].copy()
    df[["latitude", "longitude"]] = df[["latitude", "longitude"]].apply(pd.to_numeric, errors="coerce")
    # Telangana lies roughly within lat 15.8-19.95, lon 77.2-81.4. Anything
    # outside that box is a data-entry error, so blank it rather than plot it.
    bad = ~df["latitude"].between(15.5, 20.5) | ~df["longitude"].between(77.0, 81.8)
    df.loc[bad, ["latitude", "longitude"]] = np.nan
    return df.drop_duplicates(SHOP_KEY)


def build_master() -> pd.DataFrame:
    """Triple join: transactions <- card status (shop-month) <- locations (shop)."""
    trans = load_transactions()
    cards = load_card_status()
    locs = load_locations()

    master = trans.merge(cards, on=MONTH_KEY, how="left", validate="one_to_one")
    master = master.merge(locs, on=SHOP_KEY, how="left", validate="many_to_one")

    master["date"] = pd.to_datetime(dict(year=master["year"], month=master["month"], day=1))
    master["area_type"] = np.where(master["distName"].isin(URBAN_DISTRICTS), "Urban", "Rural")
    return master


def main() -> None:
    master = build_master()
    out = PROCESSED_DIR / "master_monthly.parquet"
    master.to_parquet(out, index=False)
    print(f"Saved {out.name}: {len(master):,} shop-month rows, "
          f"{master[SHOP_KEY].drop_duplicates().shape[0]:,} shops, "
          f"{master['date'].min():%b %Y} to {master['date'].max():%b %Y}")


if __name__ == "__main__":
    main()
