"""Manually sync current dashboard CSV outputs into the AquaSat SQLite store."""

from __future__ import annotations

import argparse
import json

from aquasat_database import sync_csv_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-import even if source CSVs are unchanged")
    parser.add_argument("--database", help="optional SQLite path; defaults to aquasat.sqlite3 here")
    args = parser.parse_args()
    result = sync_csv_outputs(db_path=args.database, force=args.force)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
