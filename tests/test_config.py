from pathlib import Path
from vtuber.config import get_config_dir, ensure_config_dir, get_skills_dir


def test_get_config_dir():
    config_dir = get_config_dir()
    assert config_dir.name == ".vtuber"
    assert str(Path.home()) in str(config_dir)


def test_ensure_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = ensure_config_dir()
    assert config_dir.exists()
    assert config_dir.is_dir()


def test_get_skills_dir():
    result = get_skills_dir()
    assert result == Path.home() / ".vtuber" / "skills"
