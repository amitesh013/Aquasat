"""Download date-stamped Veeranam storage from CMWSSB yearly archive pages.

The archive extract is written to a separate review CSV. It does not alter the
project's ground-truth or dashboard files. Blank and malformed source rows are
reported and omitted; zero storage is retained as a numeric value.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CAPACITY_MCFT = 1465.0
SOURCE = "CMWSSB Veeranam annual statistics page"
BASE_URL = "https://cmwssb.tn.gov.in/statistics/veeranam%20{year2}.htm"
DATE_FORMATS = ("%d.%m.%y", "%d/%m/%y", "%d-%m-%y")


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._table = []
            self.tables.append(self._table)
        elif tag == "tr" and self._table is not None:
            self._row = []
            self._table.append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._row.append("")
            self._in_cell = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._in_cell = False
        elif tag == "tr":
            self._row = None
        elif tag == "table":
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._row:
            self._row[-1] += data


def parse_day(raw: str, expected_year: int) -> date | None:
    raw = re.sub(r"\s+", " ", raw).strip()
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt).date()
            if parsed.year == expected_year:
                return parsed
        except ValueError:
            continue
    return None


def parse_archive(html: str, year: int, url: str) -> tuple[list[dict], dict]:
    parser = TableParser()
    parser.feed(html)
    date_rows = []
    for table in parser.tables:
        for row in table:
            cells = [re.sub(r"\s+", " ", cell).strip() for cell in row]
            if cells and parse_day(cells[0], year):
                date_rows.append(cells)

    if not date_rows:
        raise ValueError(f"No dated Veeranam storage rows found on {url}")

    records: dict[str, dict] = {}
    report = {"date_rows": 0, "accepted": 0, "missing_storage": 0,
              "invalid_rows": [], "duplicate_rows": []}
    for cells in date_rows:
        day = parse_day(cells[0], year)
        assert day is not None
        report["date_rows"] += 1
        raw_storage = cells[1].strip() if len(cells) > 1 else ""
        if not raw_storage or raw_storage.casefold() in {"-", "—", "–", "na", "n/a"}:
            report["missing_storage"] += 1
            continue
        try:
            value = float(raw_storage.replace(",", ""))
        except ValueError:
            report["invalid_rows"].append({"date": day.isoformat(), "storage": raw_storage})
            continue
        if not 0 <= value <= CAPACITY_MCFT:
            report["invalid_rows"].append({"date": day.isoformat(), "storage": raw_storage,
                                           "reason": "outside 0..1465 mcft"})
            continue
        record = {"date": day.isoformat(), "reservoir": "veeranam",
                  "official_mcft": value, "source": SOURCE, "source_url": url}
        key = day.isoformat()
        if key in records:
            if records[key]["official_mcft"] != value:
                report["duplicate_rows"].append({"date": key,
                    "first_value": records[key]["official_mcft"], "later_value": value})
            continue
        records[key] = record
    report["accepted"] = len(records)
    return [records[k] for k in sorted(records)], report


def fetch(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": "AquaSat research data collector/1.0"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f"HTTP status {response.status} for {url}")
        return response.read().decode("utf-8", errors="replace")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2022)
    parser.add_argument("--output", default="veeranam_cmwssb_archive_2016_2022.csv")
    parser.add_argument("--spacing", type=float, default=1.0,
                        help="Seconds between archive page requests")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the review report without writing the CSV")
    args = parser.parse_args(argv)
    if args.start_year > args.end_year or args.start_year < 2000 or args.end_year > 2099:
        parser.error("years must be an increasing range between 2000 and 2099")

    all_rows, per_year, failures = [], {}, []
    years = list(range(args.start_year, args.end_year + 1))
    for index, year in enumerate(years):
        url = BASE_URL.format(year2=f"{year % 100:02d}")
        try:
            rows, quality = parse_archive(fetch(url), year, url)
            all_rows.extend(rows)
            per_year[str(year)] = quality
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            failures.append({"year": year, "url": url, "reason": str(exc)})
        if index < len(years) - 1:
            time.sleep(max(0.0, args.spacing))

    all_rows.sort(key=lambda row: row["date"])
    report = {"years_requested": years, "rows_written": len(all_rows),
              "per_year": per_year, "page_failures": failures,
              "dry_run": args.dry_run}
    print(json.dumps(report, indent=2))
    if all_rows and not args.dry_run:
        output = Path(args.output)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "reservoir", "official_mcft",
                                                        "source", "source_url"])
            writer.writeheader()
            writer.writerows(all_rows)
        output.with_suffix(".report.json").write_text(json.dumps(report, indent=2),
                                                       encoding="utf-8")
    return 0 if all_rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
