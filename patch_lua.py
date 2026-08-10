import re

with open("plugin/StyleAI.lrdevplugin/APISearchIndex.lua", "r") as f:
    content = f.read()

# Locate the loop that builds allPhotoIds
old_loop = """
            local photoId = getPhotoIdForPhoto(photo, options)
            if photoId then
                table.insert(allPhotoIds, photoId)
                photoIdToPhotoMap[photoId] = photo
                photoIdByPhoto[photo] = photoId
            else
"""

new_loop = """
            local photoId = getPhotoIdForPhoto(photo, options)
            if photoId then
                if not photoIdToPhotoMap[photoId] then
                    table.insert(allPhotoIds, photoId)
                    photoIdToPhotoMap[photoId] = photo
                end
                photoIdByPhoto[photo] = photoId
            else
"""

content = content.replace(old_loop.strip(), new_loop.strip())

with open("plugin/StyleAI.lrdevplugin/APISearchIndex.lua", "w") as f:
    f.write(content)
