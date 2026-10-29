# -*- coding: utf-8 -*-
"""Recording fixed pipeline: ffmpeg normalization → ASR (pluggable adapter) → timestamped transcript → LLM structured minutes.

The ASR engine is selected automatically by priority:
  1) faster-whisper (local, if installed and the model is downloadable/cached)
  2) OpenAI-compatible /audio/transcriptions (if the channel endpoint supports it)
  3) not ready → raise an error explaining how to enable it (the rest of the pipeline is in place)
Produces markdown: `# 转录稿（带时间码）` + `## 结构化会议纪要` (topic/conclusions/action items/terms/disputes).
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
    # Fallback: the exe bundled with imageio-ffmpeg is named ffmpeg-win-x86_64-vX.exe,
    # while faster-whisper looks up "ffmpeg.exe" internally → copy and rename it into the project bin/
    # and inject that directory into PATH.
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
    """Convert to a unified 16k mono wav."""
    dst = os.path.join(workdir, "16k.wav")
    cmd = [ffmpeg_path(), "-y", "-i", src, "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", dst]
    p = subprocess.run(cmd, capture_output=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {p.stderr.decode(errors='replace')[-200:]}")
    return dst


def _probe_duration(wav):
    # The normalized file is always a PCM WAV, so its frame count is the most
    # reliable duration source (ffprobe may not exist next to the bundled ffmpeg).
    try:
        import wave
        with wave.open(wav, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        pass
    try:
        out = subprocess.run(
            [ffmpeg_path().replace("ffmpeg", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration", "-of", "json", wav],
            capture_output=True, timeout=60)
        return float(json.loads(out.stdout.decode() or "{}")["format"]["duration"])
    except Exception:
        return 0.0


def _asr_faster_whisper(wav, on_log, duration=0.0):
    from faster_whisper import WhisperModel
    global _ENGINE
    if _ENGINE is None:
        on_log("加载本地 ASR 模型 small（首次会下载）…")
        _ENGINE = WhisperModel("small", device="auto", compute_type="int8")
    segs, info = _ENGINE.transcribe(
        wav, language="zh", vad_filter=True, beam_size=1,
        initial_prompt="以下是普通话的部门会议录音转写，请使用简体中文输出。")
    out = []
    last_pct, last_emit = 0, 0.0
    for s in segs:
        out.append((s.start, s.end, s.text.strip()))
        end = float(s.end or 0)
        if duration and duration > 0:
            pct = min(99, max(0, int(round(end / duration * 100))))
            if pct > last_pct and (pct - last_pct >= 1 or end - last_emit >= 30):
                on_log(f"转写 {pct}%（{end:.0f}/{duration:.0f} 秒）")
                last_pct, last_emit = pct, end
        elif end - last_emit >= 30:
            # Unknown duration: never fake a percentage.
            on_log(f"已转写 {end:.0f} 秒（总时长未知）")
            last_emit = end
    return out


def _asr_openai_api(wav, on_log):
    """OpenAI-compatible transcription endpoint (some MaaS providers offer /audio/transcriptions)."""
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
        on_log(f"音频时长 {dur:.0f} 秒，开始转写（0%）" if dur > 0
               else "开始转写（总时长未知，显示已转写时长）")
        segments = None
        try:
            import faster_whisper  # noqa
            segments = _asr_faster_whisper(wav, on_log, dur)
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
        on_log("转写完成（100%），生成结构化纪要…")
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
