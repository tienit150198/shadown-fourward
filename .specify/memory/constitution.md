# SHADOW Project Constitution

**Version:** 1.0.0-draft  
**Status:** Accepted for pilot, pending Q3/Q6/Q7 implementation details  
**Scope:** `shadown-fourward/`

## Purpose

This constitution defines invariants for SHADOW features. Detailed architecture lives in
`docs/SYSTEM_DESIGN_ANALYSIS.md`; agent operating rules live in the workspace `AGENTS.md`.

## Principles

### I. Server-authoritative guidance

Backend owns Guide Runtime state and step transitions. PWA/KMM sends intent and frames, then renders server state.
Clients never decide completion or inject internal events. Guide changes must account for stale results, cancellation,
disconnect and model errors.

### II. AI proposes; deterministic code guards

LLMs may draft, classify, judge or suggest. Schema validation, semantic validation, state machines, rule engines and
scoring enforce invariants before output is persisted or presented as valid.

### III. Explicit degradation

Mock and fallback providers are supported behavior and must be labeled. Auto mode may fall back according to its chain;
an explicitly selected provider returns a real error instead of silently returning mock output.

### IV. Bounded data and cost

Image, transcript, token and provider features require size, timeout, cancellation, rate and concurrency limits. Long-
lived secrets remain server-side. Vendor, retention and cost impact must be recorded in feature artifacts.

### V. Single-node persistence is the current contract

JSON/filesystem and in-process locks are single-node behavior. A feature must not assume multi-process or multi-replica
safety. Database, object storage, multi-tenant identity or durable session changes require a separate architecture decision.

### VI. Compatible client contracts

REST, WebSocket and worker payloads have a documented contract. Additive optional fields are preferred; removing or
changing semantics requires a migration/deprecation plan. KMM owns native UI/device capability; SHADOW owns AI
orchestration and business truth.

### VII. Evidence before completion

Every requirement has acceptance criteria and an exact verification command. Checklists are not runtime evidence. Missing
environment, device or vendor verification must be reported as `not verified`.

### VIII. License-aware design

Current CatVTON/IDM-VTON paths and some seed assets are non-commercial/demo-only. Commercial features need a licensed
provider/model/asset plan before implementation.

### IX. Surgical brownfield change

Prefer a small vertical slice, reuse existing patterns, and avoid new dependencies, abstractions or services without an
approved need. Every task states file scope and rollback behavior.

## Governance

- Constitution changes require reason, impact, migration/rollback and human approval.
- `MAJOR` changes alter or remove a principle; `MINOR` adds a principle; `PATCH` clarifies without changing meaning.
- Every feature plan includes a Constitution Check.
- Q3 (URL versioning), Q6 (final authentication contract) and Q7 (contract-test implementation) are accepted in direction
  but remain **provisional** until the pending colleague input arrives.
