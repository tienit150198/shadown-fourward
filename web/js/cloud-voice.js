import { ApiError, fetchWithAccess, statusMessage } from './api.js';

export function recordingSupported() {
  return !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder === 'function';
}

export async function recordVoice() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const parts = [];
  try {
    const recorder = new MediaRecorder(stream);
    const stopped = new Promise((resolve) => {
      recorder.addEventListener('dataavailable', (e) => { if (e.data.size) parts.push(e.data); });
      recorder.addEventListener('stop', () => {
        stream.getTracks().forEach((track) => track.stop());
        resolve(new Blob(parts, { type: recorder.mimeType }));
      }, { once: true });
      recorder.addEventListener('error', () => {
        stream.getTracks().forEach((track) => track.stop());
        resolve(null);
      }, { once: true });
    });
    recorder.start();
    return {
      stop() {
        if (recorder.state !== 'inactive') recorder.stop();
        return stopped;
      },
      cancel() {
        if (recorder.state !== 'inactive') recorder.stop();
        stream.getTracks().forEach((track) => track.stop());
      },
    };
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    throw error;
  }
}

async function audioRequest(path, type, body, signal) {
  const response = await fetchWithAccess(path, {
    method: 'POST', headers: { 'Content-Type': type }, body, signal, cache: 'no-store',
  });
  if (!response.ok) throw new ApiError('Voice unavailable', {
    userMessage: statusMessage(response.status) || 'Dịch vụ giọng nói chưa phản hồi. Bạn có thể nhập câu hỏi.',
  });
  return response;
}

export async function transcribeVoice(blob, signal) {
  const response = await audioRequest('/api/voice/transcribe', blob.type, blob, signal);
  return (await response.json()).text;
}

export async function voiceAudio(text, signal) {
  const response = await audioRequest('/api/voice/speech', 'application/json', JSON.stringify({ text }), signal);
  return response.arrayBuffer();
}
