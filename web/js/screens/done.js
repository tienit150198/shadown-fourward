// Done: celebration + session stats.
import { h, icon, navigate, fmtDurationLong, createScope } from '../ui.js';
import { getDoneStats } from '../store.js';
import { speak } from '../speech.js';

export function mount(el) {
  const scope = createScope();
  const stats = getDoneStats();
  if (!stats) {
    navigate('#/home', { replace: true });
    return () => scope.dispose();
  }

  const stat = (ic, value, label) => h('div', { class: 'stat' },
    h('div', { class: 'stat-icon' }, icon(ic, 20)), h('div', { class: 'stat-value' }, value), h('div', { class: 'stat-label' }, label));

  const corrections = stats.corrections || 0;
  const praise = corrections === 0
    ? 'Không cần sửa lần nào — xuất sắc!'
    : `SHADOW đã nhắc bạn ${corrections} lần. Lần sau sẽ nhanh hơn!`;

  const confetti = h('div', { class: 'confetti', 'aria-hidden': 'true' },
    Array.from({ length: 18 }, (_, i) => h('i', { style: `--i:${i};--x:${(i * 37) % 100}%` })));

  el.append(
    confetti,
    h('div', { class: 'done-wrap' },
      h('div', { class: 'done-badge' }, h('div', { class: 'done-ring' }), icon('check', 54)),
      h('div', { class: 'done-kicker' }, 'Hoàn thành'),
      h('h1', { class: 'done-title' }, stats.skillName || 'Kỹ năng'),
      stats.learnedFrom ? h('p', { class: 'done-sub' }, `Bạn vừa làm được theo cách của ${stats.learnedFrom}.`) : null,
      h('div', { class: 'stats-grid' },
        stat('clock', fmtDurationLong(stats.durationMs || 0), 'Thời gian'),
        stat('wand', String(corrections), 'Lần được sửa'),
        stat('eye', String(stats.framesChecked || 0), 'Khung hình AI xem'),
        stat('list', String(stats.steps || 0), 'Bước')),
      h('p', { class: 'done-praise' }, praise),
      h('div', { class: 'done-actions' },
        stats.skillId ? h('button', { class: 'btn btn-ghost btn-lg', type: 'button', onClick: () => navigate(`#/guide/${encodeURIComponent(stats.skillId)}`) }, icon('refresh', 20), 'Làm lại') : null,
        h('button', { class: 'btn btn-primary btn-lg', type: 'button', onClick: () => navigate('#/home') }, 'Về trang chủ'))),
  );

  // If the backend didn't send a completion line, say something ourselves.
  if (!stats.spoken) speak(`Tuyệt vời! Bạn đã hoàn thành ${stats.skillName || 'kỹ năng'}.`);
  navigator.vibrate?.([30, 50, 30, 50, 60]);

  return () => scope.dispose();
}
