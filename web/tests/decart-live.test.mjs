import test from 'node:test';
import assert from 'node:assert/strict';

import { buildOutfitPrompt, describeItemEn } from '../js/decart-live.js';

const top = {
  id: 'top-1', category: 'top', description_en: 'blue shirt',
  tryon_descriptor: {
    category: 'top', confidence: 0.91, needs_confirmation: false,
    description: 'navy cable-knit cotton sweater, relaxed fit, crew neck and ribbed cuffs',
  },
};

test('Decart description prefers the validated backend descriptor', () => {
  assert.equal(describeItemEn(top), 'a navy cable-knit cotton sweater, relaxed fit, crew neck and ribbed cuffs');
});

test('Decart outfit prompt never serializes missing descriptor values', () => {
  const prompt = buildOutfitPrompt({ top, bottom: { id: 'b', category: 'bottom', colors: ['đen'], name: 'Quần tây' } });
  assert.match(prompt, /^Substitute the current outfit/);
  assert.equal(prompt.includes('undefined'), false);
  assert.equal(prompt.includes('null'), false);
});
