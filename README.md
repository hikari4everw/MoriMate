# MoriMate

A local-first AI desktop companion for macOS / Apple Silicon: a Live2D character that lives on your screen, chats in Chinese, speaks Japanese, and remembers what you did together.

MoriMate is built on a pinned [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) backend and an Electron frontend (exact upstream revisions recorded in `UPSTREAM.json`). All model inference, speech synthesis, OCR, and storage run on your own machine — no cloud APIs, no microphone, no telemetry.

> **Status: prototype, work in progress.** The core chat loop runs and is test-covered, but the project has **not** passed its own acceptance criteria yet. See [Not implemented yet](#not-implemented-yet) — in particular, the latency target is not met and the native desktop window is not accepted.

## Goals

- A companion you can actually keep talking to: personality, playfulness, and continuity — not a support bot.
- Everything local and inspectable: models, voices, memory, and conversation history stay on disk.
- Opt-in observation only. The app never captures your desktop unless you explicitly pick a window and start observing.

## Current features

These are implemented and covered by tests or manual verification.

**Conversation**
- Chinese keyboard input, Chinese subtitles, and Japanese speech output in the same turn.
- Bilingual output as short sentence pairs, queued and played in order.
- Interruptible playback: a new turn preempts the old one, and late audio from a cancelled turn is dropped.
- Separate, independent controls for *stop current speech*, *mute*, and *stop observing*. Stopping speech keeps the generated text; it only cancels TTS and playback.

**Avatar**
- Hiyori Live2D model (from CubismWebSamples) with per-sentence expression changes (`neutral`, `joy`, `smirk`, `sadness`, `anger`, `surprise`).
- Draggable, transparent, always-on-top pet window in the desktop shell; a compact pet view and an expanded panel in the browser UI.

**Observation (opt-in, per window)**
- Enumerate windows, preview one, and select it; optional separate region selection for the game picture and the dialogue/speaker area.
- 1 Hz OCR sampling. Text must stay stable for at least 0.8 s before it is recorded, and low-confidence lines are filtered out.
- Optional on-demand vision call ("look at the current screen") that only runs when you click it.
- Captures are cancellable, so a slow screenshot cannot block the receive loop or delay stopping.

**Memory**
- Long-term memories are only created from recorded sources that carry evidence; scene-less or ambiguous data is isolated as `unknown` rather than injected globally.
- Per-scene dialogue, summaries, and memories; summaries are attempted after 15 s idle with at least 6 new sources.
- Manual memory correction and deletion. Editing invalidates dependent summaries and assistant restatements instead of letting stale content resurface.

**Proactive comments**
- Three intensity levels (quiet / natural / active) with 180 / 45 / 15 s minimum intervals. User input takes priority, and pending comments expire after 12 s.
- Independent gates for observing, proactive speech, and mute. Observation does not restart automatically after a restart.
- Arriving dialogue invalidates a stale pending comment.

**Server safety**
- WebSocket/HTTP server binds loopback only and enforces an origin allowlist; `Origin: null` and `file://` are rejected.
- Only one UI client may connect at a time.
- The model service, UI, and TTS engine use ports 18080 / 12393 / 10101 — deliberately avoiding port 8080.

## Not implemented yet

Honest list of what does **not** work or is not validated. Please do not read the feature list above as a finished product.

- **Latency target missed.** The goal is 3–5 s from input to first spoken sentence. Measured with Qwen3.5-27B + AivisSpeech まお: a 10-round retest gave a **6.6 s warm median and 8.935 s P95** (first request 4.702 s). An earlier run measured a 15.444 s warm median. Small samples, so P95 is not a long-term SLA. Off a cold, idle model the first token can take tens of seconds.
- **Native desktop window not accepted.** Electron is pinned to 44.8.0; the build and main-process typecheck pass, but the official package's resource signature does not validate, so macOS system assessment fails. The app does a `spctl` preflight and refuses to start the native window instead of bypassing Gatekeeper. The previously pinned Electron 31.7.7 was quarantined by `syspolicyd` as malware and moved to the Trash (not caused by a local cleanup). Until signing is resolved, the supported entry point is **browser mode**.
- **Real game validation is pending.** Capturing a game window inside a VM, speaker-attribution accuracy, and missed reads during fast page flips or window resizing still need testing against a real window.
- **Long-run behaviour is untested.** Continuous ~30-minute play sessions, cross-day story continuity, and sustained memory/CPU footprint have not been verified.
- **Repo-wide typecheck does not pass.** `npm run typecheck` on the full upstream tree still reports the pre-existing Cubism SDK strict-type errors. Only the build and targeted module tests are green.
- No microphone input, no speech recognition, no cloud model fallback, and no tools that control the game or the OS. These are out of scope by design, not missing pieces.
- The transparent margin of the pet window is not click-through yet.

## Requirements

- macOS 14+ on Apple Silicon (verified on an M5 Pro / 48 GiB machine)
- Xcode Command Line Tools (needed for the Swift OCR helper)
- [uv](https://github.com/astral-sh/uv), Node.js 22+
- Roughly 15 GiB of free disk for the model weights

Model weights and voice assets are downloaded locally on first setup and are not stored in git.

## Setup

```bash
./scripts/setup.sh --model 27b --voice mao
```

Sources, model revisions, and asset URLs are pinned in `config/assets.json`. The AivisSpeech engine downloads まお, コハク, and the Japanese BERT model on first launch, which can take several minutes; it uses the standard `~/Library/Application Support/AivisSpeech-Engine` directory and the launcher disables its telemetry.

## Run

The supported entry point is browser mode:

```bash
.venv/bin/python scripts/run_local.py --model 27b --voice mao --web
```

Then open <http://127.0.0.1:12393>. Double-clicking `启动桌宠.command` does the same thing.

Only Qwen3.5-27B 4bit is kept locally; the default voice is AivisSpeech まお. Ports: model **18080**, UI **12393**, TTS **10101**. If a port is already in use the script fails loudly — it never kills another process.

Native mode requires an explicit flag and currently stops at the signature preflight described above:

```bash
.venv/bin/python scripts/run_local.py --model 27b --voice mao --desktop
```

`--voice sayo` switches to VOICEVOX 小夜/SAYO instead of AivisSpeech.

### Suggested verification order

1. **Chat first** — small talk, banter, pushback, sustained multi-turn. Judge whether you *want* to keep talking; passing tests is not a personality verdict.
2. **Then sound** — compare the same line across voices; send a new message or press "stop current speech" and confirm old audio does not resume. After muting, Chinese text should still appear.
3. **Then games** — windowed VM → *observe* → refresh windows → select → preview → mark the game and dialogue regions → start. macOS will ask for Screen Recording permission the first time. Nothing is captured until you pick a window.
4. **Finally, ~30 minutes and a restart** — check memory, story continuity, the three intensity levels, fast page flips, window resizing, and resource usage. Game-companion acceptance is not claimed before this passes.

## Tests

```bash
.venv/bin/pytest -q                                            # 33 passed, 1 skipped
NATIVE_TEST=1 .venv/bin/pytest tests/test_native.py -q         # opt-in Apple Vision OCR test
node --experimental-strip-types --test desktop/tests/turn-player.test.ts
npm --prefix desktop run build
```

Current results: 33 backend tests pass with 1 native OCR test skipped by default, and 4 playback-queue tests pass. `ruff` and `git diff --check` are clean. The native OCR test only recognizes a generated Chinese image; it never screenshots your desktop.

## Configuration and data

- `config/persona.md` — the character's core personality. Automatic memory never rewrites this file.
- `config/companion.json` — model URL/path, TTS URL, speaker and style, port.
- `.runtime/companion.sqlite3` — raw dialogue and observations, bilingual replies, memory with source IDs, and per-scene summaries. Screenshots are never persisted; previews live in memory only.
- `.runtime/` also holds model weights, downloads, and evaluation output. It is gitignored, along with caches, virtualenvs, build output, and any `.env`/database files — those are treated as personal data and are not published.

## Project layout

```
backend/    Open-LLM-VTuber-based backend + the local companion entry (run_companion.py)
desktop/    Electron shell + React/TypeScript renderer (companion UI, Live2D, playback queue)
config/     persona, companion settings, Live2D expressions, asset pins
scripts/    setup, launch, asset download, benchmarking
tests/      backend test suite
docs/       design notes, progress log, and validation records
native/     Swift helper for Apple Vision OCR
```

## Provenance and licensing

Hiyori's model, the Cubism runtime, the language model, and the voice each keep their own source and license. A voice license does not substitute for a character license. Upstream revisions are pinned in `UPSTREAM.json`; assets are not committed to git. See `docs/persona-sources.md` and `docs/aivis-integration-research.md`.

## Documentation

Design and validation notes are written in Chinese and live in `docs/`, most usefully:

- `docs/validation.md` — what was measured, what passed, and what is still open
- `docs/progress.md` — chronological development log and decisions
- `docs/persona-sources.md` — where the personality and assets come from
