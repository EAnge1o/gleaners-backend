"""LLM 适配层：OpenAI 兼容接口，15s 超时，任何异常静默降级到确定性兜底解析。"""
import json
import re

import httpx

from app.config import settings


class OpenAICompatAdapter:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def extract_json(self, system: str, user_text: str) -> dict | None:
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_text},
                    ],
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _loads(content)
        except Exception:
            return None  # 超时/坏 JSON/网络异常 → 降级兜底


def _loads(content: str) -> dict | None:
    content = (content or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.S)
    if m:
        content = m.group(1)
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(content[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def get_llm() -> OpenAICompatAdapter | None:
    if settings.LLM_ENABLED and settings.OPENAI_BASE_URL and settings.OPENAI_API_KEY:
        return OpenAICompatAdapter(
            settings.OPENAI_BASE_URL, settings.OPENAI_API_KEY, settings.OPENAI_MODEL
        )
    return None
