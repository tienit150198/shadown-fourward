import { h, icon } from './ui.js';

// Presentation only: fitting always uses the conversation's shared camera task.
export class LiveOutfits {
  constructor(container, { onTry, onOther }) {
    Object.assign(this, { container, onTry, onOther });
    this.outfits = [];
    this.index = 0;
    this.introduction = '';
    container.hidden = true;
  }
  show(outfits, introduction = this.introduction) {
    this.outfits = outfits.slice(0, 1);
    this.introduction = introduction;
    this.index = 0;
    this.render();
  }
  dismiss() { this.container.hidden = true; }
  reopen() {
    if (!this.outfits.length) return { status: 'unavailable', message: 'Chưa có bộ gợi ý để mở lại.' };
    this.render();
    return { status: 'displayed', item_ids: this.outfits[this.index].item_ids };
  }
  render() {
    const outfit = this.outfits[this.index];
    this.container.hidden = !outfit;
    if (!outfit) return;
    const close = h('button', { type: 'button', class: 'live-outfit-close', 'aria-label': 'Đóng gợi ý', onClick: () => this.dismiss() }, icon('close', 18));
    const other = h('button', { type: 'button', class: 'live-outfit-other', onClick: () => {
      this.onOther(outfit);
    } }, icon('refresh', 15), 'Bộ khác');
    this.container.replaceChildren(
      h('div', { class: 'live-outfit-heading' }, h('small', null, 'GỢI Ý CHO BẠN'), close),
      h('h2', null, outfit.title || 'Một chút mới, tủ đồ quen.'),
      h('p', { class: 'live-outfit-intro' }, outfit.reason || this.introduction),
      h('ul', { class: 'live-outfit-items' }, outfit.items.map(item => h('li', null,
        item.image_url ? h('img', { src: item.image_url, alt: item.name || 'Trang phục' }) : h('div', { class: 'live-outfit-placeholder' }, 'Trang phục'),
        h('span', null, item.name || 'Trang phục')))),
      ...(outfit.tips ? [h('p', { class: 'live-outfit-tips' }, outfit.tips)] : []),
      h('div', { class: 'live-outfit-actions' }, other,
        h('button', { type: 'button', class: 'live-outfit-try', onClick: () => this.onTry(outfit) }, 'Thử bộ này', icon('right', 18))),
      h('small', { class: 'live-outfit-hint' }, 'Hoặc nói “Được, thử bộ này đi”'));
  }
}
