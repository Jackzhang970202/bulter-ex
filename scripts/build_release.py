# -*- coding: utf-8 -*-
"""Build a clean, publishable source archive.

Output: dist/knowlens-<version>.zip
Rules: include only source, frontend, vendored PageIndex, scripts, tests, and docs;
exclude data/, _probe/, .env, logs, caches, and any .exe or build artifact.
"""
import os
import re
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "vendor"))

from knowlens import config  # noqa: E402

VERSION = config.VERSION
NAME = f"knowlens-{VERSION}"
DIST = os.path.join(ROOT, "dist")
DEST = os.path.join(DIST, NAME)

INCLUDE_FILES = [
    "run.py", "requirements.txt", "pyproject.toml", "README.md", "LICENSE",
    "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", ".env.example",
    ".gitignore", ".gitattributes", "Dockerfile", "docker-compose.yml",
]
INCLUDE_DIRS = ["knowlens", "web", "vendor", "scripts", "tests", "docs",
                ".github"]
IGNORE_DIRS = {"__pycache__", ".pytest_cache", ".git", "data", "_probe",
               "_archive", "dist", ".idea", ".vscode"}
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9\-_.]{20,}|tp-[a-z0-9]{20,})")


def ignore(dirpath, names):
    out = set()
    for n in names:
        if n in IGNORE_DIRS or n.endswith((".pyc", ".pyo", ".log", ".bak")):
            out.add(n)
        if n == ".env":
            out.add(n)
    return out


def copy_tree():
    if os.path.isdir(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST, exist_ok=True)
    for f in INCLUDE_FILES:
        src = os.path.join(ROOT, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(DEST, f))
    for d in INCLUDE_DIRS:
        src = os.path.join(ROOT, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(DEST, d), ignore=ignore)


def scan():
    problems = []
    exes = []
    for dirpath, _, files in os.walk(DEST):
        for f in files:
            p = os.path.join(dirpath, f)
            if f.lower().endswith(".exe"):
                exes.append(p)
            if f.lower().endswith((".py", ".js", ".css", ".html", ".md",
                                   ".txt", ".yml", ".yaml", ".toml", ".json",
                                   ".example")):
                try:
                    text = open(p, encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                if SECRET_RE.search(text):
                    problems.append(p)
    if exes:
        raise SystemExit("禁止打包 .exe：\n" + "\n".join(exes))
    if problems:
        raise SystemExit("检测到疑似密钥，终止打包：\n" + "\n".join(problems))


def zip_package():
    zip_path = os.path.join(DIST, NAME + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, files in os.walk(DEST):
            for f in files:
                p = os.path.join(dirpath, f)
                z.write(p, os.path.relpath(p, DIST))
    return zip_path


def main():
    copy_tree()
    scan()
    zp = zip_package()
    size = os.path.getsize(zp) / 1024 / 1024
    print(f"release: {zp} ({size:.1f} MB)")
    print("included:", ", ".join(INCLUDE_DIRS + INCLUDE_FILES))
    print("excluded: data/, _probe/, .env, logs, caches, *.exe")


if __name__ == "__main__":
    main()