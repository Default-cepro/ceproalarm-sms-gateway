import json

import pytest

from src.core import commands


def _write_config(tmp_path, payload):
    path = tmp_path / "commands.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_commands_normalizes_to_lowercase(tmp_path, monkeypatch):
    path = _write_config(
        tmp_path,
        {
            "  WanwayTech  ": {
                "  GS-900  ": {
                    "  STATUS  ": {"command": "STATUS#", "expected": "GPRS:Link Up"}
                }
            }
        },
    )
    monkeypatch.setattr(commands, "CONFIG_PATH", path)
    loaded = commands.load_commands()
    assert loaded == {
        "wanwaytech": {
            "gs-900": {"status": {"command": "STATUS#", "expected": "GPRS:Link Up"}}
        }
    }


def test_get_command_success(monkeypatch):
    monkeypatch.setattr(
        commands,
        "COMMANDS",
        {"wanwaytech": {"gs-900": {"status": {"command": "STATUS#"}}}},
    )
    assert commands.get_command("WanwayTech", "GS-900") == {"command": "STATUS#"}


def test_get_command_unsupported_brand(monkeypatch):
    monkeypatch.setattr(commands, "COMMANDS", {"wanwaytech": {"gs-900": {}}})
    with pytest.raises(ValueError, match="Marca no soportada: acme"):
        commands.get_command("Acme", "gs-900")


def test_get_command_unsupported_model(monkeypatch):
    monkeypatch.setattr(
        commands, "COMMANDS", {"wanwaytech": {"gs-900": {"status": {}}}}
    )
    with pytest.raises(ValueError, match="Modelo no soportado: wanwaytech vt08f"):
        commands.get_command("WanwayTech", "VT08F")


def test_get_command_undefined_action(monkeypatch):
    monkeypatch.setattr(
        commands, "COMMANDS", {"wanwaytech": {"gs-900": {"status": {}}}}
    )
    with pytest.raises(ValueError, match="Acción 'locate' no definida"):
        commands.get_command("WanwayTech", "GS-900", "locate")


def test_real_config_has_status_action_for_known_brands():
    # Read-only assertion against the repo's config template (not data/).
    loaded = commands.load_commands()
    assert "wanwaytech" in loaded
    assert "protrack" in loaded
    assert "status" in loaded["wanwaytech"]["gs-900"]
