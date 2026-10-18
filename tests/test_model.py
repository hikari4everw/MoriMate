import pytest
import httpx
import json
import open_llm_vtuber.companion.model as model_module
from open_llm_vtuber.companion.model import LocalModel


def test_v1_rejects_nonlocal_model_endpoint():
    with pytest.raises(ValueError, match="本机"):
        LocalModel("https://example.com/v1", "anything")


def transport(monkeypatch, chunks, requests):
    original = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        text = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(
            200,
            text=text + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    monkeypatch.setattr(
        model_module.httpx,
        "AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(respond)),
    )


async def test_llama_request_budget_and_reasoning_are_separate(monkeypatch):
    requests = []
    transport(
        monkeypatch,
        [
            {
                "choices": [
                    {
                        "delta": {"reasoning_content": "private reasoning"},
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {"content": '{"zh":"你好","ja":"こんにちは"}\n'},
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ],
        requests,
    )
    model = LocalModel("http://127.0.0.1:18080/v1", "candidate", backend="llama.cpp")
    output = "".join(
        [
            chunk
            async for chunk in model.stream([], max_tokens=8192, reasoning_budget=128)
        ]
    )
    assert "private" not in output
    assert (
        model.last_first_token_seconds is not None
        and model.last_first_token_seconds >= 0
    )
    assert model.last_reasoning_characters == len("private reasoning")
    assert json.loads(output)["zh"] == "你好"
    assert requests[0]["chat_template_kwargs"]["enable_thinking"] is True
    assert requests[0]["reasoning_budget_tokens"] == 128
    assert requests[0]["max_tokens"] == 8192


async def test_memory_completion_disables_thinking(monkeypatch):
    requests = []
    transport(
        monkeypatch,
        [{"choices": [{"delta": {"content": "{}"}, "finish_reason": "stop"}]}],
        requests,
    )
    model = LocalModel("http://localhost:18080/v1", "candidate", backend="llama.cpp")
    assert await model.complete([]) == "{}"
    assert requests[0]["reasoning_budget_tokens"] == 0
    assert requests[0]["chat_template_kwargs"]["enable_thinking"] is False
    assert requests[0]["max_tokens"] == 800


async def test_length_limit_reports_truncation_after_yielding_content(monkeypatch):
    requests, output = [], []
    transport(
        monkeypatch,
        [
            {
                "choices": [
                    {
                        "delta": {"content": "complete line\npartial"},
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
        ],
        requests,
    )
    with pytest.raises(RuntimeError, match="截断"):
        async for part in LocalModel("http://localhost:18080/v1", "old").stream([]):
            output.append(part)
    assert "".join(output) == "complete line\npartial"


@pytest.mark.parametrize('mode,want', [('off', 0), ('limited', 128), ('full', -1)])
async def test_live_thinking_modes_and_maintenance_budget(monkeypatch, mode, want):
    requests = []
    transport(monkeypatch, [{'choices': [{'delta': {'content': '{}'}, 'finish_reason': 'stop'}]}], requests)
    model = LocalModel('http://127.0.0.1:18080/v1', 'candidate', backend='llama.cpp')
    model.thinking_mode = mode
    assert ''.join([chunk async for chunk in model.stream([], reasoning_budget=128)]) == '{}'
    assert requests[0]['reasoning_budget_tokens'] == want
    assert requests[0]['chat_template_kwargs']['enable_thinking'] is (want != 0)
    await model.complete([])
    assert requests[1]['reasoning_budget_tokens'] == 0
    assert requests[1]['chat_template_kwargs']['enable_thinking'] is False
