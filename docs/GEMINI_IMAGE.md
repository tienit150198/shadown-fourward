# Nano Banana photo try-on through Google AI Studio

Set these values in server `.env`, then recreate the app:

```dotenv
GEMINI_API_KEY=your-google-ai-studio-key
TRYON_PHOTO_PROVIDER=gemini
# Optional; this is the default:
GEMINI_IMAGE_MODEL=gemini-nano-banana-2.1
```

The key is shared with optional [Gemini Live voice](GEMINI_LIVE.md). Photo try-on works independently of `GEMINI_LIVE_ENABLED`. Google's Gemini Developer API is used directly, without Vertex AI or OpenRouter:

```text
POST https://generativelanguage.googleapis.com/v1/models/gemini-nano-banana-2.1:generateContent
x-goog-api-key: <server-only-key>
```

The public app API stays `POST /api/tryon/photo` with `{person_image,item_ids}`. It sends the person's image first and all available selected garment images in order, with one candidate and a 1K output. Multiple tops are intentional layers. Aspect ratio follows the closest supported ratio to the person photo. The app accepts up to eight garments; the model supports up to fourteen total references.

Generated thought images are ignored. Only a final image is converted to JPEG and saved. The result reports `provider:gemini`, actual rendered/skipped IDs and its existing authenticated media URL. The frontend labels the provider as Google AI Studio. A blocked image request returns `content_blocked`; bad keys, model permissions, missing configuration, billing/quota, timeouts and corrupt provider output use the standard structured error contract. A depleted prepaid balance is reported as non-retryable `insufficient_credits` even if Google uses HTTP 429 for that account error. No raw provider response is exposed to the client, and no paid request is automatically retried or switched to another provider.

`GET /api/tryon/config` reports the selected photo provider. `GET /api/services/status` reports local configuration and recent observed failures. Model metadata checks can confirm that a key recognizes the model, but they do not prove generation quota, quality, or garment fidelity. Actual generation can require billing or quota granted by the Google project.

The OpenRouter implementation remains available with `TRYON_PHOTO_PROVIDER=openrouter` and `OPENROUTER_API_KEY`. In `auto` mode, configured direct Gemini is preferred, followed by OpenRouter, Vidu, and the legacy local/demo chain. For production, explicitly choose the intended provider.

Official references: [Nano Banana 2.1 model](https://aistudio.google.com/docs/models/gemini-nano-banana-2.1), [Generate Content image editing](https://aistudio.google.com/docs/image-generation?generate-content=true).
