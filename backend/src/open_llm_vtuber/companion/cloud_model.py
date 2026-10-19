"""OpenAI-compatible SSE without local inference parameters or automatic retries."""
import asyncio
import json
import time

import httpx

from .model import GenerationTruncated
from .model_client import GenerationRequest, GenerationResult, ModelEvent


class CloudModel:
    thinking_mode = "provider-default"

    def __init__(self, spec, api_key, *, transport=None, record=None):
        self.spec, self.model = spec, spec.model
        self._api_key, self.transport, self.record = api_key, transport, record
        self.last_usage = {}
        self.last_first_token_seconds = None
        self.last_reasoning_characters = 0

    async def stream_events(self, request):
        body = {"model": self.model, "messages": request.messages, "stream": True,
                "stream_options": {"include_usage": True}}
        limit = min(request.max_tokens, self.spec.max_output_tokens)
        body["max_completion_tokens" if self.spec.provider == "openai" else "max_tokens"] = limit
        if self.spec.provider == "openai":
            body["store"] = False
        started, usage, finish, status = time.monotonic(), {}, None, "error"
        self.last_usage, self.last_first_token_seconds = {}, None
        self.last_reasoning_characters = 0
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120, connect=15), trust_env=False,
                follow_redirects=False, proxy=self.spec.proxy or None,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST", self.spec.base_url + "/chat/completions", json=body,
                    headers={"Authorization": "Bearer " + self._api_key},
                ) as response:
                    if response.status_code != 200:
                        labels = {401: "鉴权失败", 403: "无访问权限", 429: "限流或额度不足"}
                        raise RuntimeError(f"云API {labels.get(response.status_code, '请求失败')}（HTTP {response.status_code}），未自动重试")
                    ended = False
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            ended = True
                            break
                        try:
                            event = json.loads(data)
                        except (ValueError, TypeError):
                            raise RuntimeError("云API流格式无效，已停止") from None
                        if not isinstance(event, dict) or event.get("error"):
                            raise RuntimeError("云API流返回错误，已停止")
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                            self.last_usage = usage
                            yield ModelEvent("usage", usage=usage)
                        for choice in event.get("choices") or []:
                            if choice.get("index", 0) != 0:
                                continue
                            delta = choice.get("delta") or {}
                            self.last_reasoning_characters += len(delta.get("reasoning_content") or delta.get("reasoning") or "")
                            if delta.get("refusal"):
                                raise RuntimeError("云模型拒绝了本次请求")
                            text = delta.get("content")
                            if isinstance(text, str) and text:
                                if self.last_first_token_seconds is None:
                                    self.last_first_token_seconds = time.monotonic() - started
                                yield ModelEvent("text", text=text)
                            reason = choice.get("finish_reason")
                            if reason:
                                finish = reason
                                if reason == "length":
                                    raise GenerationTruncated()
                                if reason != "stop":
                                    raise RuntimeError("云模型拒绝或无法完成本次回复")
                                ended = True
                    if not ended:
                        raise RuntimeError("云API连接中断，已保留完整句子；可手动重试")
                    status = "ok"
                    yield ModelEvent("end", usage=usage, finish_reason=finish or "stop")
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except (httpx.HTTPError, OSError):
            raise RuntimeError("云API网络连接失败或超时，未自动重试；请检查地址和代理") from None
        finally:
            if self.record:
                self.record({"purpose": request.purpose, "usage": usage or None, "status": status,
                             "seconds": round(time.monotonic() - started, 3)})

    async def generate(self, request):
        parts, usage, finish = [], {}, None
        async for event in self.stream_events(request):
            if event.kind == "text":
                parts.append(event.text)
            if event.usage:
                usage = event.usage
            if event.finish_reason:
                finish = event.finish_reason
        return GenerationResult("".join(parts), usage, finish)

    async def stream(self, messages, max_tokens=400, *, reasoning_budget=0, purpose="chat"):
        async for event in self.stream_events(GenerationRequest(messages, max_tokens, purpose)):
            if event.kind == "text":
                yield event.text

    async def complete(self, messages, max_tokens=800, *, purpose="memory"):
        return (await self.generate(GenerationRequest(messages, max_tokens, purpose))).text
