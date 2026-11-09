"""Deterministic state and intent transitions for the conversational stylist.

The module deliberately contains no FastAPI, provider or storage dependency so
the conversation policy can be tested in mock mode and reused by web/KMM
adapters later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

StylistAction = Literal[
    "provide_context",
    "exclude_item",
    "keep_item",
    "refine_formality",
    "refine_temperature",
    "show_another",
    "select_outfit",
    "reset",
    "unknown",
]


@dataclass(frozen=True)
class StylistIntent:
    """The allowlisted result of one user message."""

    action: StylistAction = "unknown"
    slot_updates: dict[str, Any] = field(default_factory=dict)
    target_item_id: str | None = None
    target_outfit_index: int | None = None
    value: int | None = None


@dataclass
class StylistConversationState:
    """Short-lived conversation state; never a replacement for style memory."""

    session_id: str
    event: str = ""
    city: str | None = None
    date: str | None = None
    style: str = ""
    formality_delta: int = 0
    temperature_delta: int = 0
    anchor_item_id: str | None = None
    excluded_item_ids: set[str] = field(default_factory=set)
    current_outfit_ids: list[str] = field(default_factory=list)
    shown_outfit_keys: set[str] = field(default_factory=set)
    pending_question: str | None = None
    turn_count: int = 0
    state_version: int = 0

    def public_dict(self) -> dict[str, Any]:
        """Return only data safe to expose to a client."""

        return {
            "session_id": self.session_id,
            "event": self.event,
            "city": self.city,
            "date": self.date,
            "style": self.style,
            "formality_delta": self.formality_delta,
            "temperature_delta": self.temperature_delta,
            "anchor_item_id": self.anchor_item_id,
            "excluded_item_ids": sorted(self.excluded_item_ids),
            "current_outfit_ids": list(self.current_outfit_ids),
            "pending_question": self.pending_question,
            "turn_count": self.turn_count,
            "state_version": self.state_version,
        }


def _copy_state(state: StylistConversationState) -> StylistConversationState:
    return StylistConversationState(
        session_id=state.session_id,
        event=state.event,
        city=state.city,
        date=state.date,
        style=state.style,
        formality_delta=state.formality_delta,
        temperature_delta=state.temperature_delta,
        anchor_item_id=state.anchor_item_id,
        excluded_item_ids=set(state.excluded_item_ids),
        current_outfit_ids=list(state.current_outfit_ids),
        shown_outfit_keys=set(state.shown_outfit_keys),
        pending_question=state.pending_question,
        turn_count=state.turn_count,
        state_version=state.state_version,
    )


def _safe_text(value: Any, max_length: int = 200) -> str:
    return str(value or "").strip()[:max_length]


def _apply_slot_updates(state: StylistConversationState, updates: dict[str, Any]) -> None:
    allowed = {"event", "city", "date", "style"}
    for key in allowed:
        if key in updates and updates[key] is not None:
            setattr(state, key, _safe_text(updates[key], 80 if key != "event" else 200))


def apply_intent(
    state: StylistConversationState,
    intent: StylistIntent,
    valid_item_ids: set[str],
) -> StylistConversationState:
    """Apply an already parsed intent while enforcing item-ID invariants."""

    next_state = _copy_state(state)
    next_state.turn_count += 1
    next_state.state_version += 1
    _apply_slot_updates(next_state, intent.slot_updates)

    requested_anchor = intent.slot_updates.get("anchor_item_id")
    if requested_anchor in valid_item_ids and requested_anchor not in next_state.excluded_item_ids:
        next_state.anchor_item_id = str(requested_anchor)

    item_id = intent.target_item_id
    if intent.action in {"exclude_item", "keep_item"} and item_id in valid_item_ids:
        if intent.action == "exclude_item":
            next_state.excluded_item_ids.add(item_id)
            if next_state.anchor_item_id == item_id:
                next_state.anchor_item_id = None
            next_state.current_outfit_ids = [
                value for value in next_state.current_outfit_ids if value != item_id
            ]
        else:
            next_state.excluded_item_ids.discard(item_id)
            next_state.anchor_item_id = item_id

    if intent.action == "refine_formality" and intent.value is not None:
        next_state.formality_delta = max(-2, min(2, next_state.formality_delta + intent.value))
    elif intent.action == "refine_temperature" and intent.value is not None:
        next_state.temperature_delta = max(-2, min(2, next_state.temperature_delta + intent.value))
    elif intent.action == "show_another":
        next_state.current_outfit_ids = []
    elif intent.action == "select_outfit" and intent.target_outfit_index is not None:
        next_state.pending_question = None
    elif intent.action == "reset":
        session_id = next_state.session_id
        next_state = StylistConversationState(session_id=session_id, state_version=state.state_version + 1)

    return next_state


def missing_required_slots(state: StylistConversationState) -> list[str]:
    """Return only slots whose absence prevents a useful first suggestion."""

    return ["event"] if not state.event.strip() else []


def outfit_key(item_ids: list[str] | tuple[str, ...]) -> str:
    """Stable key used to suppress repeated outfit cards in one session."""

    return ",".join(sorted({str(item_id) for item_id in item_ids if item_id}))


__all__ = [
    "StylistAction",
    "StylistConversationState",
    "StylistIntent",
    "apply_intent",
    "missing_required_slots",
    "outfit_key",
]
