"""Tests for persona system prompt building."""

from pathlib import Path
from vtuber.persona import build_system_prompt


def test_build_system_prompt_with_files(tmp_path: Path):
    """Test building system prompt from actual files."""
    persona = tmp_path / "persona.md"
    user = tmp_path / "user.md"
    persona.write_text("# Test Persona\n- Friendly")
    user.write_text("# Test User\n- Name: Tester")

    result = build_system_prompt(persona, user)
    assert "Test Persona" in result
    assert "Test User" in result


def test_build_system_prompt_defaults(tmp_path: Path):
    """Test building system prompt with missing files uses defaults."""
    persona = tmp_path / "missing_persona.md"
    user = tmp_path / "missing_user.md"

    result = build_system_prompt(persona, user)
    assert "VTuber" in result  # Default persona name
    assert "User" in result  # Default user name


def test_build_system_prompt_with_skills(tmp_path, monkeypatch):
    """Test that skill summary is injected into system prompt."""
    persona = tmp_path / "persona.md"
    user = tmp_path / "user.md"
    persona.write_text("# Test Persona")
    user.write_text("# Test User")

    # Create a skills dir with a skill
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "test-skill").mkdir()
    (skills_dir / "test-skill" / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill\n---\n\nDo test things.\n"
    )

    monkeypatch.setattr("vtuber.tools.skills.get_skills_dir", lambda: skills_dir)

    result = build_system_prompt(persona, user)
    assert "test-skill" in result
    assert "A test skill" in result
    assert "skill_invoke" in result
