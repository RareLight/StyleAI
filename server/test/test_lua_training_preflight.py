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
local preflight = require('TrainingPreflight')
{body}
"""
    completed = subprocess.run(
        [LUA, "-e", script], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip().splitlines()


def test_preflight_pages_large_deduplicated_input_in_original_order():
    lines = _run_lua(
        """
local ids = {}
for i = 1, 2500 do table.insert(ids, 'p' .. tostring(i)) end
table.insert(ids, 'p1')
local pages = {}
local ok, result = preflight.run(ids, false, nil, function(chunk)
    table.insert(pages, #chunk)
    return { existing_photo_ids = {}, needed_photo_ids = chunk }
end)
print(tostring(ok), table.concat(pages, ','), #result.needed_photo_ids,
    result.needed_photo_ids[1], result.needed_photo_ids[#result.needed_photo_ids])
"""
    )

    assert lines == ["true\t1000,1000,500\t2500\tp1\tp2500"]


def test_preflight_partial_page_failure_stops_without_returning_partial_state():
    lines = _run_lua(
        """
local ids = {}
for i = 1, 1500 do table.insert(ids, 'p' .. tostring(i)) end
local pages = 0
local ok, result = preflight.run(ids, false, nil, function(chunk)
    pages = pages + 1
    if pages == 2 then return nil, 'page failed' end
    return { existing_photo_ids = {}, needed_photo_ids = chunk }
end)
print(tostring(ok), result, pages)
"""
    )

    assert lines == ["false\tpage failed\t2"]


def test_preflight_cancellation_between_pages_prevents_next_request():
    lines = _run_lua(
        """
local ids = {}
for i = 1, 1500 do table.insert(ids, 'p' .. tostring(i)) end
local canceled = false
local pages = 0
local progress = { isCanceled = function() return canceled end }
local ok, result = preflight.run(ids, false, progress, function(chunk)
    pages = pages + 1
    canceled = true
    return { existing_photo_ids = {}, needed_photo_ids = chunk }
end)
print(tostring(ok), result, pages)
"""
    )

    assert lines == ["false\tTraining preflight canceled\t1"]


def test_preflight_retry_and_force_retrain_have_no_retained_partial_state():
    lines = _run_lua(
        """
local calls = 0
local function request(chunk, force)
    calls = calls + 1
    if calls == 1 then return nil, 'transient' end
    return { existing_photo_ids = {}, needed_photo_ids = chunk, force = force }
end
local firstOk = preflight.run({ 'b', 'a', 'a' }, true, nil, request)
local secondOk, result = preflight.run({ 'b', 'a', 'a' }, true, nil, request)
local sorted = preflight.sortedUniqueIds({ 'b', 'a', 'a' })
print(tostring(firstOk), tostring(secondOk), table.concat(result.needed_photo_ids, ','),
    tostring(result.force_retrain), table.concat(sorted, ','), calls)
"""
    )

    assert lines == ["false\ttrue\tb,a\ttrue\ta,b\t2"]


def test_fingerprint_payload_is_stable_and_covers_request_semantics():
    lines = _run_lua(
        """
local empty = preflight.fingerprintPayload({}, nil, false)
local one = preflight.fingerprintPayload({ 'p1' }, 'catalog', false)
local repeated = preflight.fingerprintPayload({ 'b', 'a', 'b' }, 'selected', true)
local reordered = preflight.fingerprintPayload({ 'a', 'b' }, 'selected', true)
print(empty.schema, empty.kind, #empty.photo_ids, empty.scope,
    tostring(empty.force_retrain))
print(table.concat(one.photo_ids, ','), one.scope, tostring(one.force_retrain))
print(table.concat(repeated.photo_ids, ','), repeated.scope,
    tostring(repeated.force_retrain))
print(table.concat(reordered.photo_ids, ','), reordered.scope,
    tostring(reordered.force_retrain))
"""
    )

    assert lines == [
        "training_operation_v1\ttraining\t0\tselected\tfalse",
        "p1\tcatalog\tfalse",
        "a,b\tselected\ttrue",
        "a,b\tselected\ttrue",
    ]
