from __future__ import annotations

from stylist_conversation import (
    StylistConversationState,
    StylistIntent,
    apply_intent,
    missing_required_slots,
)
from stylist_chat import parse_stylist_intent
from providers import MockProvider

import asyncio


def test_context_is_merged_without_overwriting_unmentioned_slots():
    state = StylistConversationState(session_id="s1", event="đi làm", city="Hà Nội")

    updated = apply_intent(
        state,
        StylistIntent(action="provide_context", slot_updates={"style": "thanh lịch"}),
        valid_item_ids=set(),
    )

    assert updated.event == "đi làm"
    assert updated.city == "Hà Nội"
    assert updated.style == "thanh lịch"
    assert updated.turn_count == 1


def test_unknown_item_cannot_become_anchor_or_exclusion():
    state = StylistConversationState(session_id="s1")

    updated = apply_intent(
        state,
        StylistIntent(
            action="exclude_item",
            target_item_id="ghost",
            slot_updates={"anchor_item_id": "ghost"},
        ),
        valid_item_ids={"top-1"},
    )

    assert updated.anchor_item_id is None
    assert updated.excluded_item_ids == set()


def test_exclude_item_and_keep_item_are_mutually_consistent():
    state = StylistConversationState(session_id="s1", anchor_item_id="top-1")

    excluded = apply_intent(
        state,
        StylistIntent(action="exclude_item", target_item_id="top-1"),
        valid_item_ids={"top-1", "bottom-1"},
    )
    assert excluded.anchor_item_id is None
    assert excluded.excluded_item_ids == {"top-1"}

    kept = apply_intent(
        excluded,
        StylistIntent(action="keep_item", target_item_id="bottom-1"),
        valid_item_ids={"top-1", "bottom-1"},
    )
    assert kept.anchor_item_id == "bottom-1"
    assert kept.excluded_item_ids == {"top-1"}


def test_refinement_actions_update_deltas_and_reset_clears_session():
    state = StylistConversationState(session_id="s1", event="đi chơi")
    refined = apply_intent(
        state,
        StylistIntent(action="refine_formality", value=1),
        valid_item_ids=set(),
    )
    refined = apply_intent(
        refined,
        StylistIntent(action="refine_temperature", value=-1),
        valid_item_ids=set(),
    )
    assert refined.formality_delta == 1
    assert refined.temperature_delta == -1

    reset = apply_intent(refined, StylistIntent(action="reset"), valid_item_ids=set())
    assert reset.session_id == "s1"
    assert reset.event == ""
    assert reset.current_outfit_ids == []
    assert reset.turn_count == 0


def test_missing_required_slots_asks_for_event_only_when_needed():
    assert missing_required_slots(StylistConversationState(session_id="s1")) == ["event"]
    assert missing_required_slots(StylistConversationState(session_id="s1", event="đi học")) == []


def test_mock_parser_extracts_event_and_city_from_vietnamese_message():
    intent = asyncio.run(
        parse_stylist_intent(
            MockProvider(),
            "Tôi đi đám cưới ở Hà Nội",
            StylistConversationState(session_id="s1"),
            [],
        )
    )

    assert intent.action == "provide_context"
    assert intent.slot_updates["event"] == "đám cưới"
    assert intent.slot_updates["city"] == "Hà Nội"


def test_mock_parser_detects_refinement_and_exclusion():
    state = StylistConversationState(session_id="s1", current_outfit_ids=["top-1"])
    formal = asyncio.run(parse_stylist_intent(MockProvider(), "Trang trọng hơn", state, []))
    exclude = asyncio.run(
        parse_stylist_intent(
            MockProvider(),
            "Áo này đang giặt",
            state,
            [{"id": "top-1", "name": "Áo sơ mi trắng", "category": "top"}],
        )
    )

    assert formal.action == "refine_formality" and formal.value == 1
    assert exclude.action == "exclude_item" and exclude.target_item_id == "top-1"
