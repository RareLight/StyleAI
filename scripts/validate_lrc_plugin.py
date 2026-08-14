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
