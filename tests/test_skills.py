"""Tests for the skills MCP tools."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from vtuber.tools.skills import (
    build_skill_summary,
    set_refresh_event,
    skill_create,
    skill_delete,
    skill_invoke,
    skill_refresh,
    skill_update,
)

GREETING_SKILL_MD = """\
---
name: greeting
description: Generate a friendly greeting
---

You are a greeting generator. Produce a warm, friendly greeting for the user.
"""

FAREWELL_SKILL_MD = """\
---
name: farewell
description: Generate a farewell message
---

You are a farewell generator. Produce a warm goodbye message for the user.
"""


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """Create a temp skills dir with 'greeting' and 'farewell' sample skills."""
    sd = tmp_path / "skills"
    sd.mkdir()

    greeting_dir = sd / "greeting"
    greeting_dir.mkdir()
    (greeting_dir / "SKILL.md").write_text(GREETING_SKILL_MD, encoding="utf-8")

    farewell_dir = sd / "farewell"
    farewell_dir.mkdir()
    (farewell_dir / "SKILL.md").write_text(FAREWELL_SKILL_MD, encoding="utf-8")

    return sd


# ── build_skill_summary ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_skill_summary_with_skills(skills_dir: Path):
    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        summary = build_skill_summary()
        assert "greeting" in summary
        assert "farewell" in summary
        assert "Generate a friendly greeting" in summary
        assert "Generate a farewell message" in summary
        assert "skill_invoke" in summary


@pytest.mark.asyncio
async def test_build_skill_summary_empty(tmp_path: Path):
    empty_dir = tmp_path / "skills"
    empty_dir.mkdir()
    with patch("vtuber.tools.skills.get_skills_dir", return_value=empty_dir):
        summary = build_skill_summary()
        assert summary == ""


@pytest.mark.asyncio
async def test_build_skill_summary_no_dir(tmp_path: Path):
    missing_dir = tmp_path / "nonexistent"
    with patch("vtuber.tools.skills.get_skills_dir", return_value=missing_dir):
        summary = build_skill_summary()
        assert summary == ""


# ── skill_invoke ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_invoke(skills_dir: Path):
    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_invoke.handler({"skill": "greeting"})
        text = result["content"][0]["text"]
        assert "greeting generator" in text


@pytest.mark.asyncio
async def test_skill_invoke_not_found(skills_dir: Path):
    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_invoke.handler({"skill": "nonexistent"})
        text = result["content"][0]["text"]
        assert "not found" in text


# ── skill_create ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_create(tmp_path: Path):
    sd = tmp_path / "skills"
    sd.mkdir()
    with patch("vtuber.tools.skills.get_skills_dir", return_value=sd):
        result = await skill_create.handler({"name": "new_skill"})
        text = result["content"][0]["text"]
        assert str(sd / "new_skill") in text
        assert (sd / "new_skill").is_dir()


@pytest.mark.asyncio
async def test_skill_create_already_exists(skills_dir: Path):
    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_create.handler({"name": "greeting"})
        text = result["content"][0]["text"]
        assert "already exists" in text


# ── skill_update ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_update(skills_dir: Path):
    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_update.handler({"name": "greeting"})
        text = result["content"][0]["text"]
        assert str(skills_dir / "greeting" / "SKILL.md") in text
        assert "skill_refresh" in text


@pytest.mark.asyncio
async def test_skill_update_not_found(skills_dir: Path):
    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_update.handler({"name": "nonexistent"})
        text = result["content"][0]["text"]
        assert "not found" in text


# ── skill_delete ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_delete(skills_dir: Path):
    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_delete.handler({"name": "greeting"})
        text = result["content"][0]["text"]
        assert "Deleted" in text
        assert not (skills_dir / "greeting").exists()


@pytest.mark.asyncio
async def test_skill_delete_not_found(skills_dir: Path):
    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_delete.handler({"name": "nonexistent"})
        text = result["content"][0]["text"]
        assert "not found" in text


# ── skill_refresh ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_refresh():
    event = asyncio.Event()
    set_refresh_event(event)
    assert not event.is_set()

    result = await skill_refresh.handler({})
    text = result["content"][0]["text"]
    assert "refresh" in text.lower()
    assert event.is_set()
