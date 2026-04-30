"""
refetch_multi_athlete_events.py
================================
Targeted refetch for sports whose event pages have multi-athlete rows
(crews, teams, relays). The original individual scrape used a parser that
only captured the first athlete-link per row, undercounting these events.
With the multi-athlete-row fix in scrape_olympedia.py, refetching just these
sports recovers the missing athletes.

Covers Tokyo (61) + Paris (63) + (optionally) Beijing (62) / 2018 (60) /
2026 (72) for sports where multi-athlete rows exist (Rowing eights/fours/
pairs, Sailing crews, Equestrian Team finals, Athletics relays, Cycling
Team Pursuit, etc.).
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scrape_olympedia import (
    Fetcher, EditionMeta, AthleteRow, parse_results_table,
    list_events_for_sport, _infer_sex_from_event, write_csv,
    load_edition_metadata,
)

# (edition_id, sport_path, sport_name, event_name_filter or None)
# event_name_filter: a function event_name -> bool, None = include all events of this sport
def all_events(_): return True
def relays_only(name): return "relay" in name.lower()

def doubles_or_team(name): return any(k in name.lower() for k in ("doubles", "team", "mixed"))

REFETCH_TARGETS = [
    (61, "/editions/61/sports/ATH", "Athletics", relays_only),
    (63, "/editions/63/sports/ATH", "Athletics", relays_only),
    (61, "/editions/61/sports/ROW", "Rowing", all_events),
    (63, "/editions/63/sports/ROW", "Rowing", all_events),
    (61, "/editions/61/sports/SAL", "Sailing", all_events),
    (63, "/editions/63/sports/SAL", "Sailing", all_events),
    (61, "/editions/61/sports/EJP", "Equestrian Jumping", all_events),
    (63, "/editions/63/sports/EJP", "Equestrian Jumping", all_events),
    (61, "/editions/61/sports/EVE", "Equestrian Eventing", all_events),
    (63, "/editions/63/sports/EVE", "Equestrian Eventing", all_events),
    (61, "/editions/61/sports/EDR", "Equestrian Dressage", all_events),
    (63, "/editions/63/sports/EDR", "Equestrian Dressage", all_events),
    (61, "/editions/61/sports/CTR", "Cycling Track", all_events),
    (63, "/editions/63/sports/CTR", "Cycling Track", all_events),
    # Round-2 additions: Tennis/Badminton/Table Tennis/Fencing doubles & team events
    (61, "/editions/61/sports/TEN", "Tennis", doubles_or_team),
    (63, "/editions/63/sports/TEN", "Tennis", doubles_or_team),
    (61, "/editions/61/sports/BDM", "Badminton", doubles_or_team),
    (63, "/editions/63/sports/BDM", "Badminton", doubles_or_team),
    (61, "/editions/61/sports/TTE", "Table Tennis", all_events),
    (63, "/editions/63/sports/TTE", "Table Tennis", all_events),
    (61, "/editions/61/sports/FEN", "Fencing", doubles_or_team),
    (63, "/editions/63/sports/FEN", "Fencing", doubles_or_team),
    # Rugby Sevens — was completely missed because list_sports regex required
    # 3 alpha chars and the olympedia code is RU7
    (61, "/editions/61/sports/RU7", "Rugby Sevens", all_events),
    (63, "/editions/63/sports/RU7", "Rugby Sevens", all_events),
    # Winter 2026 fixes: Figure Skating Pairs/Ice Dance/Team are 2+ athletes per row
    (72, "/editions/72/sports/FSK", "Figure Skating", all_events),
    (60, "/editions/60/sports/FSK", "Figure Skating", all_events),
    (62, "/editions/62/sports/FSK", "Figure Skating", all_events),
    # Luge Doubles + Team Relay are multi-athlete-row events
    (72, "/editions/72/sports/LUG", "Luge", all_events),
    (60, "/editions/60/sports/LUG", "Luge", all_events),
    (62, "/editions/62/sports/LUG", "Luge", all_events),
]
DELAY = 4.0
STARTING_ID = 1_500_000


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    meta_by_id = load_edition_metadata(repo / "data" / "edition_metadata.csv")
    fetcher = Fetcher(delay=DELAY)
    next_id = STARTING_ID
    all_rows: list[AthleteRow] = []

    for edition_id, sport_path, sport_name, event_filter in REFETCH_TARGETS:
        meta = meta_by_id[edition_id]
        print(f"\n=== ed {edition_id} ({meta.year} {meta.season}) {sport_name} ===", flush=True)
        try:
            sport_soup = fetcher.get(sport_path)
        except Exception as exc:
            print(f"  failed to fetch {sport_path}: {exc}", flush=True)
            continue
        events = list_events_for_sport(sport_soup)
        events = [(n, p) for (n, p) in events if event_filter(n)]
        print(f"  {len(events)} events to refetch", flush=True)

        for event_name, event_path in events:
            ev_soup = fetcher.get(event_path)
            results = parse_results_table(ev_soup)
            unique_paths = set()
            new_rows: list[AthleteRow] = []
            for r in results:
                ath_path = r.get("AthletePath")
                if not ath_path or ath_path in unique_paths:
                    continue
                unique_paths.add(ath_path)
                new_rows.append(AthleteRow(
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
                    Sport=sport_name,
                    Event=f"{sport_name} {event_name}",
                    Medal=r.get("Medal"),
                ))
                next_id += 1
            print(f"     {event_name}: {len(new_rows)} unique athletes", flush=True)
            all_rows.extend(new_rows)

    out = repo / "new_athletes_multi_athlete_supplement.csv"
    write_csv(all_rows, out)
    print(f"\nWrote {len(all_rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
