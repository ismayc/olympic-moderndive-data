"""
audit_v2.py
===========
Deeper audit: hunt for events with anomalously few rows by comparing each
new-edition (Sport, Event-pattern) to the same pattern in the immediately
prior comparable Games (2016 for 2020/2024, 2014 for 2018/2022/2026).
Surfaces specific events where rows are far below historical norm so we can
target them for refetch.
"""

import sys, re
from pathlib import Path
import pandas as pd

REPO = Path(__file__).resolve().parent.parent

# Map each new edition to the most recent comparable historical edition
COMPARE = {
    (2018, "Winter"): (2014, "Winter"),
    (2020, "Summer"): (2016, "Summer"),
    (2022, "Winter"): (2014, "Winter"),
    (2024, "Summer"): (2016, "Summer"),
    (2026, "Winter"): (2014, "Winter"),
}


def banner(s):
    print(f"\n{'='*78}\n{s}\n{'='*78}")


def normalize_event(ev: str) -> str:
    """
    Olympedia events use ", Men/Women" suffix; rgriff23 uses "Men's/Women's"
    prefix. Collapse both to a normalized form: keep the sport prefix +
    base event name + sex marker.
    Example: "Athletics Men's 100 metres" and "Athletics 100 metres, Men"
    both -> "athletics 100 metres M".
    """
    s = ev.lower()
    sex = "X"
    for tok, m in [(", men", "M"), (", women", "F"), (", mixed", "X"),
                   (", open", "X"), ("men's", "M"), ("women's", "F"),
                   ("girls'", "F"), ("boys'", "M"), (", girls", "F"), (", boys", "M")]:
        if tok in s:
            s = s.replace(tok, "")
            sex = m
            break
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return f"{s} {sex}"


def main() -> int:
    df = pd.read_csv(REPO / "athlete_events_through_2026.csv", na_values=["NA"])
    df["EvKey"] = df.Event.apply(normalize_event)

    banner("A. Suspected event-level undercounts (new-edition rows < 50% of comparable historical edition)")
    print(f"{'NewYr':<6}{'Sn':<4}{'Sport':<22}{'Event':<55}{'now':>4} {'hist':>5} {'%':>5}")
    candidates = []
    for (new_yr, new_sn), (hist_yr, hist_sn) in COMPARE.items():
        new = df[(df.Year == new_yr) & (df.Season == new_sn)]
        hist = df[(df.Year == hist_yr) & (df.Season == hist_sn)]
        new_rows = new.groupby(["Sport", "EvKey", "Event"]).size().reset_index(name="now_rows")
        hist_rows = hist.groupby(["Sport", "EvKey"]).size().reset_index(name="hist_rows")
        merged = new_rows.merge(hist_rows, on=["Sport", "EvKey"], how="left")
        merged["pct"] = merged.now_rows / merged.hist_rows
        # Suspect: hist >= 30 (had real data) and now < 50% of hist
        sus = merged[(merged.hist_rows >= 30) & (merged.pct < 0.5)].sort_values("hist_rows", ascending=False)
        for _, r in sus.iterrows():
            candidates.append((new_yr, new_sn, r.Sport, r.Event, int(r.now_rows), int(r.hist_rows), r.pct))
            print(f"{new_yr:<6}{new_sn:<4}{r.Sport[:20]:<22}{r.Event[:53]:<55}"
                  f"{int(r.now_rows):>4} {int(r.hist_rows):>5} {r.pct:>5.0%}")

    banner("B. Sports that exist in historical edition but ABSENT from new edition")
    for (new_yr, new_sn), (hist_yr, hist_sn) in COMPARE.items():
        new_sports = set(df[(df.Year == new_yr) & (df.Season == new_sn)].Sport.unique())
        hist_sports = set(df[(df.Year == hist_yr) & (df.Season == hist_sn)].Sport.unique())
        missing = sorted(hist_sports - new_sports)
        if missing:
            print(f"\n  {new_yr} {new_sn}: {len(missing)} historical sports not present:")
            for s in missing:
                hist_count = len(df[(df.Year == hist_yr) & (df.Sport == s)])
                print(f"    {s} (hist had {hist_count} rows)")

    banner("C. Bio coverage opportunities — athletes WITHOUT bios in new editions who DO have bios in historical editions")
    bios_hist = df[(df.Year < 2018) & df.Height.notna()].drop_duplicates("Name").set_index("Name")
    new_no_bio = df[(df.Year >= 2018) & df.Height.isna()]
    can_backfill = new_no_bio[new_no_bio.Name.isin(bios_hist.index)]
    print(f"  rows in new editions missing Height that COULD be backfilled from prior bios:"
          f" {can_backfill.shape[0]:,} / {new_no_bio.shape[0]:,} ({100*can_backfill.shape[0]/new_no_bio.shape[0]:.1f}%)")
    print(f"  unique athletes with backfillable bios: {can_backfill.Name.nunique():,}")

    banner("D. Per-(NewYear, Sport) athlete count vs comparable historical")
    print(f"{'New':<6}{'Sn':<4}{'Sport':<25}{'now ath':>10}{'hist ath':>10}{'now ev':>8}{'hist ev':>8}")
    for (new_yr, new_sn), (hist_yr, hist_sn) in COMPARE.items():
        new = df[(df.Year == new_yr) & (df.Season == new_sn)]
        hist = df[(df.Year == hist_yr) & (df.Season == hist_sn)]
        for sport in sorted(set(new.Sport.unique()) & set(hist.Sport.unique())):
            n_now = new[new.Sport == sport].Name.nunique()
            n_hist = hist[hist.Sport == sport].Name.nunique()
            e_now = new[new.Sport == sport].Event.nunique()
            e_hist = hist[hist.Sport == sport].Event.nunique()
            if n_hist >= 50 and n_now < n_hist * 0.7:
                print(f"{new_yr:<6}{new_sn:<4}{sport[:23]:<25}{n_now:>10}{n_hist:>10}{e_now:>8}{e_hist:>8}")

    banner("E. Total athlete count by edition (final view)")
    meta = pd.read_csv(REPO / "data" / "edition_metadata.csv")
    for _, m in meta.iterrows():
        n = df[(df.Year == m.year) & (df.Season == m.season)].Name.nunique()
        gap = n - m.participants
        print(f"  {m.year} {m.season:<7} {n:>6} / {m.participants:>6}  ({gap:+5d}, {100*gap/m.participants:+.1f}%)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
