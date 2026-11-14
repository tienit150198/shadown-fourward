import assert from 'node:assert/strict';
import test from 'node:test';
import { LiveGuide } from '../js/live-guide.js';

test('live guide uses existing sessions and bounded frames without opening a second camera',()=>{
  const sent=[],states=[],speech=[];
  class Socket {
    constructor(callbacks){this.callbacks=callbacks;this.isOpen=true;this.bufferedAmount=0;}
    connect(){}
    send(message){sent.push(message);return true;}
    close(){this.closed=true;}
  }
  const guide=new LiveGuide({skillId:'fold-shirt',Socket,getFrame:()=> 'frame',onState:s=>states.push(s),onSpeech:s=>speech.push(s)});
  guide.start();
  try {
    guide.receive({type:'session.ready',session_id:'guide-session'});
    assert.equal(sent[0].type,'session.start');
    assert.equal(states.at(-1).guide_session_id,'guide-session');
    guide.receive({type:'hud.state',phase:'GUIDING',step:{index:0}});
    guide.frame();guide.frame();
    assert.equal(sent.filter(m=>m.type==='vision.frame').length,1);
    guide.receive({type:'vision.result'});guide.frame();
    assert.equal(sent.filter(m=>m.type==='vision.frame').length,2);
    guide.receive({type:'speech',text:'Gấp tay áo vào trong.'});
    assert.deepEqual(speech,['Gấp tay áo vào trong.']);
    guide.move('next');assert.equal(sent.at(-1).direction,'forward');
    guide.receive({type:'hud.state',phase:'COMPLETED',step:{index:1}});
    assert.equal(states.at(-1).status,'completed');
    const count=sent.length;guide.frame();assert.equal(sent.length,count);
  } finally {guide.stop();}
  assert.equal(guide.socket.closed,true);
});
