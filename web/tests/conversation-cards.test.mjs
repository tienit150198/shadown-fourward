import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

const source = await readFile(new URL('../js/screens/conversation.js', import.meta.url), 'utf8');
function harness(turns = []) {
  let stored = JSON.stringify({ turns });
  const context = { sessionStorage: { getItem: () => stored, setItem: (_, value) => { stored = value; } } };
  vm.runInNewContext(source.slice(source.indexOf('const SESSION_KEY'), source.indexOf('\nfunction actionLink')) + '\nglobalThis.state = conversationState; globalThis.save = saveConversation; globalThis.normalize = normalizeOutfits;', context);
  return { context, saved: () => JSON.parse(stored) };
}
const plain = (value) => JSON.parse(JSON.stringify(value));
const outfit = { title: 'Ngày mát', reason: 'Nhẹ và thoải mái', item_ids: ['shirt', 'coat'], items: [
  { id: 'shirt', name: 'Áo trắng', image_url: '/wardrobe/shirt.jpg' },
  { id: 'coat', name: 'Áo khoác', image_url: 'https://example.com/coat.jpg' },
] };

test('outfit cards and completed activity survive transcript storage', () => {
  const first = harness([{ question: 'Mặc gì?', answer: 'Thử bộ này.', outfits: [outfit], activities: [{ state: 'done', label: 'Đã xem tủ đồ' }, { state: 'running', label: 'Đang tìm' }] }]);
  first.context.save();
  const second = harness(first.saved().turns);
  assert.deepEqual(plain(second.context.state.turns[0].outfits[0].item_ids), ['shirt', 'coat']);
  assert.equal(second.context.state.turns[0].outfits[0].items[0].image_url, '/wardrobe/shirt.jpg');
  assert.deepEqual(plain(second.context.state.turns[0].activities), [{ state: 'done', label: 'Đã xem tủ đồ' }]);
});

test('card normalization bounds payloads and deduplicates ordered selections', () => {
  const { context } = harness();
  const value = context.normalize([outfit, outfit, { ...outfit, item_ids: ['coat', 'shirt'] }, { ...outfit, item_ids: ['third'] }, { ...outfit, item_ids: ['fourth'] }]);
  assert.equal(value.length, 3);
  assert.deepEqual(plain(value[1].item_ids), ['coat', 'shirt']);
  const large = context.normalize([{ title: 'x'.repeat(400), reason: 'x'.repeat(2000), item_ids: Array.from({ length: 30 }, (_, i) => String(i)), items: [{ id: '0', image_url: 'javascript:alert(1)' }, { id: 'unknown', name: 'Ignored' }] }])[0];
  assert.equal(large.item_ids.length, 20);
  assert.equal(large.title.length, 160);
  assert.equal(large.reason.length, 1000);
  assert.equal(large.items.length, 1);
  assert.equal(large.items[0].image_url, '');
});

test('interleaved text, tool activity, cards and actions keep chronological order after reload', () => {
  const timeline = [
    { type: 'text', text: 'Mình kiểm tra nhé.' },
    { type: 'tool_status', state: 'done', label: 'Đã xem tủ đồ' },
    { type: 'text', text: 'Có bộ phù hợp.' },
    { type: 'outfits', outfits: [outfit] },
    { type: 'tool_status', state: 'done', label: 'Đã xem thời tiết' },
    { type: 'action', action: { route: 'wardrobe', label: 'Mở tủ đồ' } },
    { type: 'text', text: 'Bạn thử bộ này nhé.' },
  ];
  const first = harness([{ question: 'Mặc gì?', answer: 'Có bộ phù hợp.', timeline }]);
  first.context.save();
  const second = harness(first.saved().turns);
  const saved = plain(second.context.state.turns[0].timeline);
  assert.deepEqual(saved.map(block => block.type), timeline.map(block => block.type));
  second.context.h = (tag, props, ...children) => ({ tag, props, children });
  second.context.icon = () => null;
  second.context.URLSearchParams = URLSearchParams;
  second.context.ACTION_ROUTES = new Set(['wardrobe', 'tryon-photo', 'tryon-live']);
  vm.runInNewContext(source.slice(source.indexOf('function actionLink'), source.indexOf('\nexport function mount')) + '\nglobalThis.renderTimeline = timelineNodes;', second.context);
  const nodes = second.context.renderTimeline(saved);
  assert.deepEqual(plain(nodes.map(node => node.props.class)), [
    'chat-bubble chat-assistant', 'chat-activity', 'chat-bubble chat-assistant',
    'chat-outfits', 'chat-activity', 'chat-actions', 'chat-bubble chat-assistant',
  ]);
});

test('older transcripts drop preparation labels and standalone buttons already on outfit cards', () => {
  const state = harness([{ question: 'Mặc gì?', answer: 'Bộ này nhé.', activities: [{ state: 'done', label: 'Đã chuẩn bị lựa chọn' }], timeline: [
    { type: 'text', text: 'Bộ này nhé.' },
    { type: 'outfits', outfits: [outfit] },
    { type: 'tool_status', state: 'done', label: 'Đã chuẩn bị lựa chọn' },
    { type: 'action', action: { route: 'tryon-photo', label: 'Thử bằng ảnh', items: ['shirt', 'coat'] } },
  ] }]);
  const turn = plain(state.context.state.turns[0]);
  assert.deepEqual(turn.timeline.map(block => block.type), ['text', 'outfits']);
  assert.equal(turn.activities, undefined);
});
