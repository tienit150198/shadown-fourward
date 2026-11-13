import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

const source = await readFile(new URL('../js/live-outfits.js', import.meta.url), 'utf8');
function setup(outfits) {
  const calls = [];
  const container = { hidden: false, replaceChildren(...children) { this.children = children; } };
  const context = { h: (tag, props, ...children) => ({ tag, props: props || {}, children: children.flat().filter(Boolean) }), icon: name => ({ tag: 'icon', name }) };
  vm.runInNewContext(source.slice(source.indexOf('export class')).replace('export class', 'globalThis.LiveOutfits = class'), context);
  const panel = new context.LiveOutfits(container, { onTry: outfit => calls.push(outfit.item_ids), onOther: () => calls.push('other') });
  if (outfits) panel.show(outfits, 'Một chút mới.');
  const find = label => {
    const walk = node => node && typeof node === 'object' ? [node, ...(node.children || []).flatMap(walk)] : [];
    return container.children.flatMap(walk).find(node => node.props?.class === label);
  };
  return { panel, container, calls, find };
}
const outfits = [{title:'Hai lớp',reason:'Trời mát',item_ids:['shirt','coat'],items:[{name:'Áo',image_url:'/shirt.jpg'},{name:'Khoác',image_url:'/coat.jpg'}]}, {title:'Bộ hai',item_ids:['pants'],items:[]}];
test('live overlay keeps one current outfit and requests alternatives from the AI', () => {
  const {panel,container,calls,find} = setup(outfits);
  assert.equal(container.hidden,false);
  find('live-outfit-try').props.onClick();
  assert.deepEqual(calls[0], ['shirt','coat']);
  find('live-outfit-other').props.onClick();
  assert.equal(panel.index,0);
  find('live-outfit-try').props.onClick();
  assert.equal(calls[1],'other');
  assert.deepEqual(calls[2],['shirt','coat']);
  assert.equal(panel.outfits.length,1);
  find('live-outfit-close').props.onClick();
  assert.equal(container.hidden,true);
  assert.equal(panel.reopen().status,'displayed');
  assert.equal(panel.index,0);
});
test('new suggestions replace old selection and one-outfit alternative requests a new choice', () => {
  const {panel,calls,find} = setup(outfits);
  panel.index=1;
  panel.show([outfits[0]], 'Bộ mới');
  assert.equal(panel.index,0);
  find('live-outfit-other').props.onClick();
  assert.deepEqual(calls,['other']);
  assert.equal(setup().panel.reopen().status,'unavailable');
});
