# Weather providers and fallback

Open-Meteo remains the primary provider: its geocoding resolves city names (preferring Vietnam), and its daily forecast supplies temperature, rain probability and WMO conditions. Both providers are keyless.

If primary geocoding or forecasting times out, fails, or returns malformed/wrong-date data, the backend calls **wttr.in JSON API**. When primary geocoding succeeded, the fallback receives the same coordinates. If geocoding itself is down, wttr.in resolves the requested city independently. An explicit unknown-city result and invalid dates/coordinates are not retried through a different provider.

Healthy primary requests never call the fallback. Each provider gets a maximum six-second phase budget, keeping total failover within approximately twelve seconds and the web client's fifteen-second weather timeout. Primary and successful fallback results use the existing 30-minute in-memory cache. Cached fallback results are reused until expiry, avoiding repeated primary failures and extra round trips.

The existing weather fields are preserved. Successful responses additionally include:

```json
{"provider":"wttr.in","fallback":true,"fallback_reason":"primary_unavailable"}
```

Primary results use `provider:open-meteo` and `fallback:false`. Temperatures are Celsius. Fallback rain probability is the maximum reported hourly chance of rain, rather than a probability inferred from rainfall amount. Conditions are mapped to the closest supported WMO family; unknown codes/probabilities remain null.

wttr.in normally provides **three days**, while primary requests support today through fourteen days ahead. The fallback must contain the exact requested date; it never substitutes today's forecast for a future date. If neither source can serve that date, the existing `available:false` response is returned. No synthetic forecast is generated. Fallback days are the provider's reported local calendar dates; the primary uses Asia/Bangkok (UTC+7).

`GET /api/services/status` reports `weather` for the usable weather feature and separate `weather_primary` / `weather_fallback` observations. A successful fallback keeps weather usable while the primary status can show its recent failure. Status is temporary observed state, not active probing.

All existing callers benefit automatically: `/api/weather`, the stylist screen, HTTP chat tools and Gemini Live tools share `WeatherService`. No extra frontend request or API key is required.

References: [Open-Meteo forecast API](https://open-meteo.com/en/docs), [wttr.in JSON API documentation](https://github.com/chubin/wttr.in#json-output).
