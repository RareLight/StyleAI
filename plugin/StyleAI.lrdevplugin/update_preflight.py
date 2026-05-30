import re

with open('APISearchIndex.lua', 'r') as f:
    content = f.read()

target = """    progressScope:setCaption(LOC("$$$/StyleAI/AnalyzeAndIndex/ProcessingPhotos=Processing ^1 photos with ^2...",
        #selectedPhotos, options.model or "AI"))
    progressScope:setPortionComplete(0, numPhotos)

    local photoToProcessStack = {}
    for _, photo in ipairs(selectedPhotos) do
        table.insert(photoToProcessStack, photo)
    end

    local maxWorkers = tonumber(prefs.indexingParallelTasks) or 2
    local stats = { processed = 0, success = 0, failed = 0 }"""

replacement = """    local maxWorkers = tonumber(prefs.indexingParallelTasks) or 2
    local stats = { processed = 0, success = 0, failed = 0 }

    local photoToProcessStack = {}
    
    if options.regenerate_metadata == false then
        progressScope:setCaption(LOC("$$$/StyleAI/AnalyzeAndIndex/PreflightCheck=Verifying existing index..."))
        local allIds = {}
        local idToPhotoMap = {}
        for _, photo in ipairs(selectedPhotos) do
            local photoId = getPhotoIdForPhoto(photo)
            if photoId then
                table.insert(allIds, photoId)
                idToPhotoMap[photoId] = photo
            end
        end
        
        local body = {
            photo_ids = allIds,
            tasks = options.tasks,
            regenerate_metadata = false
        }
        local checkCid = getCatalogId()
        if checkCid then
            body.catalog_id = checkCid
        end
        
        local result, err = _request('POST', getBaseUrl() .. ENDPOINTS.CHECK_UNPROCESSED, body)
        if not err and result and (result.photo_ids or result.uuids) then
            local needingPhotoIds = result.photo_ids or result.uuids
            local needingSet = {}
            for _, pid in ipairs(needingPhotoIds) do needingSet[pid] = true end
            
            for _, pid in ipairs(allIds) do
                if needingSet[pid] then
                    table.insert(photoToProcessStack, idToPhotoMap[pid])
                else
                    stats.processed = stats.processed + 1
                    stats.success = stats.success + 1
                end
            end
        else
            log:warn("Pre-flight check failed, falling back to full process. Error: " .. tostring(err))
            for _, photo in ipairs(selectedPhotos) do
                table.insert(photoToProcessStack, photo)
            end
        end
    else
        for _, photo in ipairs(selectedPhotos) do
            table.insert(photoToProcessStack, photo)
        end
    end

    progressScope:setCaption(LOC("$$$/StyleAI/AnalyzeAndIndex/ProcessingPhotos=Processing ^1 photos with ^2...",
        #photoToProcessStack, options.model or "AI"))
    progressScope:setPortionComplete(stats.processed, numPhotos)"""

if target in content:
    content = content.replace(target, replacement)
    with open('APISearchIndex.lua', 'w') as f:
        f.write(content)
    print("Preflight check added successfully")
else:
    print("Target block not found")

