import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../js/live-lessons.js',import.meta.url),'utf8');
function render(state){
  const context={h:(tag,props,...children)=>({tag,props:props || {},children:children.flat().filter(x=>x!==null && x!==undefined),setAttribute(k,v){this.props[k]=v;}}),icon:()=>null,stepImageUrl:s=>s.reference_image_url || null};
  vm.runInNewContext(source.slice(source.indexOf('export function')).replace('export function','function')+';globalThis.render=lessonPanel;',context);
  return context.render(state,{onConfirm(){},onDismiss(){}});
}
const flatten=node=>node && typeof node==='object'?[node,...(node.children || []).flatMap(flatten)]:[];
test('review presents real steps and captured images with save confirmation and no manual editor',()=>{
  const nodes=flatten(render({mode:'reviewing',status:'review',title:'Gấp áo',message:'Xem lại',lesson:{steps:[{instruction:'Gấp tay áo',reference_image_url:'/skills-media/_drafts/draft/frame.jpg'}]}}));
  assert.ok(nodes.some(n=>n.tag==='img' && n.props.src.includes('/_drafts/')));
  assert.ok(nodes.some(n=>n.tag==='button' && n.props.class.includes('live-lesson-save')));
  assert.ok(!nodes.some(n=>['input','textarea','select'].includes(n.tag)));
});
test('guided overlay shows current instruction and progress without manual step editors',()=>{
  const nodes=flatten(render({mode:'guiding',status:'guiding',step:1,message:'Đang xem',lesson:{steps:[{instruction:'Chuẩn bị'},{instruction:'Gấp tay áo'}]}}));
  assert.ok(nodes.some(n=>n.tag==='h2' && n.children.includes('Gấp tay áo')));
  assert.equal(nodes.filter(n=>n.props['data-state']==='active').length,1);
  assert.ok(!nodes.some(n=>['input','textarea'].includes(n.tag)));
});
