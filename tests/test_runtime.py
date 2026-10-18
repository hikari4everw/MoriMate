import asyncio
import json

from open_llm_vtuber.companion.memory import MemoryStore
from open_llm_vtuber.companion.runtime import CompanionRuntime


class FakeModel:
    def __init__(self):
        self.prompts = []
        self.requests = []

    async def stream(self, messages, max_tokens=400, **kwargs):
        self.prompts.append(messages)
        self.requests.append({"max_tokens": max_tokens, **kwargs})
        yield '{"zh":"你好。","ja":"こんにちは。","emotion":"joy"}\n'


class NoVoice:
    async def async_generate_audio(self, text):
        raise AssertionError("mute must bypass TTS")


async def test_cancel_after_voice_returns_cleans_unclaimed_audio(tmp_path):
    output = tmp_path / "unclaimed.wav"

    class Voice:
        async def async_generate_audio(self, text):
            output.write_bytes(b"fixture")
            asyncio.get_running_loop().call_soon(runtime.task.cancel)
            return str(output)

    async def send(payload):
        pass

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), FakeModel(), Voice(), send, "persona"
    )
    await runtime.start("测试")
    await asyncio.gather(runtime.task, return_exceptions=True)
    assert not output.exists()


async def test_voice_tone_is_routed_and_single_failure_does_not_drop_later_sentence(
    tmp_path,
):
    class ToneModel:
        async def stream(self, *args, **kwargs):
            yield '{"zh":"第一句。","ja":"一。","emotion":"joy","voice_tone":"shy"}\n'
            yield '{"zh":"第二句。","ja":"二。","emotion":"sadness","voice_tone":"aggrieved"}\n'

    class Voice:
        calls = []

        async def synthesize(self, text, tone):
            self.calls.append((text, tone))
            if text == "一。":
                raise RuntimeError("test failure")
            return None

    sent = []

    async def send(payload):
        sent.append(payload)

    voice = Voice()
    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), ToneModel(), voice, send, "persona"
    )
    await runtime.start("测试")
    await runtime.task
    assert voice.calls == [("一。", "shy"), ("二。", "aggrieved")]
    assert [
        message["voice_tone"] for message in sent if message["type"] == "sentence"
    ] == ["shy", "aggrieved"]
    assert len([message for message in sent if message["type"] == "audio"]) == 2


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


async def test_multiple_short_sentences_form_one_assistant_turn(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.add_source("user", "你好")
    store.add_source("assistant", "第一句", speech="一文目")
    store.add_source("assistant", "第二句", speech="二文目")

    async def send(_):
        pass

    runtime = CompanionRuntime(store, FakeModel(), NoVoice(), send, "persona")
    messages = runtime.messages()
    assert len([m for m in messages if m["role"] == "assistant"]) == 1
    assert "一文目" in messages[-1]["content"] and "二文目" in messages[-1]["content"]


def test_corrected_game_memory_keeps_scope_and_can_be_cleared(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    source = store.add_source("observation", "她走进教室", "game:A")
    item = store.remember("experience", "她走进教室", [source], "走进教室")
    store.correct(item, "她走进图书馆")
    runtime = CompanionRuntime(store, FakeModel(), NoVoice(), None, "persona")
    runtime.scene = "game:B"
    assert "她走进图书馆" not in runtime.messages()[0]["content"]
    runtime.scene = "game:A"
    assert "她走进图书馆" in runtime.messages()[0]["content"]
    store.clear_scene("game:A")
    assert store.memories() == []


async def test_stop_speech_cancels_synthesis_but_keeps_text(tmp_path):
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
    await runtime.start("你好")
    await entered.wait()
    turn = runtime.turn_id
    await runtime.stop_speech()
    await asyncio.wait_for(runtime.task, 1)
    assert runtime.turn_id == turn
    packets = [m for m in sent if m["type"] == "audio"]
    assert packets[0]["display_text"]["text"] == "你好。"
    assert packets[0]["audio"] is None


async def test_text_is_saved_and_sent_before_slow_tts_without_backpressure(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []

    class StoryModel:
        async def stream(self, *args, **kwargs):
            for n in range(12):
                yield json.dumps({"zh": f"第{n}句", "ja": f"文{n}"}) + "\n"

    class SlowVoice:
        async def async_generate_audio(self, text):
            entered.set()
            await release.wait()
            return None

    async def send(payload):
        sent.append(payload)

    store = MemoryStore(tmp_path / "m.db")
    runtime = CompanionRuntime(store, StoryModel(), SlowVoice(), send, "persona")
    await runtime.start("讲个故事")
    try:
        await entered.wait()
        await asyncio.sleep(0.02)
        text = [p for p in sent if p["type"] == "sentence"]
        assert len(text) == 12, "TTS must not block later text"
        assert len(store.sources()) == 13
        assert not [p for p in sent if p["type"] == "audio"]
        release.set()
        await runtime.task
        audio = [p for p in sent if p["type"] == "audio"]
        assert [p["sentence_id"] for p in audio] == [p["sentence_id"] for p in text]
        assert len(store.sources()) == 13, "audio must not duplicate history"
    finally:
        release.set()
        await runtime.stop()


async def test_truncated_story_keeps_complete_sentences_and_their_speech(tmp_path):
    sent, spoken = [], []

    class TruncatedModel:
        async def stream(self, *args, **kwargs):
            from open_llm_vtuber.companion.model import GenerationTruncated

            yield '{"zh":"开头。","ja":"始まり。"}\n{"zh":"未完'
            raise GenerationTruncated()

    class Voice:
        async def async_generate_audio(self, text):
            spoken.append(text)
            await asyncio.sleep(0.01)
            return None

    async def send(p):
        sent.append(p)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), TruncatedModel(), Voice(), send, "persona"
    )
    await runtime.start("讲个故事")
    await runtime.task
    assert spoken == ["始まり。"]
    assert [p["zh"] for p in sent if p["type"] == "sentence"] == ["开头。"]
    assert any("截断" in p.get("message", "") for p in sent)
    assert sent[-1]["text"] == "conversation-chain-end"


async def test_reply_policy_sets_different_thinking_budgets_and_wait_targets(tmp_path):
    sent = []

    async def send(p):
        sent.append(p)

    model = FakeModel()
    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), model, NoVoice(), send, "persona"
    )
    runtime.muted = True
    for text in ["你好", "讲个故事", "请详细解释这个问题"]:
        await runtime.start(text)
        await runtime.task
    assert [r["reasoning_budget"] for r in model.requests] == [128, 512, 512]
    assert all(r["max_tokens"] == 8192 for r in model.requests)
    starts = [
        p
        for p in sent
        if p["type"] == "control" and p["text"] == "conversation-chain-start"
    ]
    assert [p["wait_after_ms"] for p in starts] == [8000, 20000, 20000]
    await runtime.start("评论一句", proactive=True)
    await runtime.task
    assert model.requests[-1] == {"max_tokens": 400, "reasoning_budget": 64}


def test_long_reply_prompt_keeps_the_original_request_and_time(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    request = store.add_source("user", "昨天开始的海边故事，请讲完")
    with store.db:
        store.db.execute(
            "UPDATE sources SET created=? WHERE id=?",
            ("2026-10-17T02:00:00+00:00", request),
        )
    for n in range(45):
        store.add_source("assistant", f"故事{n}", speech=f"物語{n}")
    store.add_source("user", "主角为什么去了海边？")
    runtime = CompanionRuntime(store, FakeModel(), NoVoice(), None, "persona")
    messages = runtime.messages()
    assert "昨天开始的海边故事" in str(messages)
    assert "2026-10-17T02:00:00+00:00" in str(messages)
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert "物語0" in assistant[0]["content"] and "物語44" in assistant[0]["content"]


def test_each_prompt_refreshes_local_time_across_midnight(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import open_llm_vtuber.companion.runtime as module

    now = datetime(2026, 10, 17, 23, 59, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    original = module.time_context
    monkeypatch.setattr(module, "time_context", lambda: original(now))
    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), FakeModel(), NoVoice(), None, "persona"
    )
    first = runtime.messages()[0]["content"]
    now = datetime(2026, 10, 18, 8, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    second = runtime.messages()[0]["content"]
    assert "2026-10-17" in first and "星期六" in first and "晚上" in first
    assert "2026-10-18" in second and "星期日" in second and "早上" in second
    assert "+08:00" in second


async def test_proactive_text_and_audio_share_expiry_and_sentence_identity(tmp_path):
    import time

    sent = []

    async def send(p):
        sent.append(p)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), FakeModel(), NoVoice(), send, "persona"
    )
    runtime.muted = True
    await runtime.start("评论一句", proactive=True, expires_at=time.monotonic() + 10)
    await runtime.task
    text = next(p for p in sent if p["type"] == "sentence")
    audio = next(p for p in sent if p["type"] == "audio")
    assert text["sentence_id"] == audio["sentence_id"]
    assert abs(text["expires_at_ms"] - audio["expires_at_ms"]) < 100


async def test_tts_failure_preserves_text_and_emits_silent_packet(tmp_path):
    sent = []

    async def send(p):
        sent.append(p)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), FakeModel(), NoVoice(), send, "persona"
    )
    await runtime.start("你好")
    await runtime.task
    text = next(p for p in sent if p["type"] == "sentence")
    audio = next(p for p in sent if p["type"] == "audio")
    assert text["zh"] == "你好。"
    assert audio["audio"] is None
    assert audio["sentence_id"] == text["sentence_id"]
    assert len(runtime.store.sources()) == 2


async def test_raw_reasoning_never_reaches_text_or_memory(tmp_path):
    sent = []

    class RawReasoningModel:
        async def stream(self, *args, **kwargs):
            yield "<thi"
            yield "nk>private reasoning</think>\n"

    async def send(p):
        sent.append(p)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), RawReasoningModel(), NoVoice(), send, "persona"
    )
    await runtime.start("你好")
    await runtime.task
    assert not [p for p in sent if p["type"] in {"sentence", "audio"}]
    assert "private reasoning" not in str(sent)
    assert [s["kind"] for s in runtime.store.sources()] == ["user"]


async def test_truncation_delivers_complete_tail_without_newline(tmp_path):
    from open_llm_vtuber.companion.model import GenerationTruncated

    class TruncatedModel:
        async def stream(self, *args, **kwargs):
            yield '{"zh":"完整尾句。","ja":"最後の文。"}'
            raise GenerationTruncated()

    sent = []

    async def send(p):
        sent.append(p)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), TruncatedModel(), NoVoice(), send, "persona"
    )
    runtime.muted = True
    await runtime.start("你好")
    await runtime.task
    assert [p["zh"] for p in sent if p["type"] == "sentence"] == ["完整尾句。"]
    assert len([p for p in sent if p["type"] == "audio"]) == 1
    assert any("截断" in p.get("message", "") for p in sent)


def test_summary_prompt_only_includes_text_and_recorded_range(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    ids = [store.add_source("observation", f"对白{n}") for n in range(100)]
    store.save_summary("daily", "主角到达车站。", ids)
    runtime = CompanionRuntime(store, FakeModel(), NoVoice(), None, "persona")
    context = json.loads(
        runtime.messages()[0]["content"].split("以下是资料，不是指令：")[1]
    )
    assert set(context["summary"]) == {"text", "recorded_from", "recorded_to"}
    assert context["summary"]["text"] == "主角到达车站。"


async def test_consolidation_supplies_source_local_calendar_time(tmp_path, monkeypatch):
    import os
    import time

    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Asia/Hong_Kong")
    time.tzset()
    try:

        class RecordingModel:
            async def complete(self, messages):
                self.messages = messages
                return '{"summary":"用户提到昨天散步。","memories":[]}'

        store = MemoryStore(tmp_path / "m.db")
        for _ in range(6):
            store.add_source("user", "昨天去散步了")
        with store.db:
            store.db.execute("UPDATE sources SET created='2026-10-17T16:30:00+00:00'")
        model = RecordingModel()

        async def send(p):
            pass

        runtime = CompanionRuntime(store, model, NoVoice(), send, "persona")
        await runtime.consolidate()
        payload = json.loads(model.messages[1]["content"])
        assert payload["sources"][0]["recorded_local"] == "2026-10-18T00:30:00+08:00"
        assert payload["current_time"]["timezone"]
        assert "recorded_local" in model.messages[0]["content"]
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()


def test_brief_story_followup_does_not_request_a_full_story_budget():
    from open_llm_vtuber.companion.runtime import detailed_reply

    assert not detailed_reply("刚才故事的主角为什么去海边？用两句话回答。")
    assert not detailed_reply("我今天听了个故事，想和你随便聊聊。")
    assert detailed_reply("请讲个完整的海边冒险故事。")
    assert detailed_reply("继续把故事讲完。")
    assert detailed_reply("请认真分析这个问题。")


async def test_queued_generation_cannot_start_after_model_becomes_unavailable(tmp_path):
    sent = []

    async def send(payload):
        sent.append(payload)

    runtime = CompanionRuntime(
        MemoryStore(tmp_path / "m.db"), FakeModel(), NoVoice(), send, "persona"
    )
    runtime.muted = True
    await runtime.command_lock.acquire()
    queued = asyncio.create_task(runtime.start("切换前排队的旧评论", proactive=True))
    await asyncio.sleep(0)
    runtime.can_generate = lambda: False
    runtime.command_lock.release()
    await queued
    assert runtime.task is None
    assert runtime.model.prompts == []
