import { LiveGuide } from './live-guide.js';
import { finalizeSkill } from './screens/review.js';
import { api } from './api.js';
import { wardrobeApi } from './wardrobe-api.js';

// One camera stays owned by the conversation screen. Tasks borrow its frames/stream.
export class CameraTasks {
  constructor({ openCamera, getFrame, getStream, startTryon, render, notify = () => {}, guideFactory = options=>new LiveGuide(options), skillApi = api, arApi = wardrobeApi, now = () => Date.now() }) {
    Object.assign(this, { openCamera, getFrame, getStream, startTryon, render, notify, guideFactory, skillApi, arApi, now });
    this.state = { mode: 'chat', status: 'ready', title: 'Camera tự hiểu nhu cầu', message: 'Nói điều bạn muốn làm. SKI sẽ xử lý ngay tại đây.' };
    this.revision = 0;
    this.lessonRevision = 0;
  }

  context() { return { current_task: this.state.mode, task_title: this.state.title.slice(0, 200), selected_item_ids: this.state.selected_ids || [], lesson_state:this.lessonContext() }; }
  update(state) { this.state = { ...this.state, ...state }; this.render(this.state); }
  execution() {
    const { mode, status, message, saved_skill, selected_ids, applied_ids } = this.state;
    return { mode, status, message, lesson_state:this.lessonContext(), ...(saved_skill ? { saved_skill } : {}), ...(mode === 'tryon' ? { selected_ids, applied_ids } : {}) };
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
    if (['compiling','reviewing'].includes(this.state.mode)) return {...this.execution(),message:'Bài dạy đang được biên soạn hoặc chờ xem lại. Hãy lưu hoặc hủy trước khi chuyển việc.'};
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
    const revision = result.intent==='teach_skill' ? ++this.revision : this.revision;
    if (result.intent === 'teach_skill') {
      await this.openCamera();
      if (signal?.aborted || revision !== this.revision) return null;
      this.stopAr(); this.stopGuide(); this.draft=null;
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
      this.stopGuide();
      const config = await this.arApi.tryonXmaxConfig();
      if (signal?.aborted || revision !== this.revision) return null;
      if (!config.available) {
        this.update({ mode: 'tryon', status: 'unavailable', title: 'Thử đồ trực tiếp', message: 'Chức năng thay đồ tạm chưa sẵn sàng. Bạn vẫn có thể trò chuyện và tư vấn trang phục.', lesson: null, saved_skill: null });
        return this.execution();
      }
      const items = (await this.arApi.listItems()).filter(item => ['top', 'bottom', 'dress', 'outerwear'].includes(item.category));
      if (signal?.aborted || revision !== this.revision) return null;
      this.stopAr();
      this.update({ mode: 'tryon', status: 'selecting', title: 'Thử đồ trực tiếp', message: plan && plan.status !== 'ready' ? plan.message : items.length ? 'Nói tên, màu hoặc kiểu áo và quần muốn thử. SKI tự chọn đồ trong tủ và mở video mặc thử; bạn cũng có thể chọn ảnh.' : 'Tủ đồ chưa có ảnh trang phục để thử. Hãy thêm một món vào tủ đồ.', items, selected_ids: (this.state.selected_ids || []).filter(id => items.some(item => item.id === id)), lesson: null, saved_skill: null });
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
      if (result.context?.lesson?.id) return this.lessonControl({command:'start_guidance',skill_id:result.context.lesson.id,lesson:result.context.lesson},{signal});
      const lesson = result.context?.lesson || null;
      const step = lesson?.name === this.state.lesson?.name ? this.state.step || 0 : 0;
      this.update({ mode: 'guiding', status: 'guiding', title: lesson?.name || result.label,
        lesson, step, saved_skill: null,
        message: result.context?.lesson ? 'Đang hướng dẫn theo bài đã lưu. Bạn có thể hỏi tiếp bằng giọng nói.' : 'SKI sẽ hướng dẫn từng bước và xem thao tác qua camera.' });
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
    this.update({ status: 'loading', showGarments: false, applied_ids: ids, item_id: ids[0], title: items.map(item => item.name || item.id).join(' + '), message: 'Chúng tôi đang thay đồ cho bạn… Để camera thấy cả người khi thử áo và quần.' });
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
        this.update({ status: 'error', message: error.userMessage || error.message || 'Chưa thể thay đồ lúc này. Bạn thử lại nhé.' });
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

  finish({save = false} = {}) {
    const recording = this.recording;
    if (save && this.state.mode==='compiling') {
      this.saveAfterCompile = true;
      this.update({message:'Đã nhận yêu cầu lưu. Bài sẽ được lưu khi biên soạn xong.'});
    }
    if (!recording) return this.execution();
    if (!recording.transcript.length) {
      this.update({message:'Chưa ghi được lời hướng dẫn. Hãy làm mẫu và giải thích trước khi xem lại.'});
      return this.execution();
    }
    this.capture('end');
    clearInterval(this.timer);
    this.recording = null;
    const revision = this.revision;
    const abort = new AbortController();
    this.compileAbort = abort;
    this.saveAfterCompile = save;
    this.update({ mode: 'compiling', status: 'compiling', message: 'Đang biên soạn bài dạy từ lời nói và hình camera…' });
    this.pending = (async () => {
      try {
        const result = await this.skillApi.compile({ title: recording.title, teacher_name: '', transcript: recording.transcript, frames: recording.frames, notes: '' }, { signal: abort.signal });
        if (abort.signal.aborted || revision !== this.revision) return;
        if (result.compiled_by === 'mock') throw new Error('AI chưa biên soạn được bài. Chưa lưu bản chia bước tự động; hãy thử dạy lại.');
        if (!result.skill?.steps?.length || result.skill.steps.length>30 || !result.draft_id) throw new Error('Bài dạy chưa có các bước hợp lệ. Chưa lưu.');
        this.draft={skill:structuredClone(result.skill),draftId:result.draft_id,id:'camera-'+crypto.randomUUID().replaceAll('-','').slice(0,16),edited:new Set()};
        this.lessonRevision++;
        this.update({mode:'reviewing',status:'review',title:result.skill.display_name || recording.title,
          message:'Xem lại các bước. Bạn có thể nhờ sửa bằng giọng nói, rồi nói “xác nhận và lưu”.',lesson:result.skill,reviewHidden:false});
        if (this.saveAfterCompile) {
          this.saveAfterCompile = false;
          await this.saveLesson(abort.signal);
        } else this.notify(this.execution());
      } catch (error) {
        if (abort.signal.aborted || revision !== this.revision) return;
        this.recording = recording;
        this.saveAfterCompile = false;
        this.timer = setInterval(() => { this.capture(); this.update({frames:this.recording?.frames.length || 0}); },2000);
        this.update({ mode: 'teaching', status: 'error', message: (error.userMessage || error.message || 'Chưa biên soạn được bài dạy.') + ' Nội dung đã ghi vẫn còn; bạn có thể nói “lưu lại” để thử lại.' });
        this.notify(this.execution());
      } finally { if (this.compileAbort === abort) this.compileAbort = null; }
    })();
    return this.execution();
  }

  lessonContext() {
    const lesson=this.draft?.skill || this.state.lesson;
    if (!lesson && !this.recording && !['compiling','reviewing'].includes(this.state.mode)) return null;
    return {status:this.state.status,title:(lesson?.display_name || this.state.title || '').slice(0,160),
      revision:this.lessonRevision,step:this.state.step || 0,skill_id:this.draft?.draftId ? null : lesson?.id || null,
      steps:(lesson?.steps || []).slice(0,30).map((s,i)=>({number:i+1,instruction:(s.instruction || s.on_enter_speech || '').slice(0,800),tip:(s.tip || '').slice(0,400),warning:(s.warning || '').slice(0,400)}))};
  }

  stopGuide() { this.guide?.stop(); this.guide=null; }

  async lessonControl(control, {signal}={}) {
    if (signal?.aborted) return null;
    const {command}=control;
    if (command==='stop') { this.cancel('Đã dừng bài học. Bản nháp chưa được lưu.'); return this.execution(); }
    if (command==='start_teaching') {
      if (this.recording) return this.execution();
      const result=await this.handle({intent:'teach_skill',source:'tool_call',label:'Dạy kỹ năng',camera_command:'route_request'}, {request:control.title || 'Bài dạy qua camera',signal});
      if (this.recording && control.title) this.rename(control.title);
      return result;
    }
    if (command==='finish_teaching') return this.finish();
    if (['save','finish_and_save'].includes(command) && (this.recording || this.state.mode==='compiling')) return this.finish({save:true});
    if (command==='start_guidance') {
      if (this.recording || ['reviewing','compiling'].includes(this.state.mode)) return {...this.execution(),error:'Hãy lưu hoặc hủy bài dạy hiện tại trước.'};
      const revision=++this.revision; this.stopGuide();
      const lesson=control.lesson || await this.skillApi.getSkill(control.skill_id);
      await this.openCamera();
      if (signal?.aborted || revision!==this.revision) return null;
      this.stopAr(); this.stopGuide(); this.draft=null;
      this.update({mode:'guiding',status:'connecting',title:lesson.display_name || lesson.name,lesson,step:0,message:'Đang mở bài học trong camera…'});
      this.guide=this.guideFactory({skillId:control.skill_id,getFrame:this.getFrame,
        onState:state=>{if(revision===this.revision && this.state.mode==='guiding') this.update(state);},
        onSpeech:text=>{if(revision===this.revision && this.state.mode==='guiding') this.notify({...this.execution(),speech:text,message:text});}});
      this.guide.start();
      return this.execution();
    }
    if (['next','previous','repeat'].includes(command)) {
      if (this.state.mode!=='guiding') return {...this.execution(),error:'Chưa có bài đang hướng dẫn.'};
      if (command==='repeat') {
        const step=this.state.lesson?.steps?.[this.state.step || 0];
        this.notify({...this.execution(),speech:step?.instruction || step?.on_enter_speech || this.state.message});
      } else if (!this.guide?.move(command)) return {...this.execution(),error:'Bài học chưa kết nối. Hãy thử lại.'};
      return this.execution();
    }
    if (this.state.status==='saving') return {...this.execution(),error:'Đang lưu bài học. Hãy đợi lưu xong.'};
    if (command==='review') {
      if (['teaching','compiling'].includes(this.state.mode)) return {...this.execution(),error:'Hãy kết thúc bài dạy và đợi biên soạn trước khi xem lại.'};
      if (control.skill_id && !control.lesson) {
        const revision=this.revision;
        const lesson=await this.skillApi.getSkill(control.skill_id);
        if (signal?.aborted || revision!==this.revision) return null;
        control={...control,lesson};
      }
      if (control.lesson) {
        this.stopGuide(); this.stopAr();
        this.draft={skill:structuredClone(control.lesson),draftId:null,id:control.skill_id,edited:new Set()};
        this.lessonRevision++;
      }
      if (!this.draft) return {...this.execution(),error:'Chưa có bản nháp để xem.'};
      this.update({mode:'reviewing',status:'review',lesson:this.draft.skill,title:this.draft.skill.display_name,reviewHidden:false});
      return this.execution();
    }
    if (!this.draft || this.state.status==='saving') return {...this.execution(),error:'Chưa có bản nháp sẵn sàng để chỉnh hoặc lưu.'};
    if (control.revision!==this.lessonRevision) return {...this.execution(),error:'Bản nháp đã thay đổi. Dùng revision mới trong lesson_state rồi thử lại.'};
    if (['save','finish_and_save'].includes(command)) return this.saveLesson(signal);
    const skill=structuredClone(this.draft.skill), steps=skill.steps;
    if (command==='rename') {
      if (!control.title?.trim()) return {...this.execution(),error:'Cần tên bài học.'};
      skill.display_name=control.title;
    } else if (command==='reorder_steps') {
      const order=control.order || [];
      if (order.length!==steps.length || new Set(order).size!==steps.length || order.some(n=>n<1 || n>steps.length)) return {...this.execution(),error:'Thứ tự mới phải gồm tất cả các bước, mỗi bước đúng một lần.'};
      skill.steps=order.map(n=>steps[n-1]);
    } else {
      const index=(control.step_number || 0)-1;
      if(command==='insert_step') {
        if(steps.length>=30 || index<0 || index>steps.length || !control.instruction || !control.detector_prompt) return {...this.execution(),error:'Bước mới cần vị trí, hướng dẫn và detector_prompt, tối đa 30 bước.'};
        const id='live-step-'+crypto.randomUUID().replaceAll('-','').slice(0,12);
        steps.splice(index,0,{id,task_id:id,detector_key:'live-edit-'+id,evaluation_mode:'match_value',expected_value:'done'});
      }
      const step=steps[index];
      if (!step) return {...this.execution(),error:'Không tìm thấy bước này.'};
      if (command==='remove_step') {
        if (steps.length===1) return {...this.execution(),error:'Bài học cần ít nhất một bước.'};
        steps.splice(index,1);
      } else if (['update_step','insert_step'].includes(command)) {
        if (control.instruction && !control.detector_prompt) return {...this.execution(),error:'Cần detector_prompt mới khớp với thao tác sau khi sửa.'};
        for (const key of ['instruction','tip','warning']) if (control[key]!==undefined) step[key]=control[key];
        if (control.instruction) {
          this.draft.edited.add(step.id);
          step.on_enter_speech=control.instruction;
          const detectorKey='live-edit-'+step.id;
          skill.detectors={...skill.detectors,[detectorKey]:{field:'state',prompt:control.detector_prompt+' Return JSON {"state":"done"} only if the described action is visibly complete, otherwise {"state":"not_done"}.'}};
          step.detector_key=detectorKey; step.evaluation_mode='match_value'; step.expected_value='done';
          for (const key of ['complete_condition','progress_condition','complete_value','progress_value','complete_on','target_count','count_edge','rearm_condition','value_path','verify','progress_once_speech','mismatch_speech']) delete step[key];
          step.milestones=[]; step.ignored_values=[]; step.ignore_values=[];
          step.complete_speech='Đã hoàn thành bước này.'; delete step.success_speech;
          skill.quality={status:'unchecked',reason:'Bước đã được sửa bằng giọng nói; chưa tự kiểm tra lại.'};
        }
      } else return {...this.execution(),error:'Lệnh bài học chưa được hỗ trợ.'};
    }
    this.draft.skill=finalizeSkill(skill,{editedInstructions:this.draft.edited});
    if(command==='reorder_steps') for(const step of this.draft.skill.steps) for(const milestone of step.milestones || []) if(milestone.next_step_id) milestone.next_step_id=step.next_step_id;
    this.lessonRevision++;
    this.update({mode:'reviewing',status:'review',lesson:this.draft.skill,title:this.draft.skill.display_name,reviewHidden:false,message:'Đã chỉnh bản nháp. Bạn muốn sửa gì thêm hay xác nhận lưu?'});
    return this.execution();
  }

  async saveLesson(signal) {
    const draft=this.draft, revision=this.revision;
    if (!draft || signal?.aborted) return this.execution();
    const skill=finalizeSkill(draft.skill,{draftId:draft.draftId,editedInstructions:draft.edited});
    const prefix=`/skills-media/_drafts/${draft.draftId}/`;
    const payload=JSON.parse(JSON.stringify(skill),(_key,value)=>typeof value==='string' && draft.draftId && value.startsWith(prefix)?value.replace(prefix,`/skills-media/${draft.id}/`):value);
    payload.id=draft.id;
    const abort=new AbortController(); this.saveAbort=abort;
    const combined=signal?AbortSignal.any([signal,abort.signal]):abort.signal;
    this.update({status:'saving',message:'Đang lưu bài học…'});
    try {
      await this.skillApi.saveSkill(draft.id,payload,{signal:combined});
      if (combined.aborted || revision!==this.revision || this.draft!==draft) return null;
      this.draft=null; this.lessonRevision++;
      this.update({mode:'chat',status:'saved',title:skill.display_name || this.state.title,message:'Đã lưu bài học. Bạn có thể nhờ SKI hướng dẫn ngay.',lesson:payload,saved_skill:{id:draft.id,name:(skill.display_name || this.state.title || '').slice(0,160),steps:skill.steps.length}});
      this.notify(this.execution());
    } catch(error) {
      if (combined.aborted || revision!==this.revision || this.draft!==draft) return null;
      this.update({mode:'reviewing',status:'review',message:error.userMessage || error.message || 'Chưa lưu được bài học. Bản nháp vẫn còn.'});
      return {...this.execution(),error:this.state.message};
    } finally { if (this.saveAbort===abort) this.saveAbort=null; }
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
    this.stopGuide(); this.saveAbort?.abort(); this.draft=null; this.lessonRevision++;
    this.compileAbort?.abort();
    this.compileAbort = null;
    this.recording = null;
    this.saveAfterCompile = false;
    this.stopAr();
    this.update({ mode: 'chat', status: 'ready', title: 'Camera tự hiểu nhu cầu', message, lesson: null, saved_skill: null });
  }
}
