import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TurnPlayer } from '../src/renderer/src/companion/turn-player.ts';

test('old playback completion cannot release or restart a new turn', async () => {
  const played: number[] = [];
  let release!: () => void;
  const player = new TurnPlayer(async (n: number) => {
    played.push(n);
    if (n === 1) await new Promise<void>(r => { release = r; });
  }, () => {});
  player.begin(1);
  player.enqueue(1, 1);
  player.enqueue(1, 2);
  player.stop();
  player.begin(2);
  player.enqueue(1, 3);
  player.enqueue(2, 4);
  release();
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(played, [1, 4]);
});

test('stop rejects late audio until a new acknowledged turn begins', async () => {
  const played: number[] = [];
  const player = new TurnPlayer(async (n: number) => { played.push(n); }, () => {});
  player.begin(1);
  player.stop();
  player.enqueue(1, 5);
  await new Promise(r => setTimeout(r, 0));
  assert.deepEqual(played, []);
});

test('a new socket session accepts restarted backend turn numbers', async () => {
  const played: number[] = [];
  const player = new TurnPlayer(async (n: number) => { played.push(n); }, () => {});
  player.begin(12);
  player.resetSession();
  player.begin(1);
  player.enqueue(1, 9);
  await new Promise(r => setTimeout(r, 0));
  assert.deepEqual(played, [9]);
});

test('cancel playback preserves queued and later text in the same turn', async () => {
  const played: number[] = [];
  let release!: () => void;
  const player = new TurnPlayer(async (n: number) => {
    played.push(n);
    if (n === 1) await new Promise<void>(r => { release = r; });
  }, () => { release?.(); });
  player.begin(1);
  player.enqueue(1, 1);
  player.enqueue(1, 2);
  player.cancelPlayback();
  player.enqueue(1, 3);
  await new Promise(r => setTimeout(r, 0));
  assert.deepEqual(played, [1, 2, 3]);
});

test('text arrives before audio, is deduplicated, and remains valid after stopping speech', () => {
  const player = new TurnPlayer(async (_n: number) => {}, () => {});
  assert.equal(player.begin(1), true);
  assert.equal(player.acceptSentence(1, 1), true);
  assert.equal(player.acceptSentence(1, 1), false);
  player.cancelPlayback();
  assert.equal(player.acceptSentence(1, 2), true);
  player.stop();
  assert.equal(player.acceptSentence(1, 3), false);
  player.begin(2);
  assert.equal(player.acceptSentence(1, 4), false);
  assert.equal(player.acceptSentence(2, 1), true);
});

test('duplicate audio is not replayed and late audio after completion is rejected', async () => {
  const played: number[] = [];
  const completed: number[] = [];
  let release!: () => void;
  const player = new TurnPlayer(async (n: number) => {
    played.push(n);
    await new Promise<void>(r => { release = r; });
  }, () => {}, turn => completed.push(turn));
  player.begin(1);
  player.enqueue(1, 7, 1);
  player.enqueue(1, 7, 1);
  player.end(1);
  release();
  await new Promise(r => setTimeout(r, 0));
  player.enqueue(1, 8, 2);
  assert.deepEqual(played, [7]);
  assert.deepEqual(completed, [1]);
  assert.equal(player.begin(1), false);
});

test('reconnect allows reused sentence IDs in a restarted backend session', () => {
  const player = new TurnPlayer(async (_n: number) => {}, () => {});
  player.begin(12);
  assert.equal(player.acceptSentence(12, 1), true);
  player.resetSession();
  player.begin(1);
  assert.equal(player.acceptSentence(1, 1), true);
});

test('expired proactive text is dropped even if its socket message arrives late', () => {
  const player = new TurnPlayer(async (_n: number) => {}, () => {});
  player.begin(1);
  assert.equal(player.acceptSentence(1, 1, Date.now() - 1000), false);
  assert.equal(player.acceptSentence(1, 2, Date.now() + 10000), true);
});
