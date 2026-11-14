import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

const screen = await readFile(new URL('../js/screens/conversation.js', import.meta.url), 'utf8');
const start = screen.indexOf('const SESSION_KEY =');
const end = screen.indexOf('\nfunction actionLink', start);
assert.ok(start >= 0 && end > start, 'conversation persistence helpers are present');
const persistenceSource = screen.slice(start, end);
const sessionKey = 'shadow:conversation';

function createHarness(value) {
  const entries = new Map();
  if (value !== undefined) entries.set(sessionKey, JSON.stringify(value));
  const context = {
    sessionStorage: {
      getItem(key) { return entries.get(key) ?? null; },
      setItem(key, storedValue) { entries.set(key, String(storedValue)); },
    },
  };
  vm.runInNewContext(`${persistenceSource}
    globalThis.__conversationState = conversationState;
    globalThis.__completedTurns = completedTurns;
    globalThis.__loadConversation = loadConversation;
    globalThis.__saveConversation = saveConversation;
  `, context, { filename: 'conversation-persistence.js' });
  return {
    context,
    savedValue() {
      const stored = entries.get(sessionKey);
      return stored === undefined ? undefined : JSON.parse(stored);
    },
  };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test('loads legacy array transcripts and filters malformed turns', () => {
  const harness = createHarness([
    { question: 'What goes with blue?', answer: 'Try cream.', actions: [{ route: 'wardrobe' }] },
    { question: 'x'.repeat(501), answer: 'too long' },
    { question: 'Missing answer' },
    { question: 'And shoes?', answer: 'White sneakers.' },
  ]);

  assert.deepEqual(plain(harness.context.__conversationState), {
    conversationId: null,
    turns: [
      { question: 'What goes with blue?', answer: 'Try cream.', actions: [] },
      { question: 'And shoes?', answer: 'White sneakers.', actions: [] },
    ],
  });
});

test('accepts canonical UUIDs and rejects malformed stored IDs', () => {
  const validId = 'F47AC10B-58CC-4372-A567-0E02B2C3D479';
  const valid = createHarness({ conversationId: validId, turns: [] });
  assert.equal(valid.context.__conversationState.conversationId, validId);

  for (const invalidId of [
    'not-a-uuid',
    'f47ac10b-58cc-4372-a567-0e02b2c3d47',
    'f47ac10b-58cc-4372-c567-0e02b2c3d479',
    '00000000-0000-0000-0000-000000000000',
  ]) {
    const invalid = createHarness({ conversationId: invalidId, turns: [] });
    assert.equal(invalid.context.__conversationState.conversationId, null, invalidId);
  }
});

test('bounds loaded and saved transcripts to the most recent 12 turns', () => {
  const turns = Array.from({ length: 15 }, (_, index) => ({
    question: `Question ${index}`,
    answer: `Answer ${index}`,
  }));
  const harness = createHarness(turns);

  assert.deepEqual(
    plain(harness.context.__completedTurns).map(({ question }) => question),
    turns.slice(-12).map(({ question }) => question),
  );
  harness.context.__saveConversation();
  assert.deepEqual(
    harness.savedValue().turns.map(({ question }) => question),
    turns.slice(-12).map(({ question }) => question),
  );
});

test('round-trips the conversation ID beside the transcript', () => {
  const originalId = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';
  const nextId = '9d2e4e8f-a7c6-4d2b-8c39-f179d28a4361';
  const harness = createHarness({
    conversationId: originalId,
    turns: [{ question: 'What should I wear?', answer: 'A navy jacket.' }],
  });

  harness.context.__conversationState.conversationId = nextId;
  harness.context.__saveConversation();
  assert.deepEqual(harness.savedValue(), {
    conversationId: nextId,
    turns: [{ question: 'What should I wear?', answer: 'A navy jacket.' }],
  });

  const reloaded = createHarness(harness.savedValue());
  assert.equal(reloaded.context.__conversationState.conversationId, nextId);
  assert.deepEqual(plain(reloaded.context.__completedTurns).map(({ question, answer }) => ({ question, answer })), [
    { question: 'What should I wear?', answer: 'A navy jacket.' },
  ]);
});
