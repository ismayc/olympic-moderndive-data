"""
scrape_edition_metadata.py
==========================
Scrape per-edition metadata from olympedia.org for every Summer/Winter
Olympic Games from Athens 1896 through Milano-Cortina 2026, including the
five cancelled editions (1916, 1940 S/W, 1944 S/W) and the 1906 Athens
Intercalated Games. Output schema matches the existing
data/edition_metadata.csv (14 columns).

USAGE
-----
    python scripts/scrape_edition_metadata.py \\
        --out data/edition_metadata.csv --delay 4.0

The script preserves any non-empty `notes` already present in --out for
edition_ids it overwrites, so hand-curated notes survive a re-scrape.

NOTES
-----
* Edition IDs are hardcoded below from olympedia's /editions index page.
* Olympedia rate-limits aggressively below ~3-4s; keep --delay >= 4.0.
* Cancelled editions have no opening/closing/participant data; those
  fields are emitted as empty (NA) and a default note is attached.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE = "https://www.olympedia.org"
DEFAULT_DELAY = 4.0
USER_AGENT = (
    "OlympicHistoryUpdater/1.0 "
    "(personal research; contact: chester.ismay@gmail.com)"
)
MAX_RETRIES = 5
BACKOFF_BASE = 30.0


# Edition inventory: (edition_id, season_label, cancelled, default_note)
# season_label is what we emit in the `season` column. It matches the
# original athlete_events.csv convention: 1906 Intercalated and the 1956
# Stockholm Equestrian sub-edition are both labelled "Summer" because that
# is how rgriff23/Olympic_history (and athlete_events_through_2026.csv)
# tag those rows.
EDITIONS: list[tuple[int, str, bool, str]] = [
    # Summer Games (chronological, including cancelled and 1906)
    (1,  "Summer", False, ""),  # 1896 Athina
    (2,  "Summer", False, ""),  # 1900 Paris
    (3,  "Summer", False, ""),  # 1904 St. Louis
    (4,  "Summer", False, "Intercalated Games; not officially recognised by the IOC, but included here to match the original athlete_events.csv"),  # 1906 Athina
    (5,  "Summer", False, ""),  # 1908 London
    (6,  "Summer", False, ""),  # 1912 Stockholm
    (50, "Summer", True,  "Cancelled due to World War I"),  # 1916 Berlin (planned)
    (7,  "Summer", False, ""),  # 1920 Antwerpen
    (8,  "Summer", False, ""),  # 1924 Paris
    (9,  "Summer", False, ""),  # 1928 Amsterdam
    (10, "Summer", False, ""),  # 1932 Los Angeles
    (11, "Summer", False, ""),  # 1936 Berlin
    (51, "Summer", True,  "Cancelled due to World War II"),  # 1940 Helsinki (planned; originally Tokyo)
    (52, "Summer", True,  "Cancelled due to World War II"),  # 1944 London (planned)
    (12, "Summer", False, ""),  # 1948 London
    (13, "Summer", False, ""),  # 1952 Helsinki
    (14, "Summer", False, ""),  # 1956 Melbourne
    (48, "Summer", False, "Equestrian events held in Stockholm due to Australian horse-quarantine law; treated as a separate edition by olympedia but as part of 1956 Summer in athlete_events.csv"),  # 1956 Stockholm Equestrian
    (15, "Summer", False, ""),  # 1960 Roma
    (16, "Summer", False, ""),  # 1964 Tokyo
    (17, "Summer", False, ""),  # 1968 Mexico City
    (18, "Summer", False, ""),  # 1972 München
    (19, "Summer", False, ""),  # 1976 Montréal
    (20, "Summer", False, ""),  # 1980 Moskva
    (21, "Summer", False, ""),  # 1984 Los Angeles
    (22, "Summer", False, ""),  # 1988 Seoul
    (23, "Summer", False, ""),  # 1992 Barcelona
    (24, "Summer", False, ""),  # 1996 Atlanta
    (25, "Summer", False, ""),  # 2000 Sydney
    (26, "Summer", False, ""),  # 2004 Athina
    (53, "Summer", False, ""),  # 2008 Beijing
    (54, "Summer", False, ""),  # 2012 London
    (59, "Summer", False, ""),  # 2016 Rio de Janeiro
    (61, "Summer", False, ""),  # 2020 Tokyo (held 2021)
    (63, "Summer", False, ""),  # 2024 Paris

    # Winter Games (chronological, including cancelled)
    (29, "Winter", False, ""),  # 1924 Chamonix
    (30, "Winter", False, ""),  # 1928 Sankt Moritz
    (31, "Winter", False, ""),  # 1932 Lake Placid
    (32, "Winter", False, ""),  # 1936 Garmisch-Partenkirchen
    (55, "Winter", True,  "Cancelled due to World War II"),  # 1940 Garmisch-Partenkirchen (planned; originally Sapporo)
    (56, "Winter", True,  "Cancelled due to World War II"),  # 1944 Cortina d'Ampezzo (planned)
    (33, "Winter", False, ""),  # 1948 Sankt Moritz
    (34, "Winter", False, ""),  # 1952 Oslo
    (35, "Winter", False, ""),  # 1956 Cortina d'Ampezzo
    (36, "Winter", False, ""),  # 1960 Squaw Valley
    (37, "Winter", False, ""),  # 1964 Innsbruck
    (38, "Winter", False, ""),  # 1968 Grenoble
    (39, "Winter", False, ""),  # 1972 Sapporo
    (40, "Winter", False, ""),  # 1976 Innsbruck
    (41, "Winter", False, ""),  # 1980 Lake Placid
    (42, "Winter", False, ""),  # 1984 Sarajevo
    (43, "Winter", False, ""),  # 1988 Calgary
    (44, "Winter", False, ""),  # 1992 Albertville
    (45, "Winter", False, ""),  # 1994 Lillehammer
    (46, "Winter", False, ""),  # 1998 Nagano
    (47, "Winter", False, ""),  # 2002 Salt Lake City
    (49, "Winter", False, ""),  # 2006 Torino
    (57, "Winter", False, ""),  # 2010 Vancouver
    (58, "Winter", False, ""),  # 2014 Sochi
    (60, "Winter", False, ""),  # 2018 PyeongChang
    (62, "Winter", False, ""),  # 2022 Beijing
    (72, "Winter", False, ""),  # 2026 Milano-Cortina
]


OUTPUT_COLUMNS = [
    "edition_id", "games", "year", "season", "city", "country",
    "opening_ceremony", "closing_ceremony",
    "participants", "nocs", "medal_events", "disciplines",
    "notes", "source",
]


@dataclass
class EditionRow:
    edition_id: int
    games: str
    year: int
    season: str
    city: str
    country: str
    opening_ceremony: str   # YYYY-MM-DD or ""
    closing_ceremony: str
    participants: str       # int as str, or "" for cancelled
    nocs: str
    medal_events: str
    disciplines: str
    notes: str
    source: str


# ---------------------------------------------------------------------------
# HTTP layer (mirrors scrape_olympedia.py for consistency)
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
                resp = self.session.get(url, timeout=30)
            except requests.exceptions.RequestException as exc:
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
# Parsing
# ---------------------------------------------------------------------------

# Months as olympedia writes them (English, full names).
_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}


def _parse_facts_table(soup: BeautifulSoup) -> dict[str, str]:
    """
    Pull the Facts table into a {label: value} dict. Olympedia renders this
    as a two-column <table> where each row is `<th>Label</th><td>Value</td>`
    or `<td>Label</td><td>Value</td>`. We accept both shapes.
    """
    facts: dict[str, str] = {}
    for table in soup.select("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) != 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            if not label:
                continue
            # Strip the "(Venues)" link suffix from Host city; olympedia
            # appends ", (Venues)" or " (Venues)" after the city/country.
            value = re.sub(r"\s*\(\s*Venues\s*\)\s*$", "", value).strip()
            value = re.sub(r"\s*,\s*\(\s*Venues\s*\)\s*$", "", value).strip()
            facts[label] = value
    return facts


def _parse_year_from_h1(h1_text: str) -> int:
    m = re.match(r"(\d{4})", h1_text)
    if not m:
        raise ValueError(f"Could not parse year from h1: {h1_text!r}")
    return int(m.group(1))


def _split_host_city(value: str) -> tuple[str, str]:
    """`"Berlin, Germany"` -> `("Berlin", "Germany")`. Country may be empty."""
    parts = [p.strip() for p in value.split(",", 1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], ""


def _parse_ceremony_date(value: str, fallback_year: int) -> str:
    """
    Olympedia ceremony cells are either "DD Month" (older editions) or
    "DD Month YYYY" (newer ones). Return ISO date or "" if unparseable.
    """
    value = value.strip()
    if not value:
        return ""
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?", value)
    if not m:
        return ""
    day = int(m.group(1))
    month_name = m.group(2)
    year = int(m.group(3)) if m.group(3) else fallback_year
    month = _MONTHS.get(month_name)
    if not month:
        return ""
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return ""


_PARTICIPANTS_RE = re.compile(r"(\d[\d,]*)\s+from\s+(\d+)")
_MEDAL_EVENTS_RE = re.compile(r"(\d[\d,]*)\s+in\s+(\d+)")


def _parse_count_pair(value: str, regex: re.Pattern) -> tuple[str, str]:
    if not value:
        return "", ""
    m = regex.search(value.replace(",", ""))
    if not m:
        return "", ""
    return m.group(1), m.group(2)


def parse_edition_page(
    soup: BeautifulSoup, edition_id: int, season_override: str, cancelled: bool, default_note: str
) -> EditionRow:
    h1 = soup.find("h1")
    if not h1:
        raise ValueError(f"No <h1> on edition {edition_id}")
    h1_text = h1.get_text(" ", strip=True)
    year = _parse_year_from_h1(h1_text)

    facts = _parse_facts_table(soup)
    city, country = _split_host_city(facts.get("Host city", ""))

    # For cancelled editions, opening/closing/participants/medal_events are
    # absent from the Facts table; emit empty strings.
    opening = _parse_ceremony_date(facts.get("Opening ceremony", ""), year)
    closing = _parse_ceremony_date(facts.get("Closing ceremony", ""), year)
    participants, nocs = _parse_count_pair(facts.get("Participants", ""), _PARTICIPANTS_RE)
    medal_events, disciplines = _parse_count_pair(facts.get("Medal events", ""), _MEDAL_EVENTS_RE)

    return EditionRow(
        edition_id=edition_id,
        games=f"{year} {season_override}",
        year=year,
        season=season_override,
        city=city,
        country=country,
        opening_ceremony=opening,
        closing_ceremony=closing,
        participants=participants,
        nocs=nocs,
        medal_events=medal_events,
        disciplines=disciplines,
        notes=default_note,
        source=f"{BASE}/editions/{edition_id}",
    )


# ---------------------------------------------------------------------------
# Existing-notes preservation
# ---------------------------------------------------------------------------

def load_existing_notes(path: Path) -> dict[int, str]:
    """Read any existing edition_metadata.csv to preserve hand-curated notes."""
    if not path.exists():
        return {}
    notes: dict[int, str] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                eid = int(row["edition_id"])
            except (KeyError, ValueError):
                continue
            note = (row.get("notes") or "").strip()
            if note:
                notes[eid] = note
    return notes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/edition_metadata.csv",
                    help="Output CSV path (default: %(default)s)")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help="Seconds between requests (default: %(default)s; do not lower below 4.0)")
    ap.add_argument("--editions", type=int, nargs="*", default=None,
                    help="Optional subset of edition_ids to scrape (default: all)")
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing_notes = load_existing_notes(out_path)
    if existing_notes:
        print(f"Preserving curated notes for {len(existing_notes)} existing edition_id(s).", flush=True)

    fetcher = Fetcher(delay=args.delay)

    targets = EDITIONS
    if args.editions:
        wanted = set(args.editions)
        targets = [e for e in EDITIONS if e[0] in wanted]
        missing = wanted - {e[0] for e in EDITIONS}
        if missing:
            print(f"WARN: skipping unknown edition_ids: {sorted(missing)}", flush=True)

    rows: list[EditionRow] = []
    for i, (eid, season, cancelled, default_note) in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] /editions/{eid} ({season}{', cancelled' if cancelled else ''}) ...", flush=True)
        soup = fetcher.get(f"/editions/{eid}")
        try:
            row = parse_edition_page(soup, eid, season, cancelled, default_note)
        except Exception as exc:
            print(f"  PARSE ERROR on edition {eid}: {exc}", flush=True)
            raise
        # Preserve any pre-existing curated note over the default one.
        if eid in existing_notes:
            row.notes = existing_notes[eid]
        rows.append(row)
        print(f"    -> {row.year} {row.season} {row.city}/{row.country}; "
              f"participants={row.participants or 'NA'} nocs={row.nocs or 'NA'} "
              f"events={row.medal_events or 'NA'}/{row.disciplines or 'NA'}", flush=True)

    rows.sort(key=lambda r: (r.year, r.season, r.edition_id))

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        for r in rows:
            writer.writerow([
                r.edition_id, r.games, r.year, r.season, r.city, r.country,
                r.opening_ceremony, r.closing_ceremony,
                r.participants, r.nocs, r.medal_events, r.disciplines,
                r.notes, r.source,
            ])

    print(f"\nWrote {len(rows)} rows to {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
