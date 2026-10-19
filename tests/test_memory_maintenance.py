import asyncio
import json
import pytest
from open_llm_vtuber.companion.memory import MemoryStore


class Summarizer:
    def __init__(self):
        self.requests = []
    async def complete(self, messages):
        self.requests.append(messages)
        return json.dumps({'summary': '已记录的事情', 'memories': []})


def service(store, model):
    from open_llm_vtuber.companion.memory_maintenance import MemoryMaintenance
    return MemoryMaintenance(store, model)


async def test_oldest_pending_sources_survive_large_backlog_and_scene_changes(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    for i in range(130):
        store.add_source('user', f'旧资料{i}', 'game:A')
    for i in range(6):
        store.add_source('user', f'新资料{i}', 'game:B')
    model = Summarizer()
    result = await service(store, model).run_batch()
    payload = json.loads(model.requests[0][1]['content'])
    assert [s['id'] for s in payload['sources']] == list(range(1, 41))
    assert result['scene'] == 'game:A'
    pending = store.db.execute('SELECT COUNT(*) FROM sources WHERE processed=0').fetchone()[0]
    assert pending == 96


async def test_failed_request_pauses_until_explicit_retry(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    for _ in range(6):
        store.add_source('user', '新资料')
    class Broken:
        calls = 0
        async def complete(self, messages):
            self.calls += 1
            raise RuntimeError('限流')
    model = Broken()
    worker = service(store, model)
    with pytest.raises(RuntimeError):
        await worker.run_batch()
    assert await worker.run_batch() == {'state': 'paused'}
    assert model.calls == 1
    worker.retry()
    with pytest.raises(RuntimeError):
        await worker.run_batch()
    assert model.calls == 2


async def test_forget_during_summarization_discards_stale_result(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    ids = [store.add_source('user', '我喜欢咖啡') for _ in range(6)]
    item = store.remember('preference', '喜欢咖啡', [ids[0]], '喜欢咖啡')
    entered, release = asyncio.Event(), asyncio.Event()
    class Slow:
        async def complete(self, messages):
            entered.set()
            await release.wait()
            return json.dumps({'summary': '咖啡', 'memories': [{'kind': 'preference', 'text': '爱喝咖啡', 'source_ids': [ids[0]], 'evidence': '喜欢咖啡'}]})
    task = asyncio.create_task(service(store, Slow()).run_batch())
    await entered.wait()
    store.forget(item)
    release.set()
    assert (await task)['state'] == 'stale'
    assert store.memories() == [] and store.summary('daily') is None


async def test_cancellation_does_not_mark_sources_processed(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    for _ in range(6):
        store.add_source('user', '资料')
    entered = asyncio.Event()
    class Slow:
        async def complete(self, messages):
            entered.set()
            await asyncio.Event().wait()
    worker = service(store, Slow())
    task = asyncio.create_task(worker.run_batch())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.db.execute('SELECT SUM(processed) FROM sources').fetchone()[0] == 0
    assert not worker.paused


async def test_failed_maintenance_stays_paused_after_restart(tmp_path):
    path = tmp_path / 'm.db'
    store = MemoryStore(path)
    for _ in range(6):
        store.add_source('user', '资料')
    class Broken:
        async def complete(self, messages):
            raise RuntimeError('额度不足')
    with pytest.raises(RuntimeError):
        await service(store, Broken()).run_batch()
    store.close()
    reopened = MemoryStore(path)
    model = Summarizer()
    worker = service(reopened, model)
    assert await worker.run_batch() == {'state': 'paused'}
    assert model.requests == []
    worker.retry()
    assert (await worker.run_batch())['state'] == 'saved'
