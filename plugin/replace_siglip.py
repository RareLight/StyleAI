import os

plugin_dir = "/Users/anna/Documents/Coding/StyleAI/plugin"

for root, dirs, files in os.walk(plugin_dir):
    for file in files:
        if file.endswith(".txt") or file.endswith(".lua"):
            file_path = os.path.join(root, file)
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            if "OpenCLIP" in content or "OpenClip" in content or "open_clip" in content:
                # Be careful with the GitHub URL, open_clip should stay as is there, but wait, the model is siglip!
                # Actually let's just replace OpenCLIP -> SigLIP2 and OpenClip -> SigLIP2
                new_content = content.replace("OpenCLIP", "SigLIP2")
                new_content = new_content.replace("OpenClip", "SigLIP2")
                
                # For Defaults.lua we want to update the URL and Author too. Let's do it manually later.
                
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                print(f"Updated {file_path}")
