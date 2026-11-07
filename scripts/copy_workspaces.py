#!/usr/bin/env python3
"""Copy each workspace into the DSH workspace storage root, names unchanged.

Why Python and not robocopy/Copy-Item: the workspace names are non-ASCII and the
copy must be byte-for-byte faithful, timestamp included. ``shutil.copy2`` keeps
mtime and Python handles UTF-8 paths natively on every platform.

Empty workspaces are materialised as empty directories so that "this workspace
exists" survives the copy.

Usage:
    python copy_workspaces.py --manifest workspaces.json \
        --source-root ~/WorkBuddy --dest-root ~/Documents/deepseek-harness
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--source-root", default=None,
                    help="override the manifest's sourceRoot")
    ap.add_argument("--dest-root", required=True)
    ap.add_argument("--results", default=None, help="where to write copy-results.json")
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8-sig") as fh:
        manifest = json.load(fh)
    source_root = args.source_root or manifest.get("sourceRoot") or ""

    results = []
    for entry in manifest["workspaces"]:
        name = entry["name"]
        src = entry.get("path") or os.path.join(source_root, name)
        dst = os.path.join(args.dest_root, name)
        started = time.time()
        errors: list[str] = []

        if not os.path.isdir(src):
            results.append({"name": name, "status": "MISSING-SOURCE", "src": src})
            print(f"[MISS] {name}")
            continue

        os.makedirs(dst, exist_ok=True)
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            copy_function=shutil.copy2,
                            ignore_dangling_symlinks=True)
        except Exception as exc:  # noqa: BLE001 - report, don't abort the batch
            errors.append(repr(exc))

        files = total = 0
        for dirpath, _dirs, filenames in os.walk(dst):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                    files += 1
                except OSError as exc:
                    errors.append(f"{os.path.join(dirpath, f)}: {exc}")

        results.append({
            "name": name, "status": "OK" if not errors else "ERROR",
            "src": src, "dst": dst, "files": files, "bytes": total,
            "seconds": round(time.time() - started, 1), "errors": errors,
        })
        flag = "OK  " if not errors else "ERR "
        print(f"[{flag}] {name}: {files} files, {total / 1048576:.2f} MB")

    if args.results:
        with open(args.results, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        print(f"results -> {args.results}")

    failed = [r for r in results if r["status"] != "OK"]
    if failed:
        print(f"{len(failed)} workspace(s) not copied cleanly", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
