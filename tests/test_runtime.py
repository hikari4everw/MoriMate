import asyncio

from open_llm_vtuber.companion.memory import MemoryStore
from open_llm_vtuber.companion.runtime import CompanionRuntime


class FakeModel:
    def __init__(self):
        self.prompts = []

    async def stream(self, messages, max_tokens=400):
        self.prompts.append(messages)
        yield '{"zh":"你好。","ja":"こんにちは。","emotion":"joy"}\n'


class NoVoice:
    async def async_generate_audio(self, text):
        raise AssertionError("mute must bypass TTS")


async def test_muted_reply_keeps_text_and_stores_history(tmp_path):
    sent = []

    async def send(payload):
        sent.append(payload)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), FakeModel(), NoVoice(), send, "persona"
    )
    runtime.muted = True
    await runtime.start("你好")
    await runtime.task
    audio = [m for m in sent if m["type"] == "audio"]
    assert audio[0]["display_text"]["text"] == "你好。"
    assert audio[0]["audio"] is None
    assert [s["kind"] for s in runtime.store.sources()] == ["user", "assistant"]
    assert not runtime.busy


async def test_user_preempts_slow_tts_and_old_generation_never_sends(tmp_path):
    entered = asyncio.Event()
    sent = []

    class SlowVoice:
        async def async_generate_audio(self, text):
            entered.set()
            await asyncio.sleep(100)

    async def send(payload):
        sent.append(payload)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), FakeModel(), SlowVoice(), send, "persona"
    )
    await runtime.start("旧问题")
    await entered.wait()
    old_turn = runtime.turn_id
    runtime.muted = True
    await runtime.start("新问题")
    await runtime.task
    assert not [m for m in sent if m["type"] == "audio" and m["turn_id"] == old_turn]
    assert any(m["type"] == "audio" and m["turn_id"] == runtime.turn_id for m in sent)


async def test_model_error_restores_idle_and_reports_error(tmp_path):
    class BrokenModel:
        async def stream(self, *args, **kwargs):
            yield "not JSON\n"

    sent = []

    async def send(p):
        sent.append(p)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), BrokenModel(), NoVoice(), send, "persona"
    )
    await runtime.start("你好")
    await runtime.task
    assert any(m["type"] == "error" for m in sent)
    assert not runtime.busy


async def test_deleted_memory_not_in_next_model_prompt(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    source = store.add_source("user", "我喜欢咖啡")
    item = store.remember("preference", "喜欢咖啡", [source], "喜欢咖啡")
    model = FakeModel()

    async def send(_):
        pass

    runtime = CompanionRuntime(store, model, NoVoice(), send, "persona")
    runtime.muted = True
    store.forget(item)
    await runtime.start("聊聊今天")
    await runtime.task
    assert "咖啡" not in str(model.prompts[-1])


async def test_proactive_request_cannot_preempt_user(tmp_path):
    entered = asyncio.Event()

    class SlowModel:
        async def stream(self, *args, **kwargs):
            entered.set()
            await asyncio.sleep(100)
            yield ""

    async def send(_):
        pass

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), SlowModel(), NoVoice(), send, "persona"
    )
    await runtime.start("用户提问")
    await entered.wait()
    turn = runtime.turn_id
    await runtime.start("游戏评论", proactive=True)
    assert runtime.turn_id == turn
    await runtime.stop()


async def test_expired_comment_never_synthesizes(tmp_path):
    import time

    sent = []

    async def send(p):
        sent.append(p)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), FakeModel(), NoVoice(), send, "persona"
    )
    await runtime.start("过时评论", proactive=True, expires_at=time.monotonic() - 1)
    if runtime.task:
        await runtime.task
    assert not [p for p in sent if p["type"] == "audio"]


async def test_history_keeps_bilingual_format_on_second_turn(tmp_path):
    model = FakeModel()

    async def send(_):
        pass

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), model, NoVoice(), send, "persona"
    )
    runtime.muted = True
    await runtime.start("第一轮")
    await runtime.task
    await runtime.start("第二轮")
    await runtime.task
    import json

    assistant = [m for m in model.prompts[-1] if m["role"] == "assistant"]
    assert json.loads(assistant[0]["content"])["ja"] == "こんにちは。"
