"""托盘（P4）：pystray + PIL 绘制四态图标（正常/黄角标/红/复盘沙漏）。
双击=开大屏；菜单按规格 §8.1。气泡只用于：严重错误 & 复盘完成。"""
import threading
import webbrowser
from PIL import Image, ImageDraw
from . import config, db, repo, distiller

COLORS = {"normal": (57, 135, 229, 255), "pending": (250, 178, 25, 255),
          "error": (208, 59, 59, 255), "running": (144, 133, 233, 255)}


def _img(state: str, badge: int = 0) -> Image.Image:
    size = 32
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([1, 1, 30, 30], radius=8, fill=(26, 26, 25, 255),
                        outline=(255, 255, 255, 26), width=1)
    c = COLORS.get(state, COLORS["normal"])
    if state == "running":                     # 沙漏：双三角
        d.polygon([(10, 8), (22, 8), (16, 16)], fill=c)
        d.polygon([(10, 24), (22, 24), (16, 16)], fill=c)
    else:
        d.ellipse([10, 10, 22, 22], fill=c)
    if badge > 0:                              # 数字角标
        d.ellipse([18, 0, 31, 13], fill=(13, 13, 13, 255))
        d.ellipse([19, 1, 30, 12], fill=c)
        d.text((22, 3), str(min(badge, 9)), fill=(13, 13, 13, 255))
    return im


class Tray:
    def __init__(self, stop_all=None):
        self.stop_all = stop_all
        self.icon = None
        self._state, self._badge = None, None
        self._notified_error = set()
        self._prev_distill = db.kv_get("distilling")

    # 状态推导（§8.1 优先级：红 > 沙漏 > 黄 > 灰蓝）
    def compute_state(self):
        s = repo.stats()
        badge = s["pending"]
        red = any(("drift" in e["kind"] or "schema" in e["kind"]) for e in s["errors"]) \
            or s["hb"]["collector"] > 30
        running = s["distilling"]
        if red:
            return "error", badge
        if running:
            return "running", badge
        hours = s["hours_since_distill"]
        remind = (hours == -1 or (s["undigested_sessions"] > 0 and
                                  (hours >= config.load_settings()["reminder_hours"])))
        if badge > 0 or remind:
            return "pending", badge
        return "normal", 0

    def _refresh(self):
        import pystray
        state, badge = self.compute_state()
        if (state, badge) != (self._state, self._badge):
            self._state, self._badge = state, badge
            if self.icon:
                self.icon.icon = _img(state, badge)
                self.icon.title = (f"经验管家 · "
                                   f"{'异常' if state=='error' else '复盘中' if state=='running' else f'{badge} 条待审' if badge else '正常'}")
        s = repo.stats()
        if state == "error":
            for e in s["errors"]:
                if ("drift" in e["kind"] or "schema" in e["kind"]) \
                        and e["dedup_key"] not in self._notified_error \
                        and config.load_settings()["notify_bubble"]:
                    self._notified_error.add(e["dedup_key"])
                    self.icon.notify(f"经验管家异常：{e['kind']}", "需要处理")
        if self._prev_distill == "1" and s["distilling"] == False:
            if config.load_settings()["notify_bubble"]:
                self.icon.notify(f"复盘完成 · {s['pending']} 条候选待审", "经验管家")
        self._prev_distill = "1" if s["distilling"] else ""

    def _loop(self):
        import time
        while self.icon and self.icon.visible:
            try:
                self._refresh()
            except Exception:
                pass
            time.sleep(2)

    def open_dash(self, *_):
        webbrowser.open(f"http://127.0.0.1:{config.load_settings()['port']}")

    def menu(self):
        import pystray
        s = repo.stats()
        return pystray.Menu(
            pystray.MenuItem("显示大屏", lambda i, b: self.open_dash(), default=True),
            pystray.MenuItem("现在复盘",
                             lambda i, b: distiller.start(),
                             enabled=lambda _: not distiller.is_running()),
            pystray.MenuItem(lambda _: f"待审候选（{repo.stats()['pending']}）",
                             self.open_dash),
            pystray.MenuItem(lambda _: f"项目：已接入 {len(repo.stats()['connected'])} · "
                                       f"待确认 {len(repo.stats()['pending_connects'])}",
                             self.open_dash),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("暂停采集 2h", lambda i, b: repo.set_pause(2),
                             enabled=lambda _: not repo.is_paused()),
            pystray.MenuItem("恢复采集", lambda i, b: db.kv_set("paused_until", ""),
                             enabled=lambda _: repo.is_paused()),
            pystray.MenuItem("设置", self.open_dash),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出经验管家", self._quit),
        )

    def _quit(self, icon=None, item=None):
        if self.stop_all:
            self.stop_all()
        if self.icon:
            self.icon.visible = False

    def run(self):
        import pystray
        self.icon = pystray.Icon("butler", _img("normal"), "经验管家", self.menu())
        threading.Thread(target=self._loop, daemon=True).start()
        self.icon.run()
