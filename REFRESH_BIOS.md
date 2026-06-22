# Refreshing team / multi-athlete bios (2018–2026)

## Why this is needed

The individual-event scrape (`scrape_olympedia.py` without `--team-events-only`)
fetches each athlete's olympedia bio, so individual-sport athletes have
Sex/Age/Height/Weight. But the two refetch passes that recover **team rosters**
and **multi-athlete-row events** historically ran *without* fetching bios:

- the team-roster pass used `scrape_olympedia.py … --team-events-only --skip-bio`, and
- `refetch_multi_athlete_events.py` hard-coded `Age=None, Height=None, Weight=None`.

So every team-event and multi-athlete-row athlete in the 2018–2026 editions was
written with **no bio**, and only picked up Height/Weight if their exact name
also appeared in the individual scrape or the historical 1896–2016 dataset
(name-based backfill in `merge_new_editions.py`). Age is **never** backfilled
(it is per-Games). Net effect, measured on `athlete_events_through_2026.csv`:

| Cohort | Team-sport athletes | Missing age | Missing height/weight |
|---|---|---|---|
| 1896–2016 (rgriff23 import) | 36,473 | 4% | 23% |
| 2018–2026 (this repo) | 7,826 | **95%** | **88%** |

The gap is entirely in the editions this repo scraped. Olympedia *does* hold
these bios now (verified live, June 2026 — Sidney Crosby, Rasmus Dahlin, etc.
all have full measurements). The fix is to re-run the two refetch passes **with
bios** and re-merge.

## What changed in the code

`refetch_multi_athlete_events.py` now fetches bios by default (shared cache),
and gained a CLI:

```
--delay FLOAT        polite request delay (default 4.0; keep >= 4.0)
--out PATH           output CSV (default new_athletes_multi_athlete_supplement.csv)
--editions ID ...    restrict to a subset, e.g. --editions 72 for 2026 only
--skip-bio           old behaviour (Age/Height/Weight = NA)
```

The team-roster pass needs no code change — just **drop `--skip-bio`**.

## Run: all five editions (2018–2026)  ·  ~12–14 h at 4 s delay

```bash
# 1. Team rosters WITH bios (shared bio cache across all five editions)
python scripts/scrape_olympedia.py \
  --editions 60 61 62 63 72 \
  --team-events-only \
  --out new_athletes_team_rosters.csv \
  --delay 4.0
for ed in 60 61 62 63 72; do
  mv checkpoints/edition_${ed}.csv checkpoints/edition_${ed}_team.csv
done

# 2. Multi-athlete-row events WITH bios (now the default)
python scripts/refetch_multi_athlete_events.py --delay 4.0

# 3. Re-merge (backfill only fills gaps the fresh bios didn't, never overrides)
python scripts/merge_new_editions.py \
  --out new_athletes_2018_2026.csv \
  --supplement new_athletes_multi_athlete_supplement.csv \
  --supplement new_athletes_sailing.csv \
  --external-bios athlete_events.csv

# 4. Re-combine with the 1896–2016 original
python scripts/combine_with_original.py \
  --original athlete_events.csv \
  --new      new_athletes_2018_2026.csv \
  --out      athlete_events_through_2026.csv

# 5. Audit
python scripts/audit_scrape.py
```

## Run: 2026 only  ·  ~1–1.5 h

```bash
python scripts/scrape_olympedia.py --editions 72 --team-events-only \
  --out new_athletes_team_rosters_2026.csv --delay 4.0
mv checkpoints/edition_72.csv checkpoints/edition_72_team.csv

python scripts/refetch_multi_athlete_events.py --editions 72 --delay 4.0
# then merge + combine + audit as above
```

> Note: re-running step 1 for all editions overwrites the existing
> `edition_NN_team.csv` checkpoints. The individual-event checkpoints
> (`edition_NN_individual.csv`) are untouched and do not need re-scraping —
> they already have bios.
