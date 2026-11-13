import { h, icon, createScope } from '../ui.js';
import { startCamera, stopCamera } from '../camera.js';
import { wardrobeApi } from '../wardrobe-api.js';

export function mount(el) {
  const scope = createScope();
  let config;
  let active = null;

  const stage = h('div', { class: 'tl-stage' });
  const note = h('p', { class: 'tl-msg-body', role: 'status' });
  const start = h('button', { class: 'btn btn-primary btn-xl hidden', type: 'button', onClick: () => startAr() },
    icon('play', 20), 'Mở camera');
  const retry = h('button', { class: 'btn btn-ghost hidden', type: 'button', onClick: () => loadConfig() }, 'Tải lại');
  const card = h('div', { class: 'tl-card' },
    h('span', { class: 'tl-kicker' }, 'Snap Camera Kit'),
    h('h1', { class: 'tl-title' }, 'Thử áo AR'), note, start, retry,
    h('a', { class: 'btn btn-ghost block', href: '#/tryon-photo' }, icon('image', 20), 'Thử đồ bằng ảnh'));
  const loading = h('div', { class: 'tl-connect hidden' },
    h('span', { class: 'tl-spin', 'aria-hidden': 'true' }),
    h('div', { class: 'tl-connect-text', role: 'status' }, 'Đang mở camera và tải Lens…'),
    h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: () => stop() }, 'Huỷ'));
  const root = h('div', { class: 'tl-root', dataset: { phase: 'preflight' } }, stage,
    h('header', { class: 'tl-top' },
      h('a', { class: 'icon-btn glass tl-back', href: '#/wardrobe', 'aria-label': 'Về tủ đồ' }, icon('back', 24)),
      h('div', { class: 'tl-top-chips' }, h('span', { class: 'chip tl-live-chip' }, 'AR trực tiếp'))),
    h('div', { class: 'tl-side' },
      h('button', { class: 'icon-btn glass danger', type: 'button', 'aria-label': 'Dừng camera', onClick: () => stop() }, icon('close', 24))),
    h('div', { class: 'tl-bottom' }, card), loading);
  el.append(root);

  function setPhase(phase) {
    root.dataset.phase = phase;
    card.classList.toggle('hidden', phase !== 'preflight');
    loading.classList.toggle('hidden', phase !== 'connecting');
  }

  const readyText = 'Thử mẫu áo AR qua camera trước. Đứng lùi để thấy từ đầu đến hông.';

  async function loadConfig() {
    note.textContent = 'Đang kiểm tra Lens…';
    start.classList.add('hidden');
    retry.classList.add('hidden');
    try {
      config = await wardrobeApi.tryonArConfig();
      if (scope.disposed) return;
      note.textContent = config?.available ? readyText : 'Chưa có Lens mẫu. Liên hệ quản trị viên để bật Snap Camera Kit.';
      start.classList.toggle('hidden', !config?.available);
    } catch {
      if (scope.disposed) return;
      note.textContent = 'Không tải được cấu hình AR. Kiểm tra mạng rồi thử lại.';
      retry.classList.remove('hidden');
    }
  }

  function destroyKit(kit, sessionReady) {
    if (!kit) return;
    // Camera Kit registers the session only after createSession finishes.
    void Promise.resolve(sessionReady).catch(() => {}).then(() => kit.destroy()).catch(() => {});
  }

  function stop(message = readyText) {
    if (!active) return;
    const run = active;
    active = null;
    clearTimeout(run.timeout);
    stopCamera(run.stream);
    run.canvas.remove();
    destroyKit(run.kit, run.sessionReady);
    setPhase('preflight');
    note.textContent = message;
  }

  async function startAr() {
    if (active || !config?.available) return;
    const startedAt = performance.now();
    const canvas = h('canvas', { class: 'tl-video tl-ar-canvas', 'aria-label': 'Camera thử áo AR' });
    const run = { canvas };
    active = run;
    stage.append(canvas);
    setPhase('connecting');
    run.timeout = setTimeout(() => stop('Camera hoặc Lens tải quá lâu. Thử mở lại.'), 40000);

    try {
      // Camera permission and the SDK download can run in parallel.
      const camera = startCamera(null, { facingMode: 'user' }).then((stream) => {
        if (active === run) run.stream = stream;
        else stopCamera(stream);
      });
      const sdk = import('../../vendor/snap-camera-kit.js');
      const [{ bootstrapCameraKit, createMediaStreamSource, Transform2D }] = await Promise.all([sdk, camera]);
      if (active !== run) return;

      const kit = await bootstrapCameraKit({ apiToken: config.api_token });
      if (active !== run) { destroyKit(kit); return; }
      run.kit = kit;
      run.sessionReady = kit.createSession({ liveRenderTarget: canvas });
      const [session, lens] = await Promise.all([
        run.sessionReady,
        kit.lensRepository.loadLens(config.lens_id, config.lens_group_id),
      ]);
      if (active !== run) return;

      session.events.addEventListener('error', () => {
        if (active === run) stop('Lens không chạy được trên thiết bị này. Bạn có thể thử lại hoặc thử đồ bằng ảnh.');
      });
      const source = createMediaStreamSource(run.stream, { transform: Transform2D.MirrorX, cameraType: 'front' });
      await session.setSource(source);
      if (active !== run) return;
      await session.play();
      if (active !== run) return;
      // Camera Kit shows its own terms prompt when required; allow time to read it.
      clearTimeout(run.timeout);
      const applied = await session.applyLens(lens);
      if (active !== run) return;
      if (!applied) { stop('Chưa áp dụng được Lens. Thử mở lại.'); return; }
      performance.measure('shadow:snap-ar-startup', { start: startedAt });
      setPhase('live');
    } catch (err) {
      if (active === run) stop(err?.userMessage || 'Không mở được Snap Camera Kit. Thử lại hoặc kiểm tra cấu hình Lens.');
    }
  }

  scope.listen(document, 'visibilitychange', () => {
    if (document.visibilityState === 'hidden') stop();
  });
  scope.add(() => stop());
  void loadConfig();
  return () => scope.dispose();
}
