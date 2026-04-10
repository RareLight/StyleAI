import os
import re

def extract_loc_keys(directory):
    # Matches LOC "$$$/LrGeniusAI/Module/Key=Default Value"
    # and LOC("$$$/LrGeniusAI/Module/Key=Default Value", ...)
    pattern = re.compile(r'LOC\s*\(?\s*["\'](\$\$\$/LrGeniusAI/[^"\']+)["\']')
    
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
        # For EN, we use union of extracted and existing
        keys_to_use = sorted(list(set(extracted_keys.keys()) | set(existing_strings.keys())))
    
    new_content = []
    for key in keys_to_use:
        # Ignore old keys from previous project names if they aren't in current extraction
        if not key.startswith('$$$/LrGeniusAI/'):
            if key not in extracted_keys and (not base_strings or key not in base_strings):
                continue

        val = existing_strings.get(key)
        
        # For EN, if missing, use extracted default
        if not base_strings:
            if not val or val == "":
                val = extracted_keys.get(key) or ""
        else:
            # For non-EN, if missing, use EN value as placeholder (or empty)
            if not val or val == "":
                # If the DE/FR value was missing, we can see if it was in EN
                val = base_strings.get(key) or ""
        
        val = val.replace('"', '\\"')
        new_content.append(f'"{key}" = "{val}"')
    
    output = '\n'.join(new_content)
    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(output)
    return load_translated_strings(target_path)

if __name__ == "__main__":
    lua_dir = "/Users/bm/src/LrGeniusAI/plugin/LrGeniusAI.lrdevplugin"
    trans_en = "/Users/bm/src/LrGeniusAI/plugin/LrGeniusAI.lrdevplugin/TranslatedStrings_en.txt"
    trans_de = "/Users/bm/src/LrGeniusAI/plugin/LrGeniusAI.lrdevplugin/TranslatedStrings_de.txt"
    trans_fr = "/Users/bm/src/LrGeniusAI/plugin/LrGeniusAI.lrdevplugin/TranslatedStrings_fr.txt"
    
    en_strings = sync_translations(lua_dir, trans_en)
    print("Synched English.")
    sync_translations(lua_dir, trans_de, en_strings)
    print("Synched German.")
    sync_translations(lua_dir, trans_fr, en_strings)
    print("Synched French.")
