from pathlib import Path
import shutil
import subprocess

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin"
LUA = shutil.which("lua")


def _run_lua(body: str) -> list[str]:
    if LUA is None:
        pytest.skip("lua interpreter is not available")
    script = f"""
package.path = {str(PLUGIN_ROOT / "?.lua")!r} .. ';' .. package.path
local modelSort = require('ModelParameterSort')
{body}
"""
    completed = subprocess.run(
        [LUA, "-e", script], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip().splitlines()


def test_parameter_sort_orders_descending_and_puts_unknown_sizes_last():
    lines = _run_lua(
        """
local models = {
    { key = 'unknown-z', title = 'Zeta' },
    { key = '7b', title = 'Seven', details = { params_string = '7B' } },
    { key = 'unknown-a', title = 'Alpha' },
    { key = '70b', title = 'Seventy', details = { params_string = '70B' } },
    { key = '31b', title = 'Thirty One', details = { params_string = '31B' } },
}
modelSort.descending(models)
for _, model in ipairs(models) do print(model.key) end
"""
    )

    assert lines == ["70b", "31b", "7b", "unknown-a", "unknown-z"]


def test_parameter_sort_uses_conventional_total_for_moe_metadata():
    lines = _run_lua(
        """
local gemma = {
    key = 'unsloth/gemma-4-26b-a4b-it@q4_k_xl',
    title = 'Gemma 4 26B A4B',
    details = { params_string = '128x2.6B' },
}
local mixtral = {
    key = 'mixtral-8x7b',
    title = 'Mixtral 8x7B',
    details = { params_string = '8x7B' },
}
print(modelSort.parameterCount(gemma))
print(modelSort.parameterCount(mixtral))
"""
    )

    assert lines == ["26000000000.0", "56000000000.0"]
