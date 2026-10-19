import { test } from 'node:test';
import assert from 'node:assert/strict';
import { modelAccess, connectionSubmission } from '../src/renderer/src/companion/protocol.ts';

test('unauthorized guest cannot send or change shared cloud model', () => {
  assert.deepEqual(modelAccess(false, true, { cloud: true, cloud_guests_allowed: false, model_phase: 'ready' }),
    { canSend: false, canConfigure: false, reason: '主人尚未开放此云模型' });
  assert.equal(modelAccess(false, true, { cloud: true, cloud_guests_allowed: true, model_phase: 'ready' }).canSend, true);
  assert.equal(modelAccess(true, true, { model_phase: 'loading' }).canSend, false);
});

test('blank key preserves Keychain value and secret is outside metadata', () => {
  const payload = connectionSubmission({ id: 'chat', model: 'chosen' }, ' private-key ');
  assert.equal(payload.api_key, 'private-key');
  assert.equal(JSON.stringify(payload.connection).includes('private-key'), false);
  assert.equal('api_key' in connectionSubmission({ id: 'chat' }, ''), false);
});
