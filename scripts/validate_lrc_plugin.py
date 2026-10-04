#!/usr/bin/env python3
"""
Lightroom Classic Plugin Requirements Validator
Scans the Lightroom Classic plugin source directory for common anti-patterns
and strict SDK requirements.

Usage:
  python3 scripts/validate_lrc_plugin.py [plugin_dir]
"""

import os
import re
import subprocess
import sys


# Lightroom persists each custom metadata field's complete specification in the
# catalog. Changing any part of that specification without advancing the field's
# own version prevents the plug-in from loading. Keep prior signatures here so a
# future contract edit must add a new version instead of silently rewriting one.
# A signature is: title LOC string, data type, read-only, searchable, browsable.
METADATA_SCHEMA_VERSION = 35
METADATA_FIELD_CONTRACT_HISTORY = {
    "aiLastRun": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/aiLastRun=Last AI run",
            "string",
            True,
            True,
            True,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/aiLastRun=StyleAI Metadata Date",
            "string",
            True,
            True,
            True,
        ),
    },
    "aiModel": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/aiModel=AI model",
            "string",
            True,
            True,
            True,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/aiModel=StyleAI Metadata Model",
            "string",
            True,
            True,
            True,
        ),
    },
    "aiEditLastRun": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditLastRun=Last AI edit run",
            "string",
            True,
            True,
            True,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/AiEditLastRun=StyleAI Edit Date",
            "string",
            True,
            True,
            True,
        ),
    },
    "aiEditModel": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditModel=AI edit model",
            "string",
            True,
            True,
            True,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/AiEditModel=StyleAI Edit Model",
            "string",
            True,
            False,
            False,
        ),
    },
    "aiEditStatus": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditStatus=AI edit status",
            "string",
            True,
            True,
            True,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/AiEditStatus=Legacy StyleAI edit status",
            "string",
            True,
            False,
            False,
        ),
    },
    "aiEditApplied": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditApplied=StyleAI Edit Applied",
            "string",
            True,
            True,
            True,
        ),
    },
    "aiEditState": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditState=StyleAI Edit State",
            "string",
            True,
            True,
            True,
        ),
    },
    "aiEditReview": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditReview=StyleAI Edit Review",
            "string",
            True,
            True,
            True,
        ),
    },
    "aiEditInferenceId": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditInferenceId=AI edit inference ID",
            "string",
            True,
            True,
            False,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/AiEditInferenceId=AI edit inference ID",
            "string",
            True,
            False,
            False,
        ),
    },
    "aiEditSummary": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditSummary=AI edit summary",
            "string",
            True,
            True,
            True,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/AiEditSummary=StyleAI Edit Summary",
            "string",
            True,
            True,
            True,
        ),
    },
    "aiEditWarnings": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditWarnings=AI edit warnings",
            "string",
            True,
            True,
            True,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/AiEditWarnings=StyleAI Edit Warnings",
            "string",
            True,
            True,
            True,
        ),
    },
    "aiEditRecipe": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/AiEditRecipeJson=AI edit recipe JSON",
            "string",
            True,
            False,
            False,
        ),
    },
    "pendingAiEditApplicationEvent": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/PendingAiEditApplicationEvent=Pending AI edit application event",
            "string",
            True,
            False,
            False,
        ),
    },
    "photoContext": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/photoContext=Photo context",
            "string",
            False,
            True,
            True,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/photoContext=StyleAI Photo Instructions",
            "string",
            False,
            True,
            True,
        ),
    },
    "keywords": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/keywords=AI Keywords",
            "string",
            True,
            True,
            True,
        ),
        2: (
            "$$$/StyleAI/AIMetadataProvider/keywords=Legacy StyleAI Keywords",
            "string",
            True,
            False,
            False,
        ),
    },
    "globalPhotoId": {
        2: (
            "$$$/StyleAI/AIMetadataProvider/GlobalPhotoId=Global Photo ID",
            "string",
            True,
            True,
            False,
        ),
        3: (
            "$$$/StyleAI/AIMetadataProvider/GlobalPhotoId=Global Photo ID",
            "string",
            True,
            False,
            False,
        ),
    },
    "globalPhotoIdFileSize": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/GlobalPhotoIdFileSize=Global Photo ID File Size",
            "string",
            True,
            False,
            False,
        ),
    },
    "globalPhotoIdFileModificationDate": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/GlobalPhotoIdModificationDate=Global Photo ID File Modification Date",
            "string",
            True,
            False,
            False,
        ),
    },
    "globalPhotoIdAlgorithm": {
        1: (
            "$$$/StyleAI/AIMetadataProvider/GlobalPhotoIdAlgorithm=Global Photo ID Algorithm",
            "string",
            True,
            False,
            False,
        ),
    },
}


def check_info_manifest(plugin_dir):
    """Evaluate Info.lua and validate Lightroom's strict menu-table contract."""
    info_path = os.path.join(plugin_dir, "Info.lua")
    if not os.path.isfile(info_path):
        return [f"[Manifest Error] Required file is missing: {info_path}"]

    with open(info_path, encoding="utf-8") as info_file:
        info_source = info_file.read()

    source_errors = []
    if re.search(r"\bLrShutdownApp\s*=", info_source):
        source_errors.append(
            "[Manifest Error] LrShutdownApp must not be registered; Lightroom 15.5 "
            "can deadlock while dispatching its completion callback."
        )
    for key in ("LrLibraryMenuItems", "LrExportMenuItems", "LrHelpMenuItems"):
        assignment = re.search(rf"\b{key}\s*=\s*([^\s,]+)", info_source)
        if not assignment:
            continue
        first_value = assignment.group(1)
        if re.fullmatch(r"[A-Za-z_]\w*", first_value):
            source_errors.append(
                f"[Manifest Error] {key} must use a literal table so empty or nil "
                "menu state cannot be hidden behind a variable."
            )
        elif re.search(rf"\b{key}\s*=\s*\{{\s*\}}", info_source):
            source_errors.append(
                f"[Manifest Error] {key} must be omitted instead of registered "
                "as an empty table."
            )
    if source_errors:
        return source_errors

    lua_validator = r"""
LOC = function(value) return value end
local info_path = os.getenv("STYLEAI_INFO_PATH")
if info_path == nil or info_path == "" then
    error("STYLEAI_INFO_PATH was not provided")
end
local manifest = dofile(info_path)
if type(manifest) ~= "table" then
    error("Info.lua must return a table")
end
for _, key in ipairs({ "LrLibraryMenuItems", "LrExportMenuItems", "LrHelpMenuItems" }) do
    local menu = manifest[key]
    if menu ~= nil then
        if type(menu) ~= "table" then
            error(key .. " must be a table when present")
        end
        if #menu == 0 then
            error(key .. " must be omitted instead of registered as an empty table")
        end
        for index, item in ipairs(menu) do
            if type(item) ~= "table" then
                error(key .. "[" .. index .. "] must be a table")
            end
            if type(item.title) ~= "string" or item.title == "" then
                error(key .. "[" .. index .. "].title must be a non-empty string")
            end
            if type(item.file) ~= "string" or item.file == "" then
                error(key .. "[" .. index .. "].file must be a non-empty string")
            end
        end
    end
end
"""
    try:
        environment = os.environ.copy()
        environment["STYLEAI_INFO_PATH"] = os.path.abspath(info_path)
        result = subprocess.run(
            ["lua", "-e", lua_validator],
            capture_output=True,
            env=environment,
            text=True,
        )
    except FileNotFoundError:
        print(
            "Warning: 'lua' interpreter not found in PATH. Skipping manifest evaluation."
        )
        return []

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return [f"[Manifest Error] Info.lua: {detail}"]
    return []


def check_lua_syntax(plugin_dir):
    """Run luac -p to check for Lua syntax errors."""
    errors = []
    for root, _, files in os.walk(plugin_dir):
        for file in files:
            if not file.endswith(".lua"):
                continue
            path = os.path.join(root, file)
            # Try to run luac if available
            try:
                result = subprocess.run(
                    ["luac", "-p", path], capture_output=True, text=True
                )
                if result.returncode != 0:
                    errors.append(f"[Syntax Error] {file}: {result.stderr.strip()}")
            except FileNotFoundError:
                # luac not installed, skip syntax check
                print(
                    "Warning: 'luac' compiler not found in PATH. Skipping syntax check."
                )
                return []
    return errors


def check_photo_metadata_properties(plugin_dir):
    """Ensure photo plug-in properties are declared by the metadata provider."""
    provider_path = os.path.join(plugin_dir, "MetadataProvider.lua")
    if not os.path.isfile(provider_path):
        return [f"[Metadata Error] Required file is missing: {provider_path}"]

    with open(provider_path, encoding="utf-8") as provider_file:
        provider_source = provider_file.read()
    declared_ids = set(re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', provider_source))
    errors = []

    for root, _, files in os.walk(plugin_dir):
        for filename in files:
            if not filename.endswith(".lua"):
                continue
            path = os.path.join(root, filename)
            with open(path, encoding="utf-8") as source_file:
                source = source_file.read()

            constants = dict(
                re.findall(
                    r'\blocal\s+([A-Z][A-Z0-9_]*)\s*=\s*["\']([^"\']+)["\']',
                    source,
                )
            )
            property_pattern = re.compile(
                r"(?:\bphoto|\w+\.photo):(?:get|set)PropertyForPlugin"
                r'\s*\(\s*_PLUGIN\s*,\s*([A-Z][A-Z0-9_]*|["\'][^"\']+["\'])'
            )
            for match in property_pattern.finditer(source):
                token = match.group(1)
                property_id = (
                    token[1:-1] if token[0] in {'"', "'"} else constants.get(token)
                )
                if property_id and property_id not in declared_ids:
                    line_number = source.count("\n", 0, match.start()) + 1
                    errors.append(
                        f"[Metadata Error] {filename}:{line_number} -> Photo property "
                        f"'{property_id}' is not declared in MetadataProvider.lua."
                    )
    return errors


def check_metadata_contract_versions(plugin_dir):
    """Reject catalog-facing field changes that omit a per-field version bump."""
    provider_path = os.path.join(plugin_dir, "MetadataProvider.lua")
    if not os.path.isfile(provider_path):
        return [f"[Metadata Error] Required file is missing: {provider_path}"]

    with open(provider_path, encoding="utf-8") as provider_file:
        source = provider_file.read()

    errors = []
    schema_match = re.search(r"\bschemaVersion\s*=\s*(\d+)", source)
    if not schema_match or int(schema_match.group(1)) != METADATA_SCHEMA_VERSION:
        actual = schema_match.group(1) if schema_match else "missing"
        errors.append(
            "[Metadata Contract Error] Provider schemaVersion is "
            f"{actual}; expected {METADATA_SCHEMA_VERSION}."
        )

    parsed = {}
    field_pattern = re.compile(
        r'\bid\s*=\s*"(?P<id>[^"]+)"(?P<body>.*?)(?=\n\s*\},)', re.DOTALL
    )
    for match in field_pattern.finditer(source):
        field_id = match.group("id")
        body = match.group("body")

        def string_property(name):
            found = re.search(rf'\b{name}\s*=\s*"([^"]+)"', body, re.DOTALL)
            return found.group(1) if found else None

        def boolean_property(name):
            found = re.search(rf"\b{name}\s*=\s*(true|false)", body)
            return found.group(1) == "true" if found else None

        title_match = re.search(r'\btitle\s*=\s*LOC\(\s*"([^"]+)"', body, re.DOTALL)
        version_match = re.search(r"\bversion\s*=\s*(\d+)", body)
        parsed[field_id] = (
            int(version_match.group(1)) if version_match else None,
            (
                title_match.group(1) if title_match else None,
                string_property("dataType"),
                boolean_property("readOnly"),
                boolean_property("searchable"),
                boolean_property("browsable"),
            ),
        )

    expected_ids = set(METADATA_FIELD_CONTRACT_HISTORY)
    actual_ids = set(parsed)
    for field_id in sorted(expected_ids - actual_ids):
        errors.append(
            f"[Metadata Contract Error] Expected field '{field_id}' is missing."
        )
    for field_id in sorted(actual_ids - expected_ids):
        errors.append(
            f"[Metadata Contract Error] Field '{field_id}' has no version history. "
            "Add its version-1 signature to METADATA_FIELD_CONTRACT_HISTORY."
        )

    for field_id in sorted(expected_ids & actual_ids):
        version, signature = parsed[field_id]
        history = METADATA_FIELD_CONTRACT_HISTORY[field_id]
        current_version = max(history)
        if version is None:
            errors.append(
                f"[Metadata Contract Error] Field '{field_id}' has no explicit version."
            )
        elif version != current_version:
            errors.append(
                f"[Metadata Contract Error] Field '{field_id}' declares version {version}; "
                f"contract history requires {current_version}."
            )
        elif signature != history[version]:
            errors.append(
                f"[Metadata Contract Error] Field '{field_id}' changed without a new "
                f"version. Add version {version + 1} with the new signature to "
                "METADATA_FIELD_CONTRACT_HISTORY and increment schemaVersion."
            )

        ordered_signatures = [history[item] for item in sorted(history)]
        if len(ordered_signatures) != len(set(ordered_signatures)):
            errors.append(
                f"[Metadata Contract Error] Field '{field_id}' contains a version bump "
                "without a contract change."
            )
    return errors


def scan_files(plugin_dir):
    """Scan all Lua files for best practice violations."""
    errors = []

    # Regex definitions
    require_pattern = re.compile(r'require\s*\(\s*["\']([^"\']+)["\']\s*\)')
    pcall_pattern = re.compile(r"(?<!LrTasks\.)\bpcall\s*\(")
    ui_string_pattern = re.compile(r'\b(title|tooltip)\s*=\s*("[^"]+"|\'[^\']+\')')

    for root, _, files in os.walk(plugin_dir):
        for file in files:
            if not file.endswith(".lua"):
                continue
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception as e:
                errors.append(f"[File Error] {file}: Could not read file ({e})")
                continue

            for i, line in enumerate(lines):
                line_num = i + 1

                # Check 1: require statements cannot contain '/' or '.'
                # because Lightroom's module loader throws "invalid characters in script name"
                for match in require_pattern.finditer(line):
                    module_name = match.group(1)
                    if "/" in module_name or "." in module_name:
                        errors.append(
                            f"[Invalid Require] {file}:{line_num} -> require(\"{module_name}\") contains invalid characters '/' or '.'. Lightroom requires a flat directory structure for modules."
                        )

                # Check 2: pcall should usually be LrTasks.pcall
                if pcall_pattern.search(line):
                    # The synchronous OS probe during plug-in initialization cannot
                    # safely enter the task scheduler.
                    if file == "Init.lua" and line_num == 36:
                        continue
                    # Ignore lines that are explicitly doing fallback
                    if "status, a, b, c = pcall(" not in line:
                        errors.append(
                            f"[Native pcall] {file}:{line_num} -> Found native 'pcall'. Prefer 'LrTasks.pcall' to allow yielding inside async tasks."
                        )

                # Check 3: UI strings should be localized
                if file != "TaskAutomatedTests.lua" and not line.strip().startswith(
                    "--"
                ):
                    for match in ui_string_pattern.finditer(line):
                        val = match.group(2).strip("'\"")
                        if "LOC" not in line and "bind" not in line:
                            if (
                                val
                                and any(c.isalpha() for c in val)
                                and val != "StyleAI"
                            ):
                                errors.append(
                                    f'[Unlocalized String] {file}:{line_num} -> Found {match.group(1)}="{val}" without LOC wrapper.'
                                )

                # Check 4: Lightroom omits these familiar-looking APIs. They fail
                # only at runtime, so reject them during plug-in validation.
                for unsupported, replacement in (
                    ("os.rename", "LrFileUtils.move"),
                    ("LrHttp.encodeForUrl", "a local percent-encoding helper"),
                ):
                    if unsupported in line:
                        errors.append(
                            f"[Unsupported Lightroom API] {file}:{line_num} -> "
                            f"'{unsupported}' is unavailable; use {replacement}."
                        )

    return errors


def main():
    plugin_dir = sys.argv[1] if len(sys.argv) > 1 else "plugin/StyleAI.lrdevplugin"
    if not os.path.exists(plugin_dir):
        print(f"Error: Plugin directory '{plugin_dir}' does not exist.")
        sys.exit(1)

    print(f"Validating Lightroom Classic plugin at '{plugin_dir}'...")

    all_errors = []

    syntax_errors = check_lua_syntax(plugin_dir)
    all_errors.extend(syntax_errors)

    manifest_errors = check_info_manifest(plugin_dir)
    all_errors.extend(manifest_errors)

    metadata_errors = check_photo_metadata_properties(plugin_dir)
    all_errors.extend(metadata_errors)

    metadata_contract_errors = check_metadata_contract_versions(plugin_dir)
    all_errors.extend(metadata_contract_errors)

    scan_errors = scan_files(plugin_dir)
    all_errors.extend(scan_errors)

    if all_errors:
        print(f"\nFound {len(all_errors)} violations:\n")
        for err in all_errors:
            print(f"  {err}")
        print("\nValidation FAILED.")
        sys.exit(1)
    else:
        print("\nAll checks passed. Plugin conforms to requirements.")
        sys.exit(0)


if __name__ == "__main__":
    main()
