"""
audit_scrape.py
===============
Compare the scraped athlete_events_through_2026.csv against the verified
metadata in data/edition_metadata.csv and data/medal_table_summary.csv.
Reports per-edition gaps and per-NOC medal mismatches with hypotheses.
"""

import sys
from pathlib import Path
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
NEW_EDITIONS = [2018, 2020, 2022, 2024, 2026]


def banner(s: str) -> None:
    print(f"\n{'='*78}\n{s}\n{'='*78}")


def main() -> int:
    df = pd.read_csv(REPO / "athlete_events_through_2026.csv", na_values=["NA"])
    meta = pd.read_csv(REPO / "data" / "edition_metadata.csv")
    mt = pd.read_csv(REPO / "data" / "medal_table_summary.csv")

    banner("A. Per-edition totals: scraped vs metadata")
    print(f"{'Year':<6}{'Season':<8}{'athletes':>10}{'expected':>10}{'gap':>8}"
          f"{'NOCs':>6}{'expected':>10}{'events':>8}{'expected':>10}")
    for _, m in meta.iterrows():
        sub = df[(df.Year == m.year) & (df.Season == m.season)]
        n_ath = sub.Name.nunique()
        n_noc = sub.NOC.nunique()
        n_ev = sub.Event.nunique()
        gap = n_ath - m.participants
        pct = 100 * gap / m.participants
        print(f"{m.year:<6}{m.season:<8}{n_ath:>10}{m.participants:>10}{gap:>+6} ({pct:+.1f}%)"
              f"{n_noc:>6}{m.nocs:>10}{n_ev:>8}{m.medal_events:>10}")

    banner("B. Medal counts per NOC: scraped vs verified medal table")
    # Build scraped medal counts per (year, season, noc)
    medals = df[df.Medal.isin(["Gold", "Silver", "Bronze"]) & df.Year.isin(NEW_EDITIONS)]
    scraped = (medals.groupby(["Year", "Season", "NOC", "Medal"]).size()
                      .unstack(fill_value=0).reset_index())
    for col in ("Gold", "Silver", "Bronze"):
        if col not in scraped.columns:
            scraped[col] = 0
    scraped["scraped_total"] = scraped.Gold + scraped.Silver + scraped.Bronze

    # Compare with verified medal table
    cmp = mt.merge(scraped,
                   left_on=["year", "season", "noc"],
                   right_on=["Year", "Season", "NOC"],
                   how="outer", suffixes=("_truth", "_scraped"))
    cmp = cmp.fillna({"gold": 0, "silver": 0, "bronze": 0, "total": 0,
                      "Gold": 0, "Silver": 0, "Bronze": 0, "scraped_total": 0})
    for col in ("year", "season", "noc"):
        cmp[col] = cmp[col].fillna(cmp[col[:1].upper() + col[1:]] if col != "noc" else cmp["NOC"])
    cmp["delta_total"] = cmp["scraped_total"].astype(int) - cmp["total"].astype(int)
    cmp["delta_gold"]  = cmp["Gold"].astype(int)  - cmp["gold"].astype(int)
    cmp["delta_silver"] = cmp["Silver"].astype(int) - cmp["silver"].astype(int)
    cmp["delta_bronze"] = cmp["Bronze"].astype(int) - cmp["bronze"].astype(int)

    # Per-edition medal totals (scraped vs truth)
    print("Per-edition medal totals (scraped vs verified):")
    print(f"{'Year':<6}{'Season':<8}{'gold scr/tru':>14}{'silver scr/tru':>16}"
          f"{'bronze scr/tru':>16}{'total scr/tru':>16}")
    for (yr, sn), grp in cmp.groupby(["year", "season"]):
        g = (int(grp.Gold.sum()), int(grp.gold.sum()))
        s = (int(grp.Silver.sum()), int(grp.silver.sum()))
        b = (int(grp.Bronze.sum()), int(grp.bronze.sum()))
        t = (int(grp.scraped_total.sum()), int(grp.total.sum()))
        print(f"{int(yr):<6}{sn:<8}{g[0]:>6}/{g[1]:<6} {s[0]:>6}/{s[1]:<6} "
              f"  {b[0]:>6}/{b[1]:<6}  {t[0]:>6}/{t[1]:<6}")

    banner("C. Top 10 NOC medal-count discrepancies (|delta_total| largest)")
    top = cmp.iloc[cmp.delta_total.abs().sort_values(ascending=False).index].head(20)
    print(f"{'Year':<6}{'Season':<8}{'NOC':<5}{'country':<22}"
          f"{'G':>4}{'/':<2}{'tru':>4}{'  S':>4}{'/':<2}{'tru':>4}"
          f"{'  B':>4}{'/':<2}{'tru':>4}{'  Δtot':>7}")
    for _, r in top.iterrows():
        country = str(r.get("country") or "")[:20]
        print(f"{int(r.year):<6}{r.season:<8}{r.noc:<5}{country:<22}"
              f"{int(r.Gold):>4}/{int(r.gold):<4}{int(r.Silver):>4}/{int(r.silver):<4}"
              f"{int(r.Bronze):>4}/{int(r.bronze):<4}{int(r.delta_total):>+7}")

    banner("D. Per-(edition, sport) row counts; sports with zero rows")
    for yr in NEW_EDITIONS:
        sub = df[df.Year == yr]
        if sub.empty:
            continue
        per_sport = sub.groupby("Sport").agg(
            rows=("Name", "size"),
            athletes=("Name", "nunique"),
            events=("Event", "nunique"),
            medals=("Medal", lambda s: s.isin(["Gold","Silver","Bronze"]).sum()),
        ).sort_values("rows", ascending=False)
        print(f"\n--- {yr} {sub.Season.iloc[0]} ({per_sport.shape[0]} sports) ---")
        print(per_sport.to_string())

    banner("E. Bio coverage (Sex/Age/Height/Weight) per edition")
    print(f"{'Year':<6}{'rows':>7}{'sex_pct':>10}{'age_pct':>10}{'h_pct':>8}{'w_pct':>8}")
    for yr in NEW_EDITIONS:
        sub = df[df.Year == yr]
        n = len(sub)
        if n == 0: continue
        print(f"{yr:<6}{n:>7}"
              f"{100*sub.Sex.notna().mean():>9.1f}%"
              f"{100*sub.Age.notna().mean():>9.1f}%"
              f"{100*sub.Height.notna().mean():>7.1f}%"
              f"{100*sub.Weight.notna().mean():>7.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
