#!/usr/bin/env python3
"""
Lightroom Classic Plugin Requirements Validator
Scans the Lightroom Classic plugin source directory for common anti-patterns
and strict SDK requirements.

Usage:
  python3 scripts/validate_lrc_plugin.py [plugin_dir]
"""

import sys
import os
import re
import subprocess

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
                result = subprocess.run(["luac", "-p", path], capture_output=True, text=True)
                if result.returncode != 0:
                    errors.append(f"[Syntax Error] {file}: {result.stderr.strip()}")
            except FileNotFoundError:
                # luac not installed, skip syntax check
                print("Warning: 'luac' compiler not found in PATH. Skipping syntax check.")
                return []
    return errors

def scan_files(plugin_dir):
    """Scan all Lua files for best practice violations."""
    errors = []
    
    # Regex definitions
    require_pattern = re.compile(r'require\s*\(\s*["\']([^"\']+)["\']\s*\)')
    pcall_pattern = re.compile(r'(?<!LrTasks\.)\bpcall\s*\(')
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
                    if '/' in module_name or '.' in module_name:
                        errors.append(f"[Invalid Require] {file}:{line_num} -> require(\"{module_name}\") contains invalid characters '/' or '.'. Lightroom requires a flat directory structure for modules.")

                # Check 2: pcall should usually be LrTasks.pcall
                if pcall_pattern.search(line):
                    # Ignore lines that are explicitly doing fallback
                    if "status, a, b, c = pcall(" not in line:
                        errors.append(f"[Native pcall] {file}:{line_num} -> Found native 'pcall'. Prefer 'LrTasks.pcall' to allow yielding inside async tasks.")

                # Check 3: UI strings should be localized
                if not line.strip().startswith("--"):
                    for match in ui_string_pattern.finditer(line):
                        val = match.group(2).strip("'\"")
                        if 'LOC' not in line and 'bind' not in line:
                            if val and any(c.isalpha() for c in val) and val != "StyleAI":
                                errors.append(f"[Unlocalized String] {file}:{line_num} -> Found {match.group(1)}=\"{val}\" without LOC wrapper.")

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
