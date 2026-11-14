import { h, icon } from './ui.js';
import { stepImageUrl } from './screens/review.js';

export function lessonPanel(state, { onConfirm, onDismiss }) {
  const lesson=state.lesson || {};
  const close=h('button',{type:'button',class:'live-outfit-close','aria-label':'Đóng bảng bài học',onClick:onDismiss},icon('close',18));
  close.setAttribute('aria-label',state.mode==='reviewing'?'Thu gọn bản nháp':'Dừng bài học');
  if (state.mode==='reviewing') {
    return h('div',{class:'live-lesson-review'},
      h('div',{class:'live-outfit-heading'},h('small',null,'BÀI HỌC CỦA BẠN'),close),
      h('h2',null,'Xem lại cùng SKI'),h('h3',null,lesson.display_name || state.title),
      h('ol',{class:'live-lesson-steps'},(lesson.steps || []).map((step,i)=>{
        const image=stepImageUrl(step,lesson.id);
        return h('li',null,h('span',{class:'live-lesson-number'},String(i+1)),
          image?h('img',{src:image,alt:`Ảnh mẫu bước ${i+1}`,loading:'lazy'}):null,
          h('div',null,h('p',null,step.instruction || step.on_enter_speech || `Bước ${i+1}`),
            step.tip?h('small',null,step.tip):null,step.warning?h('small',{class:'live-lesson-warning'},step.warning):null));
      })),
      h('p',{class:'live-lesson-hint'},'Nói điều bạn muốn sửa: “Đổi tên bài”, “Sửa bước hai”, “Đưa bước ba lên đầu”…'),
      h('p',{class:'live-lesson-status',role:'status'},state.message),
      h('button',{type:'button',class:'live-outfit-try live-lesson-save',disabled:state.status==='saving',onClick:onConfirm},icon('check',18),state.status==='saving'?'Đang lưu…':'Xác nhận & lưu'),
      h('small',{class:'live-outfit-hint'},'Hoặc nói “Xác nhận và lưu bài”'));
  }
  if (state.mode==='guiding') {
    const steps=lesson.steps || [],index=Math.min(state.step || 0,Math.max(0,steps.length-1)),step=steps[index];
    return h('div',{class:'live-lesson-guide'},
      h('div',{class:'live-outfit-heading'},h('small',null,state.status==='completed'?'ĐÃ HOÀN THÀNH':`BƯỚC ${index+1} / ${steps.length || '…'}`),close),
      h('h2',null,state.status==='completed'?'Bạn đã làm được rồi.':step?.instruction || step?.on_enter_speech || state.title),
      step?.tip?h('p',null,step.tip):null,step?.warning?h('p',{class:'live-lesson-warning'},step.warning):null,
      h('div',{class:'live-lesson-progress','aria-label':`Bước ${index+1} trên ${steps.length}`},steps.map((_,i)=>h('span',{'data-state':i<index?'done':i===index?'active':'pending'}))),
      h('small',{class:'live-lesson-status',role:'status'},state.message),
      h('small',{class:'live-outfit-hint'},'Nói “Bước tiếp”, “Quay lại” hoặc “Nhắc lại” khi cần.'));
  }
  return h('div',{class:'live-lesson-recording'},
    h('div',{class:'live-outfit-heading'},h('small',null,state.mode==='compiling'?'ĐANG BIÊN SOẠN':'ĐANG GHI BÀI DẠY'),close),
    h('h2',null,state.title),
    h('p',{role:'status'},state.message),
    state.mode==='teaching'?h('small',null,`${state.lines || 0} câu hướng dẫn · ${state.frames || 0} khung hình`):null,
    h('small',{class:'live-outfit-hint'},state.mode==='teaching'?'Nói “Dạy xong, lưu lại đi” để lưu; hoặc “Cho xem lại” để kiểm tra trước.':'Bạn có thể tiếp tục trò chuyện trong lúc chờ.'));
}
