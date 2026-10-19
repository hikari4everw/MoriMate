import json
import asyncio
import httpx
import pytest

from open_llm_vtuber.companion.connections import ConnectionSpec


def client(provider='openai', events=None, *, done=True, code=200, records=None):
    from open_llm_vtuber.companion.cloud_model import CloudModel
    def respond(request):
        if records is not None:
            records.append(request)
        text = ''.join('data: ' + json.dumps(e) + '\n\n' for e in (events or []))
        return httpx.Response(code, text=text + ('data: [DONE]\n\n' if done else ''), headers={'content-type': 'text/event-stream'})
    return CloudModel(ConnectionSpec('test', provider, 'chosen'), 'secret', transport=httpx.MockTransport(respond))


@pytest.mark.parametrize('provider', ['openai', 'deepseek', 'gemini', 'compatible'])
async def test_cloud_stream_excludes_reasoning_and_local_parameters(provider):
    records = []
    if provider == 'compatible':
        provider = 'openai'
    model = client(provider, [
        {'choices': [{'delta': {'reasoning_content': 'hidden'}}]},
        {'choices': [{'delta': {'content': '你'}}]},
        {'choices': [{'delta': {'content': '好'}, 'finish_reason': 'stop'}]},
        {'choices': [], 'usage': {'prompt_tokens': 11, 'completion_tokens': 2}},
    ], records=records)
    assert ''.join([p async for p in model.stream([], reasoning_budget=128)]) == '你好'
    body = json.loads(records[0].content)
    assert records[0].headers['authorization'] == 'Bearer secret'
    assert not {'enable_thinking', 'chat_template_kwargs', 'reasoning_budget_tokens', 'top_k'} & body.keys()
    assert model.last_usage['prompt_tokens'] == 11


async def test_missing_end_is_not_success_even_with_partial_text():
    model = client(events=[{'choices': [{'delta': {'content': 'partial'}}]}], done=False)
    with pytest.raises(RuntimeError, match='中断'):
        await model.complete([])


async def test_length_and_refusal_are_explicit():
    for reason, message in [('length', '截断'), ('content_filter', '拒绝')]:
        model = client(events=[{'choices': [{'delta': {}, 'finish_reason': reason}]}])
        with pytest.raises(RuntimeError, match=message):
            await model.complete([])


@pytest.mark.parametrize('status', [401, 429, 500])
async def test_cloud_http_errors_do_not_leak_key_or_retry(status):
    records = []
    model = client(code=status, records=records)
    with pytest.raises(RuntimeError) as error:
        await model.complete([])
    assert 'secret' not in str(error.value)
    assert len(records) == 1


async def test_cancel_closes_http_stream():
    class Body(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            await asyncio.Event().wait()
        async def aclose(self):
            self.closed = True
    body = Body()
    from open_llm_vtuber.companion.cloud_model import CloudModel
    model = CloudModel(ConnectionSpec('test', 'openai', 'chosen'), 'secret', transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=body)))
    started = asyncio.Event()
    async def run():
        async for _ in model.stream([]):
            started.set()
    task = asyncio.create_task(run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert body.closed
