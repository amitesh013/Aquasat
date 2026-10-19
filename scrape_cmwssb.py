"""Safely collect date-verified CMWSSB reservoir storage observations.

Uses only the standard library so parsing and merge safety tests are offline.
The website's live form is inspected before every request; unknown forms or
table schemas are rejected instead of guessed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit, parse_qsl
from urllib.request import Request, urlopen

URL = "https://cmwssb.tn.gov.in/lake-level"
CAPACITY = {"poondi": 3231, "cholavaram": 1081, "puzhal": 3300,
            "chembarambakkam": 3645, "veeranam": 1465}
ALIASES = {"poondi": {"poondi"}, "cholavaram": {"cholavaram"},
           "puzhal": {"puzhal", "red hills", "redhills"},
           "chembarambakkam": {"chembarambakkam"}, "veeranam": {"veeranam"}}
MISSING = {"", "-", "—", "–", "na", "n/a", "null", "nil"}


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms, self.tables = [], []
        self._form = None
        self._table = None
        self._row = None
        self._cell = None
        self.texts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._form = {"action": a.get("action", ""), "method": a.get("method", "get").lower(), "inputs": []}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None:
            self._form["inputs"].append(a)
        elif tag == "table":
            self._table = []
            self.tables.append(self._table)
        elif tag == "tr" and self._table is not None:
            self._row = []
            self._table.append(self._row)
        elif tag in ("th", "td") and self._row is not None:
            self._cell = {"tag": tag, "text": ""}
            self._row.append(self._cell)

    def handle_data(self, data):
        val = data.strip()
        if val:
            self.texts.append(val)
        if self._cell is not None:
            self._cell["text"] += data

    def handle_endtag(self, tag):
        if tag in ("th", "td"):
            self._cell = None
        elif tag == "tr":
            self._row = None
        elif tag == "table":
            self._table = None
        elif tag == "form":
            self._form = None


class ScrapeError(ValueError):
    pass


def parse_date(value: str) -> date:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ScrapeError(f"Unrecognized date: {value!r}")


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def numeric_storage(value: str, reservoir: str) -> float | None:
    raw = value.strip().replace(",", "")
    if raw.casefold() in MISSING:
        return None
    try:
        number = float(raw)
    except ValueError as exc:
        raise ScrapeError(f"Invalid storage for {reservoir}: {value!r}") from exc
    if number < 0 or number > CAPACITY[reservoir]:
        raise ScrapeError(f"Storage {number:g} mcft for {reservoir} outside 0..{CAPACITY[reservoir]}")
    return number


def parse_response(html: str, requested: date) -> dict:
    parser = PageParser()
    parser.feed(html)
    date_matches = []
    joined = " ".join(parser.texts)
    for match in re.finditer(r"Lake\s+Storage\s+As\s+On\s*[-:]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})", joined, re.I):
        date_matches.append(parse_date(match.group(1).replace("/", "-")))
    if len(set(date_matches)) != 1 or date_matches[0] != requested:
        raise ScrapeError(f"Returned observation date {date_matches or 'unrecognized'} does not match requested {requested.isoformat()}")
    for table in parser.tables:
        headers = [normalize_header(c["text"]) for row in table for c in row if c["tag"] == "th"]
        # Use the actual Storage (mcft) heading; the previous-year column has
        # a distinct, longer heading and can never be selected by position.
        matches = [i for i, h in enumerate(headers) if h == "storage (mcft)"]
        if len(matches) != 1:
            continue
        header_row = next((row for row in table if any(c["tag"] == "th" for c in row)), None)
        if header_row is None:
            continue
        headings = [normalize_header(c["text"]) for c in header_row]
        storage_indices = [i for i, h in enumerate(headings) if h == "storage (mcft)"]
        if len(storage_indices) != 1:
            continue
        idx = storage_indices[0]
        found = {}
        for row in table:
            cells = [re.sub(r"\s+", " ", c["text"]).strip() for c in row if c["tag"] == "td"]
            if not cells:
                continue
            label = cells[0].strip().casefold()
            key = next((r for r, names in ALIASES.items() if label in names), None)
            if key:
                if len(cells) <= idx:
                    raise ScrapeError(f"Row for {key} has no current storage cell")
                if key in found:
                    raise ScrapeError(f"Duplicate reservoir row: {key}")
                found[key] = numeric_storage(cells[idx], key)
        if not found:
            raise ScrapeError("Expected storage table found but no recognized reservoir rows")
        return {"date": requested.isoformat(), "values": found, "missing_reservoirs": sorted(set(CAPACITY) - set(found))}
    raise ScrapeError("Expected unique 'Storage (mcft)' header not found")


def discover_form(html: str) -> tuple[str, str, str]:
    parser = PageParser()
    parser.feed(html)
    candidates = []
    for form in parser.forms:
        dates = [i for i in form["inputs"] if "date" in (i.get("name", "") + " " + i.get("placeholder", "")).casefold()]
        if len(dates) == 1 and dates[0].get("name"):
            candidates.append((form, dates[0]))
    if len(candidates) != 1:
        raise ScrapeError("Could not identify exactly one named date field in the live form")
    form, field = candidates[0]
    method = form["method"].upper()
    if method not in {"GET", "POST"}:
        raise ScrapeError(f"Unsupported live form method: {method}")
    return method, form["action"], field["name"]


def _request(url: str, method: str = "GET", data: dict | None = None, timeout: int = 20) -> str:
    payload = urlencode(data).encode() if data and method.upper() == "POST" else None
    if data and method.upper() == "GET":
        parts = urlsplit(url)
        query = urlencode(parse_qsl(parts.query, keep_blank_values=True) + list(data.items()))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    req = Request(url, data=payload, method=method, headers={"User-Agent": "AquaSat research data collector/1.0"})
    with urlopen(req, timeout=timeout) as response:
        if response.status != 200:
            raise ScrapeError(f"HTTP status {response.status}")
        return response.read().decode("utf-8", errors="replace")


def fetch_day(day: date, opener=_request, retries: int = 2) -> dict:
    last = None
    for attempt in range(retries + 1):
        try:
            html = opener(URL)
            method, action, field = discover_form(html)
            target = urljoin(URL, action or URL)
            # The page advertises dd-mm-yyyy; keep ISO internally.
            form_date = day.strftime("%d-%m-%Y")
            response = opener(target, method, {field: form_date})
            return parse_response(response, day)
        except (HTTPError, URLError, TimeoutError, OSError, ScrapeError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise ScrapeError(str(last))


def read_records(path: Path) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def merge_records(base: list[dict], scraped: Iterable[dict], fields: list[str] | None = None) -> tuple[list[dict], list[dict], dict]:
    fields = fields or ["date"] + list(CAPACITY)
    date_col = next((c for c in fields if c.casefold() in {"date", "date_str", "observation_date"}), None)
    if date_col is None:
        raise ScrapeError("Base CSV has no recognizable date column")
    out = [dict(r) for r in base]
    by_date = {}
    for i, row in enumerate(out):
        try:
            key = parse_date(row.get(date_col, "")).isoformat()
        except ScrapeError:
            continue
        by_date.setdefault(key, i)
    conflicts, counts = [], {"rows_added": 0, "values_added": 0, "existing_skipped": 0, "auto_filled_values": []}
    for record in scraped:
        key = parse_date(str(record["date"])).isoformat()
        values = record.get("values", record)
        idx = by_date.get(key)
        if idx is None:
            new = {f: "" for f in fields}
            new[date_col] = key
            for reservoir in CAPACITY:
                if reservoir in fields and values.get(reservoir) is not None:
                    new[reservoir] = str(values[reservoir])
                    counts["auto_filled_values"].append({"date": key, "reservoir": reservoir, "value": values[reservoir], "source": "CMWSSB lake-level page"})
            out.append(new)
            by_date[key] = len(out) - 1
            counts["rows_added"] += 1
            counts["values_added"] += sum(values.get(r) is not None for r in CAPACITY if r in fields)
            continue
        row = out[idx]
        for reservoir in CAPACITY:
            incoming = values.get(reservoir)
            if incoming is None or reservoir not in fields:
                continue
            existing = (row.get(reservoir) or "").strip()
            if existing:
                try:
                    old_value = float(existing.replace(",", ""))
                except ValueError:
                    old_value = existing
                if old_value == incoming:
                    counts["existing_skipped"] += 1
                else:
                    conflicts.append({"date": key, "reservoir": reservoir, "manual_value": existing, "scraped_value": incoming})
            else:
                row[reservoir] = str(incoming)
                counts["values_added"] += 1
                counts["auto_filled_values"].append({"date": key, "reservoir": reservoir, "value": incoming, "source": "CMWSSB lake-level page"})
    return out, conflicts, counts


def atomic_write(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8", dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        f.flush()
    tmp.replace(path)


def run(args) -> int:
    dates_file = getattr(args, "dates_file", None)
    if dates_file:
        try:
            date_lines = Path(dates_file).read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise ScrapeError(f"Could not read --dates-file: {exc}") from exc
        days, seen = [], set()
        for line_number, line in enumerate(date_lines, start=1):
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            try:
                parsed = parse_date(value)
            except ScrapeError as exc:
                raise ScrapeError(f"Invalid date on line {line_number} of {dates_file}: {exc}") from exc
            if parsed not in seen:
                days.append(parsed)
                seen.add(parsed)
        if not days:
            raise ScrapeError("--dates-file contains no dates")
    else:
        start, end = parse_date(args.from_date), parse_date(args.to_date)
        if start > end:
            raise ScrapeError("--from must be on or before --to")
        if args.interval < 1:
            raise ScrapeError("--interval must be a positive integer")
        days = []
        day = start
        while day <= end:
            days.append(day)
            day += timedelta(days=args.interval)
    retrieved, rejected = [], []
    for i, day in enumerate(days):
        try:
            retrieved.append(fetch_day(day))
        except ScrapeError as exc:
            rejected.append({"date": day.isoformat(), "reason": str(exc)})
        if i < len(days) - 1:
            time.sleep(max(args.spacing, 0))
    output = Path(args.output)
    base_path = Path(args.base) if args.base else None
    if base_path:
        base, fields = read_records(base_path)
        if not fields:
            raise ScrapeError("Base CSV is missing or has no header")
        merged, conflicts, counts = merge_records(base, retrieved, fields)
    else:
        fields = ["date"] + list(CAPACITY) + ["source"]
        merged = [{"date": x["date"], **{k: x["values"].get(k, "") for k in CAPACITY},
                   "source": "CMWSSB lake-level page"} for x in retrieved]
        conflicts, counts = [], {"rows_added": len(merged), "values_added": sum(len(x["values"]) for x in retrieved), "existing_skipped": 0, "auto_filled_values": []}
    report = {"requested_dates": [d.isoformat() for d in days], "retrieved_dates": [x["date"] for x in retrieved],
              "rejected": rejected, "missing_values_by_date": {x["date"]: x["missing_reservoirs"] for x in retrieved},
              "accepted_values_by_reservoir": {r: sum(x["values"].get(r) is not None for x in retrieved) for r in CAPACITY},
              "conflicts_for_review": conflicts, **counts, "dry_run": args.dry_run}
    print(json.dumps(report, indent=2))
    if not retrieved:
        # A wholly unsuccessful run must not replace either a prior scrape or
        # the user's base CSV with an empty file.
        return 2
    if not args.dry_run:
        atomic_write(output, merged, fields)
        report_path = output.with_suffix(".report.json")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date", default=(date.today() - timedelta(days=7)).isoformat(), help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", default=date.today().isoformat(), help="End date (YYYY-MM-DD)")
    parser.add_argument("--interval", type=int, default=1, help="Days between requested dates")
    parser.add_argument("--dates-file", help="Optional file with one requested date per line (ISO YYYY-MM-DD recommended); blank lines and # comments are ignored")
    parser.add_argument("--output", default="cmwssb_scraped_observations.csv")
    parser.add_argument("--base", help="Optional existing wide CSV; manual non-empty values are retained")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--spacing", type=float, default=1.0, help="Seconds between dates")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except ScrapeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
