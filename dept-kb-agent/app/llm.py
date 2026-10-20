# -*- coding: utf-8 -*-
"""LLM 统一通道：litellm 封装 + 主/备探测回退。

litellm 模型名规则：openai/<id> 走 OpenAI 兼容（OPENAI_API_KEY/BASE_URL），
anthropic/<id> 走 Anthropic Messages（ANTHROPIC_API_KEY/BASE_URL）。
启动时探测主通道，失败自动切备用并记日志；运行时连续失败也降级。
"""
import json
import os
import re
import threading

from . import config, db

_state = {"channel": None, "model_ref": None, "fail": 0, "lock": threading.Lock()}

CHANNELS = {"mimo": config.MIMO, "maas": config.MAAS}


def _apply_env(ch):
    if ch["protocol"] == "anthropic":
        os.environ["ANTHROPIC_API_KEY"] = ch["api_key"]
        os.environ["ANTHROPIC_BASE_URL"] = ch["base_url"]
        os.environ["ANTHROPIC_AUTH_TOKEN"] = ch["api_key"]
    else:
        os.environ["OPENAI_API_KEY"] = ch["api_key"]
        os.environ["OPENAI_BASE_URL"] = ch["base_url"]
    os.environ["LITELLM_DROP_PARAMS"] = "True"
    os.environ.setdefault("TIKTOKEN_CACHE_DIR",
                          os.path.join(config.DATA, "tiktoken_cache"))


def probe(name, timeout=60):
    """向指定通道发一条极小请求验证可用。

    推理型模型（如 mimo）可能先输出 thinking 导致 content 为空，
    因此以“HTTP 层成功返回 choices”为连通判据，不要求正文非空。
    """
    import litellm
    litellm.suppress_debug_info = True
    ch = CHANNELS[name]
    _apply_env(ch)
    ref = ("openai/" if ch["protocol"] == "openai" else "anthropic/") + ch["model"]
    try:
        r = litellm.completion(
            model=ref, messages=[{"role": "user",
                                  "content": "Reply with exactly: PONG"}],
            max_tokens=128, timeout=timeout, drop_params=True)
        out = ((r.choices[0].message.content or "").strip())
        return True, out[:40] or "(thinking-only)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:200]


def init():
    """选通道：mimo 优先，失败切 maas。"""
    with _state["lock"]:
        if _state["channel"]:
            return _state["channel"]
        for name in ("mimo", "maas"):
            ok, info = probe(name)
            print(f"[llm] probe {name}: {ok} {info}")
            if ok:
                _state["channel"] = name
                _state["model_ref"] = ("openai/" if CHANNELS[name]["protocol"]
                                       == "openai" else "anthropic/") \
                    + CHANNELS[name]["model"]
                break
        if not _state["channel"]:
            _state["channel"] = "mimo"
            _state["model_ref"] = "openai/" + config.MIMO["model"]
        return _state["channel"]


def channel():
    if not _state["channel"]:
        init()
    return _state["channel"]


def model_ref():
    if not _state["model_ref"]:
        init()
    return _state["model_ref"]


def current_channel():
    """当前生效通道的配置 dict（mimo 或 maas）。"""
    if not _state["channel"]:
        init()
    return CHANNELS[_state["channel"]]


def complete(messages, temperature=0.2, max_tokens=4000, json_mode=False):
    """非流式补全。返回 (text, usage)。"""
    import litellm
    ref = model_ref()
    kw = {}
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    r = litellm.completion(model=ref, messages=messages, temperature=temperature,
                           max_tokens=max_tokens, timeout=300, drop_params=True,
                           **kw)
    text = r.choices[0].message.content or ""
    u = getattr(r, "usage", None)
    usage = {"in": getattr(u, "prompt_tokens", 0) or 0,
             "out": getattr(u, "completion_tokens", 0) or 0} if u else {"in": 0, "out": 0}
    if json_mode:
        text = _strip_fences(text)
    return text, usage


def complete_json(messages, temperature=0.1, max_tokens=4000, retries=1):
    """要求模型输出单个 JSON 对象；解析失败重试一次。"""
    for i in range(retries + 1):
        text, usage = complete(messages, temperature=temperature,
                               max_tokens=max_tokens,
                               json_mode=(i == 0))
        obj = _extract_json(text)
        if obj is not None:
            return obj, usage
        messages = messages + [{"role": "user",
                                "content": "上一条输出不是合法 JSON。"
                                           "只重新输出一个合法 JSON 对象，不要其他文字。"}]
    raise ValueError(f"LLM JSON 解析失败: {text[:200]}")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _strip_fences(text):
    m = _FENCE.search(text)
    return m.group(1).strip() if m else text.strip()


def _extract_json(text):
    text = _strip_fences(text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None
