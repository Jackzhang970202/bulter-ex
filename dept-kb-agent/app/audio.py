# -*- coding: utf-8 -*-
"""录音固定脚本管线：ffmpeg 归一 → ASR（适配器可插拔）→ 时间戳转录 → LLM 结构化纪要。

ASR 引擎按优先级自动选择：
  1) faster-whisper（本地，若已安装且模型可下载/已缓存）
  2) openai 兼容 /audio/transcriptions（若通道端点支持）
  3) 未就绪 → 抛错并在错误信息中说明如何启用（管线其余部分已就位）
产出 markdown：`# 转录稿（带时间码）` + `## 结构化会议纪要`（主题/结论/行动项/术语/分歧）。
"""
import json
import os
import shutil
import subprocess
import tempfile

from . import config, llm

AUDIO_EXTS = ("mp3", "wav", "m4a", "mp4", "aac", "flac", "ogg", "wma")

_ENGINE = None


def ffmpeg_path():
    p = shutil.which("ffmpeg")
    if p:
        return p
    # 兜底：imageio-ffmpeg 自带的 exe 名为 ffmpeg-win-x86_64-vX.exe，
    # 而 faster-whisper 内部按 "ffmpeg.exe" 查找 → 复制重命名进项目 bin/
    # 并把该目录注入 PATH。
    try:
        import imageio_ffmpeg
        src = imageio_ffmpeg.get_ffmpeg_exe()
        if not (src and os.path.isfile(src)):
            return None
        bindir = os.path.join(config.DATA, "bin")
        os.makedirs(bindir, exist_ok=True)
        dst = os.path.join(bindir, "ffmpeg.exe")
        if not os.path.isfile(dst):
            shutil.copyfile(src, dst)
        os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
        return dst
    except Exception:
        return None


def _norm_wav(src, workdir):
    """统一转 16k 单声道 wav。"""
    dst = os.path.join(workdir, "16k.wav")
    cmd = [ffmpeg_path(), "-y", "-i", src, "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", dst]
    p = subprocess.run(cmd, capture_output=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {p.stderr.decode(errors='replace')[-200:]}")
    return dst


def _probe_duration(wav):
    try:
        out = subprocess.run(
            [ffmpeg_path().replace("ffmpeg", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration", "-of", "json", wav],
            capture_output=True, timeout=60)
        return float(json.loads(out.stdout.decode() or "{}")["format"]["duration"])
    except Exception:
        return 0.0


def _asr_faster_whisper(wav, on_log):
    from faster_whisper import WhisperModel
    global _ENGINE
    if _ENGINE is None:
        on_log("加载本地 ASR 模型 small（首次会下载）…")
        _ENGINE = WhisperModel("small", device="auto", compute_type="int8")
    segs, info = _ENGINE.transcribe(
        wav, language="zh", vad_filter=True, beam_size=1,
        initial_prompt="以下是普通话的部门会议录音转写，请使用简体中文输出。")
    out = []
    for s in segs:
        out.append((s.start, s.end, s.text.strip()))
        on_log(f"转写进度 {s.end:.0f}s")
    return out


def _asr_openai_api(wav, on_log):
    """OpenAI 兼容转写端点（部分 MaaS 提供 /audio/transcriptions）。"""
    import requests
    ch = llm.current_channel()
    if ch["protocol"] != "openai":
        raise RuntimeError("当前通道不支持 audio API")
    url = ch["base_url"].rstrip("/") + "/audio/transcriptions"
    with open(wav, "rb") as f:
        r = requests.post(url, headers={"Authorization": "Bearer " + ch["api_key"]},
                          files={"file": (os.path.basename(wav), f)},
                          data={"model": "whisper-1"}, timeout=600)
    if r.status_code != 404 and r.ok:
        text = r.json().get("text", "")
        return [(0, 0, ln.strip()) for ln in text.split("\n") if ln.strip()]
    raise RuntimeError(f"audio/transcriptions 不可用 ({r.status_code})")


MINUTES_PROMPT = (
    "你是会议记录整理助手。根据带时间戳的转录稿，输出结构化会议纪要 JSON，字段："
    '{"topic": "会议主题", "agenda": ["议程"], "conclusions": ["结论/决议，'
    '每条注明大约时间点"], "action_items": [{"item": "...", "owner": "...", '
    '"due": "..."}], "terms": ["出现的系统名/专有名词"], '
    '"disputes": ["未决分歧"]}。只输出 JSON。'
)


def transcribe_to_minutes_md(path, name, on_log):
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError("未找到 ffmpeg：录音管线依赖 ffmpeg 转码。"
                           "安装（winget install Gyan.FFmpeg）后重试。")
    on_log("ffmpeg 归一化音频…")
    workdir = tempfile.mkdtemp(prefix="dkb-audio-")
    try:
        wav = _norm_wav(path, workdir)
        dur = _probe_duration(wav)
        on_log(f"音频时长约 {dur:.0f}s，开始转写…")
        segments = None
        try:
            import faster_whisper  # noqa
            segments = _asr_faster_whisper(wav, on_log)
        except ImportError:
            on_log("faster-whisper 未安装，尝试 OpenAI 兼容转写端点…")
            try:
                segments = _asr_openai_api(wav, on_log)
            except Exception as e:
                raise RuntimeError(
                    f"ASR 引擎均不可用：{e}。请 pip install faster-whisper "
                    "（本地模型）或配置支持 /audio/transcriptions 的端点。")
        if not segments:
            raise RuntimeError("ASR 输出为空")
        ts_lines = []
        for st, en, text in segments:
            mm, ss = divmod(int(st), 60)
            ts_lines.append(f"[{mm:02d}:{ss:02d}] {text}")
        transcript = "\n".join(ts_lines)
        on_log("转录完成，生成结构化纪要…")
        minutes_md = _structure_minutes(name, transcript)
        return (f"# {name} 转录稿\n\n（带时间码全文）\n\n{transcript}\n\n"
                + minutes_md)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _structure_minutes(name, transcript):
    try:
        obj, _ = llm.complete_json(
            [{"role": "system", "content": MINUTES_PROMPT},
             {"role": "user", "content": transcript[:12000]}],
            max_tokens=1500)
    except Exception:
        return "## 结构化会议纪要\n\n（自动生成失败，见上方转录稿）"

    def li(items):
        return "\n".join(f"- {x}" for x in items) if items else "- （无）"
    acts = obj.get("action_items") or []
    act_md = "\n".join(
        f"| {a.get('item','')} | {a.get('owner','')} | {a.get('due','')} |"
        for a in acts) or "| （无） |  |  |"
    return (
        "## 结构化会议纪要\n\n"
        f"**主题**：{obj.get('topic', name)}\n\n"
        "### 议程\n" + li(obj.get("agenda", [])) + "\n\n"
        "### 结论与决议\n" + li(obj.get("conclusions", [])) + "\n\n"
        "### 行动项\n\n| 事项 | 负责人 | 期限 |\n|---|---|---|\n" + act_md + "\n\n"
        "### 关键术语\n" + li(obj.get("terms", [])) + "\n\n"
        "### 未决分歧\n" + li(obj.get("disputes", [])) + "\n")
