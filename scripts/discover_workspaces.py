#!/usr/bin/env python3
"""Discover the source tool's workspaces and emit a migration manifest.

The source agent (an Electron desktop app) records two things we care about:

  * a ``workspaces`` table — workspaces the user opened recently;
  * ``sessions.cwd`` — the working directory of every local session.

Neither alone is complete (the table is a recents list; old sessions may point at
workspaces the user never re-opened), so the manifest is the union of both.

Usage:
    python discover_workspaces.py --db ~/.workbuddy/workbuddy.db \
        --source-root ~/WorkBuddy --out workspaces.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys


def read_workspaces(db_path: str) -> tuple[list[str], list[str]]:
    """Return (workspace paths from the recents table, local session cwds)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        table_paths = [row[0] for row in cur.execute("SELECT path FROM workspaces")]
        cwds = [row[0] for row in cur.execute("SELECT DISTINCT cwd FROM sessions")]
    finally:
        con.close()
    return table_paths, cwds


def normalize(path: str) -> str:
    """Canonical absolute form; the source mixes / and \\ in its records."""
    return os.path.normpath(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="source tool's SQLite database")
    ap.add_argument("--source-root", required=True,
                    help="directory holding one folder per workspace")
    ap.add_argument("--out", required=True, help="manifest JSON to write")
    ap.add_argument("--include-empty", action="store_true",
                    help="keep workspaces whose directory is empty")
    args = ap.parse_args()

    table_paths, cwds = read_workspaces(args.db)
    seen: dict[str, str] = {}
    for raw in table_paths + cwds:
        if not raw:
            continue  # cloud sessions have no cwd
        path = normalize(raw)
        seen.setdefault(os.path.normcase(path), path)

    entries, skipped = [], []
    for path in sorted(seen.values(), key=lambda p: os.path.basename(p).lower()):
        name = os.path.basename(path)
        if not os.path.isdir(path):
            skipped.append({"name": name, "path": path, "reason": "directory missing"})
            continue
        files = bytes_total = 0
        for dirpath, _dirs, filenames in os.walk(path):
            for f in filenames:
                try:
                    bytes_total += os.path.getsize(os.path.join(dirpath, f))
                    files += 1
                except OSError:
                    pass
        if files == 0 and not args.include_empty:
            skipped.append({"name": name, "path": path, "reason": "empty directory"})
            continue
        entries.append({"name": name, "path": path, "files": files, "bytes": bytes_total})

    manifest = {"sourceRoot": args.source_root, "workspaces": entries, "skipped": skipped}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(f"workspaces: {len(entries)}  skipped: {len(skipped)}")
    for s in skipped:
        print(f"  skip {s['name']!r}: {s['reason']}")
    print(f"manifest -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
