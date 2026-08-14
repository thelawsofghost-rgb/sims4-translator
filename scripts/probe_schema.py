#!/usr/bin/env python3
"""临时诊断: 验证 Ollama ni-fei 能否按 JSON Schema 严格返回结构化译文。

用法: python scripts/probe_schema.py
输出: HTTP status + 模型返回的 content (应为合法 JSON, zh 非空即通过)。
验证后此文件可删除 (不入生产)。
"""
import httpx

# 统一本机 client: trust_env=False (不读系统代理) + 127.0.0.1 (不走 localhost)
_client = httpx.Client(base_url="http://127.0.0.1:11434", trust_env=False, timeout=120)

SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "zh": {"type": "string"},
                },
                "required": ["id", "zh"],
            },
        }
    },
    "required": ["translations"],
}

PROMPT = (
    "你是模拟人生4动作包汉化专家。把下面 Target 行的内容翻译为简体中文。\n"
    "规则: 只输出最终中文, 不解释不思考; 严格按 JSON Schema 输出 translations 数组, "
    "每项 id 用给定 id, zh 为译文。\n"
    "id=k1\nTarget: walk near desk"
)

payload = {
    "model": "ni-fei:latest",
    "messages": [
        {"role": "system", "content": "You translate Sims 4 pose names to simplified Chinese. Output strictly as JSON matching the provided schema."},
        {"role": "user", "content": PROMPT},
    ],
    "stream": False,
    "think": False,
    "format": SCHEMA,
    "options": {"temperature": 0.0, "num_predict": 256},
}

r = _client.post("/api/chat", json=payload)
print("HTTP status:", r.status_code)
print("content>>>", r.json().get("message", {}).get("content"))
