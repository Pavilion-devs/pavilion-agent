"""Download BLS Producer Price Index series (via FRED's public CSV) into one long CSV,
then copy it into the Zetaris shared file area so a FILESTORE table can read it.

Usage: .venv/bin/python scripts/fetch_ppi.py [--no-copy]

Why FRED: the BLS v1 API (no key) returns only the last three years unless sent a
JSON POST, which Zetaris REST tables cannot do. FRED republishes the same BLS
series as plain CSV with full history. Data: U.S. Bureau of Labor Statistics, public domain.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

START = "2018-01-01"
OUT = ROOT / "data" / "ppi" / "ppi_series.csv"
PLATFORM = Path(os.environ.get("ZETARIS_PLATFORM_DIR", ROOT.parent / "zetaris-platform"))
CONTAINER_DIR = "/home/zetaris/customer-data/shared/causeway/ppi"

# series_id -> (short name, what it measures, which part of the story it speaks to)
SERIES = {
    "PCU455110455110": ("dept_store_margins", "PPI: Department stores (retail margins)", "general merchandise retail margins"),
    "PCU445110445110": ("grocery_margins", "PPI: Supermarkets and grocery retailers (retail margins)", "grocery retail margins"),
    "PCU449210449210": ("electronics_margins", "PPI: Electronics and appliance retailers (retail margins)", "electronics retail margins"),
    "PCU484121484121": ("truckload_freight", "PPI: General freight trucking, long-distance truckload", "inbound freight cost"),
    "PCU493110493110": ("warehousing", "PPI: General warehousing and storage", "distribution cost"),
    "PPICOR": ("core_final_demand", "PPI: Final demand less foods, energy and trade services", "broad cost inflation"),
}


def fetch(series_id: str, client: httpx.Client) -> list[tuple[str, float]]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={START}"
    resp = client.get(url)
    resp.raise_for_status()
    rows = []
    for rec in csv.DictReader(io.StringIO(resp.text)):
        value = rec.get(series_id, "").strip()
        if value and value != ".":
            rows.append((rec["observation_date"], float(value)))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-copy", action="store_true", help="only write the CSV, don't copy into Zetaris")
    args = parser.parse_args()

    ua = os.environ.get("SEC_USER_AGENT", "Causeway")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": ua}, timeout=30) as client, OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["series_id", "series_name", "description", "story_role", "observation_date", "value"])
        for sid, (name, desc, role) in SERIES.items():
            rows = fetch(sid, client)
            for date, value in rows:
                w.writerow([sid, name, desc, role, date, value])
            print(f"{sid:18} {name:20} {len(rows)} months, {rows[0][0]} .. {rows[-1][0]}")
            time.sleep(0.5)
    print("wrote", OUT)

    if not args.no_copy:
        compose = ["docker", "compose", "--project-directory", str(PLATFORM)]
        subprocess.run(compose + ["exec", "-T", "driver", "mkdir", "-p", CONTAINER_DIR], check=True)
        subprocess.run(compose + ["cp", str(OUT), f"driver:{CONTAINER_DIR}/ppi_series.csv"], check=True)
        print("copied into Zetaris at", f"{CONTAINER_DIR}/ppi_series.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
