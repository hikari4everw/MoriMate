import asyncio
import pytest
from open_llm_vtuber.companion.connections import ConnectionStore
from test_connections import Vault


class LocalService:
    def __init__(self):
        self.ready = True
        self.phase = 'ready'
        self.active_key = '27b'
        self.transport = object()
        self.stopped = False
        self.on_change = None
    async def stop_process(self):
        self.stopped = True
    async def start(self, key):
        self.active_key, self.ready = key, True
        self.phase = 'ready'
    def snapshot(self):
        return {'active_model_key': self.active_key, 'model_phase': self.phase, 'available_models': []}
    async def close(self):
        await self.stop_process()


def router(tmp_path):
    from open_llm_vtuber.companion.model_router import ModelRouter
    store = ConnectionStore(tmp_path / 'cloud.json', Vault())
    store.save({'id': 'cloud', 'provider': 'openai', 'model': 'chosen'}, 'key')
    local = LocalService()
    return ModelRouter(tmp_path, local, connections=store), local


async def test_select_cloud_releases_only_owned_local_and_persists(tmp_path):
    route, local = router(tmp_path)
    changed = []
    async def notify():
        changed.append(route.snapshot()['model_phase'])
    route.on_change = notify
    await route.select('cloud:cloud')
    assert local.stopped
    assert route.snapshot()['cloud'] and route.ready
    assert changed[0] == 'stopping' and changed[-1] == 'ready'
    restored, _ = router(tmp_path)
    await restored.start('27b')
    assert restored.snapshot()['active_model_key'] == 'cloud:cloud'
    assert not route.allowed('guest')
    assert route.allowed('owner')
    route.connections.save({'id': 'cloud', 'provider': 'openai', 'model': 'chosen', 'allow_guests': True})
    assert route.allowed('guest')


async def test_guest_binding_checks_authorization_before_any_cloud_call(tmp_path):
    route, _ = router(tmp_path)
    await route.select('cloud:cloud')
    with pytest.raises(RuntimeError, match='授权'):
        await route.bind('guest').complete([])
    assert route.usage.rows() == []


async def test_usage_unknown_and_purpose_are_preserved(tmp_path):
    from open_llm_vtuber.companion.usage import UsageStore
    store = UsageStore(tmp_path / 'usage.sqlite3')
    store.record({'space': 'guest-1', 'connection': 'cloud', 'purpose': 'memory', 'usage': None, 'status': 'cancelled', 'seconds': 1})
    row = store.rows()[0]
    assert row['usage'] is None and row['purpose'] == 'memory'
    assert row['space'] == 'guest-1'
    store.close()
