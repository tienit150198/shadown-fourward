from __future__ import annotations

import json

import pytest

from conftest import FAKE_JPEG, make_test_skill
from skill_store import SkillNotFound, SkillStore, SkillValidationError, slugify_id


def test_slugify_id():
    assert slugify_id("Pha Cà Phê Đá!") == "pha-ca-phe-da"
    assert slugify_id("../../etc/passwd") == "etc-passwd"
    assert slugify_id("   ") == ""


def test_list_get_and_urls(skills_dir):
    store = SkillStore(skills_dir)
    (skills_dir / "broken.json").write_text("{not json", encoding="utf-8")
    items = store.list()
    assert [i["id"] for i in items] == ["test-skill"]
    item = items[0]
    assert item["steps"] == 3 and item["learned_from"] == "Cô Lan"
    # no cover_image -> falls back to first reference image
    assert item["cover_image_url"] == "/skills-media/test-skill/frames/f01.jpg"
    full = store.get_dict("test-skill")
    assert full["steps"][0]["reference_image_url"] == "/skills-media/test-skill/frames/f01.jpg"
    assert full["steps"][2]["reference_image_url"] is None
    assert store.media_path("test-skill", "frames/f01.jpg") is not None
    assert store.media_path("test-skill", "../test-skill.json") is None
    with pytest.raises(SkillNotFound):
        store.get("nope")


def test_save_promotes_draft(tmp_path):
    store = SkillStore(tmp_path / "skills")
    draft = store.drafts_root / "draft123" / "frames"
    draft.mkdir(parents=True)
    (draft / "f01.jpg").write_bytes(FAKE_JPEG)
    (draft / "f02.jpg").write_bytes(FAKE_JPEG)

    skill = make_test_skill("Kỹ Năng Mới")
    skill["draft_id"] = "draft123"
    skill["steps"][0]["reference_image_url"] = "/ignored"  # extra keys tolerated
    saved = store.save(skill)

    assert saved.id == "ky-nang-moi"
    assert (store.root / "ky-nang-moi.json").is_file()
    assert (store.root / "ky-nang-moi" / "frames" / "f01.jpg").read_bytes() == FAKE_JPEG
    assert not (store.drafts_root / "draft123").exists()
    on_disk = json.loads((store.root / "ky-nang-moi.json").read_text(encoding="utf-8"))
    assert "draft_id" not in on_disk and "reference_image_url" not in on_disk["steps"][0]
    assert on_disk["display_name"] == "Pha trà thử nghiệm"  # utf-8 preserved
    assert store.media_path("ky-nang-moi", "frames/f02.jpg") is not None
    assert [i["id"] for i in store.list()] == ["ky-nang-moi"]

    assert store.delete("ky-nang-moi") is True
    assert not (store.root / "ky-nang-moi").exists()
    assert store.delete("ky-nang-moi") is False


def test_save_rejects_invalid(tmp_path):
    store = SkillStore(tmp_path / "skills")
    bad = make_test_skill()
    bad["steps"][0]["detector_key"] = "missing"
    with pytest.raises(SkillValidationError, match="unknown detector_key"):
        store.save(bad)
    bad = make_test_skill()
    bad["steps"][1]["reference_image"] = "../../secret.jpg"
    with pytest.raises(SkillValidationError, match="unsafe"):
        store.save(bad)
    bad = make_test_skill()
    bad["steps"][0]["evaluation_mode"] = "nonsense"
    with pytest.raises(SkillValidationError):
        store.save(bad)


def test_rest_skill_crud(client, skills_dir):
    assert client.get("/api/health").json() == {
        "ok": True,
        "mode": "mock",
        "vision_model": "google/gemini-2.5-flash",
        "compiler_model": "google/gemini-2.5-flash",
        "access_required": False,
    }
    listing = client.get("/api/skills").json()
    assert listing[0]["id"] == "test-skill"
    assert {"id", "display_name", "learned_from", "summary", "steps", "cover_image_url"} <= set(listing[0])

    full = client.get("/api/skills/test-skill").json()
    assert full["steps"][1]["reference_image_url"] == "/skills-media/test-skill/frames/f02.jpg"
    media = client.get("/skills-media/test-skill/frames/f02.jpg")
    assert media.status_code == 200 and media.content == FAKE_JPEG
    assert client.get("/skills-media/test-skill/../test-skill.json").status_code == 404
    assert client.get("/api/skills/missing").status_code == 404

    # PUT with draft promotion; path id wins over body id
    draft = skills_dir / "_drafts" / "d1" / "frames"
    draft.mkdir(parents=True)
    (draft / "f01.jpg").write_bytes(FAKE_JPEG)
    # draft previews are served before the skill is saved
    assert client.get("/skills-media/_drafts/d1/frames/f01.jpg").content == FAKE_JPEG
    body = dict(full, id="whatever", display_name="Bản sửa", draft_id="d1")
    res = client.put("/api/skills/ban-sua", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["id"] == "ban-sua" and res.json()["display_name"] == "Bản sửa"
    assert (skills_dir / "ban-sua" / "frames" / "f01.jpg").is_file()

    bad = dict(full, start_step_id="nope")
    res = client.put("/api/skills/bad", json=bad)
    assert res.status_code == 422 and "không hợp lệ" in res.json()["message"]

    assert client.delete("/api/skills/ban-sua").json() == {"ok": True}
    assert client.delete("/api/skills/ban-sua").status_code == 404


def test_ask_mock(client):
    res = client.post(
        "/api/ask",
        json={"skill_id": "test-skill", "step_id": "s1", "question": "Cầm thế nào?", "image": None},
    )
    assert res.status_code == 200
    answer = res.json()["answer"]
    assert "Cầm ấm lên" in answer and "Cầm bằng quai" in answer
    assert client.post("/api/ask", json={"skill_id": "x", "question": "?"}).status_code == 404
    assert client.post("/api/ask", json={"skill_id": "test-skill", "question": "  "}).status_code == 422
