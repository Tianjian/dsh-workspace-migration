#!/usr/bin/env python3
"""Register the copied workspaces in the DSH workspace registry.

The registry is ``$DSH_HOME/storages/workspace.json`` (domain ``workspace`` v2):

    tables.workspaces[<id>] = {path, title, sessionIds, createdAt, updatedAt}
    global.workspaceIds     = display order, must cover every table entry

The write is idempotent: an existing entry for the same absolute path is reused
instead of duplicated. Before writing, the file is copied to a timestamped
backup next to it.

The registry is read once at process start, so **restart DSH afterwards** — and
kill it rather than quitting gracefully (see docs/04-pitfalls.md).

Usage:
    python import_registry.py --manifest workspaces.json \
        --dest-root ~/Documents/deepseek-harness \
        --registry ~/.dsh/storages/workspace.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone


def now_iso() -> str:
    stamp = datetime.now(timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


def self_check(registry: dict) -> list[str]:
    """The five invariants DSH validates at startup; return problems found."""
    problems: list[str] = []
    order = registry["global"]["workspaceIds"]
    table = registry["tables"]["workspaces"]
    if len(order) != len(set(order)):
        problems.append("global.workspaceIds contains duplicates")
    missing = [i for i in order if i not in table]
    if missing:
        problems.append(f"order references {len(missing)} missing workspace(s)")
    if len(order) < len(table):
        problems.append(f"{len(table) - len(order)} workspace(s) absent from order")
    paths = [os.path.normcase(w["path"]) for w in table.values()]
    if len(paths) != len(set(paths)):
        problems.append("duplicate workspace path")
    owned = [s for w in table.values() for s in w["sessionIds"]]
    if len(owned) != len(set(owned)):
        problems.append("a session is owned by two workspaces")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dest-root", required=True,
                    help="DSH workspace storage root (the copy destination)")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--out", default=None, help="write an import report here")
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8-sig") as fh:
        manifest = json.load(fh)

    backup = f"{args.registry}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(args.registry, backup)
    print(f"backup -> {backup}")

    with open(args.registry, encoding="utf-8-sig") as fh:
        registry = json.load(fh)
    if registry.get("unit", {}).get("name") != "workspace":
        print("unexpected registry domain", file=sys.stderr)
        return 1

    table = registry["tables"]["workspaces"]
    order = registry["global"]["workspaceIds"]
    by_path = {os.path.normcase(w["path"]): wid for wid, w in table.items()}

    added, reused, skipped = [], [], []
    stamp = now_iso()
    for entry in manifest["workspaces"]:
        name = entry["name"]
        path = os.path.join(args.dest_root, name)
        if not os.path.isdir(path):
            skipped.append({"name": name, "reason": "directory missing"})
            continue
        key = os.path.normcase(path)
        if key in by_path:
            wid = by_path[key]
            if table[wid].get("title") != name:
                table[wid]["title"] = name
                table[wid]["updatedAt"] = stamp
            reused.append({"name": name, "id": wid})
            continue
        wid = str(uuid.uuid4())
        table[wid] = {
            "path": path, "title": name, "sessionIds": [],
            "createdAt": stamp, "updatedAt": stamp,
        }
        order.append(wid)
        by_path[key] = wid
        added.append({"name": name, "id": wid})

    problems = self_check(registry)
    if problems:
        print("refusing to write, registry would be inconsistent:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(args.registry), suffix=".tmp")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(registry, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        shutil.move(tmp, args.registry)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    report = {"added": added, "reused": reused, "skipped": skipped,
              "totalWorkspaces": len(table)}
    print(f"added {len(added)}, reused {len(reused)}, skipped {len(skipped)}, "
          f"total {len(table)}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"report -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
