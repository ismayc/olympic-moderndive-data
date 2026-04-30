"""
merge_new_editions.py
=====================
Concatenate the per-edition individual + team-roster CSVs into a single
new-editions file ready for combine_with_original.py.

Steps:
  1. Read all 10 per-edition CSVs (5 individual + 5 team rosters).
  2. Dedupe rows that the parser double-counted (e.g. Modern Pentathlon Paris
     emits 21 rows per athlete because olympedia exposes per-discipline ranking
     tables on the event page).
  3. For an athlete that appears in BOTH the team roster (no bio) and an
     individual event (with bio), backfill bio fields onto the team-roster row.
  4. Write merged CSV.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import pandas as pd

EDITIONS = [60, 61, 62, 63, 72]
COLUMNS = ["ID", "Name", "Sex", "Age", "Height", "Weight", "Team", "NOC",
           "Games", "Year", "Season", "City", "Sport", "Event", "Medal"]


def load_one(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, na_values=["NA", "", "NaN"])[COLUMNS]


def dedupe_within_event(df: pd.DataFrame) -> pd.DataFrame:
    """
    Some olympedia event pages (notably Modern Pentathlon Paris) have multiple
    result/ranking tables that each produce a row per athlete. Collapse those
    so each (Name, Year, Sport, Event, NOC) appears once. When duplicates
    exist, prefer the row with a Medal set, then with the most non-null fields.
    """
    if df.empty:
        return df
    df = df.copy()
    df["_has_medal"] = df["Medal"].notna()
    df["_filled"] = df[["Sex", "Age", "Height", "Weight"]].notna().sum(axis=1)
    df = df.sort_values(["_has_medal", "_filled"], ascending=False)
    deduped = df.drop_duplicates(
        subset=["Name", "Year", "Sport", "Event"], keep="first"
    )
    return deduped.drop(columns=["_has_medal", "_filled"]).sort_index()


def backfill_bios(individual: pd.DataFrame, team: pd.DataFrame,
                  external_bios: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Team-roster rows are scraped with --skip-bio. Backfill Sex/Height/Weight
    from (a) the individual scrape data, then (b) any external bios source
    (e.g. the historical 1896-2016 dataset). Age is per-Games and not back-filled."""
    if team.empty:
        return team
    sources = [individual]
    if external_bios is not None and not external_bios.empty:
        sources.append(external_bios)
    bios = pd.concat(sources, ignore_index=True)
    bios = (bios.dropna(subset=["Name"])
                .sort_values(["Height", "Weight"], na_position="last")
                .drop_duplicates("Name", keep="first")
                .set_index("Name")[["Sex", "Height", "Weight"]])
    team = team.copy()
    for col in ["Sex", "Height", "Weight"]:
        mask = team[col].isna() & team["Name"].isin(bios.index)
        team.loc[mask, col] = team.loc[mask, "Name"].map(bios[col])
    return team


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoints", type=Path, default=Path("checkpoints"))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--supplement", type=Path, action="append", default=[],
                   help="Optional supplementary CSV (e.g. Athletics relays refetch). "
                        "Repeatable. Rows are deduped against the main set.")
    p.add_argument("--external-bios", type=Path, default=None,
                   help="External CSV (e.g. the original 1896-2016 athlete_events.csv) "
                        "to use as a bio source when team-roster athletes lack height/weight.")
    args = p.parse_args()

    external_bios = pd.read_csv(args.external_bios, na_values=["NA", "", "NaN"])[COLUMNS] if args.external_bios else None

    individual_frames = []
    team_frames = []
    for ed in EDITIONS:
        ind_path = args.checkpoints / f"edition_{ed}_individual.csv"
        team_path = args.checkpoints / f"edition_{ed}_team.csv"
        ind = load_one(ind_path)
        team = load_one(team_path) if team_path.exists() else pd.DataFrame(columns=COLUMNS)
        ind = dedupe_within_event(ind)
        print(f"  ed {ed}: individual {len(ind):>6d}  team {len(team):>5d}")
        individual_frames.append(ind)
        team_frames.append(team)

    individual = pd.concat(individual_frames, ignore_index=True)
    team = pd.concat(team_frames, ignore_index=True)

    # Optional supplements (e.g. Athletics relays refetch with multi-athlete fix)
    supplements = []
    for sup_path in args.supplement:
        sup = load_one(sup_path)
        print(f"  supplement {sup_path.name}: {len(sup)} rows")
        supplements.append(sup)

    # Backfill bios for team-roster + supplement rows from individual data
    # and (optionally) the external historical bios source
    team = backfill_bios(individual, team, external_bios=external_bios)
    if supplements:
        sup_combined = pd.concat(supplements, ignore_index=True)
        sup_combined = backfill_bios(individual, sup_combined, external_bios=external_bios)
    else:
        sup_combined = pd.DataFrame(columns=COLUMNS)

    merged = pd.concat([individual, team, sup_combined], ignore_index=True)
    # Final dedupe: same athlete shouldn't appear twice in the SAME event
    # (could happen if a team-event athlete also slipped into individual scrape)
    merged = merged.drop_duplicates(
        subset=["Name", "Year", "Sport", "Event"], keep="first"
    )
    merged.to_csv(args.out, index=False, na_rep="NA")
    print(f"\nWrote {len(merged):,} rows to {args.out}")

    # Sanity report
    print("\nRows per Games:")
    counts = merged.groupby(["Year", "Season"]).size()
    print(counts.to_string())
    print(f"\nMedal distribution:\n{merged.Medal.value_counts(dropna=False).to_string()}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
