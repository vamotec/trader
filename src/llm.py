"""
LLM 客户端
──────────
主力：DeepSeek（便宜，够用）
备用：Anthropic（DeepSeek失败时自动切换）

两者都兼容 OpenAI Chat Completions 格式，统一用 httpx 调用。
"""

import logging
from typing import Any

import httpx

from config import DEEPSEEK_API_KEY, ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

# ── 端点配置 ──────────────────────────────────────────────────────────────────

PROVIDERS: list[dict[str, Any]] = [
    {
        "name":    "DeepSeek",
        "url":     "https://api.deepseek.com/chat/completions",
        "model":   "deepseek-chat",
        "api_key": DEEPSEEK_API_KEY,
        "headers": {},   # Authorization 在下面统一注入
    },
    {
        "name":    "Anthropic",
        # Anthropic 也提供兼容 OpenAI 格式的端点
        "url":     "https://api.anthropic.com/v1/messages",
        "model":   "claude-haiku-4-5-20251001",   # 最快最便宜，备用够用
        "api_key": ANTHROPIC_API_KEY,
        "headers": {
            "anthropic-version": "2023-06-01",
            # Anthropic 用 x-api-key，不用 Authorization Bearer
            # 在 _call_anthropic 里单独处理
        },
    },
]


async def chat(prompt: str, max_tokens: int = 512) -> str:
    """
    依次尝试各 provider，返回第一个成功的文本响应。
    全部失败时抛出最后一个异常。
    """
    last_exc: Exception = RuntimeError("No providers configured")

    for provider in PROVIDERS:
        if not provider["api_key"]:
            log.debug("Skipping %s: no API key", provider["name"])
            continue
        try:
            text = await _call(provider, prompt, max_tokens)
            if provider["name"] != "DeepSeek":
                log.warning("Used fallback provider: %s", provider["name"])
            return text
        except Exception as e:
            log.warning("%s failed: %s — trying next provider", provider["name"], e)
            last_exc = e

    raise last_exc


async def _call(provider: dict[str, Any], prompt: str, max_tokens: int) -> str:
    if provider["name"] == "Anthropic":
        return await _call_anthropic(provider, prompt, max_tokens)
    return await _call_openai_compat(provider, prompt, max_tokens)


async def _call_openai_compat(
    provider: dict[str, Any], prompt: str, max_tokens: int
) -> str:
    """OpenAI Chat Completions 格式（DeepSeek 等）"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            provider["url"],
            headers={
                "Authorization": f"Bearer {provider['api_key']}",
                "Content-Type":  "application/json",
                **provider.get("headers", {}),
            },
            json={
                "model":      provider["model"],
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _call_anthropic(
    provider: dict[str, Any], prompt: str, max_tokens: int
) -> str:
    """Anthropic Messages API（格式与 OpenAI 略有不同）"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            provider["url"],
            headers={
                "x-api-key":         provider["api_key"],
                "anthropic-version": "2023-06-01",
                "Content-Type":      "application/json",
            },
            json={
                "model":      provider["model"],
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
    return resp.json()["content"][0]["text"]