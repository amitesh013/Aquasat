import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

from scrape_cmwssb import (
    CAPACITY, ScrapeError, atomic_write, discover_form, merge_records,
    parse_response, read_records,
)


def page(day="16-10-2026", rows=None, header=None):
    header = header or ["RESERVOIR", "Full Tank Level (ft.)", "Full Capacity (mcft)", "Level (ft)",
                        "Storage (mcft)", "Storage Level (%)", "Inflow (cusecs)", "Outflow (cusecs)",
                        "Rainfall (mm)", "Storage as on same day last year (mcft)"]
    rows = rows or [
        ["POONDI", "140", "3231", "126", "379", "11", "-", "8", "6", "3081"],
        ["CHOLAVARAM", "65", "1081", "49", "120", "11", "-", "8", "6", "195"],
        ["RED HILLS", "50", "3300", "43", "1,886", "57", "315", "201", "26", "2980"],
        ["CHEMBARAMBAKKAM", "85", "3645", "75", "1332", "36", "356", "188", "8", "2375"],
        ["VEERANAM", "47", "1465", "43", "539.8", "36", "340", "125", "3", "1002.65"],
    ]
    th = "".join(f"<th>{x}</th>" for x in header)
    trs = "".join("<tr>" + "".join(f"<td>{x}</td>" for x in row) + "</tr>" for row in rows)
    return f"<p>Lake Storage As On - {day}</p><table><tr>{th}</tr>{trs}</table>"


class ScraperTests(unittest.TestCase):
    def test_select_current_storage_column_not_previous_year(self):
        result = parse_response(page(), date(2026, 10, 16))
        self.assertEqual(result["values"]["poondi"], 379)
        self.assertNotEqual(result["values"]["poondi"], 3081)

    def test_all_five_values_and_alias(self):
        result = parse_response(page(), date(2026, 10, 16))
        self.assertEqual(set(result["values"]), set(CAPACITY))
        self.assertEqual(result["values"]["puzhal"], 1886)

    def test_missing_reservoir_reported(self):
        h = page(rows=[["POONDI", "140", "3231", "126", "379", "11", "-", "8", "6", "3081"]])
        self.assertEqual(len(parse_response(h, date(2026, 10, 16))["missing_reservoirs"]), 4)

    def test_unexpected_header_rejected(self):
        h = page(header=["RESERVOIR", "Capacity", "Current", "Previous year"])
        with self.assertRaises(ScrapeError): parse_response(h, date(2026, 10, 16))

    def test_missing_table_rejected(self):
        with self.assertRaises(ScrapeError): parse_response("Lake Storage As On - 16-10-2026", date(2026, 10, 16))

    def test_returned_date_mismatch_rejected(self):
        with self.assertRaises(ScrapeError): parse_response(page(day="15-10-2026"), date(2026, 10, 16))

    def test_form_fields_discovered_and_ambiguous_rejected(self):
        html = '<form method="post" action="/lake-level"><input name="date" placeholder="dd-mm-yyyy"></form>'
        self.assertEqual(discover_form(html), ("POST", "/lake-level", "date"))
        with self.assertRaises(ScrapeError): discover_form("<form><input name='q'></form>")

    def test_http_failures_and_timeouts_are_rejected(self):
        from scrape_cmwssb import fetch_day
        for failure in (TimeoutError("timeout"), URLError("offline"), OSError("network"), HTTPError("url", 503, "unavailable", {}, None)):
            with self.assertRaises(ScrapeError):
                fetch_day(date(2026, 10, 16), opener=lambda *a, _err=failure, **k: (_ for _ in ()).throw(_err), retries=0)

    def test_missing_and_thousands_separator(self):
        rows = [["POONDI", "x", "3231", "x", "1,000", "x", "x", "x", "x", "900"]]
        self.assertEqual(parse_response(page(rows=rows), date(2026, 10, 16))["values"]["poondi"], 1000)
        rows[0][4] = "-"
        self.assertIsNone(parse_response(page(rows=rows), date(2026, 10, 16))["values"]["poondi"])

    def test_capacity_bound_rejected(self):
        rows = [["POONDI", "x", "3231", "x", "4000", "x", "x", "x", "x", "900"]]
        with self.assertRaises(ScrapeError): parse_response(page(rows=rows), date(2026, 10, 16))

    def test_normalized_duplicate_dates_preserve_manual_value_and_merge_missing(self):
        base = [{"date_str": "16/10/2026", "poondi": "379", "cholavaram": "", "puzhal": "", "chembarambakkam": "", "veeranam": "", "notes": "manual"}]
        values = {r: 1.0 for r in CAPACITY}
        merged, conflicts, counts = merge_records(base, [{"date": "2026-10-16", "values": values}], list(base[0]))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["poondi"], "379")
        self.assertEqual(merged[0]["notes"], "manual")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(counts["values_added"], 4)

    def test_new_dates_are_added_once(self):
        base = [{"date": "01/01/2026", **{r: "" for r in CAPACITY}}]
        merged, _, counts = merge_records(base, [{"date": "2026-01-02", "values": {"poondi": 5}}], ["date", *CAPACITY])
        self.assertEqual(len(merged), 2)
        self.assertEqual(counts["rows_added"], 1)
        self.assertEqual(counts["auto_filled_values"][0]["source"], "CMWSSB lake-level page")

    def test_atomic_output_round_trip(self):
        with tempfile.TemporaryDirectory(dir=".") as td:
            path = Path(td) / "data.csv"
            rows = [{"date": "2026-01-01", "poondi": "2"}]
            atomic_write(path, rows, ["date", "poondi"])
            actual, fields = read_records(path)
            self.assertEqual(fields, ["date", "poondi"])
            self.assertEqual(actual, rows)

    def test_failed_fetch_never_replaces_existing_output(self):
        # run() fetches before writing; a rejected page leaves the target untouched.
        from argparse import Namespace
        from scrape_cmwssb import run
        with tempfile.TemporaryDirectory(dir=".") as td:
            path = Path(td) / "existing.csv"
            path.write_text("sentinel", encoding="utf-8")
            args = Namespace(from_date="2026-10-16", to_date="2026-10-16", interval=1,
                             output=str(path), base=None, dry_run=False, spacing=0)
            import scrape_cmwssb
            prior = scrape_cmwssb.fetch_day
            scrape_cmwssb.fetch_day = lambda _d: (_ for _ in ()).throw(ScrapeError("invalid HTML"))
            try:
                run(args)
            finally:
                scrape_cmwssb.fetch_day = prior
            self.assertEqual(path.read_text(encoding="utf-8"), "sentinel")

    def test_dry_run_does_not_modify_output(self):
        from argparse import Namespace
        from scrape_cmwssb import run
        with tempfile.TemporaryDirectory(dir=".") as td:
            path = Path(td) / "existing.csv"
            path.write_text("sentinel", encoding="utf-8")
            args = Namespace(from_date="2026-10-16", to_date="2026-10-16", interval=1,
                             output=str(path), base=None, dry_run=True, spacing=0)
            import scrape_cmwssb
            prior = scrape_cmwssb.fetch_day
            scrape_cmwssb.fetch_day = lambda d: {"date": d.isoformat(), "values": {"poondi": 10}, "missing_reservoirs": ["x"]}
            try:
                run(args)
            finally:
                scrape_cmwssb.fetch_day = prior
            self.assertEqual(path.read_text(encoding="utf-8"), "sentinel")


if __name__ == "__main__":
    unittest.main()
