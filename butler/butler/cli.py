"""命令行入口：python -m butler <command>"""
import argparse
import os
import sys
import threading
import time
from pathlib import Path
from . import config, db, repo, filterpipe, connector, renderer, distiller, exporter, \
    __version__


def _bootstrap():
    config.ensure_dirs()
    db.init_db()
    filterpipe.ensure_default_rules()
    connector.ensure_hook_bat()


def cmd_init(_a):
    _bootstrap()
    print("已初始化：", config.DATA)


def cmd_status(_a):
    _bootstrap()
    s = repo.stats()
    v = (s["last_distill"] or {}).get("finished", "—")
    hb = s["hb"]["collector"]
    d = s["digest"] or {}
    print(f"""经验管家 v{__version__}   数据目录 {config.DATA}
账本      rules={len(s['rules'])} active={s['active_rule_count']} profiles={len(s['profiles'])} 候选待审={s['pending']}
采集      心跳={'活(' + str(hb) + 's前)' if hb >= 0 else '未运行'} 暂停中={s['paused']} 未消化会话={s['undigested_sessions']}
发行版    v{d.get('ver', '—')} {d.get('bytes', '—')}B 待重渲={s['digest_dirty'] or '否'}
注入24h   ok={s['inject_24h']['ok']} miss={s['inject_24h']['miss']}
蒸馏      最近={v} 进行中={s['distilling']}
项目      已接入={len(s['connected'])} 待确认={len(s['pending_connects'])}
未决错误  {len(s['errors'])} 条""")


def cmd_run(a):
    _bootstrap()
    # 已修复缺陷的历史错误自动标记已处理（若复发会由 log_error 自动重新亮起）
    db.conn().execute("UPDATE errors SET resolved=1 WHERE resolved=0 AND kind IN "
                      "('file:IndexError','schema_drift')")
    db.conn().commit()
    renderer.render_all()
    from .collector import Collector
    col = Collector()
    col.start()
    port = config.load_settings()["port"]
    from . import server
    server.serve_thread(port)
    threading.Thread(target=_server_hb, daemon=True).start()
    print(f"经验管家已启动：大屏 http://127.0.0.1:{port}  （托盘{'禁用' if a.no_tray else '运行中'}）")
    if a.no_tray:
        while True:
            time.sleep(60)
    from .tray import Tray
    def stop_all():
        col.stop()
        if tray.icon:
            tray.icon.visible = False
        print("经验管家已退出（已接入项目的 hook 读静态文件，可继续独立工作）")
        sys.stdout.flush()
        import os
        os._exit(0)
    tray = Tray(stop_all=stop_all)
    tray.run()


def _server_hb():
    while True:
        try:
            repo.heartbeat("server")
        except Exception:
            pass
        time.sleep(5)


def cmd_distill(_a):
    _bootstrap()
    ok, why = distiller.start()
    if not ok:
        print("已在运行中"); return
    while distiller.is_running():
        print("·", db.kv_get("distill_progress")); time.sleep(5)
    s = repo.stats()
    print(f"完成：候选 {s['pending']} 条 → 大屏审批 http://127.0.0.1:{config.load_settings()['port']}")


def cmd_connect(a):
    _bootstrap()
    if connector.install(a.path):
        renderer.render_all()
        print("已接入：", a.path)
    else:
        sys.exit("接入失败（路径不存在或 settings 写入失败）")


def cmd_uninstall(a):
    _bootstrap()
    if a.all:
        for p in repo.connected_projects():
            connector.uninstall(p["path"])
            print("已摘除：", p["path"])
        renderer.render_all()
    else:
        connector.uninstall(a.path)
        renderer.render_all()
        print("已摘除：", a.path)


def cmd_rehook(_a):
    _bootstrap()
    n = connector.rehook()
    renderer.render_all()
    print(f"已重写 {n} 个项目的 hook 路径")


def cmd_export(_a):
    _bootstrap()
    print("导出到：", exporter.export_kit())


def cmd_autostart(a):
    import subprocess
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    import pathlib
    if not pathlib.Path(exe).exists():
        exe = sys.executable
    cmdline = f'"{exe}" -m butler run --no-tray'
    if a.state == "on":
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", "ButlerExperience",
                            "/SC", "ONLOGON", "/TR", f'"{cmdline}"'],
                           capture_output=True, text=True)
        print("计划任务：", "已注册（开机自启，无托盘模式，大屏仍可用）" if r.returncode == 0
              else r.stderr.strip() or "注册失败：请手动添加开机启动项")
    else:
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", "ButlerExperience"],
                       capture_output=True, text=True)
        print("计划任务：已移除")


def cmd_filter_test(a):
    _bootstrap()
    text = open(a.file, encoding="utf-8", errors="replace").read() if a.file else sys.stdin.read()
    masked, cats = filterpipe.mask_text(text)
    print(masked)
    print(f"\n---- 命中 {len(cats)} 处：{sorted(set(cats))}", file=sys.stderr)


def _check_web_js():
    """打包前置守门：页面内联 JS 语法检查（node 存在时强制，否则提示跳过）。"""
    import re
    import shutil
    import subprocess
    src = (config.PKG_ROOT / "web" / "index.html").read_text("utf-8")
    js = "\n".join(m.group(1) for m in re.finditer(r"<script>([\s\S]*?)</script>", src))
    node = shutil.which("node")
    if not node:
        print("未找到 node，跳过前端语法检查（建议装 node 后再打包）")
        return True
    tmp = config.APP_ROOT / "build"
    tmp.mkdir(exist_ok=True)
    f = tmp / "webcheck.js"
    f.write_text(js, "utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    if r.returncode != 0:
        print("前端 JS 语法错误，打包中止：\n" + (r.stderr or "")[:1500])
        return False
    print("前端 JS 语法检查通过")
    return True


def cmd_build(a):
    """PyInstaller 打包成绿色单文件夹 exe（数据落在 exe 同级 ./data）。
    --windowed：无控制台窗口（双击即用、关不掉），适合分发；默认 --console 便于开发观察。"""
    import glob
    import subprocess
    if not _check_web_js():
        sys.exit(1)
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onedir", "--windowed" if a.windowed else "--console", "--name", "butler",
            "--add-data", f"{config.PKG_ROOT / 'web'}{os.pathsep}web",
            "--collect-submodules", "uvicorn",
            "--collect-all", "anyio"]
    # conda 的 _ssl.pyd 依赖 libssl/libcrypto，PyInstaller 常漏收 → 强制带上
    base = Path(sys.prefix)
    for pat in ("libssl*.dll", "libcrypto*.dll"):
        for dll in glob.glob(str(base / "Library" / "bin" / pat)):
            args += ["--add-binary", f"{dll}{os.pathsep}."]
    for mod in ("matplotlib", "numpy", "IPython", "jedi", "parso", "prompt_toolkit",
                "zmq", "tornado", "tkinter", "_tkinter", "pytest", "py", "watchfiles"):
        args += ["--exclude-module", mod]
    args.append(str(config.APP_ROOT / "butler_build.py"))
    print("打包命令:", " ".join(args))
    r = subprocess.run(args, cwd=str(config.APP_ROOT))
    if r.returncode == 0:
        exe = config.APP_ROOT / "dist" / "butler" / "butler.exe"
        print(f"完成（未自动打包 pyinstaller 依赖见 requirements-dev）: {exe}")
        print("首次运行请双击 dist/butler/butler.exe，数据目录在其同级 ./data")
    else:
        sys.exit("PyInstaller 退出码非 0；如缺依赖请先: pip install pyinstaller")


def cmd_test(_a):
    import unittest
    suite = unittest.defaultTestLoader.discover(str(config.APP_ROOT / "tests"))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if r.wasSuccessful() else 1)


def _console_utf8():
    """Windows 控制台代码页自适应，防中文乱码（GBK 终端跑 exe 时）。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _windowed_stdio():
    """--windowed 打包态下 stdout/stderr 为 None，print 会崩 → 重定向到运行日志。"""
    if getattr(sys, "frozen", False) and sys.stdout is None:
        try:
            config.ensure_dirs()
            f = open(config.LOGS / "run.log", "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = f
        except Exception:
            class _Null:
                def write(self, *a): return 0
                def flush(self): pass
            sys.stdout = sys.stderr = _Null()


def main(argv=None):
    _console_utf8()
    _windowed_stdio()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["run"]                     # 双击 exe 无参数 = 常驻运行（托盘+大屏）
    p = argparse.ArgumentParser(prog="butler", description="经验管家（本地 AI 经验沉淀）")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    r = sub.add_parser("run", help="常驻运行（采集+大屏+托盘）")
    r.add_argument("--no-tray", action="store_true")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("distill", help="手动复盘（前台等待）").set_defaults(fn=cmd_distill)
    c = sub.add_parser("connect"); c.add_argument("path"); c.set_defaults(fn=cmd_connect)
    u = sub.add_parser("uninstall"); u.add_argument("path", nargs="?"); \
        u.add_argument("--all", action="store_true"); u.set_defaults(fn=cmd_uninstall)
    sub.add_parser("rehook", help="搬家后重写所有项目 hook 路径").set_defaults(fn=cmd_rehook)
    sub.add_parser("export", help="导出纯 markdown 经验包").set_defaults(fn=cmd_export)
    au = sub.add_parser("autostart"); au.add_argument("state", choices=["on", "off"])
    au.set_defaults(fn=cmd_autostart)
    ft = sub.add_parser("filter-test"); ft.add_argument("file", nargs="?")
    ft.set_defaults(fn=cmd_filter_test)
    b = sub.add_parser("build", help="PyInstaller 打包绿色 exe")
    b.add_argument("--windowed", action="store_true", help="无控制台窗口版（分发用）")
    b.set_defaults(fn=cmd_build)
    sub.add_parser("test", help="运行单元/集成测试").set_defaults(fn=cmd_test)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
