"""
combine_with_original.py
========================
Combine the rgriff23/Olympic_history athlete_events.csv (1896-2016) with
newly-scraped editions to produce a single dataset spanning 1896-2026.

USAGE
-----
    python combine_with_original.py \
        --original athlete_events.csv \
        --new      new_athletes.csv \
        --out      athlete_events_through_2026.csv

This script:
  1. Loads both CSVs.
  2. Verifies they share the 15-column schema.
  3. Reassigns IDs in the new file so that ID is unique across the merged set.
  4. Standardises NOC codes, City names, and Event names against the original
     to keep downstream analyses consistent.
  5. Writes the combined CSV plus a small QC report.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


COLUMNS = ["ID", "Name", "Sex", "Age", "Height", "Weight", "Team", "NOC",
           "Games", "Year", "Season", "City", "Sport", "Event", "Medal"]

# Map olympedia city names to the spellings used in the original 1896-2016 CSV
CITY_MAP = {
    "Milano-Cortina d'Ampezzo": "Milano-Cortina",
    "Pyeongchang": "PyeongChang",
}

# Sport renames where olympedia and sports-reference disagree.
# Olympedia consistently uses "Cycling X", "Canoe X", "Equestrian X" for the
# split disciplines; the 1896-2016 dataset rolls them up.
SPORT_MAP = {
    "Artistic Gymnastics": "Gymnastics",
    "Rhythmic Gymnastics": "Gymnastics",
    "Trampolining": "Gymnastics",
    "Beach Volleyball": "Volleyball",
    "Marathon Swimming": "Swimming",
    "Synchronized Swimming": "Synchronized Swimming",
    "Artistic Swimming": "Synchronized Swimming",
    # Cycling — match olympedia's actual sport names
    "Cycling BMX Freestyle": "Cycling",
    "Cycling BMX Racing": "Cycling",
    "Cycling Mountain Bike": "Cycling",
    "Cycling Road": "Cycling",
    "Cycling Track": "Cycling",
    # Canoeing
    "Canoe Sprint": "Canoeing",
    "Canoe Slalom": "Canoeing",
    # Equestrianism
    "Equestrian Jumping": "Equestrianism",
    "Equestrian Eventing": "Equestrianism",
    "Equestrian Dressage": "Equestrianism",
    # Legacy short forms (kept for safety)
    "BMX Freestyle": "Cycling",
    "BMX Racing": "Cycling",
    "Mountain Biking": "Cycling",
    "Road Cycling": "Cycling",
    "Track Cycling": "Cycling",
    "Cycling BMX": "Cycling",
}


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, na_values=["NA", "", "NaN"])
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    return df[COLUMNS].copy()


def harmonize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["City"] = df["City"].replace(CITY_MAP)
    df["Sport"] = df["Sport"].replace(SPORT_MAP)
    # Original used "Team" as country name spellings - normalise whitespace
    df["Team"] = df["Team"].astype(str).str.strip()
    df["NOC"] = df["NOC"].astype(str).str.upper().str[:3]
    return df


def reassign_ids(original: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """
    Original rgriff23 IDs are 1..N where N is the number of *unique athletes*.
    We extend that scheme: each unique Name in the new data gets a new ID,
    starting at original.ID.max() + 1.
    """
    new = new.copy()
    next_id = int(original["ID"].max()) + 1
    name_to_id: dict[str, int] = {}

    # Carry over IDs for athletes that appear in BOTH datasets
    name_to_existing = (
        original.drop_duplicates("Name").set_index("Name")["ID"].to_dict()
    )

    new_ids: list[int] = []
    for name in new["Name"]:
        if name in name_to_existing:
            new_ids.append(int(name_to_existing[name]))
            continue
        if name not in name_to_id:
            name_to_id[name] = next_id
            next_id += 1
        new_ids.append(name_to_id[name])

    new["ID"] = new_ids
    return new


def qc_report(df: pd.DataFrame) -> str:
    g = df.groupby(["Year", "Season"]).size().reset_index(name="rows")
    g["athletes"] = (df.groupby(["Year", "Season"])["ID"].nunique()
                       .reset_index(drop=True))
    return g.to_string(index=False)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--original", type=Path, required=True)
    p.add_argument("--new", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--report", type=Path, default=None,
                   help="Optional path for QC report")
    args = p.parse_args()

    print(f"Loading original: {args.original}")
    original = load(args.original)
    print(f"  {len(original):,} rows, "
          f"years {original.Year.min()}-{original.Year.max()}")

    print(f"Loading new: {args.new}")
    new = load(args.new)
    print(f"  {len(new):,} rows, "
          f"years {new.Year.min()}-{new.Year.max()}")

    original = harmonize(original)
    new = harmonize(new)
    new = reassign_ids(original, new)

    combined = pd.concat([original, new], ignore_index=True)
    combined = combined.sort_values(["Year", "Season", "Sport", "Event", "ID"])
    combined.to_csv(args.out, index=False)
    print(f"\nWrote {len(combined):,} rows to {args.out}")

    report = qc_report(combined)
    print("\n=== Rows per Games ===")
    print(report)
    if args.report:
        args.report.write_text(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
