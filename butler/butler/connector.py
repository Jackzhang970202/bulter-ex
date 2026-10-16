"""注入器：项目接入/卸载/rehook。
只写项目的 .claude/settings.local.json（个人层，合并生效，不进 git、不碰用户全局配置）。
hook 命令 = 一个自定位的静态 bat（%~dp0），程序崩溃/未运行时会话照常启动；
文件缺失→注入空→静默 exit 0，并往 inject.log 记 miss（可观测）。"""
import json
import re
from pathlib import Path
from . import config, db, repo

BAT_TMPL = """@echo off
if exist "%~dp0releases\\%~1.md" (
  type "%~dp0releases\\%~1.md"
  >> "%~dp0logs\\inject.log" echo ok %~1 %DATE:~0,10% %TIME%
) else (
  >> "%~dp0logs\\inject.log" echo miss %~1 %DATE:~0,10% %TIME%
)
exit /b 0
"""

SIG = "hook.cmd"          # 我方 hook 条目的识别签名
# 覆盖全部触发源（含 compact——规则复活的通道）；用显式 alternation 而非通配
MATCHER = "startup|resume|clear|compact|fork"


def ensure_hook_bat():
    config.LOGS.mkdir(parents=True, exist_ok=True)
    want = BAT_TMPL
    cur = config.HOOK_BAT.read_text("utf-8", errors="ignore") if config.HOOK_BAT.exists() else ""
    if cur != want:
        config.HOOK_BAT.write_text(want, "utf-8")


def _hook_command(path: str) -> str:
    """cmd/bash 双兼容：无空格路径用正斜杠形式（两种 shell 都能执行 .cmd）；
    含空格时回退引号形式（此时假设 Windows cmd 语义）。"""
    h = config.project_hash(path)
    p = str(config.HOOK_BAT)
    if " " in p:
        return f'"{p}" {h}'
    return f"{p.replace(chr(92), '/')} {h}"


def _settings_file(project: Path) -> Path:
    return project / ".claude" / "settings.local.json"


def _load_settings(project: Path) -> dict:
    f = _settings_file(project)
    if f.exists():
        try:
            return json.loads(f.read_text("utf-8"))
        except json.JSONDecodeError:
            repo.log_error("connector", "settings_local_corrupt", str(f))
            return {}
    return {}


def _our_entries(hooks_cfg: list) -> list:
    out = []
    for m in hooks_cfg:
        kept = [h for h in m.get("hooks", []) if SIG in (h.get("command") or "")]
        if kept:
            out.append((m, kept))
    return out


def install(project_path: str):
    """写入 SessionStart hook（幂等：已存在则更新 command）。返回 True/False。"""
    project = Path(project_path)
    if not project.is_dir():
        return False
    ensure_hook_bat()
    f = _settings_file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    data = _load_settings(project)
    hooks = data.setdefault("hooks", {})
    ss = hooks.setdefault("SessionStart", [])
    cmd = _hook_command(project_path)
    ours = _our_entries(ss)
    if ours:
        for m, hs in ours:
            for h in hs:
                h["command"] = cmd
    else:
        ss.append({"matcher": MATCHER, "hooks": [{"type": "command", "command": cmd}]})
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(f)
    repo.mark_connected(project_path)
    return True


def uninstall(project_path: str):
    """只摘除我方 hook 条目，其余原样保留。"""
    f = _settings_file(Path(project_path))
    if f.exists():
        try:
            data = json.loads(f.read_text("utf-8"))
        except json.JSONDecodeError:
            return False
        hooks = data.get("hooks") or {}
        ss = hooks.get("SessionStart") or []
        cleaned = []
        for m in ss:
            hs = [h for h in m.get("hooks", []) if SIG not in (h.get("command") or "")]
            if hs:
                m2 = dict(m); m2["hooks"] = hs
                cleaned.append(m2)
        if cleaned:
            hooks["SessionStart"] = cleaned
        else:
            hooks.pop("SessionStart", None)
        if not hooks:
            data.pop("hooks", None)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    repo.mark_uninstalled(project_path)
    return True


def rehook():
    """数据目录搬家后：重写所有已接入项目的 hook command。"""
    n = 0
    for prj in repo.connected_projects():
        if Path(prj["path"]).is_dir() and install(prj["path"]):
            n += 1
    repo.event("rehook", detail={"projects": n})
    return n


def installed_here(project_path: str) -> bool:
    """settings.local.json 中已含我方条目（以文件为准的真相检查）。"""
    data = _load_settings(Path(project_path))
    return bool(_our_entries((data.get("hooks") or {}).get("SessionStart") or []))
