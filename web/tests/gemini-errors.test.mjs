import test from 'node:test';
import assert from 'node:assert/strict';
import { geminiLiveError } from '../js/gemini-errors.js';
test('Google balance, billing, permissions and quota failures stay distinguishable and credential safe',()=>{
  for (const [text,status,code] of [
    ['Prepayment credits are depleted tk-private https://google.test',429,'insufficient_credits'],
    ['Insufficient funds',1011,'insufficient_credits'],
    ['Billing is not enabled',403,'billing_required'],
    ['API key not valid secret',403,'provider_auth_failed'],
    ['Resource exhausted quota',429,'rate_limited'],
    ['connection timed out',0,'provider_timeout'],
  ]) {
    const error=geminiLiveError({message:text},status);
    assert.equal(error.code,code);
    assert.doesNotMatch(error.message,/tk-private|https:|secret/);
  }
  assert.equal(geminiLiveError({detail:{code:'missing_api_key',message:'private'}}).code,'missing_api_key');
});
