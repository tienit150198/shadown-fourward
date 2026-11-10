# Implementation Plan: KMM API Contract

## Constitution Check

- Server-authoritative Guide: satisfied; DTO mirrors server state and does not add client transitions.
- AI deterministic guards: satisfied; contract exposes result/source, not model internals as truth.
- Explicit degradation: satisfied; Stylist source/fallback semantics are documented.
- Bounded data/cost: satisfied; image/rate/timeout limits are part of contract.
- Single-node persistence: satisfied; no durable session or database claim.
- Compatible client contracts: **Q3 provisional**; additive-field policy is accepted.
- Evidence before completion: **Q7 provisional**; test mechanism awaits colleague input.
- License-aware: try-on worker excluded from V1.
- Surgical brownfield: no runtime endpoint change and no new dependency unless approved.

## Planned artifacts

1. `docs/API.md` — Vietnamese human-readable API reference.
2. `specs/001-kmm-api-contract/data-model.md` — boundary DTO model.
3. `specs/001-kmm-api-contract/contracts/rest-api.md` — REST scope/rules.
4. `specs/001-kmm-api-contract/contracts/websocket.md` — Guide message contract.
5. `BaseKMM/shared/...` DTO/Ktor examples only after Q4 dependency check.
6. Contract fixtures/test harness only after Q7 decision.

## Dependency direction

```text
KMM DTO/client → REST/WebSocket boundary → SHADOW backend
KMM UI → KMM state → KMM client → SHADOW contract
```

KMM must not import backend files or persist server JSON directly.

## Compatibility strategy

- V1 documents current endpoint paths.
- Q3 decides whether `/api/v1` aliases are introduced later.
- Additive optional fields are backward-compatible.
- Breaking changes require deprecation/migration notes and a separate feature.

## Risks

- Runtime payload may drift before contract tests exist.
- Ktor dependency/version may not yet be present in `BaseKMM`.
- Access code may be replaced by real authentication later.
- WebSocket session semantics are not durable.

## Rollback

This feature is documentation/DTO-first. Rollback removes docs/DTO/fixtures only; it must not delete or migrate user data
or modify existing backend endpoints.
