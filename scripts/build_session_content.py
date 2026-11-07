#!/usr/bin/env python3
"""Build the text a per-workspace session should show.

For each workspace it walks the copied tree and renders a Markdown inventory:
size overview, top-level structure, deliverables grouped by type (with sizes),
and a note that hidden directories are process material, not deliverables.

Only file *names*, types and sizes are emitted — never file contents — so the
result is safe to publish.

Usage:
    python build_session_content.py --manifest workspaces.json \
        --dest-root ~/Documents/deepseek-harness --out session-manifest.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections import defaultdict

DOC_EXT = {".md", ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv",
           ".html", ".htm", ".txt", ".slide"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}
CODE_EXT = {".py", ".js", ".mjs", ".ts", ".ps1", ".sh", ".c", ".h", ".ino", ".json"}

GROUPS = {
    "演示文稿 / 幻灯片": {".pptx", ".slide"},
    "文档 / 报告": {".md", ".txt", ".docx"},
    "PDF": {".pdf"},
    "表格 / 数据": {".xlsx", ".xls", ".csv"},
    "网页 / HTML": {".html", ".htm"},
}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} GB"


def scan(root: str) -> dict:
    files = size = images = code = 0
    by_ext: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    docs: list[tuple[str, int, str]] = []
    top: list[str] = []

    for entry in sorted(os.scandir(root), key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.is_dir():
            n = sum(len(f) for _, _, f in os.walk(entry.path))
            top.append(f"{entry.name}/ ({n} 个文件)" if n else f"{entry.name}/ (空目录)")
        else:
            top.append(entry.name)

    for dirpath, _dirs, filenames in os.walk(root):
        for f in filenames:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            try:
                fsize = os.path.getsize(full)
            except OSError:
                continue
            files += 1
            size += fsize
            ext = os.path.splitext(f)[1].lower()
            by_ext[ext][0] += 1
            by_ext[ext][1] += fsize
            if ext in IMG_EXT:
                images += 1
            elif ext in CODE_EXT:
                code += 1
            if ext in DOC_EXT:
                docs.append((rel, fsize, ext))
    return {"files": files, "bytes": size, "by_ext": by_ext, "docs": docs,
            "images": images, "code": code, "top": top}


def render(name: str, path: str, s: dict, generated: str) -> str:
    docs = sorted(s["docs"], key=lambda d: -d[1])
    out = [f"# {name} — 工作区主要内容与产出物", "", "## 一、概况", ""]
    out += [f"- 工作区路径：`{path}`",
            f"- 规模：**{s['files']} 个文件**，共 **{human(s['bytes'])}**（含隐藏目录）",
            f"- 图片 {s['images']} 个 · 代码/配置 {s['code']} 个 · 文档类 {len(s['docs'])} 个",
            "", "## 二、顶层结构", ""]
    out += [f"- `{t}`" for t in s["top"][:14]]
    if len(s["top"]) > 14:
        out.append(f"- …另有 {len(s['top']) - 14} 项")

    out += ["", "## 三、主要产出物", ""]
    for label, exts in GROUPS.items():
        items = [d for d in docs if d[2] in exts]
        if not items:
            continue
        out += [f"### {label}（{len(items)} 个）", ""]
        out += [f"- `{rel}` — {human(sz)}" for rel, sz, _ in items[:12]]
        if len(items) > 12:
            out.append(f"- …另有 {len(items) - 12} 个同类文件")
        out.append("")

    heaviest = sorted(s["by_ext"].items(), key=lambda kv: -kv[1][1])[:6]
    out += ["## 四、素材与中间件", "",
            f"- 图片素材 {s['images']} 个（含 `.<tool>` 内的裁剪/参考图）",
            "- 体积占比最高的类型：" + "、".join(
                f"`{ext or '(无扩展名)'}` {human(sz)}" for ext, (_n, sz) in heaviest),
            "", "## 五、备注", "",
            f"- 本清单由工作区文件系统扫描生成，快照时间 {generated}。",
            "- 隐藏目录（`.workbuddy`、`.cache` 等）一并统计，它们是过程素材，不是最终交付物。", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dest-root", required=True)
    ap.add_argument("--registry", default=None,
                    help="workspace registry, to resolve workspaceId per workspace")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8-sig") as fh:
        manifest = json.load(fh)

    by_title = {}
    if args.registry:
        with open(args.registry, encoding="utf-8-sig") as fh:
            registry = json.load(fh)
        by_title = {w["title"]: wid for wid, w in registry["tables"]["workspaces"].items()}

    generated = time.strftime("%Y-%m-%d %H:%M:%S")
    entries = []
    for entry in manifest["workspaces"]:
        name = entry["name"]
        path = os.path.join(args.dest_root, name)
        if not os.path.isdir(path):
            print(f"skip missing {name}", file=sys.stderr)
            continue
        s = scan(path)
        entries.append({
            "workspaceId": by_title.get(name),
            "name": name,
            "cwd": path,
            "title": f"{name} 工作区内容与产出物",
            "sessionId": f"session-{uuid.uuid4()}",
            "question": "列举本工作区的主要内容和产出物。",
            "answer": render(name, path, s, generated),
            "files": s["files"],
            "bytes": s["bytes"],
        })
        print(f"scanned {name}: {s['files']} files, {len(entries[-1]['answer'])} chars")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=2)
    print(f"manifest -> {args.out} ({len(entries)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
