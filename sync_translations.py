import os
import re

def extract_loc_keys(directory):
    # Matches LOC "$$$/StyleAI/Module/Key=Default Value"
    # and LOC("$$$/StyleAI/Module/Key=Default Value", ...)
    pattern = re.compile(r'LOC\s*\(?\s*["\'](\$\$\$/StyleAI/[^"\']+)["\']')
    
    keys = {}
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.lua'):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    matches = pattern.findall(content)
                    for m in matches:
                        if '=' in m:
                            key, val = m.split('=', 1)
                            keys[key] = val
                        else:
                            if m not in keys:
                                keys[m] = ""
    return keys

def load_translated_strings(path):
    strings = {}
    if not os.path.exists(path):
        return strings
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with open(path, 'r', encoding='utf-16') as f:
            lines = f.readlines()
            
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('--'):
            continue
        match = re.search(r'["\'](\$\$\$/[^"\']+)["\']\s*=\s*["\'](.*)["\']', line)
        if match:
            strings[match.group(1)] = match.group(2)
    return strings

def sync_translations(lua_dir, target_path, base_strings=None):
    extracted_keys = extract_loc_keys(lua_dir)
    existing_strings = load_translated_strings(target_path)
    
    # If base_strings is provided (e.g. for DE/FR), we use it as the source of truth for keys
    if base_strings:
        keys_to_use = sorted(list(base_strings.keys()))
    else:
        # Lua source is authoritative; stale keys from removed features must not
        # survive indefinitely in every localization file.
        keys_to_use = sorted(extracted_keys.keys())
    
    new_content = []
    for key in keys_to_use:
        # Ignore old keys from previous project names if they aren't in current extraction
        if not key.startswith('$$$/StyleAI/'):
            if key not in extracted_keys and (not base_strings or key not in base_strings):
                continue

        val = existing_strings.get(key)
        
        # For EN, always use extracted default as source of truth if present
        if not base_strings:
            if key in extracted_keys and extracted_keys[key]:
                val = extracted_keys[key]
            elif not val or val == "":
                val = extracted_keys.get(key) or ""
        else:
            # For non-EN, if missing or if it's a classification/recommendation string that needs sync, use EN value
            if not val or val == "" or "Strength" in key or "Recommend" in key or "UpgradeAssistant" in key:
                val = base_strings.get(key) or val or ""
        
        if val:
            val = re.sub(r'\\+"', '"', val)
            val = val.replace('"', '\\"')
        else:
            val = ""
        new_content.append(f'"{key}" = "{val}"')
    
    output = '\n'.join(new_content)
    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(output)
    return load_translated_strings(target_path)

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    lua_dir = os.path.join(base_dir, "plugin", "StyleAI.lrdevplugin")
    trans_en = os.path.join(lua_dir, "TranslatedStrings_en.txt")
    trans_de = os.path.join(lua_dir, "TranslatedStrings_de.txt")
    trans_fr = os.path.join(lua_dir, "TranslatedStrings_fr.txt")
    trans_es = os.path.join(lua_dir, "TranslatedStrings_es.txt")
    trans_ca = os.path.join(lua_dir, "TranslatedStrings_ca.txt")
    
    en_strings = sync_translations(lua_dir, trans_en)
    print("Synched English.")
    sync_translations(lua_dir, trans_de, en_strings)
    print("Synched German.")
    sync_translations(lua_dir, trans_fr, en_strings)
    print("Synched French.")
    sync_translations(lua_dir, trans_es, en_strings)
    print("Synched Spanish.")
    sync_translations(lua_dir, trans_ca, en_strings)
    print("Synched Catalan.")
