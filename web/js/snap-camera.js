// Borrow cloned tracks so the Snap SDK cannot stop the conversation's camera.
export async function startSharedSnap(config, stream, canvas, { onError = () => {}, signal } = {}) {
  if (!stream?.getVideoTracks().some(track => track.readyState === 'live')) throw new Error('Cần mở camera để thử áo.');
  const clone = new MediaStream(stream.getVideoTracks().map(track => track.clone()));
  let kit, sessionReady, stopped = false;
  let timeout;
  let onAbort;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    clearTimeout(timeout);
    signal?.removeEventListener('abort', onAbort);
    clone.getTracks().forEach(track => track.stop());
    canvas.hidden = true;
    void Promise.resolve(sessionReady).catch(() => {}).then(() => kit?.destroy()).catch(() => {});
  };
  try {
    if (signal?.aborted) throw new DOMException('Đã hủy tải Lens.', 'AbortError');
    await Promise.race([
      (async () => {
        const { bootstrapCameraKit, createMediaStreamSource, Transform2D } = await import('../vendor/snap-camera-kit.js');
        kit = await bootstrapCameraKit({ apiToken: config.api_token });
        if (stopped) { void kit.destroy(); return; }
        sessionReady = kit.createSession({ liveRenderTarget: canvas });
        const [session, lens] = await Promise.all([sessionReady, kit.lensRepository.loadLens(config.lens_id, config.lens_group_id)]);
        if (stopped) return;
        session.events.addEventListener('error', () => { stop(); onError('Lens không chạy được trên thiết bị này.'); });
        const front = clone.getVideoTracks()[0]?.getSettings().facingMode !== 'environment';
        await session.setSource(createMediaStreamSource(clone, { ...(front ? { transform: Transform2D.MirrorX } : {}), cameraType: front ? 'front' : 'back' }));
        if (stopped) return;
        await session.play();
        if (stopped) return;
        // Leave Snap's own terms prompt visible and give the user time to read it.
        clearTimeout(timeout);
        if (!await session.applyLens(lens)) throw new Error('Chưa áp dụng được Lens thử áo.');
        if (!stopped) canvas.hidden = false;
      })(),
      new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('Tải Lens quá lâu. Hãy thử lại.')), 40000); }),
      new Promise((_, reject) => {
        onAbort = () => reject(new DOMException('Đã hủy tải Lens.', 'AbortError'));
        signal?.addEventListener('abort', onAbort, { once: true });
      }),
    ]);
    signal?.removeEventListener('abort', onAbort);
    return { stop };
  } catch (error) { stop(); throw error; }
}
