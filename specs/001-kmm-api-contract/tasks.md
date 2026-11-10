# Tasks: KMM API Contract

Status is Draft. Tasks involving Q3/Q6/Q7 remain blocked from final acceptance until colleague input is recorded.

## T001 — Inventory runtime contracts

- **Requirement:** FR-001–FR-005
- **Files:** `backend/main.py`, `backend/wardrobe_api.py`, `backend/session_manager.py`, `backend/session_workflow.py`,
  `backend/vision_runtime.py`
- **Work:** capture actual request/response/message fields and error statuses.
- **Verify:** compare every example with source and existing backend tests.
- **Markers:** `API`, `WS`

## T002 — Write Vietnamese REST reference

- **Requirement:** FR-001–FR-003, FR-009–FR-012
- **Files:** `docs/API.md`, `contracts/rest-api.md`
- **Dependency:** T001
- **Verify:** endpoint checklist has no missing request/response/error section.
- **Markers:** `API`, `SECURITY`

## T003 — Write Guide WebSocket reference

- **Requirement:** FR-003–FR-005, FR-014
- **Files:** `docs/API.md`, `contracts/websocket.md`
- **Dependency:** T001
- **Verify:** event allowlist, internal events, stale/disconnect semantics are explicit.
- **Markers:** `WS`, `STATE`

## T004 — Check KMM transport dependencies

- **Requirement:** FR-006–FR-008
- **Files:** `BaseKMM/` only if approved
- **Dependency:** Q4 accepted; inspect current Gradle catalog first.
- **Verify:** do not add dependency without explicit approval; otherwise run KMM commands from quickstart.
- **Markers:** `KMM`, `DEPENDENCY`

## T005 — Implement Kotlin boundary DTOs/examples

- **Requirement:** FR-006–FR-008, FR-015
- **Files:** `BaseKMM/shared/src/commonMain/...` only after T004
- **Dependency:** T002, T003, T004
- **Verify:** `:shared:testAndroidHostTest` and `:androidApp:assembleDebug`.
- **Markers:** `KMM`, `API`

## T006 — Decide URL versioning (Q3)

- **Requirement:** FR-012
- **Status:** Deferred; direction accepted, final choice pending colleague.
- **Verify:** record decision in `research.md`, `plan.md` and `spec.md` before T002 is final.
- **Markers:** `API`, `DECISION`

## T007 — Decide native authentication contract (Q6)

- **Requirement:** FR-009
- **Status:** Deferred; `X-Shadow-Access` is current pilot assumption.
- **Verify:** record whether access header remains pilot contract or is replaced by auth/session token.
- **Markers:** `SECURITY`, `DECISION`

## T008 — Decide contract evidence mechanism (Q7)

- **Requirement:** FR-013
- **Status:** Deferred; fixture/OpenAPI/generated test choice pending colleague.
- **Verify:** choose one mechanism and add exact command to quickstart.
- **Markers:** `TEST`, `DECISION`

## T009 — Add contract evidence

- **Requirement:** FR-013, SC-004–SC-007
- **Dependency:** T006, T007, T008
- **Work:** add fixtures/schema checks or generated contract test according to Q7.
- **Verify:** exact command returns exit code 0 and reports counts.
- **Markers:** `TEST`, `API`

## T010 — Human handoff

- **Requirement:** SC-001–SC-008
- **Dependency:** T002, T003, T005, T009
- **Work:** review API docs, KMM compile evidence, limitations and rollback.
- **Verify:** no deferred decision is presented as committed behavior.
- **Markers:** `REVIEW`
