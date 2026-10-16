"""路径与配置。数据目录默认 = 项目根/butler/data，可用 BUTLER_HOME 环境变量覆盖。"""
import json
import os
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent          # butler/butler
if getattr(sys, "frozen", False):                    # PyInstaller：数据随 exe 同级
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = PKG_ROOT.parent                      # butler/（代码仓根）
DATA = Path(os.environ.get("BUTLER_HOME") or (APP_ROOT / "data"))

RELEASES = DATA / "releases"
FULL = RELEASES / "full"
INBOX = DATA / "inbox"
DISTILL = DATA / "distill"
SANITIZED = DISTILL / "sanitized"
RUNS = DISTILL / "runs"
SAMPLES = DISTILL / "samples"
ARCHIVE = DATA / "archive"
LOGS = DATA / "logs"
SNAPS = DATA / "snapshots"
EXPORT = DATA / "export"

DB_PATH = DATA / "butler.db"
FILTER_RULES_PATH = DATA / "filter_rules.md"
SETTINGS_PATH = DATA / "settings.json"
HOOK_BAT = DATA / "hook.cmd"
INJECT_LOG = LOGS / "inject.log"
APP_LOG = LOGS / "butler.log"

# Claude Code 会话目录（采集数据源，只读）
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

ALL_DIRS = [RELEASES, FULL, INBOX, DISTILL, SANITIZED, RUNS, SAMPLES,
            ARCHIVE, LOGS, SNAPS, EXPORT]

DEFAULT_SETTINGS = {
    "port": 8420,
    "distill_max_input_chars": 240000,      # 单次蒸馏输入预算（≈60-80K token）
    "digest_max_bytes": 6144,               # 发行版硬上限（防 compact 注入死循环；中文协议体约 5-6KB）
    "active_rule_cap": 15,                  # 规则区容量（超限触发归档建议）
    "reminder_hours": 24,                   # 距上次复盘 N 小时未做→角标提醒
    "poll_interval_sec": 3,
    "archive_keep_days": 180,
    "snapshot_keep": 14,
    "notify_bubble": True,                  # 气泡：仅严重错误+复盘完成
}


def ensure_dirs():
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            s.update(json.loads(SETTINGS_PATH.read_text("utf-8")))
        except Exception:
            pass
    return s


def save_settings(s: dict):
    SETTINGS_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), "utf-8")


def project_hash(path: str) -> str:
    """项目路径→稳定短哈希（用于 releases/<hash>.md 与 hook 参数）。"""
    import hashlib
    return hashlib.sha1(path.lower().replace("\\", "/").encode("utf-8")).hexdigest()[:10]
