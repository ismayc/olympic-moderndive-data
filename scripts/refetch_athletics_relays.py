"""
refetch_athletics_relays.py
============================
Targeted refetch of Athletics relay events for editions 61 (Tokyo) and 63
(Paris). The original individual scrape used a parser that only captured the
first athlete-link per result-table row, so relay event pages — where the
roster table has 32 rows but ~91 athlete links — were undercounted to ~16
athletes per event. Run after the multi-athlete-row fix in scrape_olympedia.py.
Output supplements (Name, Year, Sport, Event) into the existing per-edition
individual checkpoints via merge_new_editions.py.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import asdict
from pathlib import Path

# Reuse the patched scraper internals
sys.path.insert(0, str(Path(__file__).parent))
from scrape_olympedia import (
    Fetcher, EditionMeta, AthleteRow, parse_results_table,
    list_events_for_sport, _infer_sex_from_event, write_csv,
    load_edition_metadata,
)

EDITIONS_TO_REFETCH = {
    61: "/editions/61/sports/ATH",
    63: "/editions/63/sports/ATH",
}
DELAY = 4.0
STARTING_ID = 1_500_000  # well clear of existing IDs


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    meta_by_id = load_edition_metadata(repo / "data" / "edition_metadata.csv")
    fetcher = Fetcher(delay=DELAY)
    next_id = STARTING_ID
    all_rows: list[AthleteRow] = []

    for edition_id, sport_path in EDITIONS_TO_REFETCH.items():
        meta = meta_by_id[edition_id]
        print(f"\n=== Edition {edition_id} ({meta.year} {meta.season}) Athletics relays ===", flush=True)
        sport_soup = fetcher.get(sport_path)
        events = list_events_for_sport(sport_soup)
        relay_events = [(n, p) for (n, p) in events if "relay" in n.lower()]
        print(f"  found {len(relay_events)} relay events", flush=True)

        for event_name, event_path in relay_events:
            ev_soup = fetcher.get(event_path)
            results = parse_results_table(ev_soup)
            unique_paths = set()
            event_rows: list[AthleteRow] = []
            for r in results:
                ath_path = r.get("AthletePath")
                if not ath_path or ath_path in unique_paths:
                    continue
                unique_paths.add(ath_path)
                event_rows.append(AthleteRow(
                    ID=next_id,
                    Name=r.get("Athlete", "").strip(),
                    Sex=_infer_sex_from_event(event_name),
                    Age=None, Height=None, Weight=None,
                    Team=r.get("noc", "") or r.get("team", ""),
                    NOC=(r.get("noc") or "").upper()[:3],
                    Games=meta.games_label,
                    Year=meta.year,
                    Season=meta.season,
                    City=meta.city,
                    Sport="Athletics",
                    Event=f"Athletics {event_name}",
                    Medal=r.get("Medal"),
                ))
                next_id += 1
            print(f"     {event_name}: {len(event_rows)} unique athletes", flush=True)
            all_rows.extend(event_rows)

    out = repo / "new_athletes_athletics_relays.csv"
    write_csv(all_rows, out)
    print(f"\nWrote {len(all_rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
