import test from 'node:test';
import assert from 'node:assert/strict';

test('Vietnamese TTS keeps one voice even when the device voice list changes', async () => {
  const first = {name:'Vietnamese',lang:'vi-VN',localService:true};
  let voices = [first];
  let changed;
  const spoken = [];
  const synth = {getVoices:()=>voices,addEventListener:(_,fn)=>{changed=fn;},cancel(){},resume(){},
    speak(utterance){spoken.push(utterance);queueMicrotask(()=>utterance.onend?.());}};
  globalThis.SpeechSynthesisUtterance = class {constructor(text){this.text=text;}};
  globalThis.window = {speechSynthesis:synth,SpeechSynthesisUtterance:globalThis.SpeechSynthesisUtterance};
  const speech = await import('../js/speech.js?fixed-voice');
  await speech.speak('Xin chào');
  voices = [{name:'Google natural enhanced Vietnamese',lang:'vi-VN'},first];
  changed();
  await speech.speak('Mình đang nghe');
  assert.equal(spoken.length,2);
  assert.ok(spoken.every(line=>line.voice===first && line.lang==='vi-VN'));
  delete globalThis.window;delete globalThis.SpeechSynthesisUtterance;
});

test('a device without Vietnamese speech does not substitute a foreign voice', async () => {
  let count=0;
  globalThis.SpeechSynthesisUtterance = class {};
  globalThis.window = {SpeechSynthesisUtterance:globalThis.SpeechSynthesisUtterance,
    speechSynthesis:{getVoices:()=>[{name:'English',lang:'en-US'}],addEventListener(){},cancel(){},speak(){count++;}}};
  const speech=await import('../js/speech.js?no-vi-voice');
  assert.equal(speech.hasVietnameseVoice(),false);
  await speech.speak('Xin chào');
  assert.equal(count,0);
  delete globalThis.window;delete globalThis.SpeechSynthesisUtterance;
});
