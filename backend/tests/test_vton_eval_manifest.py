from __future__ import annotations

from evals.validate_vton_cases import validate_manifest


def _case(**overrides):
    case = {
        "id": "case-001",
        "asset_status": "pending-consented-assets",
        "person_image": None,
        "garment_images": [],
        "categories": ["top"],
        "consent": {"status": "pending", "record_ref": None},
        "expected_checks": ["identity", "garment_fidelity", "artifacts"],
        "human_labels": {"identity": None, "garment_fidelity": None, "artifacts": None},
    }
    case.update(overrides)
    return case


def test_vton_eval_manifest_accepts_a_pending_consent_placeholder():
    errors = validate_manifest({"schema_version": 1, "cases": [_case()]})
    assert errors == []


def test_vton_eval_manifest_rejects_private_or_embedded_asset_references():
    cases = [
        _case(id="absolute", asset_status="ready", person_image="/Users/me/private.jpg",
              garment_images=["assets/top.jpg"], consent={"status": "granted", "record_ref": "consent/a.json"}),
        _case(id="embedded", asset_status="ready", person_image="data:image/jpeg;base64,AAAA",
              garment_images=["https://example.com/top.jpg"], consent={"status": "granted", "record_ref": "consent/b.json"}),
    ]

    errors = validate_manifest({"schema_version": 1, "cases": cases})

    assert any("absolute" in error and "relative" in error for error in errors)
    assert any("embedded" in error and "data URL" in error for error in errors)
    assert any("embedded" in error and "remote URL" in error for error in errors)


def test_ready_vton_eval_case_requires_consent_and_assets():
    errors = validate_manifest({"schema_version": 1, "cases": [_case(id="ready", asset_status="ready")]})
    assert any("ready" in error and "consent" in error for error in errors)
    assert any("ready" in error and "person_image" in error for error in errors)
