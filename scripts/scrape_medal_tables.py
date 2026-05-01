"""
scrape_medal_tables.py
======================
Scrape per-edition medal tables from olympedia.org for every Summer/Winter
Olympic Games from Athens 1896 through Milano-Cortina 2026 (excluding the
five cancelled editions). Output schema extends the existing
data/medal_table_summary.csv with `edition_id` and `notes` columns:

    edition_id, games, year, season, noc, country,
    gold, silver, bronze, total, notes

The `notes` column flags rows whose NOC or edition has a known caveat
worth surfacing: composite/neutral teams (MIX, EUA, EUN, OAR, ROC, AIN,
IOA, IOP, EOR, ZZX), defunct-state NOCs (URS, TCH, YUG, GDR, FRG, BOH,
ANZ, BWI, UAR, SAR, RU1, RU2, NBO), and edition-level oddities (1906
Intercalated Games, 1956 Stockholm Equestrian sub-edition). Rows for
ordinary current NOCs have an empty `notes`.

USAGE
-----
    python scripts/scrape_medal_tables.py \\
        --out data/medal_table_summary.csv --delay 4.0
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
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


# ---------------------------------------------------------------------------
# Edition inventory (excludes the 5 cancelled editions; identical to the
# held subset of EDITIONS in scrape_edition_metadata.py)
# ---------------------------------------------------------------------------
HELD_EDITIONS: list[tuple[int, str]] = [
    # Summer
    (1, "Summer"), (2, "Summer"), (3, "Summer"), (4, "Summer"),  # 1906 Intercalated
    (5, "Summer"), (6, "Summer"), (7, "Summer"), (8, "Summer"),
    (9, "Summer"), (10, "Summer"), (11, "Summer"),
    (12, "Summer"), (13, "Summer"),
    (14, "Summer"), (48, "Summer"),  # 1956 Melbourne + 1956 Stockholm Equestrian
    (15, "Summer"), (16, "Summer"), (17, "Summer"), (18, "Summer"),
    (19, "Summer"), (20, "Summer"), (21, "Summer"), (22, "Summer"),
    (23, "Summer"), (24, "Summer"), (25, "Summer"), (26, "Summer"),
    (53, "Summer"), (54, "Summer"), (59, "Summer"),
    (61, "Summer"), (63, "Summer"),
    # Winter
    (29, "Winter"), (30, "Winter"), (31, "Winter"), (32, "Winter"),
    (33, "Winter"), (34, "Winter"), (35, "Winter"), (36, "Winter"),
    (37, "Winter"), (38, "Winter"), (39, "Winter"), (40, "Winter"),
    (41, "Winter"), (42, "Winter"), (43, "Winter"), (44, "Winter"),
    (45, "Winter"), (46, "Winter"), (47, "Winter"), (49, "Winter"),
    (57, "Winter"), (58, "Winter"), (60, "Winter"), (62, "Winter"),
    (72, "Winter"),
]

# Per-edition deviation prefix. Applies to every row of the named edition.
EDITION_NOTES: dict[int, str] = {
    4:  "1906 Intercalated Games — not officially recognised by the IOC; medal counts here are olympedia's reckoning, included for parity with athlete_events.csv",
    48: "1956 Stockholm Equestrian sub-edition — held separately due to Australian horse-quarantine; counts are independent of the Melbourne 1956 medal table",
}

# Per-(edition_id, noc) override note, applied IN ADDITION to any per-edition
# or per-NOC note. Use for cases where olympedia's current code disagrees
# with the historical designation actually used at the Games — e.g. the 2018
# PyeongChang delegation entered as Olympic Athletes from Russia (OAR) but
# olympedia retroactively re-codes them as ROC, matching how athlete_events
# now records them.
EDITION_NOC_NOTES: dict[tuple[int, str], str] = {
    (60, "ROC"): "At the 2018 PyeongChang Games this delegation was officially designated 'Olympic Athletes from Russia' (OAR); olympedia (and athlete_events_through_2026.csv) now use the ROC code for these athletes",
}

# NOC-level deviation note. Applies to any row with the given NOC code.
NOC_NOTES: dict[str, str] = {
    # Composite / mixed-NOC teams used in early Games (1896-1912).
    "MIX": "Mixed-NOC team (athletes from multiple countries entered as a combined team in early Games)",
    "ZZX": "Mixed-NOC team (legacy olympedia code for combined entries)",
    "ANZ": "Australasia (combined Australia + New Zealand team; 1908 and 1912 only)",
    "BWI": "British West Indies (federation team; 1960 only)",
    "UAR": "United Arab Republic (Egypt + Syria union; 1960 and 1964 only)",
    # Unified / political-boundary teams.
    "EUA": "Unified Team of Germany (combined East and West German athletes; 1956 Summer through 1964)",
    "EUN": "Unified Team (post-Soviet republics competing together at 1992 Summer & Winter as the Olympic Committee of the CIS)",
    "IOP": "Independent Olympic Participants (athletes from FR Yugoslavia barred from team competition under UN sanctions; 1992 Summer)",
    "IOA": "Independent Olympic Athletes (special-status individual entries; 1992, 2000, 2014)",
    # Special-status Russian / Belarusian designations after sanctions.
    "OAR": "Olympic Athletes from Russia (Russian athletes competing as neutrals after WADA/IOC doping sanctions; 2018 Winter — note that olympedia has retroactively re-coded this delegation as ROC)",
    "ROC": "Russian Olympic Committee (Russian athletes competing under ROC banner; used by olympedia for 2020 Summer, 2022 Winter, and — retroactively — the 2018 Winter delegation originally entered as OAR)",
    "AIN": "Individual Neutral Athletes (Russian and Belarusian athletes competing as neutrals; 2024 Summer & 2026 Winter)",
    # Refugee teams (debuted 2016).
    "EOR": "Refugee Olympic Team",
    "ROT": "Refugee Olympic Team",
    # Defunct states whose medals predate the modern NOC structure.
    "URS": "Soviet Union (defunct; succeeded by component republics' NOCs after 1991)",
    "TCH": "Czechoslovakia (defunct; succeeded by Czech Republic and Slovakia in 1993)",
    "YUG": "Yugoslavia / SFR Yugoslavia (defunct; succeeded by component republics' NOCs in the 1990s)",
    "GDR": "East Germany (defunct; reunified with West Germany as Germany in 1990)",
    "FRG": "West Germany (1968-1988; merged with East Germany as Germany after reunification)",
    "BOH": "Bohemia (1900, 1908, 1912; later absorbed into Czechoslovakia)",
    "RU1": "Russian Empire (pre-1917)",
    "RU2": "Russia (1908, 1912 second entry; legacy olympedia code)",
    "SAR": "Saar (1952 only; territory subsequently rejoined West Germany)",
    "NBO": "North Borneo (1956 only; later part of Malaysia)",
    "TWN": "Republic of China / Taiwan (legacy code; competes as Chinese Taipei (TPE) since 1984)",
    "ROC1": "Republic of China (mainland China pre-1949; superseded by separate PRC and Chinese Taipei NOCs)",
}


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

@dataclass
class MedalRow:
    edition_id: int
    games: str
    year: int
    season: str
    noc: str
    country: str
    gold: int
    silver: int
    bronze: int
    total: int
    notes: str


_MEDAL_HEADERS = ["NOC", "Gold", "Silver", "Bronze", "Total"]


def parse_year_from_h1(soup: BeautifulSoup) -> int:
    h1 = soup.find("h1")
    if not h1:
        raise ValueError("no <h1>")
    m = re.match(r"(\d{4})", h1.get_text(strip=True))
    if not m:
        raise ValueError(f"could not parse year from h1: {h1.get_text(strip=True)!r}")
    return int(m.group(1))


def find_medal_table(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
    """
    Olympedia renders the per-edition medal table as the FIRST table on the
    page whose <thead> headers are exactly ['NOC','Gold','Silver','Bronze','Total'].
    A second table with the same headers sometimes follows for Mixed-NOC team
    breakdowns (1896, 1900, 1908, 1912); we only want the top-level one.
    """
    for table in soup.select("table"):
        headers = [th.get_text(strip=True) for th in table.select("thead th")]
        if headers == _MEDAL_HEADERS:
            return table
    return None


def parse_medal_table(table: BeautifulSoup) -> list[tuple[str, str, int, int, int, int]]:
    """Return list of (country, noc, gold, silver, bronze, total)."""
    out: list[tuple[str, str, int, int, int, int]] = []
    # Rows that aren't direct <thead>/<tfoot> descendants are data rows.
    for tr in table.select("tr"):
        if tr.find_parent("thead") is not None:
            continue
        cells = tr.find_all(["td", "th"])
        if len(cells) != 6:
            continue
        country = cells[0].get_text(" ", strip=True)
        # Cell 1 is "<flag-img> NOC". The visible text is the NOC code; strip
        # any leading/trailing whitespace introduced by the flag image.
        noc = cells[1].get_text(" ", strip=True).strip()
        # Defensive: olympedia occasionally renders the NOC cell as
        # "<NOC code> <something>"; the 3-letter token is at the end after
        # the flag, so take the last whitespace-separated token if needed.
        if " " in noc:
            noc = noc.split()[-1]
        try:
            g = int(cells[2].get_text(strip=True))
            s = int(cells[3].get_text(strip=True))
            b = int(cells[4].get_text(strip=True))
            t = int(cells[5].get_text(strip=True))
        except ValueError:
            # tfoot Total row or similar — skip.
            continue
        out.append((country, noc, g, s, b, t))
    return out


def build_notes(noc: str, edition_id: int) -> str:
    parts: list[str] = []
    if edition_id in EDITION_NOTES:
        parts.append(EDITION_NOTES[edition_id])
    if noc in NOC_NOTES:
        parts.append(NOC_NOTES[noc])
    if (edition_id, noc) in EDITION_NOC_NOTES:
        parts.append(EDITION_NOC_NOTES[(edition_id, noc)])
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

OUTPUT_COLUMNS = [
    "edition_id", "games", "year", "season", "noc", "country",
    "gold", "silver", "bronze", "total", "notes",
]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/medal_table_summary.csv",
                    help="Output CSV path (default: %(default)s)")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help="Seconds between requests (default: %(default)s; do not lower below 4.0)")
    ap.add_argument("--editions", type=int, nargs="*", default=None,
                    help="Optional subset of edition_ids (default: all 57 held editions)")
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fetcher = Fetcher(delay=args.delay)
    targets = HELD_EDITIONS
    if args.editions:
        wanted = set(args.editions)
        targets = [e for e in HELD_EDITIONS if e[0] in wanted]
        missing = wanted - {e[0] for e in HELD_EDITIONS}
        if missing:
            print(f"WARN: skipping unknown/cancelled edition_ids: {sorted(missing)}", flush=True)

    rows: list[MedalRow] = []
    flagged = 0
    for i, (eid, season) in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] /editions/{eid} ({season}) ...", flush=True)
        soup = fetcher.get(f"/editions/{eid}")
        year = parse_year_from_h1(soup)
        table = find_medal_table(soup)
        if table is None:
            print(f"  WARN: no medal table on edition {eid}", flush=True)
            continue
        parsed = parse_medal_table(table)
        if not parsed:
            print(f"  WARN: empty medal table on edition {eid}", flush=True)
            continue
        for country, noc, g, s, b, t in parsed:
            note = build_notes(noc, eid)
            if note:
                flagged += 1
            rows.append(MedalRow(
                edition_id=eid,
                games=f"{year} {season}",
                year=year,
                season=season,
                noc=noc,
                country=country,
                gold=g, silver=s, bronze=b, total=t,
                notes=note,
            ))
        print(f"    -> {year} {season}: {len(parsed)} NOCs", flush=True)

    rows.sort(key=lambda r: (r.year, r.season, -r.gold, -r.silver, -r.bronze, r.country))

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        for r in rows:
            writer.writerow([
                r.edition_id, r.games, r.year, r.season, r.noc, r.country,
                r.gold, r.silver, r.bronze, r.total, r.notes,
            ])

    print(f"\nWrote {len(rows)} rows to {out_path}", flush=True)
    print(f"Rows with deviation notes: {flagged}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
