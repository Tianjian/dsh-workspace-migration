#!/usr/bin/env python3
"""Container-level checks on the deliverables a copy is supposed to preserve.

A byte-identical copy can still contain a broken artifact if the source was
already truncated, so this validates the container of each document:

  * Office (.docx/.pptx/.xlsx) — OPC ZIP; ``testzip()`` reads every member CRC;
  * PDF — ``%PDF`` header and ``%%EOF`` within the last 2 KiB.

It also prints a per-workspace inventory by type, which is useful as the source
material for a "what is in this workspace" summary.

Usage:
    python check_deliverables.py --manifest workspaces.json \
        --dest-root ~/Documents/deepseek-harness --out _migrate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile

OFFICE = {".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm"}
CATEGORIES = {
    "docx": {".docx", ".docm"},
    "pptx": {".pptx", ".pptm"},
    "xlsx": {".xlsx", ".xlsm"},
    "html": {".html", ".htm"},
    "md": {".md"},
    "png": {".png"},
    "svg": {".svg"},
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dest-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8-sig") as fh:
        manifest = json.load(fh)

    rows, problems = [], []
    for entry in manifest["workspaces"]:
        name = entry["name"]
        root = os.path.join(args.dest_root, name)
        counts: dict[str, int] = {}
        office_total = office_ok = pdf_total = pdf_ok = 0

        for dirpath, _dirs, filenames in os.walk(root):
            for f in filenames:
                ext = os.path.splitext(f)[1].lower()
                counts[ext] = counts.get(ext, 0) + 1
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, args.dest_root)
                if ext in OFFICE:
                    office_total += 1
                    try:
                        with zipfile.ZipFile(full) as zf:
                            bad = zf.testzip()
                        if bad is None:
                            office_ok += 1
                        else:
                            problems.append(f"{rel}: corrupt ZIP member {bad}")
                    except Exception as exc:  # noqa: BLE001
                        problems.append(f"{rel}: unreadable Office container ({exc})")
                elif ext == ".pdf":
                    pdf_total += 1
                    try:
                        with open(full, "rb") as fh:
                            head = fh.read(5)
                            fh.seek(max(0, os.path.getsize(full) - 2048))
                            tail = fh.read()
                        if head.startswith(b"%PDF") and b"%%EOF" in tail:
                            pdf_ok += 1
                        else:
                            problems.append(f"{rel}: PDF header/EOF missing")
                    except OSError as exc:
                        problems.append(f"{rel}: unreadable PDF ({exc})")

        row = {"name": name, "office": f"{office_ok}/{office_total}",
               "pdf": f"{pdf_ok}/{pdf_total}"}
        for key, exts in CATEGORIES.items():
            row[key] = sum(counts.get(e, 0) for e in exts)
        row["other"] = sum(v for k, v in counts.items()
                           if k not in OFFICE and k != ".pdf"
                           and k not in {e for exts in CATEGORIES.values() for e in exts})
        rows.append(row)

    report = {"rows": rows, "problems": problems, "allDeliverablesValid": not problems}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "deliverables-report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print(json.dumps({"workspaces": len(rows), "problems": len(problems),
                      "allValid": report["allDeliverablesValid"]}, ensure_ascii=False))
    for p in problems[:20]:
        print("PROBLEM:", p)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
