"""Central paths and settings, so every script and notebook agrees on them."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
FIGURES_DIR = ROOT / "reports" / "figures"

YEARS = [2023, 2024, 2025]
RANDOM_STATE = 42

for d in (RAW_DIR, PROCESSED_DIR, FIGURES_DIR):
    d.mkdir(parents=True, exist_ok=True)
