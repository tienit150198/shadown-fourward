import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

// Exercise the browser selection helpers without loading DOM/network dependencies.
const source = (await readFile(new URL('../js/vidu-live.js', import.meta.url), 'utf8'))
  .replace(/^import .*from '\.\/api.js';\n/m, '');
const { slotsFromItems, slotItems, composeGarmentReference } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('selection preserves same-category layers and their order', () => {
  const items = [
    { id: 'inner', category: 'top' }, { id: 'shirt', category: 'top' },
    { id: 'dress', category: 'dress' }, { id: 'pants', category: 'bottom' },
  ];
  assert.deepEqual(slotItems(slotsFromItems([...items, items[0]])), items);
  assert.deepEqual(slotItems(slotsFromItems(items.filter(it => it.id !== 'shirt'))), [items[0], items[2], items[3]]);
});

test('reference collage includes both tops in selected order', async () => {
  const drawn = [];
  globalThis.Image = class {
    naturalWidth = 300; naturalHeight = 400;
    set src(value) { this.url = value; queueMicrotask(() => this.onload()); }
  };
  globalThis.document = { createElement: () => ({
    getContext: () => ({ fillRect() {}, drawImage(image) { drawn.push(image.url); } }),
    toDataURL: () => 'data:image/jpeg;base64,result',
  }) };
  await composeGarmentReference([{ id: 'inner' }, { id: 'shirt' }], it => `/images/${it.id}.jpg`);
  assert.deepEqual(drawn, ['/images/inner.jpg', '/images/shirt.jpg']);
});
