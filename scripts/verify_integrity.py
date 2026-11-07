#!/usr/bin/env python3
"""Four-layer integrity check for a workspace migration.

Layers:
  1. relative path sets (files and directories) — missing / extra;
  2. per-file SHA-256 (full coverage, never sampled);
  3. byte counts and mtime (2 s tolerance for filesystem precision);
  4. registry membership: the destination is registered, title equals the name.

Exit code 1 if any workspace fails, so CI can gate on it.

Usage:
    python verify_integrity.py --manifest workspaces.json \
        --source-root ~/WorkBuddy --dest-root ~/Documents/deepseek-harness \
        --registry ~/.dsh/storages/workspace.json --out _migrate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan(root: str):
    """Map relpath -> (kind, size, digest, mtime); collect unreadable paths."""
    entries: dict[str, tuple[str, int, str, float]] = {}
    errors: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            full = os.path.join(dirpath, d)
            entries[os.path.relpath(full, root)] = ("dir", 0, "", 0.0)
        for f in filenames:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            try:
                entries[rel] = ("file", os.path.getsize(full), sha256(full),
                                os.path.getmtime(full))
            except OSError as exc:
                errors.append(f"{full}: {exc}")
                entries[rel] = ("file", -1, f"ERROR:{exc}", 0.0)
    return entries, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--dest-root", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mtime-tolerance", type=float, default=2.0)
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8-sig") as fh:
        manifest = json.load(fh)
    with open(args.registry, encoding="utf-8-sig") as fh:
        registry = json.load(fh)
    by_path = {os.path.normcase(w["path"]): (wid, w)
               for wid, w in registry["tables"]["workspaces"].items()}

    report = {"workspaces": []}
    failures = 0

    for entry in manifest["workspaces"]:
        name = entry["name"]
        src = os.path.join(args.source_root, name)
        dst = os.path.join(args.dest_root, name)
        item = {"name": name, "src": src, "dst": dst, "checks": {}, "problems": []}

        registered = by_path.get(os.path.normcase(dst))
        item["registered"] = registered is not None
        item["registeredTitle"] = registered[1].get("title") if registered else None
        if registered is None:
            item["problems"].append("not registered as a workspace")
        elif registered[1].get("title") != name:
            item["problems"].append(
                f"title mismatch: registry={registered[1].get('title')!r} expected={name!r}")

        if not os.path.isdir(src):
            item["problems"].append("source directory missing")
        if not os.path.isdir(dst):
            item["problems"].append("destination directory missing")

        if os.path.isdir(src) and os.path.isdir(dst):
            src_entries, src_err = scan(src)
            dst_entries, dst_err = scan(dst)

            missing = sorted(set(src_entries) - set(dst_entries))
            extra = sorted(set(dst_entries) - set(src_entries))
            content_mismatch, size_mismatch, mtime_drift = [], [], []
            for rel in set(src_entries) & set(dst_entries):
                s, d = src_entries[rel], dst_entries[rel]
                if s[0] != d[0]:
                    content_mismatch.append(rel)
                elif s[0] == "file":
                    if s[1] != d[1]:
                        size_mismatch.append(rel)
                    elif s[2] != d[2]:
                        content_mismatch.append(rel)
                    elif abs(s[3] - d[3]) > args.mtime_tolerance:
                        mtime_drift.append(rel)

            src_files = [v for v in src_entries.values() if v[0] == "file"]
            dst_files = [v for v in dst_entries.values() if v[0] == "file"]
            item["checks"] = {
                "srcFiles": len(src_files), "dstFiles": len(dst_files),
                "srcBytes": sum(v[1] for v in src_files),
                "dstBytes": sum(v[1] for v in dst_files),
                "missingCount": len(missing), "extraCount": len(extra),
                "contentMismatchCount": len(content_mismatch),
                "sizeMismatchCount": len(size_mismatch),
                "mtimeDriftCount": len(mtime_drift),
                "readErrors": src_err + dst_err,
            }
            for label, items in (("missing", missing), ("extra", extra),
                                 ("size mismatch", size_mismatch),
                                 ("content mismatch", content_mismatch),
                                 ("mtime drift", mtime_drift)):
                if items:
                    item["problems"].append(f"{label} {len(items)}: {items[:8]}")
            if src_err or dst_err:
                item["problems"].append("read errors: " + "; ".join((src_err + dst_err)[:4]))

        item["ok"] = not item["problems"]
        failures += 0 if item["ok"] else 1
        report["workspaces"].append(item)

    summary = {
        "workspaces": len(report["workspaces"]),
        "ok": sum(1 for w in report["workspaces"] if w["ok"]),
        "failed": failures,
        "totalFiles": sum(w.get("checks", {}).get("dstFiles", 0) for w in report["workspaces"]),
        "totalBytes": sum(w.get("checks", {}).get("dstBytes", 0) for w in report["workspaces"]),
    }
    summary["allOk"] = failures == 0
    report["summary"] = summary

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "integrity-report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    lines = ["| workspace | files | MB | registered | title | verdict |",
             "| --- | ---: | ---: | :---: | --- | --- |"]
    for w in report["workspaces"]:
        c = w.get("checks", {})
        lines.append(
            f"| {w['name']} | {c.get('dstFiles', 0)} | {c.get('dstBytes', 0) / 1048576:.2f} | "
            f"{'yes' if w.get('registered') else 'no'} | "
            f"{'yes' if w.get('registeredTitle') == w['name'] else 'no'} | "
            f"{'PASS' if w['ok'] else 'FAIL: ' + '; '.join(w['problems'])} |")
    lines += ["", f"Totals: {summary['workspaces']} workspaces, {summary['ok']} passing, "
                  f"{summary['failed']} failing, {summary['totalFiles']} files, "
                  f"{summary['totalBytes'] / 1048576:.2f} MB, allOk={summary['allOk']}"]
    with open(os.path.join(args.out, "integrity-report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(json.dumps(summary, ensure_ascii=False))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
