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
