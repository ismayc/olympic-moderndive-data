"""
scrape_olympedia.py
====================
Python port of the rgriff23/Olympic_history scraping methodology, adapted for
olympedia.org (the spiritual successor to sports-reference.com's Olympics
section, which was retired). Produces a CSV in the same 15-column schema as
the original athlete_events.csv:

    ID, Name, Sex, Age, Height, Weight, Team, NOC, Games, Year, Season,
    City, Sport, Event, Medal

Designed to scrape the editions added since the 2018 cutoff of the original
dataset:

    Edition          Olympedia edition_id
    -------          --------------------
    2018 Winter      60   PyeongChang
    2020 Summer      61   Tokyo (held 2021)
    2022 Winter      62   Beijing
    2024 Summer      63   Paris
    2026 Winter      72   Milano-Cortina

(Edition IDs verified by visiting each /editions/<id> page on olympedia.org.
Note that the gap from 63 to 72 reflects olympedia's interleaved numbering
of Summer and Winter editions plus Youth Games.)

USAGE
-----
    python scrape_olympedia.py --editions 60 61 62 63 72 --out new_athletes.csv

NOTES
-----
* Olympedia is a community-maintained, free site. Be respectful: this script
  uses a 1.0 second delay between requests by default. A full scrape of the
  five editions above is roughly 30-40k athlete pages and takes several hours.
* Run in chunks: --editions 72 alone for just Milano-Cortina is fastest.
* The script checkpoints to --checkpoint-dir after each edition so a crash
  doesn't lose progress.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE = "https://www.olympedia.org"
DEFAULT_DELAY = 2.0  # seconds between requests; do not lower without reason
USER_AGENT = (
    "OlympicHistoryUpdater/1.0 "
    "(personal research; contact: chester.ismay@gmail.com)"
)
# How many times to retry a 429 / transient 5xx before giving up.
MAX_RETRIES = 5
# Cap on backoff sleep after a 429 when no Retry-After header is present.
BACKOFF_BASE = 30.0


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class AthleteRow:
    """One row of the final athlete_events.csv (one athlete in one event)."""
    ID: int
    Name: str
    Sex: str            # "M" or "F"
    Age: Optional[int]
    Height: Optional[float]   # cm
    Weight: Optional[float]   # kg
    Team: str
    NOC: str            # 3-letter code
    Games: str          # e.g. "2026 Winter"
    Year: int
    Season: str         # "Summer" or "Winter"
    City: str
    Sport: str
    Event: str
    Medal: Optional[str]   # "Gold" / "Silver" / "Bronze" / None


@dataclass
class EditionMeta:
    edition_id: int
    year: int
    season: str
    city: str

    @property
    def games_label(self) -> str:
        return f"{self.year} {self.season}"


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

class Fetcher:
    def __init__(self, delay: float = DEFAULT_DELAY):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.delay = delay
        self._last = 0.0

    def get(self, path: str) -> BeautifulSoup:
        url = urljoin(BASE, path)
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(url, timeout=60)
            except requests.exceptions.RequestException as exc:
                # Network-level failure (ConnectionReset, Timeout, ChunkedEncodingError, ...)
                last_exc = exc
                self._last = time.time()
                sleep_s = BACKOFF_BASE * (2 ** attempt)
                print(f"    [NET] {type(exc).__name__} on {url} -> backing off {sleep_s:.0f}s (attempt {attempt+1}/{MAX_RETRIES})", flush=True)
                time.sleep(sleep_s)
                continue
            self._last = time.time()
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                retry_after = resp.headers.get("Retry-After")
                try:
                    sleep_s = float(retry_after) if retry_after else BACKOFF_BASE * (2 ** attempt)
                except ValueError:
                    sleep_s = BACKOFF_BASE * (2 ** attempt)
                print(f"    [{resp.status_code}] {url} -> backing off {sleep_s:.0f}s (attempt {attempt+1}/{MAX_RETRIES})", flush=True)
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        if last_exc is not None:
            raise last_exc
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")


# ---------------------------------------------------------------------------
# Edition discovery
# ---------------------------------------------------------------------------

def parse_edition(soup: BeautifulSoup, edition_id: int) -> EditionMeta:
    """Pull year, season, city from the edition overview page header."""
    h1 = soup.find("h1").get_text(strip=True)
    # e.g. "2026 Winter Olympics"
    m = re.match(r"(\d{4})\s+(Summer|Winter)\s+Olympics", h1)
    if not m:
        raise ValueError(f"Unexpected edition title: {h1!r}")
    year, season = int(m.group(1)), m.group(2)

    # City is in the Facts table, row "Host city"
    city = ""
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) == 2 and "Host city" in cells[0].get_text():
            # Strip "(Venues)" link suffix
            city = re.sub(r",.*$", "", cells[1].get_text(strip=True)).strip()
            break

    return EditionMeta(edition_id=edition_id, year=year, season=season, city=city)


def list_sports(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Return list of (sport_name, sport_path) for an edition page."""
    sports = []
    seen = set()
    # Olympedia sport codes are 3 alphanumeric chars (e.g. ATH, ROW, RU7 for
    # Rugby Sevens, BMX). Earlier versions of this regex required [A-Z]{3} and
    # silently dropped Rugby Sevens.
    for a in soup.select('a[href*="/sports/"]'):
        href = a.get("href", "")
        m = re.search(r"/editions/\d+/sports/([A-Z0-9]{3})", href)
        if not m:
            continue
        if href in seen:
            continue
        seen.add(href)
        sports.append((a.get_text(strip=True), href))
    return sports


# ---------------------------------------------------------------------------
# Sport / event / results parsing
# ---------------------------------------------------------------------------

EVENT_LINK_RE = re.compile(r"/results/\d+")


def list_events_for_sport(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """From a sport page, list (event_name, results_path) tuples."""
    events = []
    for a in soup.select(f'a[href*="/results/"]'):
        href = a.get("href", "")
        if not EVENT_LINK_RE.search(href):
            continue
        events.append((a.get_text(strip=True), href))
    # Deduplicate while preserving order
    seen = set()
    out = []
    for name, href in events:
        if href in seen:
            continue
        seen.add(href)
        out.append((name, href))
    return out


def parse_results_table(soup: BeautifulSoup) -> list[dict]:
    """
    Parse the results table on an event page. Returns list of dicts with keys
    NOC, Athlete, AthletePath, Medal (or None).

    Olympedia results tables are class="table table-striped". The data rows are
    direct children of <table> (sibling to <thead>) — there is no <tbody>. The
    header column for the athlete is "Competitor", and the medal appears as a
    cell containing the literal text "Gold" / "Silver" / "Bronze".
    """
    rows_out: list[dict] = []
    for table in soup.select("table.table"):
        headers = [th.get_text(strip=True).lower() for th in table.select("thead th")]
        joined = " ".join(headers)
        is_individual = bool(headers) and ("athlete" in joined or "competitor" in joined)
        # Roster/team-event detection: the table has many /athletes/N links and
        # a high link-to-row ratio. Match and standings tables list team names
        # in cells but have no player links, so the ratio is 0 and they're excluded.
        athlete_link_count = len(table.select('a[href^="/athletes/"]'))
        body_row_count = sum(1 for tr in table.find_all("tr") if tr.find_parent("thead") is None)
        link_ratio = athlete_link_count / body_row_count if body_row_count else 0
        is_roster = (
            not is_individual
            and athlete_link_count >= 5
            and link_ratio >= 0.5
        )
        if not is_individual and not is_roster:
            continue
        # Roster tables have a hierarchy: a team-summary row (no athlete link,
        # has NOC + maybe a Gold/Silver/Bronze cell) is followed by player rows
        # (athlete link in 'team' column, NOC blank). Carry team context down.
        current_team_name: Optional[str] = None
        current_noc: Optional[str] = None
        current_team_medal: Optional[str] = None
        for tr in table.find_all("tr"):
            if tr.find_parent("thead") is not None:
                continue
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            cell_text = [c.get_text(" ", strip=True) for c in cells]
            row = dict(zip(headers, cell_text))
            athlete_links = tr.find_all("a", href=re.compile(r"^/athletes/\d+"))
            row_medal = None
            for txt in cell_text:
                if txt in ("Gold", "Silver", "Bronze"):
                    row_medal = txt
                    break
            if is_roster and not athlete_links:
                # Team summary row — update context, do not emit a row
                noc_val = (row.get("noc") or "").strip()
                team_val = (row.get("team") or row.get("name") or "").strip()
                if noc_val and len(noc_val) <= 4 and noc_val.isupper():
                    current_noc = noc_val
                    current_team_name = team_val or current_team_name
                    current_team_medal = row_medal
                continue
            if is_roster and athlete_links:
                # Player row(s) — inherit team context. Some rosters list 2+ players
                # per <tr> (e.g. doubles, bobsleigh pairs); emit one output row per link.
                for link in athlete_links:
                    out = dict(row)
                    out["AthletePath"] = link["href"]
                    out["Athlete"] = link.get_text(strip=True)
                    out["noc"] = current_noc or ""
                    out["team"] = current_team_name or current_noc or ""
                    out["Medal"] = current_team_medal
                    rows_out.append(out)
                continue
            # Individual-results path: a single <tr> may contain multiple athlete
            # links (Beach Volleyball pairs, Tennis Doubles, Artistic Swimming
            # team rows where 4-8 swimmers share one results row, Bobsleigh sleds
            # where the pilot+brakeman share a row). Emit one row per link.
            if not athlete_links:
                row["AthletePath"] = None
                row["Athlete"] = row.get("competitor") or row.get("athlete", "")
                row["Medal"] = row_medal
                rows_out.append(row)
                continue
            for link in athlete_links:
                out = dict(row)
                out["AthletePath"] = link["href"]
                out["Athlete"] = link.get_text(strip=True)
                out["Medal"] = row_medal
                rows_out.append(out)
    return rows_out


# ---------------------------------------------------------------------------
# Athlete biographical info (the "infobox" in rgriff23's R code)
# ---------------------------------------------------------------------------

HEIGHT_RE = re.compile(r"(\d{2,3})\s*cm", re.IGNORECASE)
WEIGHT_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*kg", re.IGNORECASE)
BORN_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")


def parse_athlete_bio(soup: BeautifulSoup) -> dict:
    """Pull Sex, Height (cm), Weight (kg), date of birth from athlete page."""
    bio: dict = {"Sex": None, "Height": None, "Weight": None, "Born": None}

    info = soup.find("table", class_="biodata") or soup.find("table")
    if not info:
        return bio

    for tr in info.select("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True).rstrip(":").lower()
        value = cells[1].get_text(" ", strip=True)
        if label.startswith("sex") or label.startswith("gender"):
            bio["Sex"] = "F" if "female" in value.lower() else "M"
        elif "measurement" in label or "height" in label or "weight" in label:
            mh = HEIGHT_RE.search(value)
            if mh:
                bio["Height"] = float(mh.group(1))
            mw = WEIGHT_RE.search(value)
            if mw:
                bio["Weight"] = float(mw.group(1))
        elif "born" in label:
            m = BORN_RE.search(value)
            if m:
                bio["Born"] = (int(m.group(3)), m.group(2), int(m.group(1)))

    # Fallback for sex: olympedia events are usually gendered, leave None
    return bio


MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def age_at_games(born: Optional[tuple], games_year: int, season: str) -> Optional[int]:
    """
    Approximate age using the rgriff23 convention: integer year-difference
    based on a typical opening date. We use Feb 1 for Winter and Jul 15 for
    Summer; the original data is also approximate.
    """
    if not born:
        return None
    by, bmon, bday = born
    bmon = MONTHS.get(bmon.lower())
    if not bmon:
        return None
    if season == "Winter":
        ref = (games_year, 2, 1)
    else:
        ref = (games_year, 7, 15)
    age = ref[0] - by
    if (bmon, bday) > (ref[1], ref[2]):
        age -= 1
    return age if 8 <= age <= 100 else None


# ---------------------------------------------------------------------------
# Main pipeline for a single edition
# ---------------------------------------------------------------------------

# Sports whose Olympedia event pages are team rosters (no individual results).
# These are the ones the original parser missed; --team-events-only filters to them.
TEAM_SPORTS = {
    # Summer
    "Football", "Baseball", "Softball", "Basketball", "Handball", "Hockey",
    "Volleyball", "Water Polo", "Rugby Sevens", "Rugby", "Artistic Swimming",
    # Winter
    "Ice Hockey", "Curling", "Bobsleigh",
}


def scrape_edition(
    fetcher: Fetcher,
    edition_id: int,
    starting_id: int,
    bio_cache: dict,
    meta_override: Optional[EditionMeta] = None,
    partial_path: Optional[Path] = None,
    team_events_only: bool = False,
    skip_bio: bool = False,
) -> tuple[list[AthleteRow], int]:
    edition_soup = fetcher.get(f"/editions/{edition_id}")
    if meta_override is not None:
        meta = meta_override
    else:
        meta = parse_edition(edition_soup, edition_id)
    print(f"  -> {meta.year} {meta.season} ({meta.city})", flush=True)

    sports = list_sports(edition_soup)
    if team_events_only:
        sports = [(n, p) for (n, p) in sports if n in TEAM_SPORTS]
        print(f"     {len(sports)} team sports (filtered)", flush=True)
    else:
        print(f"     {len(sports)} sports / disciplines", flush=True)

    rows: list[AthleteRow] = []
    next_id = starting_id

    for sport_name, sport_path in sports:
        sport_soup = fetcher.get(sport_path)
        events = list_events_for_sport(sport_soup)
        print(f"     {sport_name}: {len(events)} events", flush=True)

        for event_name, event_path in events:
            ev_soup = fetcher.get(event_path)
            results = parse_results_table(ev_soup)

            for r in results:
                ath_path = r.get("AthletePath")
                if not ath_path:
                    continue

                if skip_bio:
                    bio = {"Sex": None, "Height": None, "Weight": None, "Born": None}
                else:
                    if ath_path not in bio_cache:
                        try:
                            bio_cache[ath_path] = parse_athlete_bio(fetcher.get(ath_path))
                        except Exception as exc:
                            # A persistent fetch failure on ONE athlete page (e.g. an
                            # olympedia slow window that exhausts get()'s retries) must
                            # not kill a multi-hour run. Log it, cache an empty bio so we
                            # don't re-hit it this run, and carry on.
                            print(f"     [bio-skip] {ath_path}: {type(exc).__name__}; leaving bio empty", flush=True)
                            bio_cache[ath_path] = {"Sex": None, "Height": None, "Weight": None, "Born": None}
                    bio = bio_cache[ath_path]

                rows.append(AthleteRow(
                    ID=next_id,
                    Name=r.get("Athlete", "").strip(),
                    Sex=bio.get("Sex") or _infer_sex_from_event(event_name),
                    Age=age_at_games(bio.get("Born"), meta.year, meta.season),
                    Height=bio.get("Height"),
                    Weight=bio.get("Weight"),
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

        # Per-sport partial checkpoint: rewrite the partial CSV with all rows
        # gathered so far across the edition. Cheap (a few MB at most) and lets
        # a crash mid-edition recover everything up to the last completed sport.
        if partial_path is not None:
            write_csv(rows, partial_path)
            print(f"     [partial] {sport_name} done -> {partial_path} ({len(rows)} rows)", flush=True)

    return rows, next_id


def load_edition_metadata(path: Path) -> dict[int, EditionMeta]:
    """Read data/edition_metadata.csv and return {edition_id: EditionMeta}."""
    out: dict[int, EditionMeta] = {}
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ed = int(row["edition_id"])
            except (KeyError, ValueError):
                continue
            out[ed] = EditionMeta(
                edition_id=ed,
                year=int(row["year"]),
                season=row["season"].strip(),
                city=row["city"].strip(),
            )
    return out


def _infer_sex_from_event(event_name: str) -> str:
    name = event_name.lower()
    if "women" in name or "girls" in name:
        return "F"
    return "M"  # Olympedia mixed events are rare; defaulting to M matches rgriff23


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--editions", type=int, nargs="+", required=True,
                   help="Olympedia edition IDs to scrape (e.g. 61 62 63 64 72)")
    p.add_argument("--out", type=Path, required=True,
                   help="Output CSV path")
    p.add_argument("--checkpoint-dir", type=Path, default=Path("./checkpoints"),
                   help="Directory for per-edition CSV checkpoints")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help="Seconds between HTTP requests (be polite)")
    p.add_argument("--starting-id", type=int, default=1_000_000,
                   help="Starting ID for new athlete-event rows (use 1000000+ "
                        "to avoid collision with original 1..135571 IDs)")
    p.add_argument("--team-events-only", action="store_true",
                   help="Only fetch sports in TEAM_SPORTS (Football, Ice Hockey, Curling, ...) "
                        "to backfill team rosters missed by the individual-results parser.")
    p.add_argument("--skip-bio", action="store_true",
                   help="Skip athlete bio fetches (Sex inferred from event name; Age/Height/Weight=NA). "
                        "Useful for fast team-roster refetches where bios are merged in later.")
    p.add_argument("--metadata", type=Path,
                   default=Path(__file__).resolve().parent.parent / "data" / "edition_metadata.csv",
                   help="CSV of verified edition year/season/city. When present, used in "
                        "preference to parsing the edition page header.")
    args = p.parse_args()

    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    meta_by_id = load_edition_metadata(args.metadata) if args.metadata and args.metadata.exists() else {}
    if meta_by_id:
        print(f"Loaded verified metadata for editions: {sorted(meta_by_id)}")
    fetcher = Fetcher(delay=args.delay)
    bio_cache: dict = {}
    all_rows: list[AthleteRow] = []
    next_id = args.starting_id

    for ed in args.editions:
        print(f"\n=== Edition {ed} ===")
        partial = args.checkpoint_dir / f"edition_{ed}_partial.csv"
        rows, next_id = scrape_edition(fetcher, ed, next_id, bio_cache,
                                       meta_override=meta_by_id.get(ed),
                                       partial_path=partial,
                                       team_events_only=args.team_events_only,
                                       skip_bio=args.skip_bio)
        all_rows.extend(rows)
        # Final per-edition checkpoint
        ckpt = args.checkpoint_dir / f"edition_{ed}.csv"
        write_csv(rows, ckpt)
        print(f"  checkpoint -> {ckpt} ({len(rows)} rows)")
        # Clean up partial once the final is written
        if partial.exists():
            partial.unlink()

    write_csv(all_rows, args.out)
    print(f"\nWrote {len(all_rows)} rows to {args.out}")
    return 0


COLUMNS = ["ID", "Name", "Sex", "Age", "Height", "Weight", "Team", "NOC",
           "Games", "Year", "Season", "City", "Sport", "Event", "Medal"]


def write_csv(rows: list[AthleteRow], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            d = asdict(r)
            # Match the original NA convention for missing values
            for k in ("Age", "Height", "Weight", "Medal"):
                if d[k] is None:
                    d[k] = "NA"
            w.writerow(d)


if __name__ == "__main__":
    sys.exit(main())
