# Worker API Contract

Try-on worker is **out of scope for KMM Contract V1**. Its current boundary remains documented here so the scope is
explicit:

- `GET /health`
- `POST /tryon`

Any KMM-facing use of photo try-on requires a later feature that defines provider selection, base64/image transport,
token behavior, queue/busy semantics and non-commercial license constraints.
