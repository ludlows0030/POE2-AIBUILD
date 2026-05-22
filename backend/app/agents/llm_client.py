"""统一 LLM 客户端 — 支持 DeepSeek / Anthropic 双后端。

DeepSeek: OpenAI-compatible API at api.deepseek.com
Anthropic: Native Anthropic SDK

切换方式：设置环境变量 LLM_PROVIDER=deepseek 或 LLM_PROVIDER=anthropic
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """双后端 LLM 客户端，统一 messages.create 接口。"""

    def __init__(self) -> None:
        self.provider = settings.LLM_PROVIDER
        if self.provider == "deepseek":
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
            )
            self._model = settings.DEEPSEEK_MODEL
        elif self.provider == "anthropic":
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            self._model = settings.ANTHROPIC_MODEL
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {self.provider}")

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return self.provider

    async def messages_create(
        self,
        system: str | None = None,
        messages: list[dict[str, str]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """统一的 messages.create → 返回纯文本内容。

        DeepSeek: 使用 OpenAI SDK (system 放顶层参数)
        Anthropic: 使用 Anthropic SDK (system 放顶层参数)
        """
        temp = temperature if temperature is not None else settings.LLM_TEMPERATURE
        max_tok = max_tokens if max_tokens is not None else settings.LLM_MAX_TOKENS

        if self.provider == "deepseek":
            return await self._deepseek_create(system, messages or [], max_tok, temp)
        else:
            return await self._anthropic_create(system, messages or [], max_tok, temp)

    async def _deepseek_create(
        self,
        system: str | None,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        msgs: list[dict[str, Any]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=msgs,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def _anthropic_create(
        self,
        system: str | None,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = await self._client.messages.create(**kwargs)
        return response.content[0].text


# 模块级单例
llm_client = LLMClient()
