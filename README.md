# Olympic History through 2026

An updated dataset of Olympic athletes covering Athens 1896 through Milano-Cortina 2026, extending the popular [rgriff23/Olympic_history](https://github.com/rgriff23/Olympic_history) dataset (1896-2016) with the five editions held since.

> **Looking for a ready-to-use R data package?** This repo is the *scrape pipeline*. The cleaned, packaged data is published as the **[`moderndive/olympicAthletes`](https://github.com/moderndive/olympicAthletes)** R package — install with `remotes::install_github("moderndive/olympicAthletes")`.

## What this repo contains

```
olympic-moderndive-data/
├── README.md                              # this file
├── requirements.txt                       # Python dependencies
├── athlete_events_through_2026.csv        # the final 315k-row dataset
├── new_athletes_2018_2026.csv             # just the new editions (40k rows)
├── new_athletes_*_supplement.csv          # targeted refetch outputs (used by merge)
├── scripts/
│   ├── scrape_olympedia.py                # main scraper (Python)
│   ├── scrape_olympedia.R                 # reference R port (predates v0.1, not maintained)
│   ├── refetch_multi_athlete_events.py    # targeted refetch for multi-athlete-row events
│   ├── refetch_athletics_relays.py        # targeted refetch for Athletics relays
│   ├── merge_new_editions.py              # concat per-edition CSVs + dedupe + bio backfill
│   ├── combine_with_original.py           # merge new editions with the rgriff23 1896-2016 CSV
│   ├── audit_scrape.py                    # per-edition gap audit vs olympedia metadata
│   └── audit_v2.py                        # event/sport-level historical comparison audit
├── checkpoints/                           # per-edition checkpoint CSVs (resumable)
│   ├── edition_NN_individual.csv          # individual-events scrape output
│   └── edition_NN_team.csv                # team-events refetch output
├── data/
│   ├── edition_metadata.csv               # verified facts about each new edition
│   └── medal_table_summary.csv            # full medal tables for each new edition
└── sample/
    └── sample_athlete_events.csv          # 24 verified rows showing the schema
```

## Why this exists

The original rgriff23 dataset contained 271,116 athlete-event rows scraped from sports-reference.com in May 2018. Sports-reference's Olympics section was retired and its content moved to [olympedia.org](https://www.olympedia.org/). Since the original cutoff, five Games have taken place:

| Edition         | Olympedia ID | Participants | NOCs | Medal events | Disciplines |
|-----------------|--------------|--------------|------|--------------|-------------|
| 2018 PyeongChang Winter | 60   | 2,793  | 93   | 102 | 15 |
| 2020 Tokyo Summer (held 2021) | 61 | 11,319 | 206 | 339 | 49 |
| 2022 Beijing Winter     | 62  | 2,786   | 91   | 109 | 15 |
| 2024 Paris Summer       | 63  | 10,763  | 206  | 329 | 47 |
| 2026 Milano-Cortina Winter | 72 | 2,932 | 94 | 111 | 16 |

Sources are linked in `data/edition_metadata.csv`. Counts come from olympedia's edition pages.

## Schema

The output keeps the same 15 columns as the original `athlete_events.csv` so existing notebooks and analyses keep working:

| Column   | Type         | Notes                                                        |
|----------|--------------|--------------------------------------------------------------|
| ID       | int          | Unique per athlete; new IDs start at 1,000,000 to avoid clash with originals (1..135571) |
| Name     | str          | ASCII-folded athlete name                                    |
| Sex      | "M" / "F"    | Inferred from event name when olympedia bio is missing       |
| Age      | int or NA    | Approximate age at Games, computed from DOB                  |
| Height   | float or NA  | cm                                                           |
| Weight   | float or NA  | kg                                                           |
| Team     | str          | Country/team name as displayed on olympedia                  |
| NOC      | str          | 3-letter IOC code                                            |
| Games    | str          | e.g. `"2026 Winter"`                                         |
| Year     | int          | Calendar year of the Games (Tokyo 2020 = 2020, even though held in 2021) |
| Season   | "Summer" / "Winter" |                                                       |
| City     | str          | Host city                                                    |
| Sport    | str          | Sport name; olympedia's split disciplines are collapsed back to the rgriff23 names (`Cycling Track` → `Cycling`, etc.) |
| Event    | str          | `"<Sport> <Event name>"`, matching the original convention   |
| Medal    | "Gold" / "Silver" / "Bronze" / NA | Per-player: every roster member of a medal-winning team gets the team's medal |

## How to produce the full dataset

The end-to-end pipeline is **scrape → refetch (multi-athlete events) → merge → combine**, with dedicated scripts at each stage. Total wall-clock for a full rebuild is **~24-30 hours** at the polite 4s request delay olympedia requires (do not lower this — see the rate-limit note below).

### Step 1: Get the original 1896-2016 CSV

```bash
curl -L -o athlete_events.csv \
  https://raw.githubusercontent.com/rgriff23/Olympic_history/master/data/athlete_events.csv
```

(The Kaggle mirror at <https://www.kaggle.com/datasets/heesoo37/120-years-of-olympic-history-athletes-and-results> is the same data.)

### Step 2: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Scrape the five new editions

```bash
python scripts/scrape_olympedia.py \
  --editions 60 61 62 63 72 \
  --out new_athletes_2018_2026.csv \
  --delay 4.0
```

This produces a CSV with all individual events for all five editions. Per-edition checkpoints land in `checkpoints/edition_<id>.csv` after each edition completes; each *sport within* an edition also writes a `checkpoints/edition_<id>_partial.csv` so a mid-edition crash recovers cleanly.

**Recommended split for managing the long runtime:**

```bash
# Phase A: smoke test with one small Winter edition (~2-3 hours)
python scripts/scrape_olympedia.py --editions 72 --out new_athletes_2026.csv --delay 4.0

# Phase B1: the small Winter pair (~6 hours)
python scripts/scrape_olympedia.py --editions 60 62 --out new_athletes_2018_2022.csv --delay 4.0

# Phase B2: the big Summer pair (~24 hours; bio cache shared across both)
python scripts/scrape_olympedia.py --editions 61 63 --out new_athletes_2020_2024.csv --delay 4.0
```

After scraping, **rename per-edition checkpoints to `_individual.csv`** so they don't collide with the team refetch in step 4:

```bash
for ed in 60 61 62 63 72; do
  mv checkpoints/edition_${ed}.csv checkpoints/edition_${ed}_individual.csv
done
```

### Step 4: Refetch team-event rosters and other multi-athlete-row events

The individual scrape catches per-athlete result rows but misses team-event rosters (Football, Hockey, Volleyball, Curling, Ice Hockey, …) and multi-athlete-per-row events (Athletics relays, Rowing crews, Equestrian Team, Tennis Doubles, …). Two targeted scripts handle this — **fast** (~10-15 minutes for the team script, ~1-2 hours for the multi-athlete script):

```bash
# Team-event rosters (Ice Hockey, Curling, Bobsleigh, Football, Basketball, etc.)
python scripts/scrape_olympedia.py \
  --editions 60 61 62 63 72 \
  --team-events-only --skip-bio \
  --out new_athletes_team_rosters.csv \
  --delay 4.0

for ed in 60 61 62 63 72; do
  mv checkpoints/edition_${ed}.csv checkpoints/edition_${ed}_team.csv
done

# Multi-athlete events (Rowing crews, Athletics relays, Equestrian Team,
# Cycling Track Pursuit, Sailing crews, Tennis/Badminton/Table Tennis Doubles,
# Fencing Team, Rugby Sevens, Figure Skating Pairs/Team, Luge Doubles)
python scripts/refetch_multi_athlete_events.py
```

The second script writes `new_athletes_multi_athlete_supplement.csv`. Sailing and Athletics relays each have separate small refetch scripts you can also run if needed (`refetch_athletics_relays.py`).

### Step 5: Merge the new-edition CSVs

```bash
python scripts/merge_new_editions.py \
  --out new_athletes_2018_2026.csv \
  --supplement new_athletes_multi_athlete_supplement.csv \
  --supplement new_athletes_sailing.csv \
  --external-bios athlete_events.csv
```

This concatenates the per-edition `_individual.csv` and `_team.csv` files, applies supplement files, dedupes by `(Name, Year, Sport, Event)`, and backfills bio fields from the original 1896-2016 dataset for athletes who appear in both.

### Step 6: Combine with the original

```bash
python scripts/combine_with_original.py \
  --original athlete_events.csv \
  --new      new_athletes_2018_2026.csv \
  --out      athlete_events_through_2026.csv
```

The combiner harmonises city/sport name spellings (olympedia uses "Cycling Track" / "Cycling Road" / etc.; the original used just "Cycling"), reassigns IDs to keep them globally unique, and prints a per-Games row count so you can sanity-check the output.

### Step 7: Audit (optional but recommended)

```bash
python scripts/audit_scrape.py   # per-edition + per-sport, vs verified metadata
python scripts/audit_v2.py       # vs immediately-prior comparable Games (catches event-level regressions)
```

## What works without running the scraper

The `data/` folder is independently useful even without the full scrape:

- **`edition_metadata.csv`** — verified facts about each new edition (year, season, city, opening/closing dates, participant counts).
- **`medal_table_summary.csv`** — full medal table for every new edition. Group by NOC across this and a medal extract from the original CSV to get country-level medal counts through 2026.

If you just want the `athlete_events_through_2026.csv` and don't want to run the scrape yourself, the **[`moderndive/olympicAthletes`](https://github.com/moderndive/olympicAthletes)** R package ships it pre-built (3.2 MB compressed `.rda`).

## Caveats and known limitations

**Medal counts are per-player, not per-team-event.** The schema awards one row per athlete-event, so a team gold (e.g. Ice Hockey Men) produces ~25 `Medal=Gold` rows — one per player on the winning roster — while the official IOC medal table counts that as one gold. To reproduce the IOC medal table from `athlete_events_through_2026.csv`, divide team-event medal rows by team size, or use `data/medal_table_summary.csv` directly (it uses the IOC convention).

**Athlete counts vs olympedia "participants":** All five new editions match within ±5% of olympedia's published participant counts:

| Year | Season | Scraped | Olympedia | Δ |
|---|---|---|---|---|
| 2018 | Winter | 2,921 | 2,793 | +4.6% |
| 2020 | Summer | 11,533 | 11,319 | +1.9% |
| 2022 | Winter | 2,901 | 2,786 | +4.1% |
| 2024 | Summer | 10,835 | 10,763 | +0.7% |
| 2026 | Winter | 2,903 | 2,932 | -1.0% |

Several editions are slightly *over* the reference — the scraper picks up DNS/DNF/heat-only athletes that olympedia's headline "participants" count excludes. The 2026 number will likely tick up as olympedia finishes filling in post-Games roster details (a scheduled refresh job is planned for May 2026).

## What's verified vs. what's mechanical

- **Verified (cross-checked across olympedia.org and Wikipedia/IOC):** every row in `data/edition_metadata.csv`, every row in `data/medal_table_summary.csv`, and every row in `sample/sample_athlete_events.csv` (the 2026 biathlon medal winners).
- **Produced mechanically by the scraper:** all the non-medal rows in the full dataset (athletes who competed but didn't medal) and the bio fields (height, weight, age). These are pulled directly from olympedia at scrape time and depend on whatever olympedia has for each athlete. Bio coverage degrades for newer/larger Games (~80% Height/Weight for 2018 Winter; ~25% for Paris 2024 / Milano-Cortina 2026) — same pattern as the original 1896-2016 dataset (which is missing height for ~33,900 athletes).

## Differences from the original rgriff23 methodology

The original rgriff23 R pipeline scraped `sports-reference.com`. That site no longer hosts Olympics data, so this scraper targets `olympedia.org`. Practical consequences and parser quirks worth knowing:

1. **Edition pages are at `/editions/<numeric_id>`** instead of `/olympics/<year>-<season>`. The five relevant IDs are baked into the README and the scraper docstring.
2. **Sport-code regex** — olympedia sport codes can include digits (e.g. Rugby Sevens is `RU7`); the scraper accepts `[A-Z0-9]{3}`. An earlier `[A-Z]{3}` regex silently dropped Rugby Sevens entirely.
3. **Sport names occasionally differ.** Olympedia splits "Cycling" into "Cycling Track / Road / BMX / Mountain Bike / BMX Freestyle / BMX Racing", "Canoeing" into "Canoe Sprint / Canoe Slalom", "Equestrianism" into "Equestrian Jumping / Eventing / Dressage", and "Gymnastics" into "Artistic / Rhythmic Gymnastics / Trampolining". `combine_with_original.py`'s `SPORT_MAP` collapses all of these back to the original names.
4. **City spelling.** Olympedia writes "Milano-Cortina d'Ampezzo"; the original used short forms like "PyeongChang". `CITY_MAP` in the combiner handles the renames. Verified city values are also in `data/edition_metadata.csv`, which the scraper prefers over parsing the edition page header.
5. **Multi-athlete rows.** Many event pages have rows that contain 2-8 athlete links (Bobsleigh sleds, Rowing crews, Tennis doubles, Athletics relay quartets, Artistic Swimming team octets). The parser emits one output row per athlete-link, not per table-row.
6. **Roster tables for team events.** Football, Basketball, Volleyball, Hockey, Handball, Water Polo, Ice Hockey, Curling, Rugby Sevens, Bobsleigh, and Artistic Swimming Team have a roster-style table whose headers (`Pos / Number / Team / NOC / GP …`) don't contain the word "Competitor". The parser detects these by `/athletes/N` link density rather than header strings, and inherits NOC + medal from the team-summary row immediately above each block of player rows.
7. **Sex inference.** When the olympedia bio doesn't say, the parser infers from the event name ("Women's …" / ", Women" / "Girls'" → F, otherwise M). Mixed-gender events (mixed relay, mixed team) need to be handled at analysis time if you want them split.
8. **Age.** Integer year-difference at the start of the Games, with an opening-day reference of Feb 1 (Winter) or Jul 15 (Summer). Tokyo 2020 is treated as a 2020 Games per IOC convention even though it was held in 2021.
9. **Rate limiting.** Olympedia rate-limits aggressively below ~3-4 seconds between requests. The current default `--delay 4.0` keeps `429 Too Many Requests` responses to ~zero. Lower delays trigger frequent 30-second cool-downs that, paradoxically, make the run slower. The scraper retries 429s with `Retry-After`-honoring exponential backoff, plus retries network-level errors (`ConnectionResetError`, `Timeout`, `ChunkedEncodingError`) the same way.
10. **R port is unmaintained.** `scripts/scrape_olympedia.R` predates the parser fixes from items 5-9 and has not been kept in sync. The Python scraper is the canonical pipeline.

## Citing

If you use this dataset, please cite both the original work and olympedia:

> Griffin, R. (2018). Olympic history: longitudinal data scraped from www.sports-reference.com. https://github.com/rgriff23/Olympic_history
>
> OlyMADMen. Olympedia. https://www.olympedia.org/

If you use the R package specifically:

> Ismay, C. (2026). olympicAthletes: Olympic Athlete Event Data, Athens 1896 to Milano-Cortina 2026. R package version 0.1.0. https://github.com/moderndive/olympicAthletes

## License notes

The scraper code in this repo is provided for educational use. Olympedia is a free, community-maintained resource — respect its load and rate limits. If you publish a derived dataset, include attribution and follow olympedia's terms. The packaged dataset (`olympicAthletes` R package) is released under CC BY 4.0.
