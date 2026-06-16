import re

with open("plugin/StyleAI.lrdevplugin/PluginInfoDialogSections.lua", "r") as f:
    content = f.read()

# Find the start of AI Provider Configuration
part1, rest = content.split("\t\t\t-- 2. AI Provider Configuration\n", 1)

# Find the start of My Signature Styles
ai_config, rest2 = rest.split("\t\t\tf:group_box({\n\t\t\t\twidth = groupBoxWidth,\n\t\t\t\ttitle = LOC(\"$$$/StyleAI/Training/SectionTitle=My Signature Styles\"),\n", 1)

# Find the start of Advanced Server Settings
signature_styles, part3 = rest2.split("\t\t\t-- 4. Advanced Server Settings & Maintenance\n", 1)

# Reassemble: part1 + My Signature Styles + AI Provider Config + part3
new_content = (
    part1 + 
    "\t\t\t-- 2. My Signature Styles (Editing Engine Prerequisites)\n" +
    "\t\t\tf:group_box({\n\t\t\t\twidth = groupBoxWidth,\n\t\t\t\ttitle = LOC(\"$$$/StyleAI/Training/SectionTitle=My Signature Styles\"),\n" + 
    signature_styles + 
    "\t\t\t-- 3. AI Provider Configuration (Optional for Auto-Tagging)\n" +
    ai_config + 
    "\t\t\t-- 4. Advanced Server Settings & Maintenance\n" + 
    part3
)

with open("plugin/StyleAI.lrdevplugin/PluginInfoDialogSections.lua", "w") as f:
    f.write(new_content)

print("Swapped successfully")
