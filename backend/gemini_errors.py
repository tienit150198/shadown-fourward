"""Classify known Google account failures without exposing upstream error text."""


def account_error_code(message: str, reasons: set[str] | None = None) -> str | None:
    reasons = reasons or set()
    if reasons & {'API_KEY_INVALID', 'API_KEY_SERVICE_BLOCKED', 'API_KEY_HTTP_REFERRER_BLOCKED', 'API_KEY_IP_ADDRESS_BLOCKED'}:
        return 'provider_auth_failed'
    if reasons & {'BILLING_DISABLED', 'PROJECT_BILLING_DISABLED'}:
        return 'billing_required'
    text = message.lower()
    if any(term in text for term in ('prepayment credits are depleted', 'credits are depleted', 'insufficient credits', 'insufficient credit balance')):
        return 'insufficient_credits'
    if 'billing' in text and any(term in text for term in ('not enabled', 'disabled', 'enable billing')):
        return 'billing_required'
    if any(term in text for term in ('api key not valid', 'api key is invalid', 'permission denied', 'unauthenticated')):
        return 'provider_auth_failed'
    return None
