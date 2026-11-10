import test from 'node:test';
import assert from 'node:assert/strict';

import { personPoseVerdict } from '../js/tryon-pose.js';

const point = (y, visibility = 0.95) => ({ x: 0.5, y, visibility });
function pose() {
  const landmarks = Array.from({ length: 33 }, () => point(0.5));
  landmarks[0] = point(0.08);
  landmarks[23] = point(0.55);
  landmarks[24] = point(0.55);
  landmarks[25] = point(0.82);
  landmarks[26] = point(0.82);
  return landmarks;
}

test('person gate rejects an image without a detected person', () => {
  assert.deepEqual(personPoseVerdict(null, { needKnees: true }), {
    ok: false, code: 'no_person', message: 'Chưa thấy rõ người trong ảnh. Hãy đứng thẳng, đủ sáng và chụp lại.',
  });
});

test('person gate rejects a head outside the frame', () => {
  const landmarks = pose();
  landmarks[0] = point(-0.02);
  assert.equal(personPoseVerdict(landmarks, { needKnees: false }).code, 'head_out_of_frame');
});

test('person gate requires visible hips for every outfit', () => {
  const landmarks = pose();
  landmarks[23] = point(0.55, 0.2);
  landmarks[24] = point(0.55, 0.2);
  assert.equal(personPoseVerdict(landmarks, { needKnees: false }).code, 'hips_not_visible');
});

test('person gate requires visible knees for bottoms and dresses', () => {
  const landmarks = pose();
  landmarks[25] = point(0.97);
  landmarks[26] = point(0.98);
  assert.equal(personPoseVerdict(landmarks, { needKnees: true }).code, 'knees_not_visible');
  assert.equal(personPoseVerdict(landmarks, { needKnees: false }).ok, true);
});

test('person gate accepts a visible head-to-knee pose', () => {
  assert.deepEqual(personPoseVerdict(pose(), { needKnees: true }), { ok: true, code: 'ok', message: '' });
});
