local TrainingPreflight = {}

function TrainingPreflight.uniqueIds(photoIds)
    local uniqueIds = {}
    local seenIds = {}
    for _, rawPhotoId in ipairs(photoIds or {}) do
        local photoId = tostring(rawPhotoId or "")
        if photoId ~= "" and not seenIds[photoId] then
            seenIds[photoId] = true
            table.insert(uniqueIds, photoId)
        end
    end
    return uniqueIds
end

function TrainingPreflight.sortedUniqueIds(photoIds)
    local uniqueIds = TrainingPreflight.uniqueIds(photoIds)
    table.sort(uniqueIds)
    return uniqueIds
end

function TrainingPreflight.fingerprintPayload(photoIds, scope, forceRetrain)
    return {
        schema = "training_operation_v1",
        kind = "training",
        photo_ids = TrainingPreflight.sortedUniqueIds(photoIds),
        scope = tostring(scope or "selected"),
        force_retrain = forceRetrain == true,
    }
end

function TrainingPreflight.run(photoIds, forceRetrain, progressScope, requestPage, yieldPage)
    local uniqueIds = TrainingPreflight.uniqueIds(photoIds)
    local existingSet = {}
    local neededSet = {}
    local chunkSize = 1000

    for chunkStart = 1, #uniqueIds, chunkSize do
        if progressScope and progressScope:isCanceled() then
            return false, "Training preflight canceled"
        end
        local chunk = {}
        local chunkEnd = math.min(#uniqueIds, chunkStart + chunkSize - 1)
        for index = chunkStart, chunkEnd do table.insert(chunk, uniqueIds[index]) end

        local response, err = requestPage(chunk, forceRetrain == true)
        if not response then return false, err or "Unknown error" end
        if type(response.needed_photo_ids) ~= "table" then
            return false, response.error or "Unexpected training preflight response"
        end
        for _, photoId in ipairs(response.existing_photo_ids or {}) do
            existingSet[tostring(photoId)] = true
        end
        for _, photoId in ipairs(response.needed_photo_ids) do
            neededSet[tostring(photoId)] = true
        end
        if yieldPage then yieldPage() end
    end

    local existingIds = {}
    local neededIds = {}
    for _, photoId in ipairs(uniqueIds) do
        if existingSet[photoId] then table.insert(existingIds, photoId) end
        if neededSet[photoId] then table.insert(neededIds, photoId) end
    end
    return true, {
        existing_photo_ids = existingIds,
        needed_photo_ids = neededIds,
        force_retrain = forceRetrain == true,
    }
end

return TrainingPreflight
