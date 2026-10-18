"""Local OpenAI-compatible transport; closing the stream cancels generation."""

import json
import time
import httpx
from urllib.parse import urlparse


class GenerationTruncated(RuntimeError):
    def __init__(self):
        super().__init__("回复达到生成预算被截断，已保留完整句子；可要求继续")


class LocalModel:
    def __init__(self, base_url, model, *, backend="mlx", thinking_mode="limited"):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("V1 模型地址只允许本机 HTTP 服务")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        if backend not in {"mlx", "llama.cpp"}:
            raise ValueError("未知模型运行时")
        if thinking_mode not in {"off", "limited", "full"}:
            raise ValueError("未知思考模式")
        self.backend, self.thinking_mode = backend, thinking_mode
        self.last_usage = {}
        self.last_first_token_seconds = None
        self.last_reasoning_characters = 0

    async def stream(self, messages, max_tokens=400, *, reasoning_budget=0):
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "enable_thinking": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "stream_options": {"include_usage": True},
        }
        if self.backend == "llama.cpp":
            # Maintenance passes zero even when evaluation requests full thinking.
            if reasoning_budget and self.thinking_mode == "full":
                reasoning_budget = -1
            elif self.thinking_mode == "off":
                reasoning_budget = 0
            body.update(
                temperature=0.6,
                top_p=0.95,
                top_k=20,
                reasoning_budget_tokens=reasoning_budget,
                reasoning_format="deepseek",
                chat_template_kwargs={
                    "enable_thinking": reasoning_budget != 0,
                    "preserve_thinking": False,
                },
            )
            del body["enable_thinking"]
        self.last_usage = {}
        self.last_first_token_seconds = None
        self.last_reasoning_characters = 0
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            async with client.stream(
                "POST",
                self.url,
                json=body,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    event = json.loads(data)
                    if event.get("usage"):
                        self.last_usage = event["usage"]
                    choices = event.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        self.last_reasoning_characters += len(
                            delta.get("reasoning_content")
                            or delta.get("reasoning")
                            or ""
                        )
                        text = delta.get("content")
                        if text:
                            if self.last_first_token_seconds is None:
                                self.last_first_token_seconds = (
                                    time.monotonic() - started
                                )
                            yield text
                        if choices[0].get("finish_reason") == "length":
                            raise GenerationTruncated()

    async def complete(self, messages, max_tokens=800):
        return "".join([part async for part in self.stream(messages, max_tokens)])
