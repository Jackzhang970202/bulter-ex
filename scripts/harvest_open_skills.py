"""Harvest open-source agent skills into a single installable catalog.

Sources are public GitHub repositories that use the SKILL.md convention.
Only metadata is stored; skill bodies are fetched on install so the catalog
stays small and always points at the upstream project.

Usage:
    python scripts/harvest_open_skills.py [--out catalog/open_skills.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML ships with the app
    yaml = None

SOURCES = [
    {"repo": "davila7/claude-code-templates", "branch": "main", "strip": "cli-tool/components/skills/"},
    {"repo": "wshobson/agents", "branch": "main", "strip": ""},
    {"repo": "anthropics/skills", "branch": "main", "strip": "skills/"},
    {"repo": "obra/superpowers", "branch": "main", "strip": ""},
]

UA = {"User-Agent": "KnowLens-Skill-Harvester/1.0"}
TIMEOUT = 40


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_text(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end]
    body = text[end + 4 :]
    if yaml is None:
        return {}, body
    try:
        meta = yaml.safe_load(raw) or {}
        return (meta if isinstance(meta, dict) else {}), body
    except Exception:
        return {}, body


def first_paragraph(body: str) -> str:
    for block in re.split(r"\n\s*\n", body):
        line = " ".join(x.strip() for x in block.strip().splitlines())
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        if line.startswith("|") or line.startswith("```"):
            continue
        return re.sub(r"\s+", " ", line)[:320]
    return ""


def slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "skill"


def category_from_path(path: str, strip: str) -> str:
    rel = path[len(strip):] if strip and path.startswith(strip) else path
    parts = rel.split("/")
    if len(parts) > 2:
        return parts[0]
    # wshobson/agents layout: plugins/<group>/skills/<name>/SKILL.md
    if "skills" in parts:
        i = parts.index("skills")
        if i >= 2:
            return parts[i - 1]
    return "general"


def fetch_skill(item: dict) -> dict | None:
    repo, branch, path, category, license_name = (
        item["repo"], item["branch"], item["path"], item["category"], item["license"],
    )
    raw = f"https://raw.githubusercontent.com/{repo}/{branch}/{urllib.parse.quote(path)}"
    try:
        text = get_text(raw)
    except Exception as exc:  # network hiccup: skip this one, keep the harvest going
        return {"_error": f"{repo}/{path}: {exc}"}
    meta, body = parse_frontmatter(text)
    name = str(meta.get("name") or Path(path).parent.name).strip()
    desc = str(meta.get("description") or first_paragraph(body)).strip()
    tags = meta.get("tags") or meta.get("keywords") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]
    return {
        "id": f"{slug(repo.split('/')[1])}--{slug(name)}",
        "name": name,
        "title": name.replace("-", " ").replace("_", " ").title(),
        "description": re.sub(r"\s+", " ", desc)[:600],
        "category": category,
        "tags": [str(t)[:40] for t in tags][:12],
        "kind": "prompt",
        "format": "skill-md",
        "repo": repo,
        "path": path,
        "source_url": f"https://github.com/{repo}/blob/{branch}/{urllib.parse.quote(path)}",
        "raw_url": raw,
        "license": license_name,
        "allowed_tools": meta.get("allowed-tools") or meta.get("allowed_tools") or [],
        "meta": {k: v for k, v in meta.items() if k not in {"description"}},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="catalog/open_skills.json")
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    out_path = (root / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tasks: list[dict] = []
    licenses: dict[str, str] = {}
    for src in SOURCES:
        repo, branch = src["repo"], src["branch"]
        try:
            info = get_json(f"https://api.github.com/repos/{repo}")
            branch = info.get("default_branch", branch)
            lic = (info.get("license") or {}).get("spdx_id") or "NOASSERTION"
            licenses[repo] = lic
            tree = get_json(
                f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
            )
        except Exception as exc:
            print(f"[skip] {repo}: {exc}", file=sys.stderr)
            continue
        paths = [
            node["path"]
            for node in tree.get("tree", [])
            if node.get("type") == "blob" and node["path"].endswith("SKILL.md")
        ]
        print(f"[scan] {repo}: {len(paths)} skills")
        for path in paths:
            tasks.append(
                {
                    "repo": repo,
                    "branch": branch,
                    "path": path,
                    "category": category_from_path(path, src["strip"]),
                    "license": licenses.get(repo, "NOASSERTION"),
                }
            )

    skills: dict[str, dict] = {}
    errors = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_skill, t) for t in tasks]
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            if not row:
                continue
            if "_error" in row:
                errors += 1
                continue
            key = row["id"]
            if key in skills:
                key = f"{key}-{slug(row['repo'].split('/')[1])}"
            row["id"] = key
            skills[key] = row
            if i % 100 == 0:
                print(f"  ... {i}/{len(tasks)}", flush=True)

    catalog = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [
            {"repo": s["repo"], "license": licenses.get(s["repo"], "NOASSERTION")}
            for s in SOURCES
        ],
        "total": len(skills),
        "skills": sorted(skills.values(), key=lambda x: (x["category"], x["name"])),
    }
    out_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )

    cats: dict[str, int] = {}
    for row in skills.values():
        cats[row["category"]] = cats.get(row["category"], 0) + 1
    print(f"[done] {len(skills)} skills -> {out_path}")
    print(f"[done] {errors} fetch errors")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())