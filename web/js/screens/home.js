// Home: skill list, "Dạy kỹ năng mới" CTA, backend mode badge.
import { api, mediaUrl } from '../api.js';
import { h, icon, toast, errorToast, actionSheet, confirmSheet, navigate, createScope } from '../ui.js';
import { setDraft } from '../store.js';

function modeBadge(health) {
  if (!health) return h('span', { class: 'badge badge-off' }, h('i', { class: 'dot' }), 'Offline');
  if (health.mode === 'mock') return h('span', { class: 'badge badge-demo' }, h('i', { class: 'dot' }), 'Demo');
  const model = String(health.vision_model || '').split('/').pop() || 'online';
  return h('span', { class: 'badge badge-ai', title: health.vision_model || '' }, h('i', { class: 'dot' }), `AI · ${model}`);
}

function skeletonCards() {
  return [0, 1, 2].map(() => h('div', { class: 'skill-card skeleton' },
    h('div', { class: 'skill-cover' }), h('div', { class: 'skill-body' }, h('div', { class: 'sk-line w70' }), h('div', { class: 'sk-line w40' }))));
}

export function mount(el) {
  const scope = createScope();
  const badgeSlot = h('div', { class: 'badge-slot' }, h('span', { class: 'badge badge-loading' }, h('i', { class: 'dot' }), '…'));
  const list = h('div', { class: 'skill-list' }, skeletonCards());
  const countLabel = h('span', { class: 'section-count' });

  el.append(
    h('header', { class: 'home-header' },
      h('div', { class: 'brand' },
        h('div', { class: 'brand-mark', 'aria-hidden': 'true' }),
        h('div', { class: 'brand-text' }, h('div', { class: 'brand-name' }, 'SHADOW'), h('div', { class: 'brand-sub' }, 'AI in your palm'))),
      badgeSlot),
    h('div', { class: 'home-scroll' },
      h('section', { class: 'hero' },
        h('h1', { class: 'hero-title' }, 'Làm mẫu một lần.', h('br'), h('span', { class: 'accent' }, 'AI hướng dẫn người sau.')),
        h('p', { class: 'hero-sub' }, 'Quay lại cách bạn làm và vừa làm vừa nói. SHADOW biến nó thành từng bước, rồi nhìn qua camera để kèm người tiếp theo bằng giọng nói.'),
        h('button', { class: 'cta', type: 'button', onClick: () => navigate('#/teach') },
          h('span', { class: 'cta-icon' }, icon('camera', 26)),
          h('span', { class: 'cta-text' }, h('span', { class: 'cta-title' }, 'Dạy kỹ năng mới'), h('span', { class: 'cta-sub' }, 'Quay & nói — AI tự chia bước')),
          icon('right', 22))),
      // Built-in skill: Trợ lý tủ đồ (styles in wardrobe.css)
      h('div', { class: 'section-head wd-feature-head' }, h('h2', null, 'Kỹ năng làm sẵn')),
      h('button', { class: 'wd-feature', type: 'button', onClick: () => navigate('#/wardrobe') },
        h('span', { class: 'wd-feature-emoji', 'aria-hidden': 'true' }, '👗'),
        h('span', { class: 'wd-feature-text' },
          h('span', { class: 'wd-feature-kicker' }, icon('sparkle', 12), 'Làm sẵn'),
          h('span', { class: 'wd-feature-title' }, 'Trợ lý tủ đồ'),
          h('span', { class: 'wd-feature-sub' }, 'Phối đồ theo thời tiết & sự kiện, mặc thử bằng AI. Dạy được quy tắc riêng.')),
        h('span', { class: 'wd-feature-go', 'aria-hidden': 'true' }, icon('right', 22))),
      h('div', { class: 'section-head' }, h('h2', null, 'Kỹ năng đã học'), countLabel),
      list,
      h('p', { class: 'home-foot' }, 'Show it once. AI can guide the next person.')),
  );

  async function loadHealth() {
    let health = null;
    try { health = await api.health(); } catch (err) { console.warn('health failed', err); }
    if (!scope.disposed) badgeSlot.replaceChildren(modeBadge(health));
  }

  async function loadSkills() {
    try {
      const skills = await api.listSkills();
      if (scope.disposed) return;
      renderSkills(Array.isArray(skills) ? skills : []);
    } catch (err) {
      if (scope.disposed) return;
      errorToast(err, 'Không tải được danh sách kỹ năng');
      list.replaceChildren(h('div', { class: 'empty' },
        h('div', { class: 'empty-icon' }, icon('refresh', 30)),
        h('p', null, 'Không tải được danh sách kỹ năng.'),
        h('button', { class: 'btn btn-ghost', type: 'button', onClick: () => { list.replaceChildren(...skeletonCards()); loadSkills(); } }, 'Thử lại')));
    }
  }

  function renderSkills(skills) {
    countLabel.textContent = skills.length ? `${skills.length}` : '';
    if (!skills.length) {
      list.replaceChildren(h('div', { class: 'empty' },
        h('div', { class: 'empty-icon' }, icon('sparkle', 30)),
        h('p', { class: 'empty-title' }, 'Chưa có kỹ năng nào'),
        h('p', null, 'Bấm “Dạy kỹ năng mới”, làm mẫu một lần — SHADOW sẽ học thành các bước.')));
      return;
    }
    list.replaceChildren(...skills.map(skillCard));
  }

  function skillCard(s) {
    const cover = mediaUrl(s.cover_image_url, s.id);
    const initial = (s.display_name || '?').trim().charAt(0).toUpperCase();
    const coverEl = h('div', { class: 'skill-cover' },
      cover ? h('img', { src: cover, alt: '', loading: 'lazy', onError: (e) => e.target.remove() }) : null,
      h('span', { class: 'skill-initial' }, initial));
    const card = h('article', { class: 'skill-card', tabindex: '0', role: 'button', 'aria-label': `Bắt đầu ${s.display_name}` },
      coverEl,
      h('div', { class: 'skill-body' },
        h('h3', { class: 'skill-name' }, s.display_name || s.id),
        h('div', { class: 'skill-meta' },
          s.learned_from ? h('span', null, icon('user', 14), `Học từ ${s.learned_from}`) : null,
          h('span', null, icon('list', 14), `${s.steps ?? '?'} bước`)),
        s.summary ? h('p', { class: 'skill-summary' }, s.summary) : null),
      h('div', { class: 'skill-actions' },
        h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Tuỳ chọn', onClick: (e) => { e.stopPropagation(); openMenu(s); } }, icon('more', 22)),
        h('span', { class: 'play-chip', 'aria-hidden': 'true' }, icon('play', 18))));
    const start = () => navigate(`#/guide/${encodeURIComponent(s.id)}`);
    card.addEventListener('click', start);
    card.addEventListener('keydown', (e) => { if (e.key === 'Enter') start(); });
    return card;
  }

  function openMenu(s) {
    actionSheet(s.display_name || s.id, [
      { label: 'Bắt đầu hướng dẫn', icon: 'play', onSelect: () => navigate(`#/guide/${encodeURIComponent(s.id)}`) },
      { label: 'Chỉnh sửa các bước', icon: 'edit', onSelect: () => editSkill(s.id) },
      { label: 'Xoá kỹ năng', icon: 'trash', danger: true, onSelect: () => removeSkill(s) },
    ]);
  }

  async function editSkill(id) {
    try {
      const skill = await api.getSkill(id);
      if (scope.disposed) return;
      setDraft({ skill, draft_id: null, isNew: false });
      navigate('#/review');
    } catch (err) {
      errorToast(err, 'Không mở được kỹ năng');
    }
  }

  async function removeSkill(s) {
    const ok = await confirmSheet(`Xoá “${s.display_name || s.id}”? Không thể hoàn tác.`, { okLabel: 'Xoá', danger: true });
    if (!ok) return;
    try {
      await api.deleteSkill(s.id);
      toast('Đã xoá kỹ năng', { kind: 'ok' });
      loadSkills();
    } catch (err) {
      errorToast(err, 'Không xoá được');
    }
  }

  loadHealth();
  loadSkills();
  return () => scope.dispose();
}
