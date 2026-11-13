// Never display raw Google errors: they can include project IDs, keys and URLs.
export function geminiLiveError(value, status = 0) {
  const detail = value?.detail;
  const upstream = value?.error || (detail && typeof detail === 'object' ? detail : value);
  const text = String(upstream?.message || upstream?.reason || (typeof upstream === 'string' ? upstream : '')).toLowerCase();
  const reasonCodes = (upstream?.details || []).map(entry => entry?.reason || '').join(' ');
  let code = upstream?.code;
  if (!['insufficient_credits','billing_required','provider_auth_failed','rate_limited','provider_timeout','provider_unavailable','missing_api_key'].includes(code)) {
    if (/insufficient.*(?:fund|credit|balance)|(?:credit|balance).*(?:depleted|exhausted)|not enough credits/.test(text)) code='insufficient_credits';
    else if (/BILLING_DISABLED|PROJECT_BILLING_DISABLED/.test(reasonCodes) || /billing.*(?:disabled|not enabled|enable)|enable.*billing/.test(text)) code='billing_required';
    else if (status===401 || status===403 || /API_KEY_INVALID|API_KEY_SERVICE_BLOCKED|API_KEY_HTTP_REFERRER_BLOCKED|API_KEY_IP_ADDRESS_BLOCKED/.test(reasonCodes) || /api key.*(?:invalid|not valid)|permission denied|unauthenticated/.test(text)) code='provider_auth_failed';
    else if (status===429 || /quota|resource.exhausted|rate.limit/.test(text)) code='rate_limited';
    else if (status===504 || /timeout|timed out/.test(text)) code='provider_timeout';
    else code='provider_unavailable';
  }
  const messages={
    insufficient_credits:'Gemini Live không đủ credit. Hãy nạp credit cho dự án Google rồi thử lại.',
    billing_required:'Dự án Gemini chưa bật thanh toán. Hãy kiểm tra Google AI Studio.',
    provider_auth_failed:'Gemini từ chối khóa API hoặc quyền truy cập. Hãy kiểm tra khóa và dự án Google.',
    missing_api_key:'Chưa cấu hình khóa API cho Gemini Live trên máy chủ.',
    rate_limited:'Gemini Live đã vượt hạn mức hoặc giới hạn lượt gọi. Hãy kiểm tra quota trong Google AI Studio rồi thử lại.',
    provider_timeout:'Gemini Live phản hồi quá lâu. Bạn thử kết nối lại nhé.',
    provider_unavailable:'Chưa kết nối được Gemini Live. Bạn có thể nhập câu hỏi trong lúc chờ.',
  };
  const error=new Error(messages[code]);
  error.code=code; error.service='gemini_live';
  error.retryable=['rate_limited','provider_timeout','provider_unavailable'].includes(code);
  return error;
}
