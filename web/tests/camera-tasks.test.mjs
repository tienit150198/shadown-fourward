import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { CameraTasks } from '../js/camera-tasks.js';

globalThis.crypto ??= webcrypto;
const decision = (intent, camera_command = 'route_request') => ({ intent, camera_command, confidence: .99, label: intent, context: {} });

function setup(extra = {}) {
  let opens = 0;
  const states = [], notices = [], saved = [];
  const stream = { id: 'one-camera' };
  const tasks = new CameraTasks({
    openCamera: async () => { opens++; return stream; }, getFrame: () => 'data:image/jpeg;base64,frame', getStream: () => stream,
    startTryon: async () => ({ stop() {} }), arApi: { tryonXmaxConfig: async () => ({ available: false }), listItems: async () => [] },
    render: state => states.push({ ...state }), notify: state => notices.push(state),
    skillApi: {
      compile: async () => ({ draft_id: 'draft1', compiled_by: 'ai', skill: { id: 'existing-coffee', steps: [{ id: 'step1', reference_image_url: '/skills-media/_drafts/draft1/frames/a.jpg' }] } }),
      saveSkill: async (id, skill) => { saved.push({ id, skill }); },
    }, ...extra,
  });
  return { tasks, saved, notices, states, stream, opens: () => opens };
}

test('a spoken teaching request starts recording on the shared camera; finishing one step keeps recording', async () => {
  const { tasks, opens, saved } = setup();
  try {
    await tasks.handle(decision('teach_skill'), { request: 'Tôi dạy bạn gấp áo' });
    assert.equal(opens(), 1);
    assert.equal(tasks.state.status, 'recording');
    await tasks.handle(decision('guide_folding', 'continue_task'), { request: 'Tôi xong bước một, tiếp theo gấp tay áo vào trong.' });
    assert.equal(tasks.recording.transcript.length, 1);
    assert.equal(tasks.state.status, 'recording');
    assert.equal(saved.length, 0);
  } finally { tasks.cancel(); }
});

test('finishing opens a review draft; only explicit confirmation saves a new lesson with promoted media', async () => {
  const { tasks, saved, notices } = setup();
  try {
    await tasks.handle(decision('teach_skill'), { request: 'Tôi dạy bạn pha cà phê' });
    tasks.narrate('Đầu tiên tráng phin, cho cà phê vào. Sau đó rót nước chậm.');
    const execution = await tasks.handle(decision('teach_skill', 'finish_teaching'));
    assert.equal(execution.status, 'compiling');
    await tasks.pending;
    assert.equal(saved.length, 0);
    assert.equal(tasks.state.mode,'reviewing');
    await tasks.lessonControl({command:'save',revision:tasks.lessonRevision});
    assert.equal(saved.length, 1);
    assert.notEqual(saved[0].id, 'existing-coffee');
    assert.equal(saved[0].skill.draft_id, 'draft1');
    assert.equal(saved[0].skill.steps[0].reference_image_url, `/skills-media/${saved[0].id}/frames/a.jpg`);
    assert.equal(notices[0].status, 'review');
    assert.equal(notices.at(-1).status, 'saved');
  } finally { tasks.cancel(); }
});

test('cancelling while the compiler is pending prevents a late save', async () => {
  let finish;
  const saved = [];
  const { tasks } = setup({ skillApi: {
    compile: () => new Promise(resolve => { finish = resolve; }), saveSkill: async (...args) => saved.push(args),
  } });
  await tasks.handle(decision('teach_skill'));
  tasks.narrate('Chuẩn bị dụng cụ rồi gấp tay áo vào trong.');
  tasks.finish();
  tasks.cancel();
  finish({ draft_id: 'draft1', compiled_by: 'ai', skill: { steps: [{ id: 'one' }] } });
  await tasks.pending;
  assert.deepEqual(saved, []);
  assert.equal(tasks.state.mode, 'chat');
});

test('a compiler fallback is reported truthfully and is not automatically saved as an AI lesson', async () => {
  const saved = [];
  const { tasks } = setup({ skillApi: {
    compile: async () => ({ compiled_by: 'mock', skill: { steps: [{}] } }), saveSkill: async (...args) => saved.push(args),
  } });
  await tasks.handle(decision('teach_skill'));
  tasks.narrate('Chuẩn bị dụng cụ rồi gấp tay áo vào trong.');
  tasks.finish();
  await tasks.pending;
  assert.equal(tasks.state.status, 'error');
  assert.deepEqual(saved, []);
  tasks.cancel();
});

test('missing Xmax credentials keep the same screen and never report an active transformation', async () => {
  const { tasks, opens } = setup();
  await tasks.handle(decision('tryon_live'));
  assert.equal(tasks.state.mode, 'tryon');
  assert.equal(tasks.state.status, 'unavailable');
  assert.equal(opens(), 0);
  tasks.cancel();
});

test('Xmax starts in the background with the existing camera, then releases its renderer for guidance', async () => {
  let borrowed, stopped = 0;
  const { tasks, stream } = setup({ arApi: { tryonXmaxConfig: async () => ({ available: true }), listItems: async () => [{ id: 'shirt', category: 'top', name: 'Áo trắng' }] },
    startTryon: async (_id, camera) => { borrowed = camera; return { stop: () => stopped++ }; },
  });
  await tasks.handle(decision('tryon_live'));
  await tasks.pendingTryon;
  assert.equal(borrowed, stream);
  assert.equal(tasks.state.status, 'live');
  await tasks.handle(decision('visual', 'continue_task'), { request: 'Áo này hợp với tôi không?' });
  assert.equal(stopped, 0);
  assert.equal(tasks.state.status, 'live');
  await tasks.handle(decision('guide_coffee', 'switch_task'));
  assert.equal(stopped, 1);
  assert.equal(tasks.state.mode, 'guiding');
  tasks.cancel();
});

test('ambiguous clothing requests show real garment choices without starting a paid session', async () => {
  let started = 0;
  const { tasks } = setup({ arApi: {
    tryonXmaxConfig: async () => ({ available: true }),
    listItems: async () => [{ id: 'white', category: 'top' }, { id: 'blue', category: 'top' }],
  }, startTryon: async () => { started++; return { stop() {} }; } });
  await tasks.handle(decision('tryon_live'));
  assert.equal(tasks.state.status, 'selecting');
  assert.equal(started, 0);
  tasks.selectGarment('invented-item');
  assert.equal(started, 0);
  tasks.selectGarment('blue');
  assert.equal(started, 0, 'Selecting a garment stages the outfit without a paid session');
  tasks.applyOutfit();
  await tasks.pendingTryon;
  assert.equal(started, 1);
  assert.equal(tasks.state.item_id, 'blue');
  tasks.cancel();
});

test('changing trousers preserves the shirt and sends both pieces in one generation', async () => {
  const calls = [];
  const items = [{ id: 'shirt', category: 'top' }, { id: 'pants', category: 'bottom' }, { id: 'jeans', category: 'bottom' }, { id: 'coat', category: 'outerwear' }];
  const { tasks } = setup({ arApi: { tryonXmaxConfig: async () => ({ available: true }), listItems: async () => items },
    startTryon: async ids => { calls.push([...ids]); return { stop() {} }; } });
  await tasks.handle(decision('tryon_live'));
  tasks.selectGarment('shirt'); tasks.selectGarment('pants'); tasks.applyOutfit();
  await tasks.pendingTryon;
  assert.deepEqual(calls, [['shirt', 'pants']]);
  tasks.selectGarment('jeans'); tasks.selectGarment('coat');
  assert.deepEqual(tasks.state.selected_ids, ['shirt', 'jeans', 'coat']);
  assert.equal(calls.length, 1);
  tasks.applyOutfit(); await tasks.pendingTryon;
  assert.deepEqual(calls[1], ['shirt', 'jeans', 'coat']);
  tasks.cancel();
});

test('cancelling Xmax startup closes a late renderer instead of restoring an obsolete try-on', async () => {
  let resolve, stopped = 0;
  const { tasks } = setup({ arApi: {
    tryonXmaxConfig: async () => ({ available: true }), listItems: async () => [{ id: 'shirt', category: 'top' }],
  }, startTryon: () => new Promise(done => { resolve = done; }) });
  await tasks.handle(decision('tryon_live'));
  await new Promise(done => setImmediate(done));
  tasks.cancel();
  resolve({ stop: () => { stopped++; } });
  await tasks.pendingTryon;
  assert.equal(stopped, 1);
  assert.equal(tasks.state.mode, 'chat');
  assert.equal(tasks.state.status, 'ready');
});

test('a resolved spoken outfit starts Xmax automatically and only reports live after actual frames', async () => {
  let finish, calls = [];
  const items = [{ id: 'shirt', category: 'top' }, { id: 'pants', category: 'bottom' }, { id: 'jeans', category: 'bottom' }];
  const { tasks } = setup({ arApi: { tryonXmaxConfig: async () => ({ available: true }), listItems: async () => items },
    startTryon: async ids => { calls.push(ids); return new Promise(resolve => { finish = resolve; }); } });
  const result = { ...decision('tryon_live'), context: { tryon_plan: { status: 'ready', item_ids: ['shirt', 'pants'] } } };
  await tasks.handle(result, { request: 'Mặc áo trắng và quần xám cho tôi' });
  assert.equal(tasks.state.status, 'loading');
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, [['shirt', 'pants']]);
  // Repeating the voice command during connection must not mint a second key.
  await tasks.handle(result, { request: 'Mặc luôn đi' });
  assert.equal(calls.length, 1);
  finish({ stop() {} }); await tasks.pendingTryon;
  assert.equal(tasks.state.status, 'live');
  await tasks.handle({ ...result, context: { tryon_plan: { status: 'ready', item_ids: ['shirt', 'jeans'] } } }, { request: 'Đổi sang quần jeans' });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls[1], ['shirt', 'jeans']);
  finish({ stop() {} }); await tasks.pendingTryon;
  tasks.cancel();
});

test('unresolved speech asks for clarification without starting a paid fitting', async () => {
  let calls = 0;
  const { tasks } = setup({ arApi: { tryonXmaxConfig: async () => ({ available: true }), listItems: async () => [{ id: 'shirt', category: 'top' }] },
    startTryon: async () => { calls++; return { stop() {} }; } });
  await tasks.handle({ ...decision('tryon_live'), context: { tryon_plan: { status: 'needs_clarification', item_ids: [], message: 'Trong tủ chưa có áo đó. Bạn muốn màu nào khác?' } } });
  assert.equal(calls, 0);
  assert.equal(tasks.state.status, 'selecting');
  assert.match(tasks.state.message, /chưa có áo/);
  tasks.cancel();
});

test('switching an outfit waits for the previous Xmax session to close', async () => {
  let release, calls = 0;
  const items = [{ id: 'shirt', category: 'top' }, { id: 'pants', category: 'bottom' }];
  const { tasks } = setup({ arApi: { tryonXmaxConfig: async () => ({ available: true }), listItems: async () => items },
    startTryon: async () => { calls++; return { stop: calls === 1 ? () => new Promise(resolve => { release = resolve; }) : () => {} }; } });
  const spoken = ids => ({ ...decision('tryon_live'), context: { tryon_plan: { status: 'ready', item_ids: ids } } });
  await tasks.handle(spoken(['shirt'])); await tasks.pendingTryon;
  await tasks.handle(spoken(['shirt', 'pants']));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 1);
  release(); await tasks.pendingTryon;
  assert.equal(calls, 2);
  assert.equal(tasks.state.status, 'live');
  tasks.cancel();
});

const lessonFixture=()=>({id:'saved-skill',display_name:'Gấp áo',start_step_id:'a',tasks:[{id:'a',text:'Bước A'},{id:'b',text:'Bước B'}],
  detectors:{a:{field:'state',prompt:'Old A'},b:{field:'state',prompt:'Old B'}},
  steps:[{id:'a',task_id:'a',instruction:'Bước A',evaluation_mode:'match_value',detector_key:'a',expected_value:'old',next_step_id:'b'},
    {id:'b',task_id:'b',instruction:'Bước B',evaluation_mode:'match_value',detector_key:'b',expected_value:'old',next_step_id:null}]});

test('voice edits remain drafts, reject stale revisions and synchronize instructions and detectors', async()=>{
  const {tasks,saved}=setup();
  await tasks.lessonControl({command:'review',skill_id:'saved-skill',lesson:lessonFixture()});
  const firstRevision=tasks.lessonRevision;
  let result=await tasks.lessonControl({command:'update_step',revision:firstRevision,step_number:1,instruction:'Gấp tay áo'});
  assert.ok(result.error);
  result=await tasks.lessonControl({command:'update_step',revision:firstRevision,step_number:1,instruction:'Gấp tay áo',detector_prompt:'Check that both sleeves are folded inward.'});
  assert.equal(result.lesson_state.steps[0].instruction,'Gấp tay áo');
  assert.equal(tasks.draft.skill.tasks[0].text,'Gấp tay áo');
  const step=tasks.draft.skill.steps[0];
  assert.match(tasks.draft.skill.detectors[step.detector_key].prompt,/both sleeves/);
  assert.equal(step.expected_value,'done');
  assert.equal(saved.length,0);
  assert.ok((await tasks.lessonControl({command:'save',revision:firstRevision})).error);
  await tasks.lessonControl({command:'reorder_steps',revision:tasks.lessonRevision,order:[2,1]});
  assert.equal(tasks.draft.skill.start_step_id,'b');
  assert.equal(tasks.draft.skill.steps[0].next_step_id,'a');
  await tasks.lessonControl({command:'rename',revision:tasks.lessonRevision,title:'Gấp áo nhanh'});
  await tasks.lessonControl({command:'save',revision:tasks.lessonRevision});
  assert.equal(saved.length,1);
  assert.equal(saved[0].id,'saved-skill');
  assert.equal(saved[0].skill.display_name,'Gấp áo nhanh');
  assert.equal(tasks.state.status,'saved');
  tasks.cancel();
});

test('lesson save failure retains the editable draft and removing the final step is rejected',async()=>{
  const {tasks}=setup({skillApi:{saveSkill:async()=>{throw Error('offline');}}});
  await tasks.lessonControl({command:'review',skill_id:'saved-skill',lesson:lessonFixture()});
  await tasks.lessonControl({command:'remove_step',revision:tasks.lessonRevision,step_number:2});
  assert.equal(tasks.draft.skill.steps.length,1);
  assert.ok((await tasks.lessonControl({command:'remove_step',revision:tasks.lessonRevision,step_number:1})).error);
  assert.ok((await tasks.lessonControl({command:'save',revision:tasks.lessonRevision})).error);
  assert.equal(tasks.state.mode,'reviewing');
  assert.equal(tasks.draft.skill.steps.length,1);
  tasks.cancel();
});

test('guided practice borrows the camera and forwards voice step controls to the existing engine',async()=>{
  let options,moves=[],stopped=0;
  const {tasks,opens}=setup({guideFactory:opts=>{options=opts;return{start(){},move(direction){moves.push(direction);return true;},stop(){stopped++;}};}});
  await tasks.lessonControl({command:'start_guidance',skill_id:'saved-skill',lesson:lessonFixture()});
  assert.equal(opens(),1);
  options.onState({step:1,status:'guiding'});
  assert.equal(tasks.lessonContext().step,1);
  await tasks.lessonControl({command:'previous'});
  assert.deepEqual(moves,['previous']);
  tasks.cancel();
  assert.equal(stopped,1);
  options.onState({step:0,status:'guiding'});
  assert.equal(tasks.state.mode,'chat');
});

test('a spoken new step creates a matching detector and task without saving automatically',async()=>{
  const {tasks,saved}=setup();
  await tasks.lessonControl({command:'review',skill_id:'saved-skill',lesson:lessonFixture()});
  await tasks.lessonControl({command:'insert_step',revision:tasks.lessonRevision,step_number:2,instruction:'Vuốt phẳng nếp áo',detector_prompt:'Check if the shirt is smooth and flat.'});
  assert.equal(tasks.draft.skill.steps.length,3);
  const added=tasks.draft.skill.steps[1];
  assert.equal(tasks.draft.skill.steps[0].next_step_id,added.id);
  assert.equal(added.next_step_id,'b');
  assert.equal(tasks.draft.skill.tasks.find(t=>t.id===added.task_id).text,'Vuốt phẳng nếp áo');
  assert.ok(tasks.draft.skill.detectors[added.detector_key]);
  assert.equal(saved.length,0);
  tasks.cancel();
});
