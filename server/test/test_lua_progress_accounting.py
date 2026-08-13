from pathlib import Path
import shutil
import subprocess

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugin" / "StyleAI.lrdevplugin"
LUA = shutil.which("lua")


def _lua_summary(expression: str) -> tuple[int, int, int, int]:
    if LUA is None:
        pytest.skip("lua interpreter is not available")
    script = f"""
package.path = {str(PLUGIN_ROOT / "?.lua")!r} .. ';' .. package.path
local accounting = require('ProgressAccounting')
local result = {expression}
print(result.success, result.failed, result.processed, result.total)
"""
    completed = subprocess.run(
        [LUA, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(int(value) for value in completed.stdout.split())


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("accounting.live(0, 0, {}, 0)", (0, 0, 0, 0)),
        (
            "accounting.live(3, 1, {pending=2, running=1, succeeded=4}, 10)",
            (7, 1, 8, 10),
        ),
        (
            "accounting.live(2, 1, {committing=2, failed=1, canceled=1, interrupted=1}, 9)",
            (4, 4, 8, 9),
        ),
        ("accounting.live(8, 3, {succeeded=9, failed=9}, 10)", (10, 0, 10, 10)),
        ("accounting.live(-2, -3, {succeeded=-1, failed=-1}, 5)", (0, 0, 0, 5)),
        ("accounting.terminal(2, 1, 4, 3, 10)", (6, 4, 10, 10)),
        ("accounting.terminal(0, 0, 0, 0, 12)", (0, 0, 0, 12)),
    ],
)
def test_progress_accounting_is_bounded_and_mutually_exclusive(expression, expected):
    summary = _lua_summary(expression)

    assert summary == expected
    success, failed, processed, total = summary
    assert processed == success + failed
    assert 0 <= processed <= total
