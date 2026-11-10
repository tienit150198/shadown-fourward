# Quickstart and Verification

## Current backend reference

```bash
cd shadown-fourward/backend
../.venv/bin/python -m pytest -q
```

If `.venv` is absent, report the suite as not verified; do not substitute an unpinned environment and claim parity.

## KMM verification when code is added

```bash
cd BaseKMM
export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
./gradlew :shared:testAndroidHostTest :androidApp:assembleDebug
```

## Manual Guide smoke

1. Start SHADOW in mock mode.
2. Connect a client to `/session/control`.
3. Receive `session.ready`.
4. Send `session.start` with an existing skill ID.
5. Send a JPEG `vision.frame`.
6. Verify `hud.state`, `speech`, `vision.result` and error handling.
7. Close and reconnect; verify the client starts a new session rather than claiming resume.

## Manual Stylist smoke

1. Start with mock provider and non-empty demo wardrobe.
2. Call `/api/stylist/suggest`.
3. Verify `outfits`, `source`, item IDs and weather fields.
4. Simulate model failure and verify deterministic fallback remains labeled.

## Evidence labels

- `verified`: exact command/device run succeeded.
- `mock verified`: mock path only.
- `integration verified`: external service call succeeded.
- `not verified`: environment, device, vendor or pending decision prevented verification.
