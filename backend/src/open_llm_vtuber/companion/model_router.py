"""Shared selection and per-space authorization; local process ownership stays local."""
import asyncio
import contextlib
import json
from pathlib import Path

from .cloud_model import CloudModel
from .connections import ConnectionStore
from .usage import UsageStore


class BoundModel:
    def __init__(self, router, space):
        self.router, self.space = router, space

    @property
    def thinking_mode(self):
        return self.router.snapshot().get("thinking_mode", "off")

    @property
    def context_tokens(self):
        spec = self.router.cloud_spec
        return spec.context_tokens if spec else 32768

    @property
    def max_output_tokens(self):
        spec = self.router.cloud_spec
        return spec.max_output_tokens if spec else 8192

    async def client(self):
        route = self.router
        if not route.allowed(self.space):
            raise RuntimeError("主人尚未授权访客使用当前云模型")
        if not route.ready:
            raise RuntimeError("模型正在切换或不可用")
        if route.cloud_spec:
            spec = route.cloud_spec
            key = await asyncio.to_thread(route.connections.key, spec.id)
            # Recheck after a potentially interactive Keychain operation.
            if not route.allowed(self.space) or spec != route.cloud_spec:
                raise RuntimeError("模型配置或授权已改变，请重新发送")
            return CloudModel(spec, key, record=lambda result: route.usage.record({
                **result, "space": self.space, "connection": spec.id, "model": spec.model,
            }))
        return route.local.transport if route.local else route.fallback

    async def stream(self, messages, max_tokens=400, *, reasoning_budget=0, purpose="chat"):
        client = await self.client()
        kwargs = {"max_tokens": max_tokens, "reasoning_budget": reasoning_budget}
        if isinstance(client, CloudModel):
            kwargs["purpose"] = purpose
        async for part in client.stream(messages, **kwargs):
            yield part

    async def complete(self, messages, max_tokens=800, *, purpose="memory"):
        client = await self.client()
        if isinstance(client, CloudModel):
            return await client.complete(messages, max_tokens, purpose=purpose)
        return await client.complete(messages, max_tokens)


class ModelRouter:
    def __init__(self, root, local=None, *, fallback=None, connections=None, fallback_state=None):
        self.root = Path(root)
        self.local, self.fallback = local, fallback
        self.connections = connections if connections is not None else ConnectionStore(self.root / '.runtime/connections.json')
        self.usage = UsageStore(self.root / '.runtime/usage.sqlite3')
        self.selection_path = self.root / '.runtime/model-selection.json'
        self.selection = None
        self.cloud_id = None
        self.task = None
        self.on_change = None
        self._phase, self.error = None, ""
        self.fallback_state = fallback_state or {}
        if local:
            local.on_change = self.notify

    @property
    def cloud_spec(self):
        return self.connections.get(self.cloud_id) if self.cloud_id else None

    @property
    def phase(self):
        return self._phase or (self.local.phase if self.local else 'ready')

    @property
    def ready(self):
        return self.phase == 'ready' and (bool(self.cloud_id) or self.local is None or self.local.ready)

    def allowed(self, space):
        return space == 'owner' or not self.cloud_id or self.cloud_spec.allow_guests

    def bind(self, space):
        return BoundModel(self, space)

    def snapshot(self, role='owner'):
        state = dict(self.local.snapshot() if self.local else self.fallback_state)
        available = list(state.get('available_models', []))
        if role == 'owner':
            available += [{"key": "cloud:" + spec.id, "label": spec.name or spec.model} for spec in self.connections.specs.values()]
        if self.cloud_spec:
            spec = self.cloud_spec
            state.update(active_model_key='cloud:' + spec.id, model=spec.model,
                         thinking_mode='provider-default', supports_vision=spec.supports_vision and spec.allow_images,
                         cloud_provider=spec.provider, cloud_model=spec.model,
                         allow_images=spec.allow_images, cloud_guests_allowed=spec.allow_guests)
        state.update(managed_models=bool(self.local or self.cloud_id), cloud=bool(self.cloud_id), model_phase=self.phase,
                     available_models=available, model_error=self.error or state.get('model_error', ''))
        return state

    async def notify(self):
        if self.on_change:
            await self.on_change()

    def remember(self):
        self.selection_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.selection_path.with_suffix('.tmp')
        tmp.write_text(json.dumps({"selection": self.selection}))
        tmp.replace(self.selection_path)

    async def start(self, default, *, force=False):
        selected = default
        if self.selection_path.exists() and not force:
            selected = json.loads(self.selection_path.read_text()).get('selection') or default
        if selected.startswith('cloud:'):
            try:
                await self.select(selected)
            except Exception:
                self._phase, self.error = 'error', '保存的云连接不可用，请检查连接或手动切回本地'
        elif self.local:
            await self.local.start(selected)
            self.selection = selected

    def request_switch(self, selection):
        if self.task and not self.task.done():
            raise RuntimeError('模型正在切换，请稍候')
        if isinstance(selection, str) and not selection.startswith('cloud:'):
            if not self.local:
                raise ValueError('本地模型未启用托管')
            self.local.request_switch(selection)
            self.cloud_id, self._phase = None, None
            self.task = asyncio.create_task(self._finish_local(selection))
        else:
            self._phase = 'stopping'
            self.task = asyncio.create_task(self._requested(selection))

    async def _finish_local(self, selection):
        await self.local.task
        if self.local.ready and self.local.active_key == selection:
            self.selection = selection
            self.remember()
        await self.notify()

    async def _requested(self, selection):
        try:
            await self.select(selection)
        except Exception:
            self._phase, self.error = 'error', '云连接不可用，请检查配置和钥匙串后重试'
            await self.notify()

    async def select(self, selection):
        if not isinstance(selection, str):
            raise ValueError('模型选择无效')
        if selection.startswith('cloud:'):
            spec = self.connections.get(selection[6:])
            await asyncio.to_thread(self.connections.key, spec.id)
        elif selection not in {'27b', 'qwen38-rp'} or not self.local:
            raise ValueError('当前本地模型未启用托管或模型选择无效')
        self._phase, self.error = 'stopping', ''
        await self.notify()
        try:
            if selection.startswith('cloud:'):
                if self.local:
                    await self.local.stop_process()
                    self.local.active_key = None
                self.cloud_id = selection[6:]
                self._phase = 'ready'
            else:
                self.cloud_id = None
                self._phase = None
                self.local.request_switch(selection)
                await self.local.task
                if not self.local.ready or self.local.active_key != selection:
                    raise RuntimeError(self.local.error or '本地模型加载失败')
            self.selection = selection
            self.remember()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._phase, self.error = 'error', str(exc)
        await self.notify()

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.local:
            await self.local.close()
        self.usage.close()

    async def test_connection(self, key):
        spec = self.connections.get(key)
        credential = await asyncio.to_thread(self.connections.key, key)
        client = CloudModel(spec, credential, record=lambda result: self.usage.record({
            **result, 'space': 'owner', 'connection': key, 'model': spec.model,
        }))
        await client.complete([{'role': 'user', 'content': 'Reply only OK.'}], 16, purpose='connection-test')
