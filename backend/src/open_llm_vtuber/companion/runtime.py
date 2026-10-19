import asyncio
from builtins import ExceptionGroup
import contextlib
import re
import time
from datetime import datetime
from pathlib import Path

from .bilingual import BilingualParser
from .model import GenerationTruncated
from ..agent.output_types import Actions, DisplayText
from ..utils.stream_audio import prepare_audio_payload

FORMAT = """只输出 NDJSON，每行一个完整短句对象：
{"zh":"中文短句。","ja":"同义的自然日语短句。","emotion":"neutral","voice_tone":"neutral"}
只用上述字段表达正式回复，不输出 Markdown 或思考过程。每行是一个播报短句，不是完整回复的长度限制。
日语全文与中文语义对应，第一句尽量不超过30字，每个播报短句尽量不超过60字，长内容拆成多行继续输出。
当前时间是现实时间；历史中的“昨天”等相对时间以那条资料的记录时间理解。记录时间不等于事件发生时间，游戏内时间也独立于现实时间；缺少来源就承认不知道。
emotion只控制表情，为neutral/joy/smirk/sadness/anger/surprise。
voice_tone只控制声音语气，为neutral(默认)/aggrieved(委屈)/shy(害羞)/lazy(慵懒)/combat(战斗)/affectionate(撒娇)/relieved(欣慰)/self_deprecating(自嘲)/reproachful(责备)。
不要声称自己读到了未提供的游戏内容。"""


def time_context(now=None):
    now = now or datetime.now().astimezone()
    hour = now.hour
    period = (
        "凌晨"
        if hour < 5
        else "早上"
        if hour < 11
        else "中午"
        if hour < 14
        else "下午"
        if hour < 18
        else "晚上"
    )
    return {
        "local_datetime": now.isoformat(timespec="seconds"),
        "weekday": "星期" + "一二三四五六日"[now.weekday()],
        "timezone": str(now.tzinfo),
        "period": period,
    }


def detailed_reply(text):
    if any(
        word in text
        for word in (
            "一句话",
            "两句话",
            "两句",
            "三句话",
            "简短",
            "简要",
            "简单说",
            "不要展开",
            "不用展开",
        )
    ):
        return False
    return any(
        word in text
        for word in ("详细", "展开", "深入", "认真分析", "认真讨论", "一步步")
    ) or bool(
        re.search(
            r"(讲|写|编|续|来|说|给).{0,8}(故事|童话|小说|剧本)|(故事|童话|小说|剧本).{0,6}(继续|讲完|写完)",
            text,
        )
    )


class CompanionRuntime:
    def __init__(self, store, model, voice, send, persona):
        self.store, self.model, self.voice, self.send = store, model, voice, send
        self.persona = persona
        self.muted = False
        self.silenced_turn = None
        self.voice_task = None
        self.scene = "daily"
        self.turn_id = 0
        self.task = None
        self.maintenance = None
        self.busy = False
        self.started = 0
        self.metrics = []
        self.timing = {}
        self.command_lock = asyncio.Lock()
        self.can_generate = lambda: True
        self.is_proactive = False
        self.mood = "neutral"
        self.mood_changed = 0.0
        self.scheduler = None
        self.priority = 0

    async def stop_speech(self):
        self.silenced_turn = self.turn_id
        if self.voice_task and not self.voice_task.done():
            self.voice_task.cancel()
        await self.send({"type": "speech-stopped", "turn_id": self.turn_id})

    async def stop(self):
        async with self.command_lock:
            await self._stop()

    async def _stop(self):
        self.turn_id += 1
        for task in (self.task, self.maintenance):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.busy = False
        await self.send({"type": "interrupt-signal", "turn_id": self.turn_id})

    def messages(self, instruction=None, image=None, *, query=""):
        from .context_builder import ContextBuilder, ContextRequest
        capacity = getattr(self.model, "context_tokens", 32768)
        output_limit = getattr(self.model, "max_output_tokens", 8192)
        budget = min(8000, capacity - output_limit - 1024 - (4096 if image else 0))
        self.context_bundle = ContextBuilder(self.store).build(ContextRequest(
            query=query, scene=self.scene, persona=self.persona, format_prompt=FORMAT,
            input_budget=budget, instruction=instruction, image=image,
            current_time=time_context(),
            mood=self.mood if time.monotonic() - self.mood_changed < 120 else "neutral",
        ))
        return self.context_bundle.messages

    async def start(self, text, *, proactive=False, image=None, expires_at=None):
        async with self.command_lock:
            if not self.can_generate():
                return
            if proactive and (
                self.busy or (expires_at is not None and time.monotonic() >= expires_at)
            ):
                return
            await self._stop()
            self.busy = True
            self.is_proactive = proactive
            self.started = time.monotonic()
            turn = self.turn_id
            self.timing = {"turn_id": turn}
            if not proactive:
                self.store.add_source("user", text, self.scene)
            messages = self.messages(text if proactive else None, image, query=text)
            await self.send({"type": "context-trace", "turn_id": turn, **self.context_bundle.trace})
            detailed = detailed_reply(text) and not proactive
            await self.send(
                {
                    "type": "control",
                    "text": "conversation-chain-start",
                    "turn_id": turn,
                    "wait_after_ms": 20000 if detailed else 8000,
                }
            )
            self.task = asyncio.create_task(
                self._run(
                    turn,
                    messages,
                    expires_at,
                    max_tokens=400 if proactive else 8192,
                    reasoning_budget=64 if proactive else 512 if detailed else 128,
                )
            )

    async def _run(
        self, turn, messages, expires_at=None, *, max_tokens=8192, reasoning_budget=128
    ):
        self.mark_timing(turn, "queue_entered_seconds")
        if self.scheduler is None:
            return await self._run_reply(
                turn,
                messages,
                expires_at,
                max_tokens=max_tokens,
                reasoning_budget=reasoning_budget,
            )
        await self.send({"type": "queue-status", "state": "waiting", "turn_id": turn})
        try:
            async with self.scheduler.slot(2 if self.is_proactive else self.priority):
                if turn != self.turn_id:
                    return
                if not self.can_generate() or not self.context_bundle.is_current(self.store) or (
                    expires_at is not None and time.monotonic() >= expires_at
                ):
                    await self.send(
                        {
                            "type": "control",
                            "text": "conversation-chain-end",
                            "turn_id": turn,
                        }
                    )
                    return
                await self.send(
                    {"type": "queue-status", "state": "running", "turn_id": turn}
                )
                await self._run_reply(
                    turn,
                    messages,
                    expires_at,
                    max_tokens=max_tokens,
                    reasoning_budget=reasoning_budget,
                )
        finally:
            if turn == self.turn_id:
                self.busy = False
                await self.send(
                    {"type": "queue-status", "state": "idle", "turn_id": turn}
                )

    async def _run_reply(
        self, turn, messages, expires_at=None, *, max_tokens=8192, reasoning_budget=128
    ):
        # Text pairs are bounded by max_tokens; slow TTS must not delay text delivery.
        queue = asyncio.Queue()
        self.mark_timing(turn, "generation_started_seconds")
        self.timing["queue_seconds"] = round(self.timing["generation_started_seconds"] - self.timing.get("queue_entered_seconds", 0), 3)
        expiry = (
            {"expires_at_ms": (time.time() + expires_at - time.monotonic()) * 1000}
            if expires_at is not None
            else {}
        )

        context_bundle = self.context_bundle

        def current():
            return context_bundle.is_current(self.store) and turn == self.turn_id and (
                expires_at is None or time.monotonic() < expires_at
            )

        async def produce():
            parser = BilingualParser()
            sentence_id = 0

            async def deliver(sentence):
                nonlocal sentence_id
                if not current():
                    return
                sentence_id += 1
                if sentence_id == 1:
                    self.mark_timing(turn, "first_sentence_seconds")
                    self.timing["model_wait_seconds"] = round(self.timing["first_sentence_seconds"] - self.timing["generation_started_seconds"], 3)
                source_id = self.store.add_source(
                    "assistant", sentence.zh, self.scene, speech=sentence.ja,
                    dependencies=context_bundle.source_ids, memory_ids=context_bundle.memory_ids,
                )
                await self.send(
                    {
                        "type": "sentence",
                        "turn_id": turn,
                        "sentence_id": sentence_id,
                        "source_id": source_id,
                        "zh": sentence.zh,
                        "ja": sentence.ja,
                        "emotion": sentence.emotion,
                        "voice_tone": sentence.voice_tone,
                        **expiry,
                    }
                )
                queue.put_nowait((sentence_id, sentence))
                if sentence_id == 1:
                    await self.send({"type": "turn-timing", **self.timing})

            try:
                async for chunk in self.model.stream(
                    messages, max_tokens=max_tokens, reasoning_budget=reasoning_budget,
                    **({"purpose": "proactive" if self.is_proactive else "chat"} if hasattr(self.model, "router") else {}),
                ):
                    self.mark_timing(turn, "first_token_seconds")
                    for sentence in parser.feed(chunk):
                        await deliver(sentence)
                for sentence in parser.finish():
                    await deliver(sentence)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, GenerationTruncated):
                    with contextlib.suppress(ValueError):
                        for sentence in parser.finish():
                            await deliver(sentence)
                if current():
                    await self.send(
                        {
                            "type": "error",
                            "message": str(exc)
                            if isinstance(exc, GenerationTruncated)
                            else f"回复失败：{exc}",
                            "turn_id": turn,
                        }
                    )
            finally:
                self.mark_timing(turn, "generation_finished_seconds")
                queue.put_nowait(None)

        async def consume():
            while (item := await queue.get()) is not None:
                sentence_id, sentence = item
                path = None
                synthesis_started = synthesis_finished = None
                if not current():
                    continue
                try:
                    if not self.muted and self.silenced_turn != turn:
                        try:
                            synthesis_started = time.monotonic() - self.started
                            synthesis = (
                                self.voice.synthesize(sentence.ja, sentence.voice_tone)
                                if hasattr(self.voice, "synthesize")
                                else self.voice.async_generate_audio(sentence.ja)
                            )
                            self.voice_task = asyncio.create_task(synthesis)
                            try:
                                path = await self.voice_task
                            except asyncio.CancelledError:
                                if asyncio.current_task().cancelling():
                                    raise
                                if self.silenced_turn != turn:
                                    raise
                            finally:
                                self.voice_task = None
                            synthesis_finished = time.monotonic() - self.started
                        except Exception as exc:
                            if current():
                                self.timing["synthesis_failures"] = self.timing.get("synthesis_failures", 0) + 1
                            await self.send(
                                {
                                    "type": "error",
                                    "message": f"日语合成失败：{exc}",
                                    "turn_id": turn,
                                }
                            )
                    payload = prepare_audio_payload(
                        path if not self.muted and self.silenced_turn != turn else None,
                        display_text=DisplayText(sentence.zh, "Hiyori"),
                        actions=Actions(expressions=[sentence.emotion]),
                    )
                    if not current() or (
                        expires_at is not None and time.monotonic() >= expires_at
                    ):
                        continue
                    self.mood, self.mood_changed = sentence.emotion, time.monotonic()
                    payload.update(
                        turn_id=turn, sentence_id=sentence_id, tts_text=sentence.ja
                    )
                    payload.update(expiry)
                    if "audio_sent_seconds" not in self.timing:
                        if payload.get("audio"):
                            self.timing.update(audio_sentence_id=sentence_id, audio_status="ready",
                                synthesis_started_seconds=round(synthesis_started, 3),
                                synthesis_finished_seconds=round(synthesis_finished, 3),
                                synthesis_seconds=round(synthesis_finished - synthesis_started, 3))
                            self.mark_timing(turn, "audio_sent_seconds")
                        else:
                            self.timing["audio_status"] = "muted" if self.muted else "stopped" if self.silenced_turn == turn else "failed"
                        await self.send({"type": "turn-timing", **self.timing})
                    await self.send(payload)
                finally:
                    if path:
                        Path(path).unlink(missing_ok=True)

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(produce())
                group.create_task(consume())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.send(
                {
                    "type": "error",
                    "message": f"回复失败：{exc.exceptions[0] if isinstance(exc, ExceptionGroup) else exc}",
                    "turn_id": turn,
                }
            )
        finally:
            if turn == self.turn_id:
                self.busy = False
                await self.send(
                    {
                        "type": "control",
                        "text": "conversation-chain-end",
                        "turn_id": turn,
                    }
                )

    def mark_timing(self, turn, key):
        if turn == self.turn_id and self.timing.get("turn_id") == turn:
            self.timing.setdefault(key, round(time.monotonic() - self.started, 3))

    def played(self, turn):
        if turn == self.turn_id and not any(m["turn_id"] == turn for m in self.metrics):
            self.metrics.append(
                {
                    **self.timing,
                    "turn_id": turn,
                    "first_audio_seconds": round(time.monotonic() - self.started, 3),
                }
            )
            self.metrics = self.metrics[-100:]

    async def consolidate(self):
        async with (
            self.scheduler.slot(2) if self.scheduler else contextlib.nullcontext()
        ):
            await self._consolidate()

    async def _consolidate(self):
        from .memory_maintenance import MemoryMaintenance
        if not hasattr(self, "memory_worker"):
            self.memory_worker = MemoryMaintenance(self.store, self.model, current_time=time_context)
        self.memory_worker.model = self.model
        result = await self.memory_worker.run_batch()
        if result["state"] == "saved":
            await self.send({"type": "memory-updated"})
        return result
