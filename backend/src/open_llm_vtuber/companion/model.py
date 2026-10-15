"""Local OpenAI-compatible transport; closing the stream cancels generation."""

import json
import httpx
from urllib.parse import urlparse


class LocalModel:
    def __init__(self, base_url, model):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("V1 模型地址只允许本机 HTTP 服务")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model

    async def stream(self, messages, max_tokens=400):
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            async with client.stream(
                "POST",
                self.url,
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                    "enable_thinking": False,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    choices = json.loads(data).get("choices", [])
                    if choices:
                        text = choices[0].get("delta", {}).get("content")
                        if text:
                            yield text

    async def complete(self, messages, max_tokens=800):
        return "".join([part async for part in self.stream(messages, max_tokens)])
