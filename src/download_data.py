"""
Step 1 - Data acquisition
=========================
Downloads the monthly Fair Price Shop (FPS) files for 2023-2025 from the
Telangana Open Data Portal (https://data.telangana.gov.in).

Concept: instead of hard-coding 60 URLs, we ask the portal's catalogue API
(DKAN metastore) for each dataset's list of files and keep only the years we
need. That makes the download reproducible: anyone can re-run this script.

Run from the project root:
    python src/download_data.py
"""
import re
import time
from pathlib import Path

import requests

from config import RAW_DIR, YEARS

API = "https://data.telangana.gov.in/api/1/metastore/schemas/dataset/items/{}?show-reference-ids=false"

DATASETS = {
    # folder name     : portal dataset id
    "transactions": "4a7337ce-08bd-4f74-8e00-bd6eb83eb4ff",  # FP Shop Wise Transactions
    "card_status": "84e47ac8-2c24-43a4-a045-2a7bec7212e5",   # FP Shop Card Status
    "locations": "6b5a1abe-6bf8-4c28-a400-2e42640de641",     # FP Shop Lat/Long
}


def list_files(dataset_id: str) -> list[dict]:
    """Return [{'title':..., 'url':...}] for every file in a portal dataset."""
    meta = requests.get(API.format(dataset_id), timeout=120).json()
    return [
        {"title": d["data"]["title"], "url": d["data"]["downloadURL"]}
        for d in meta["distribution"]
    ]


def month_year_from_title(title: str) -> tuple[int, int] | None:
    """Titles look like '... 01-03-2024 to 31-03-2024'. Pull out (month, year)."""
    m = re.search(r"\d{1,2}-(\d{2})-(\d{4}) to", title)
    return (int(m.group(1)), int(m.group(2))) if m else None


def download(url: str, dest: Path, retries: int = 4) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  skip (exists) {dest.name}")
        return
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            dest.write_bytes(r.content)
            print(f"  saved {dest.name} ({len(r.content)/1e6:.1f} MB)")
            return
        except requests.RequestException as e:
            wait = 2 ** (attempt + 1)
            print(f"  retry in {wait}s: {e}")
            time.sleep(wait)
    raise RuntimeError(f"Failed to download {url}")


def main() -> None:
    for folder, ds_id in DATASETS.items():
        out = RAW_DIR / folder
        out.mkdir(parents=True, exist_ok=True)
        print(f"\n== {folder}")
        for f in list_files(ds_id):
            if folder == "locations":
                download(f["url"], out / "fps_locations.csv")
                continue
            my = month_year_from_title(f["title"])
            if my and my[1] in YEARS:
                month, year = my
                download(f["url"], out / f"{folder}_{year}_{month:02d}.csv")


if __name__ == "__main__":
    main()
