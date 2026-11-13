import { api } from './api.js';
import { wardrobeApi } from './wardrobe-api.js';

// One camera stays owned by the conversation screen. Tasks borrow its frames/stream.
export class CameraTasks {
  constructor({ openCamera, getFrame, getStream, startTryon, render, notify = () => {}, skillApi = api, arApi = wardrobeApi, now = () => Date.now() }) {
    Object.assign(this, { openCamera, getFrame, getStream, startTryon, render, notify, skillApi, arApi, now });
    this.state = { mode: 'chat', status: 'ready', title: 'Camera tự hiểu nhu cầu', message: 'Nói điều bạn muốn làm. Shadow sẽ xử lý ngay tại đây.' };
    this.revision = 0;
  }

  context() { return { current_task: this.state.mode, task_title: this.state.title.slice(0, 200), selected_item_ids: this.state.selected_ids || [] }; }
  update(state) { this.state = { ...this.state, ...state }; this.render(this.state); }
  execution() {
    const { mode, status, message, saved_skill, selected_ids, applied_ids } = this.state;
    return { mode, status, message, ...(saved_skill ? { saved_skill } : {}), ...(mode === 'tryon' ? { selected_ids, applied_ids } : {}) };
  }

  capture(reason = 'interval') {
    if (!this.recording) return;
    const image = this.getFrame();
    if (!image) return;
    const frames = this.recording.frames;
    frames.push({ t: (this.now() - this.recording.started) / 1000, image, reason });
    if (frames.length > 48) this.recording.frames = frames.filter((_, i) => i === 0 || i === frames.length - 1 || i % 2 === 0);
  }

  narrate(text) {
    if (!this.recording || !text.trim()) return;
    const t = (this.now() - this.recording.started) / 1000;
    this.recording.transcript.push({ t0: this.recording.lastT || 0, t1: t, text: text.slice(0, 2000) });
    this.recording.lastT = t;
    this.capture('speech');
    this.update({ lines: this.recording.transcript.length, frames: this.recording.frames.length });
  }

  async handle(result, { request = '', signal, itemId, itemIds } = {}) {
    const plan = result.context?.tryon_plan;
    const requestedIds = plan?.status === 'ready' ? plan.item_ids : Array.isArray(itemIds) ? itemIds.slice(0, 3) : (itemId ? [itemId] : []);
    const command = result.camera_command;
    if (command === 'cancel_task') { this.cancel('Đã hủy tác vụ. Bạn có thể nói yêu cầu mới.'); return this.execution(); }
    if (this.state.mode === 'compiling') return this.execution();
    if (this.state.mode === 'tryon' && ['loading', 'live'].includes(this.state.status) && command === 'continue_task' && !requestedIds.length && result.intent !== 'tryon_live') return this.execution();
    if (this.recording) {
      if (command === 'finish_teaching') return this.finish();
      if (command === 'switch_task') {
        return { mode: 'teaching', status: 'needs_finish', message: 'Bài dạy vẫn đang ghi. Hãy nói lưu bài hoặc hủy bài trước khi chuyển việc.' };
      }
      this.narrate(request);
      return this.execution();
    }
    if (signal?.aborted) return null;
    if (result.confidence < .5) return { status: 'needs_clarification', message: 'Cần nói rõ nhu cầu trước khi bắt đầu tác vụ.' };
    const revision = this.revision;
    if (result.intent === 'teach_skill') {
      await this.openCamera();
      if (signal?.aborted || revision !== this.revision) return null;
      this.stopAr();
      const title = request.replace(/^(?:tôi|mình|em|anh|chị)\s+(?:muốn\s+|sẽ\s+)?(?:dạy|hướng dẫn)\s+(?:bạn|shadow)\s*/iu, '').trim().slice(0, 160) || 'Bài dạy qua camera';
      this.recording = { title, started: this.now(), transcript: [], frames: [] };
      this.update({ mode: 'teaching', status: 'recording', title, message: 'Đang ghi hình và lời bạn dạy. Nói “dạy xong rồi, lưu bài” để kết thúc.', lines: 0, frames: 0, saved_skill: null, lesson: null });
      this.capture('start');
      this.timer = setInterval(() => { this.capture(); this.update({ frames: this.recording?.frames.length || 0 }); }, 2000);
      return this.execution();
    }
    if (result.intent === 'tryon_live') {
      if (plan?.status === 'unchanged') return this.execution();
      if ((this.ar || this.arAbort) && requestedIds.length && requestedIds.length === this.state.applied_ids?.length && requestedIds.every(id => this.state.applied_ids.includes(id))) return this.execution();
      if (this.ar && plan && plan.status !== 'ready') {
        this.update({ message: plan.message, showGarments: plan.status === 'browse' });
        return { ...this.execution(), status: plan.status === 'browse' ? 'selecting' : 'needs_clarification' };
      }
      const config = await this.arApi.tryonXmaxConfig();
      if (signal?.aborted || revision !== this.revision) return null;
      if (!config.available) {
        this.update({ mode: 'tryon', status: 'unavailable', title: 'Thử đồ · Xmax X2.1', message: 'Chưa cấu hình XMAX_API_KEY. Camera vẫn dùng để trò chuyện và tư vấn trang phục.', lesson: null, saved_skill: null });
        return this.execution();
      }
      const items = (await this.arApi.listItems()).filter(item => ['top', 'bottom', 'dress', 'outerwear'].includes(item.category));
      if (signal?.aborted || revision !== this.revision) return null;
      this.stopAr();
      this.update({ mode: 'tryon', status: 'selecting', title: 'Thử đồ · Xmax X2.1', message: plan && plan.status !== 'ready' ? plan.message : items.length ? 'Nói tên, màu hoặc kiểu áo và quần muốn thử. Shadow tự chọn đồ trong tủ và mở video mặc thử; bạn cũng có thể chọn ảnh.' : 'Tủ đồ chưa có ảnh trang phục để thử. Hãy thêm một món vào tủ đồ.', items, selected_ids: (this.state.selected_ids || []).filter(id => items.some(item => item.id === id)), lesson: null, saved_skill: null });
      if (requestedIds.length && !requestedIds.every(id => items.some(item => item.id === id))) {
        this.update({ message: 'Một món trong bộ vừa chọn không còn trong tủ đồ. Bạn muốn thay bằng áo hoặc quần nào?' });
        return this.execution();
      }
      for (const id of requestedIds) this.selectGarment(id);
      if (requestedIds.some(id => items.some(item => item.id === id))) this.applyOutfit();
      else if (!plan && items.length === 1) { this.selectGarment(items[0].id); this.applyOutfit(); }
      else if (!plan && this.state.selected_ids.length && /(?:bộ|đồ).*(?:đã chọn|vừa chọn)|mặc.*(?:bộ này|cả bộ)/iu.test(request)) this.applyOutfit();
      return this.execution();
    }
    this.stopAr();
    if (result.intent.startsWith('guide_')) {
      const lesson = result.context?.lesson || null;
      const step = lesson?.name === this.state.lesson?.name ? this.state.step || 0 : 0;
      this.update({ mode: 'guiding', status: 'guiding', title: lesson?.name || result.label,
        lesson, step, saved_skill: null,
        message: result.context?.lesson ? 'Đang hướng dẫn theo bài đã lưu. Bạn có thể hỏi tiếp bằng giọng nói.' : 'Shadow sẽ hướng dẫn từng bước và xem thao tác qua camera.' });
    } else {
      this.update({ mode: 'chat', status: 'ready', title: result.label, message: 'Camera và trò chuyện vẫn ở cùng màn hình.', lesson: null, saved_skill: null });
    }
    return this.execution();
  }

  selectGarment(itemId) {
    const item = this.state.items?.find(item => item.id === itemId);
    if (!item || this.state.mode !== 'tryon') return;
    const selected = (this.state.selected_ids || []).map(id => this.state.items.find(row => row.id === id)).filter(Boolean)
      .filter(row => row.category !== item.category && !(item.category === 'dress' && ['top', 'bottom'].includes(row.category)) && !(['top', 'bottom'].includes(item.category) && row.category === 'dress'));
    this.update({ selected_ids: [...selected.map(row => row.id), item.id] });
  }

  removeGarment(itemId) { this.update({ selected_ids: (this.state.selected_ids || []).filter(id => id !== itemId) }); }

  applyOutfit() {
    const items = (this.state.selected_ids || []).map(id => this.state.items?.find(item => item.id === id)).filter(Boolean);
    if (!items.length || this.state.mode !== 'tryon') return;
    const ids = items.map(item => item.id);
    const closing = this.stopAr();
    const revision = ++this.revision;
    const controller = new AbortController();
    this.arAbort = controller;
    this.update({ status: 'loading', showGarments: false, applied_ids: ids, item_id: ids[0], title: items.map(item => item.name || item.id).join(' + '), message: 'Đang gửi toàn bộ bộ đồ đã chọn cho Xmax X2.1… Để camera thấy cả người khi thử áo và quần.' });
    this.pendingTryon = (async () => {
      try {
        await closing;
        if (controller.signal.aborted || revision !== this.revision) return;
        await this.openCamera();
        if (controller.signal.aborted || revision !== this.revision) return;
        const run = await this.startTryon(ids, this.getStream(), controller.signal, message => {
          if (!controller.signal.aborted && revision === this.revision) this.update({ message });
        });
        if (controller.signal.aborted || revision !== this.revision) { await run.stop(); return; }
        this.ar = run;
        this.arAbort = null;
        this.update({ status: 'live', message: `Đang thử ${items.length} món cùng lúc. Đổi áo hoặc quần sẽ giữ các món còn lại trong bộ đã chọn. Bạn vẫn có thể trò chuyện.` });
        this.notify(this.execution());
      } catch (error) {
        if (controller.signal.aborted || revision !== this.revision) return;
        this.arAbort = null;
        this.update({ status: 'error', message: error.userMessage || error.message || 'Chưa mở được Xmax X2.1.' });
        this.notify(this.execution());
      }
    })();
  }

  async addGarment(image) {
    if (this.state.mode !== 'tryon') return;
    this.stopAr();
    const revision = ++this.revision;
    const controller = new AbortController();
    this.arAbort = controller;
    this.update({ status: 'loading', message: 'Đang thêm ảnh trang phục vào tủ đồ…' });
    try {
      const item = await this.arApi.addItem({ image }, { signal: controller.signal });
      if (controller.signal.aborted || revision !== this.revision) return;
      this.update({ status: 'selecting', items: [item, ...(this.state.items || [])] });
      this.selectGarment(item.id);
    } catch (error) {
      if (!controller.signal.aborted && revision === this.revision) this.update({ status: 'error', message: error.userMessage || error.message });
    }
  }

  rename(title) {
    if (!this.recording) return;
    this.recording.title = title.trim().slice(0, 160) || 'Bài dạy qua camera';
    this.state.title = this.recording.title;
  }

  finish() {
    const recording = this.recording;
    if (!recording) return this.execution();
    if (!recording.transcript.length) {
      return { mode: 'teaching', status: 'needs_demonstration', message: 'Chưa ghi được lời hướng dẫn. Hãy làm mẫu và giải thích trước khi lưu.' };
    }
    this.capture('end');
    clearInterval(this.timer);
    this.recording = null;
    const revision = this.revision;
    const abort = new AbortController();
    this.compileAbort = abort;
    this.update({ mode: 'compiling', status: 'compiling', message: 'Đang biên soạn bài dạy từ lời nói và hình camera…' });
    this.pending = (async () => {
      try {
        const result = await this.skillApi.compile({ title: recording.title, teacher_name: '', transcript: recording.transcript, frames: recording.frames, notes: '' }, { signal: abort.signal });
        if (abort.signal.aborted || revision !== this.revision) return;
        if (result.compiled_by === 'mock') throw new Error('AI chưa biên soạn được bài. Chưa lưu bản chia bước tự động; hãy thử dạy lại.');
        if (!result.skill?.steps?.length || !result.draft_id) throw new Error('Bài dạy chưa có các bước hợp lệ. Chưa lưu.');
        // A new lesson must never overwrite an existing lesson with the same title.
        const id = 'camera-' + crypto.randomUUID().replaceAll('-', '').slice(0, 16);
        const prefix = `/skills-media/_drafts/${result.draft_id}/`;
        const skill = JSON.parse(JSON.stringify(result.skill), (_key, value) => typeof value === 'string' && value.startsWith(prefix) ? value.replace(prefix, `/skills-media/${id}/`) : value);
        skill.id = id;
        skill.draft_id = result.draft_id;
        await this.skillApi.saveSkill(id, skill, { signal: abort.signal });
        if (abort.signal.aborted || revision !== this.revision) return;
        this.update({ mode: 'chat', status: 'saved', title: recording.title, message: `Đã lưu bài dạy gồm ${skill.steps.length} bước.`, saved_skill: { id, name: recording.title, steps: skill.steps.length }, lesson: { name: recording.title, steps: skill.steps }, step: 0 });
        this.notify(this.execution());
      } catch (error) {
        if (abort.signal.aborted || revision !== this.revision) return;
        this.update({ mode: 'chat', status: 'error', message: error.userMessage || error.message || 'Chưa lưu được bài dạy.' });
        this.notify(this.execution());
      } finally { if (this.compileAbort === abort) this.compileAbort = null; }
    })();
    return this.execution();
  }

  stopAr() {
    const pending = this.arAbort ? this.pendingTryon : null;
    this.arAbort?.abort(); this.arAbort = null;
    const closing = Promise.resolve(this.ar?.stop()).catch(() => {});
    this.ar = null;
    this.arClosing = Promise.allSettled([this.arClosing, closing, pending]).then(() => {});
    return this.arClosing;
  }
  cancel(message = 'Đã dừng tác vụ camera.') {
    this.revision++;
    clearInterval(this.timer);
    this.compileAbort?.abort();
    this.compileAbort = null;
    this.recording = null;
    this.stopAr();
    this.update({ mode: 'chat', status: 'ready', title: 'Camera tự hiểu nhu cầu', message, lesson: null, saved_skill: null });
  }
}
