"""测试基座：把 config 的所有路径重定向到临时目录（不污染真实数据）。"""
import json
import tempfile
from pathlib import Path
from butler import config


def setup_home():
    tmp = Path(tempfile.mkdtemp(prefix="butler-test-"))
    config.DATA = tmp
    config.RELEASES = tmp / "releases"
    config.FULL = config.RELEASES / "full"
    config.INBOX = tmp / "inbox"
    config.DISTILL = tmp / "distill"
    config.SANITIZED = config.DISTILL / "sanitized"
    config.RUNS = config.DISTILL / "runs"
    config.SAMPLES = config.DISTILL / "samples"
    config.ARCHIVE = tmp / "archive"
    config.LOGS = tmp / "logs"
    config.SNAPS = tmp / "snapshots"
    config.EXPORT = tmp / "export"
    config.DB_PATH = tmp / "butler.db"
    config.FILTER_RULES_PATH = tmp / "filter_rules.md"
    config.SETTINGS_PATH = tmp / "settings.json"
    config.HOOK_BAT = tmp / "hook.cmd"
    config.INJECT_LOG = config.LOGS / "inject.log"
    config.APP_LOG = config.LOGS / "butler.log"
    config.CLAUDE_PROJECTS = tmp / "claude-projects"      # 伪造的 transcript 根
    config.CLAUDE_PROJECTS.mkdir(parents=True, exist_ok=True)
    config.ALL_DIRS = [config.RELEASES, config.FULL, config.INBOX, config.DISTILL,
                       config.SANITIZED, config.RUNS, config.SAMPLES, config.ARCHIVE,
                       config.LOGS, config.SNAPS, config.EXPORT]
    config.ensure_dirs()
    from butler import db
    db.reset()
    db.init_db()
    from butler import filterpipe
    filterpipe.ensure_default_rules()
    return tmp


def line(**kw):
    return json.dumps(kw, ensure_ascii=False)


def user_msg(uuid, text, cwd="D:\\proj-demo", ts="2026-10-15T10:00:00.000Z"):
    return line(type="user", uuid=uuid, timestamp=ts, cwd=cwd,
                message={"role": "user", "content": [{"type": "text", "text": text}]})


def asst_msg(uuid, text, usage=None, model="fake-model", ts="2026-10-15T10:00:01.000Z"):
    return line(type="assistant", uuid=uuid, timestamp=ts,
                message={"role": "assistant", "model": model,
                         "content": [{"type": "text", "text": text}],
                         "usage": usage or {"input_tokens": 100,
                                            "cache_creation_input_tokens": 0,
                                            "cache_read_input_tokens": 1000,
                                            "output_tokens": 50}})
