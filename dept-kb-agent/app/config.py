# -*- coding: utf-8 -*-
"""全局配置：模型端点、路径、常量。

主通道 mimo（OpenAI 兼容）；备用通道阿里 MaaS（Anthropic 兼容）。
启动时自动探测主通道，不通则回退并记录。
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DEPTKB_HOME", os.path.join(ROOT, "data"))
DB_PATH = os.path.join(DATA, "kb.db")
SPACES = os.path.join(DATA, "spaces")
UPLOADS = os.path.join(DATA, "uploads")   # 会话级临时上传（非入库）
ARCHIVE = os.path.join(DATA, "archive")   # 原始文件留档

# 模型通道
MIMO = {
    "protocol": "openai",
    "model": "mimo-v2.6-pro",
    "api_key": "tp-ced9593r555o4aeoqgzqrkmseeoje2878nh94w3k21maeeqg",
    "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
}
MAAS = {
    "protocol": "anthropic",
    "model": os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "qwen3.8-flash[1m]"),
    "api_key": "sk-sp-H.EIILY.KOPo.MEQCIAgZTfGSlLq9zO06x-HDuhxjucaa4FTjVdEoW1hWyiPHAiA_0C6_aENyIGJQfPtL-eKQa4p4HgOxHpJ1uK0mu669hA",
    "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic",
}

PORT = 8633
DIGEST_MAX_BYTES = 20480      # 空间 digest 体积红线
LIB_DESC_MAX = 400            # 单文档描述截断

# 问答行为
MAX_TURNS = 14                # agent 检索循环轮数上限
SUFFICIENCY_ROUNDS = 2        # 证据补充轮数上限
