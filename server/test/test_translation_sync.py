import importlib.util
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SYNC_SCRIPT = REPOSITORY_ROOT / "sync_translations.py"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location(
        "styleai_sync_translations", SYNC_SCRIPT
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_loc_keys_allows_opposite_quote_inside_lua_string(tmp_path):
    module = _load_sync_module()
    (tmp_path / "Strings.lua").write_text(
        "\n".join(
            (
                'LOC("$$$/StyleAI/Test/Double=This catalog\'s complete message.")',
                "LOC('$$$/StyleAI/Test/Single=Choose the \"Complete\" option.')",
            )
        ),
        encoding="utf-8",
    )

    assert module.extract_loc_keys(tmp_path) == {
        "$$$/StyleAI/Test/Double": "This catalog's complete message.",
        "$$$/StyleAI/Test/Single": 'Choose the "Complete" option.',
    }


def test_sync_preserves_existing_non_english_values(tmp_path):
    module = _load_sync_module()
    target = tmp_path / "TranslatedStrings_ca.txt"
    target.write_text(
        "\n".join(
            (
                '"$$$/StyleAI/Defaults/Strength/Low" = "Baixa"',
                '"$$$/StyleAI/UpgradeAssistant/Actions" = "Revisió de candidats"',
            )
        ),
        encoding="utf-8",
    )
    english = {
        "$$$/StyleAI/Defaults/Strength/Low": "Low",
        "$$$/StyleAI/UpgradeAssistant/Actions": "Candidate Review",
    }

    result = module.sync_translations(tmp_path, target, english)

    assert result == {
        "$$$/StyleAI/Defaults/Strength/Low": "Baixa",
        "$$$/StyleAI/UpgradeAssistant/Actions": "Revisió de candidats",
    }
