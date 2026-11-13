import { ControlSocket } from './ws.js';

// Reuse the existing detector/session engine while borrowing the Live camera.
export class LiveGuide {
  constructor({ skillId, getFrame, onState, onSpeech, Socket = ControlSocket }) {
    Object.assign(this, {skillId,getFrame,onState,onSpeech});
    this.closed=false; this.index=0; this.phase='WAITING_FOR_START'; this.pending=false;
    this.socket=new Socket({onMessage:msg=>this.receive(msg),onStatus:status=>{
      if (this.closed) return;
      if (status==='closed') { this.phase='WAITING_FOR_START'; this.onState({status:'reconnecting',message:'Đang kết nối lại bài học…'}); }
    }});
  }
  start() {
    this.socket.connect();
    this.timer=setInterval(()=>this.frame(),1500);
  }
  receive(msg) {
    if (this.closed) return;
    if (msg.type==='session.ready') {
      this.resume=this.index; this.pending=false;
      this.socket.send({type:'session.start',skill_id:this.skillId});
    } else if (msg.type==='hud.state' && ['GUIDING','COMPLETED'].includes(msg.phase)) {
      this.phase=msg.phase;
      if (this.resume>0 && msg.phase==='GUIDING') {
        const count=this.resume; this.resume=0;
        for (let i=0;i<count;i++) this.socket.send({type:'debug.step',direction:'forward'});
        return;
      }
      this.index=msg.step?.index ?? this.index;
      this.onState({status:msg.phase==='COMPLETED'?'completed':'guiding',step:this.index,
        message:msg.phase==='COMPLETED'?'Bạn đã hoàn thành bài học.':'Shadow đang quan sát thao tác của bạn.'});
    } else if (msg.type==='vision.result') this.pending=false;
    else if (msg.type==='speech' && msg.text) this.onSpeech(msg.text);
    else if (msg.type==='hud.error') { this.pending=false; this.onState({status:'error',message:msg.message || 'Chưa kiểm tra được thao tác. Bạn có thể yêu cầu chuyển bước bằng giọng nói.'}); }
  }
  frame() {
    if (this.closed || this.phase!=='GUIDING' || !this.socket.isOpen || this.socket.bufferedAmount>512*1024) return;
    if (this.pending && Date.now()-this.sentAt<15000) return;
    const image=this.getFrame();
    if (image && this.socket.send({type:'vision.frame',image,ts:Date.now(),trigger:'periodic'})) { this.pending=true; this.sentAt=Date.now(); }
  }
  move(direction) {
    if (!this.socket.isOpen) return false;
    return this.socket.send({type:'debug.step',direction:direction==='next'?'forward':'backward'});
  }
  stop() { this.closed=true; clearInterval(this.timer); this.socket.close(); }
}
