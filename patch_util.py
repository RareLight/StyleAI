import re

with open("plugin/StyleAI.lrdevplugin/Util.lua", "r") as f:
    content = f.read()

# 1. Add filename to the payload
new_code = """
	local isoSpeed = photo:getFormattedMetadata("isoSpeedRating")
	local fileName = LrPathUtils.leafName(photo:getRawMetadata("path") or "")

	local payload = table.concat({
		tostring(timestampStr),
		tostring(originalWidth),
		tostring(originalHeight),
		tostring(fileFormat),
		tostring(cameraModel),
		tostring(lens),
		tostring(focalLength),
		tostring(aperture),
		tostring(shutterSpeed),
		tostring(isoSpeed),
		tostring(fileName),
	}, "|")
"""

content = re.sub(
    r'\blocal isoSpeed = photo:getFormattedMetadata\("isoSpeedRating"\)\s+local payload = table\.concat\(\{.*?\}, "\|"\)',
    new_code.strip(),
    content,
    flags=re.DOTALL
)

# 2. Change "meta1:" to "meta2:"
content = content.replace('return "meta1:" .. digest', 'return "meta2:" .. digest')

with open("plugin/StyleAI.lrdevplugin/Util.lua", "w") as f:
    f.write(content)
