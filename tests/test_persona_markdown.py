import pytest
from pathlib import Path
from vtuber.persona import Persona


def test_load_from_markdown(tmp_path):
    persona_file = tmp_path / "persona.md"
    persona_file.write_text("""# Persona Configuration

## Basic Info
- Name: TestAgent
- Description: A test agent

## Personality Traits
- Friendly
- Helpful

## Speaking Style
- Casual
""")

    persona = Persona.from_markdown(persona_file)
    assert persona.name == "TestAgent"
    assert "Friendly" in persona.traits
    assert persona.description == "A test agent"


def test_to_system_prompt_from_markdown(tmp_path):
    persona_file = tmp_path / "persona.md"
    persona_file.write_text("""# Persona Configuration

## Basic Info
- Name: TestAgent

## Personality Traits
- Friendly
""")

    persona = Persona.from_markdown(persona_file)
    prompt = persona.to_system_prompt()
    assert "TestAgent" in prompt
    assert "Friendly" in prompt
