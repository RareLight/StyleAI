-- StyleAI Server API Wrapper
-- Provides functions to interact with the Python-based search index server.

SearchIndexAPI = {}
local WorkCoordinator = require("WorkCoordinator")

local function getBaseUrl()
    -- StyleAI is a catalog-local service.  Do not permit a remote backend:
    -- image pixels, catalog metadata, and training data must stay on this machine.
    return "http://127.0.0.1:19819"
end

function SearchIndexAPI.isLocalBackend()
    return true
end

local ENDPOINTS = {
    INDEX = "/index",
    INDEX_BY_REFERENCE = "/index_by_reference",
    INDEX_BASE64 = "/index_base64",
    INDEX_BASE64_BATCH = "/index_base64_batch",
    METADATA_GENERATE = "/metadata/generate",
    STATS = "/db/stats",
    MODELS = "/models",
    GET_IDS = "/get/ids",
    REMOVE = "/remove",
    REMOVE_METADATA = "/remove/metadata",
    PING = "/ping",
    VERSION = "/version",
    VERSION_CHECK = "/version/check",
    SHUTDOWN = "/shutdown",
    UNLOAD = "/unload",
    START_CLIP_DOWNLOAD = "/clip/download/start",
    STATUS_CLIP_DOWNLOAD = "/clip/download/status",
    CLIP_STATUS = "/clip/status",
    CANCEL_ALL_TASKS = "/cancel_all_tasks",
    CLEAR_CANCEL_TASKS = "/clear_cancel_tasks",
    INDEX_QUEUE_STATUS = "/index_queue/status",
    OPERATIONS = "/operations",
    CHECK_UNPROCESSED = "/index/check-unprocessed",
    DB_BACKUP = "/db/backup",
    DB_RESTORE = "/db/restore",
    DB_PRUNE = "/db/prune",
    SYNC_CLEANUP = "/sync/cleanup",
    TRAINING_ADD = "/training/add",
    TRAINING_ADD_BATCH = "/training/add-batch",
    TRAINING_LIST = "/training/list",
    TRAINING_COUNT = "/training/count",
    TRAINING_DELETE = "/training", -- DELETE /training/<photo_id>
    TRAINING_CLEAR = "/training",  -- DELETE /training (examples and derived policies)
    TRAINING_CLEAR_ALL = "/training/all",  -- Backward-compatible clear-all alias
    TRAINING_STATS = "/training/stats",
    STYLE_EDIT = "/style_edit",
    STYLE_EDIT_APPLICATION_EVENTS = "/style_edit/events/application",
    STYLE_EDIT_RECONCILE_EVENTS = "/style_edit/events/reconcile",
    STYLE_EDIT_OUTCOMES = "/style_edit/events/outcomes",
    STYLE_LIST = "/styles",
    STYLE_DISCOVER = "/styles/discover",
    STYLE_DISCOVER_STATUS = "/styles/discover/status",
    STYLE_UPGRADES_RECOMMENDATIONS = "/styles/upgrades/recommendations",
    STYLE_UPGRADES_FEEDBACK = "/styles/upgrades/feedback",
    STYLE_RESET_ALL = "/styles/reset-all",
    LOGS = "/logs",
    LOGS_RAW = "/logs/raw",
    INITIALIZE = "/initialize",
    RESTART = "/restart",
    HEALTH = "/health",
    DEBUG_CAPTURES = "/debug/captures",
}

local EXPORT_SETTINGS = {
    LR_export_destinationType = 'specificFolder',
    LR_export_useSubfolder = false,
    LR_format = 'JPEG',
    LR_jpeg_quality = 85,
    LR_size_doConstrain = true,
    LR_size_maxWidth = 1024,
    LR_size_maxHeight = 1024,
    LR_size_resizeType = 'dimensions',
    LR_size_units = 'pixels',
    LR_size_resolution = 72,
    LR_size_resolutionUnits = 'inch',
    LR_outputSharpeningOn = false,
    LR_minimizeEmbeddedMetadata = true,
    LR_embeddedMetadataOption = "none",
    LR_removeLocationMetadata = true,
    LR_collisionHandling = 'rename',
    LR_includeVideoFiles = false,
}

local _exportSequence = 0

local function makeOwnedExportDirectory()
    _exportSequence = _exportSequence + 1
    local tempRoot = LrPathUtils.getStandardFilePath('temp')
    local token = string.format(
        "styleai-export-%d-%d",
        math.floor(LrDate.currentTime() * 1000),
        _exportSequence
    )
    local directory = LrPathUtils.child(tempRoot, token)
    local created = LrFileUtils.createAllDirectories(directory)
    if created == false then
        log:error("Could not create owned export directory: " .. tostring(directory))
        return nil
    end
    return directory
end


-- Forward declarations for private helper functions
local _request
local _requestMultipart

-- Returns a string safe for logging; never passes a table to tostring (avoids "table: 0x...").
local function httpStatusForLog(status, hdrs)
    if type(status) == "number" then
        return tostring(status)
    end
    if type(hdrs) == "number" then
        return tostring(hdrs)
    end
    if type(hdrs) == "table" then
        local s = hdrs.status or hdrs.statusCode
        if type(s) == "number" then
            return tostring(s)
        end
        if type(s) == "string" then
            return s
        end
    end
    return "unknown"
end

-- Catalog DB migrations: one-time backend operations scoped to this catalog.
-- Each entry: { id = "unique_id", run = function(progressScope) return ok, err [, userMessage] end }. progressScope is optional (nil for migrations that don't need it). Optional userMessage is shown via LrDialogs when present.
local CATALOG_DB_MIGRATIONS = {
    -- Add future migrations here, e.g. { id = "some_breaking_change_v1", run = function(progressScope) ... return ok, err [, userMessage] end },
    -- Add future migrations here, e.g. { id = "some_breaking_change_v1", run = function(progressScope) ... return ok, err [, userMessage] end },
}

local MIGRATION_IN_PROGRESS_PREFIX = "in_progress"
-- A live migration task re-writes its marker every MIGRATION_HEARTBEAT_INTERVAL_SECONDS seconds.
-- Anything older than STALE_IN_PROGRESS_SECONDS is assumed orphaned (crashed/killed task).
-- Keep the stale threshold several multiples of the heartbeat so a brief scheduler hiccup
-- doesn't cause a parallel migration to start.
local MIGRATION_HEARTBEAT_INTERVAL_SECONDS = 30
local STALE_IN_PROGRESS_SECONDS = 120
-- LrC caches this module per plugin-session; any `in_progress` marker whose timestamp predates
-- SESSION_START_TIME was written by a prior process and its owning task no longer exists.
local SESSION_START_TIME = LrDate.currentTime()
-- In-session guard: short-circuits re-entry within the same plugin session, regardless of
-- what's persisted in the plugin property. Survives nothing; exists only to defend against
-- logic bugs where the property check doesn't fire fast enough.
local _migrationTaskRunning = false

local function formatInProgressMarker()
    return MIGRATION_IN_PROGRESS_PREFIX .. ":" .. tostring(math.floor(LrDate.currentTime()))
end

-- Rewrites the in_progress:<ts> marker with a current timestamp so the stale-detection check
-- doesn't evict a long-running live migration. No-op if the marker is no longer present
-- (e.g., the migration task just finished and stripped it).
local function updateInProgressHeartbeat(catalog)
    catalog:withPrivateWriteAccessDo(function()
        local cur = catalog:getPropertyForPlugin(_PLUGIN, "catalogDbMigrations") or ""
        local fresh = formatInProgressMarker()
        local updated, n = cur:gsub(MIGRATION_IN_PROGRESS_PREFIX .. ":%d+", fresh, 1)
        if n > 0 and updated ~= cur then
            catalog:setPropertyForPlugin(_PLUGIN, "catalogDbMigrations", updated)
        end
    end, { timeout = 15 })
end

local function shouldUseGlobalPhotoId()
    return prefs and prefs.useGlobalPhotoId ~= false
end

local function parseCompletedMigrations(raw)
    local completed = {}
    local inProgress = false
    local inProgressSince = nil
    if raw and raw ~= "" then
        for part in string.gmatch(raw, "([^,]+)") do
            local p = part:match("^%s*(.-)%s*$") or part
            if p == MIGRATION_IN_PROGRESS_PREFIX then
                -- Legacy unversioned marker (pre-timestamp plugin version): treat as stale.
                inProgress = true
                inProgressSince = inProgressSince or 0
            else
                local ts = p:match("^" .. MIGRATION_IN_PROGRESS_PREFIX .. ":(%d+)$")
                if ts then
                    inProgress = true
                    inProgressSince = tonumber(ts) or 0
                else
                    completed[p] = true
                end
            end
        end
    end
    return completed, inProgress, inProgressSince
end

local function isInProgressStale(inProgressSince)
    if not inProgressSince then
        return false
    end
    -- 0 is reserved for legacy unversioned markers — always stale.
    if inProgressSince == 0 then
        return true
    end
    if inProgressSince < SESSION_START_TIME then
        return true
    end
    if (LrDate.currentTime() - inProgressSince) > STALE_IN_PROGRESS_SECONDS then
        return true
    end
    return false
end

local function stripInProgressMarkers(raw)
    if not raw or raw == "" then return "" end
    local cleaned = raw:gsub(MIGRATION_IN_PROGRESS_PREFIX .. ":%d+", "")
        :gsub(MIGRATION_IN_PROGRESS_PREFIX, "")
        :gsub(",+", ",")
        :gsub("^,", "")
        :gsub(",$", "")
        :gsub("^%s*(.-)%s*$", "%1")
    return cleaned
end

--- Ensures all registered catalog DB migrations have been run for the active catalog. Runs pending ones in background; uses catalog plugin property catalogDbMigrations so each migration runs once per catalog.
local function ensureDbMigrationsDone()
    local _
    local catalog = LrApplication.activeCatalog()
    if not catalog then
        return
    end
    if _migrationTaskRunning then
        return
    end
    local raw = catalog:getPropertyForPlugin(_PLUGIN, "catalogDbMigrations") or ""
    local completed, inProgress, inProgressSince = parseCompletedMigrations(raw)

    -- Recover from crashed/killed prior migrations that left the marker poisoned.
    if inProgress and isInProgressStale(inProgressSince) then
        local age = inProgressSince and (LrDate.currentTime() - inProgressSince) or -1
        log:warn("Clearing stale catalogDbMigrations in_progress marker (age=" ..
            tostring(math.floor(age)) .. "s, pre_session=" ..
            tostring(inProgressSince and inProgressSince < SESSION_START_TIME) .. ")")
        catalog:withPrivateWriteAccessDo(function()
            local cur = catalog:getPropertyForPlugin(_PLUGIN, "catalogDbMigrations") or ""
            catalog:setPropertyForPlugin(_PLUGIN, "catalogDbMigrations", stripInProgressMarkers(cur))
        end, { timeout = 15 })
        raw = catalog:getPropertyForPlugin(_PLUGIN, "catalogDbMigrations") or ""
        completed, inProgress, _ = parseCompletedMigrations(raw)
    end

    if inProgress then
        return
    end
    local pending = {}
    for _, m in ipairs(CATALOG_DB_MIGRATIONS) do
        if not completed[m.id] then
            pending[#pending + 1] = m
        end
    end
    if #pending == 0 then
        return
    end
    catalog:withPrivateWriteAccessDo(function()
        local marker = formatInProgressMarker()
        local newRaw = (raw == "" or raw:match("%S") == nil) and marker or (raw .. "," .. marker)
        catalog:setPropertyForPlugin(_PLUGIN, "catalogDbMigrations", newRaw)
    end, { timeout = 15 })
    _migrationTaskRunning = true
    local heartbeatStop = false

    -- Heartbeat task: periodically refreshes the in_progress:<ts> timestamp so a long-running
    -- migration isn't mistaken for a crashed one. Sleeps in 1-second chunks so it can exit
    -- quickly once the main task finishes (avoids a race where it re-writes the marker after
    -- cleanup has already stripped it).
    LrTasks.startAsyncTask(function()
        while not heartbeatStop do
            for _ = 1, MIGRATION_HEARTBEAT_INTERVAL_SECONDS do
                if heartbeatStop then return end
                LrTasks.sleep(1)
            end
            if heartbeatStop then return end
            updateInProgressHeartbeat(catalog)
        end
    end)

    LrTasks.startAsyncTask(function()
        local runOk, runErr = LrTasks.pcall(function()
            local done = raw
            for _, m in ipairs(pending) do
                local progressScope
                local ok, err, userMessage
                if type(m.run) == "function" then
                    local status, a, b, c
                    if type(LrTasks) == "table" and type(LrTasks.pcall) == "function" then
                        status, a, b, c = LrTasks.pcall(function() return m.run(progressScope) end)
                    else
                        status, a, b, c = pcall(function() return m.run(progressScope) end)
                    end
                    if status then
                        ok, err, userMessage = a, b, c
                    else
                        ok, err, userMessage = false, tostring(a), nil
                    end
                end
                if ok then
                    done = (done == "" or done:match("%S") == nil) and m.id or (done .. "," .. m.id)
                    catalog:withPrivateWriteAccessDo(function()
                        catalog:setPropertyForPlugin(_PLUGIN, "catalogDbMigrations", done)
                    end, { timeout = 15 })
                    log:info("Catalog DB migration completed: " .. tostring(m.id))
                else
                    log:warn("Catalog DB migration failed: " .. tostring(m.id) .. " - " .. tostring(err))
                end
                if progressScope then
                    progressScope:done()
                end
            end
            catalog:withPrivateWriteAccessDo(function()
                local current = catalog:getPropertyForPlugin(_PLUGIN, "catalogDbMigrations") or ""
                catalog:setPropertyForPlugin(_PLUGIN, "catalogDbMigrations", stripInProgressMarkers(current))
            end, { timeout = 15 })
        end)
        heartbeatStop = true
        _migrationTaskRunning = false
        if not runOk then
            log:error("Catalog DB migration task crashed: " .. tostring(runErr))
        end
    end)
end

local function allCatalogDbMigrationsCompleted(completed)
    for _, m in ipairs(CATALOG_DB_MIGRATIONS) do
        if not completed[m.id] then
            return false
        end
    end
    return true
end

--- Waits for catalog-scoped DB migrations (tracked by `catalogDbMigrations`) to complete.
--- This prevents indexing from racing a future catalog-local schema operation.
--- @param timeoutSeconds number
--- @return boolean success (all migrations completed)
local function waitForCatalogDbMigrationsDone(timeoutSeconds)
    local catalog = LrApplication.activeCatalog()
    if not catalog then
        return false
    end

    timeoutSeconds = tonumber(timeoutSeconds) or 600
    local start = LrDate.currentTime()
    local sawInProgress = false

    while (LrDate.currentTime() - start) < timeoutSeconds do
        local raw = catalog:getPropertyForPlugin(_PLUGIN, "catalogDbMigrations") or ""
        local completed, inProgress, inProgressSince = parseCompletedMigrations(raw)

        if allCatalogDbMigrationsCompleted(completed) then
            return true
        end

        if inProgress then
            -- A stale marker means no task is actually running — don't block waiting for a ghost.
            if isInProgressStale(inProgressSince) then
                log:warn("waitForCatalogDbMigrationsDone: stale in_progress marker detected, aborting wait")
                return false
            end
            sawInProgress = true
        elseif sawInProgress then
            -- Previously observed in_progress, now gone but not all completed → migration failed.
            return false
        end

        LrTasks.sleep(0.5)
    end

    return false
end


local function getPhotoIdForPhoto(photo, options)
    if not photo then
        return nil, "Photo is nil"
    end
    
    local idOptions = {
        windowBytes = Util.getDefaultPartialHashWindowBytes(),
        skipCacheWrite = true
    }
    if options and options.forceRecompute then
        idOptions.forceRecompute = true
    end

    if shouldUseGlobalPhotoId() then
        return Util.getGlobalPhotoIdForPhoto(photo, idOptions)
    end
    local uuid = photo:getRawMetadata("uuid")
    if not uuid or uuid == "" then
        return nil, "Photo UUID is missing"
    end
    return uuid, nil, nil
end

function SearchIndexAPI.getPhotoIdForPhoto(photo)
    return getPhotoIdForPhoto(photo)
end

function SearchIndexAPI.findPhotoByPhotoId(photoId)
    if not photoId or photoId == "" then
        return nil
    end

    local catalog = LrApplication.activeCatalog()
    if not shouldUseGlobalPhotoId() then
        return catalog:findPhotoByUuid(photoId)
    end

    for _, photo in ipairs(catalog:getAllPhotos()) do
        local cachedId = photo:getPropertyForPlugin(_PLUGIN, "globalPhotoId")
        if cachedId == photoId then
            return photo
        end
    end

    for _, photo in ipairs(catalog:getAllPhotos()) do
        local candidateId = getPhotoIdForPhoto(photo)
        if candidateId == photoId then
            return photo
        end
    end

    return nil
end

local function scanCatalogForGlobalPhotoIds(catalog, allPhotos, idSet, targetCount, progressScope, captionPrefix)
    local photoById = {}
    local foundCount = 0
    local chunkSize = 1000

    for chunkStart = 1, #allPhotos, chunkSize do
        if progressScope and progressScope:isCanceled() then
            break
        end
        local chunkEnd = math.min(chunkStart + chunkSize - 1, #allPhotos)
        local chunkPhotos = {}
        for i = chunkStart, chunkEnd do
            table.insert(chunkPhotos, allPhotos[i])
        end

        if progressScope then
            progressScope:setPortionComplete(chunkEnd, #allPhotos)
            progressScope:setCaption(string.format("%s (%d/%d found)...", captionPrefix or "Scanning catalog", foundCount, targetCount))
        end
        LrTasks.yield()
        LrTasks.sleep(0.01)

        local batchProps = nil
        if catalog.batchGetPropertyForPlugin then
            local success, res = LrTasks.pcall(function()
                return catalog:batchGetPropertyForPlugin(_PLUGIN, "globalPhotoId", chunkPhotos)
            end)
            if success and type(res) == "table" then
                batchProps = res
            end
        end

        for _, photo in ipairs(chunkPhotos) do
            local matched = false
            local lrUuid = photo:getRawMetadata("uuid")
            local cachedId
            if batchProps then
                cachedId = batchProps[photo]
            else
                cachedId = photo:getPropertyForPlugin(_PLUGIN, "globalPhotoId")
            end

            if (lrUuid and idSet[lrUuid] and not photoById[lrUuid]) or (cachedId and idSet[cachedId] and not photoById[cachedId]) then
                if lrUuid and lrUuid ~= "" then photoById[lrUuid] = photo end
                if cachedId and cachedId ~= "" then photoById[cachedId] = photo end
                foundCount = foundCount + 1
                matched = true
            end
        end

        if foundCount >= targetCount then
            break
        end
    end

    return photoById
end

function SearchIndexAPI.findPhotosByPhotoIds(photoIds, progressScope)
    local photos = {}
    if type(photoIds) ~= "table" or #photoIds == 0 then
        return photos
    end

    local catalog = LrApplication.activeCatalog()
    local idList = {}
    local idSet = {}
    local photoById = {}
    local missingIds = {}

    for _, pidInfo in ipairs(photoIds) do
        local pidStr = type(pidInfo) == "table" and (pidInfo.globalPhotoId or pidInfo.lr_uuid) or pidInfo
        local lrUuid = type(pidInfo) == "table" and pidInfo.lr_uuid or nil
        if pidStr and pidStr ~= "" then
            table.insert(idList, { pidStr = pidStr, lrUuid = lrUuid })
            if lrUuid and lrUuid ~= "" then
                local photo = catalog:findPhotoByUuid(lrUuid)
                if photo then
                    photoById[pidStr] = photo
                    photoById[lrUuid] = photo
                end
            end
            if not photoById[pidStr] then
                local photo = catalog:findPhotoByUuid(pidStr)
                if photo then
                    photoById[pidStr] = photo
                    if lrUuid and lrUuid ~= "" then
                        photoById[lrUuid] = photo
                    end
                else
                    table.insert(missingIds, pidStr)
                    idSet[pidStr] = true
                    if lrUuid and lrUuid ~= "" then
                        idSet[lrUuid] = true
                    end
                end
            end
        end
    end

    if #missingIds > 0 then
        local startedAt = LrDate.currentTime()
        local allPhotos = catalog:getAllPhotos()
        local scannedMap = scanCatalogForGlobalPhotoIds(catalog, allPhotos, idSet, #missingIds, progressScope, "Scanning catalog")
        for k, v in pairs(scannedMap) do
            photoById[k] = v
        end
    end

    for _, item in ipairs(idList) do
        local photo = photoById[item.pidStr] or (item.lrUuid and photoById[item.lrUuid])
        if photo then
            table.insert(photos, photo)
        else
            log:warn("findPhotosByPhotoIds: Photo ID " .. tostring(item.pidStr) .. " not found in catalog.")
        end
    end

    return photos
end

function SearchIndexAPI.findPhotosByPhotoIdsMap(photoIds, progressScope)
    local photoMap = {}
    if type(photoIds) ~= "table" or #photoIds == 0 then
        return photoMap
    end

    local catalog = LrApplication.activeCatalog()
    local missingIds = {}
    local idSet = {}

    for _, pidInfo in ipairs(photoIds) do
        local pidStr = type(pidInfo) == "table" and (pidInfo.globalPhotoId or pidInfo.lr_uuid) or pidInfo
        local lrUuid = type(pidInfo) == "table" and pidInfo.lr_uuid or nil

        if pidStr and pidStr ~= "" then
            local photo = nil
            if lrUuid and lrUuid ~= "" then
                photo = catalog:findPhotoByUuid(lrUuid)
            end
            if not photo then
                photo = catalog:findPhotoByUuid(pidStr)
            end
            if photo then
                photoMap[pidStr] = photo
                if lrUuid and lrUuid ~= "" then
                    photoMap[lrUuid] = photo
                end
            else
                table.insert(missingIds, pidStr)
                idSet[pidStr] = true
                if lrUuid and lrUuid ~= "" then
                    idSet[lrUuid] = true
                end
            end
        end
    end

    if #missingIds > 0 then
        local startedAt = LrDate.currentTime()
        local allPhotos = catalog:getAllPhotos()
        local scannedMap = scanCatalogForGlobalPhotoIds(catalog, allPhotos, idSet, #missingIds, progressScope, "Scanning catalog")
        for k, v in pairs(scannedMap) do
            photoMap[k] = v
        end
    end

    return photoMap
end

---
-- Performs a single-pass catalog lookup across multiple style candidate lists simultaneously.
-- Avoids repeated catalog:getAllPhotos() scans when creating collections for multiple styles.
-- @param styleEntries table List of { fullName = "...", photoIds = { ... } }
-- @param progressScope LrProgressScope Optional progress scope
-- @return table List of { fullName = "...", photos = { LrPhoto, ... } }
---
function SearchIndexAPI.findPhotosBatchedByStyleMap(styleEntries, progressScope)
    local results = {}
    if type(styleEntries) ~= "table" or #styleEntries == 0 then
        return results
    end

    local catalog = LrApplication.activeCatalog()
    local idSet = {}
    local missingGlobalIds = {}
    local totalTargetCount = 0
    local photoById = {}

    for _, entry in ipairs(styleEntries) do
        for _, pidInfo in ipairs(entry.photoIds or {}) do
            local pidStr = type(pidInfo) == "table" and (pidInfo.globalPhotoId or pidInfo.lr_uuid) or pidInfo
            local lrUuid = type(pidInfo) == "table" and pidInfo.lr_uuid or nil

            if pidStr and pidStr ~= "" and not idSet[pidStr] then
                idSet[pidStr] = true
                local photo = nil
                if lrUuid and lrUuid ~= "" then
                    photo = catalog:findPhotoByUuid(lrUuid)
                end
                if not photo then
                    photo = catalog:findPhotoByUuid(pidStr)
                end
                if photo then
                    photoById[pidStr] = photo
                    if lrUuid and lrUuid ~= "" then
                        photoById[lrUuid] = photo
                    end
                else
                    table.insert(missingGlobalIds, pidStr)
                    totalTargetCount = totalTargetCount + 1
                    if lrUuid and lrUuid ~= "" then
                        idSet[lrUuid] = true
                    end
                end
            end
        end
    end

    if totalTargetCount > 0 then
        local startedAt = LrDate.currentTime()
        local allPhotos = catalog:getAllPhotos()
        local allPhotosElapsed = math.floor((LrDate.currentTime() - startedAt) * 1000)
        log:trace("findPhotosBatchedByStyleMap: catalog:getAllPhotos() returned " .. tostring(#allPhotos) ..
            " photos in " .. tostring(allPhotosElapsed) .. "ms")

        local scanSet = {}
        for _, pid in ipairs(missingGlobalIds) do scanSet[pid] = true end
        for k, _ in pairs(idSet) do scanSet[k] = true end
        
        local scannedMap = scanCatalogForGlobalPhotoIds(catalog, allPhotos, scanSet, totalTargetCount, progressScope, "Scanning catalog across all styles")
        for k, v in pairs(scannedMap) do
            photoById[k] = v
        end
    end

    for _, entry in ipairs(styleEntries) do
        local photosForStyle = {}
        for _, pidInfo in ipairs(entry.photoIds or {}) do
            local pidStr = type(pidInfo) == "table" and (pidInfo.globalPhotoId or pidInfo.lr_uuid) or pidInfo
            local lrUuid = type(pidInfo) == "table" and pidInfo.lr_uuid or nil
            local photo = photoById[pidStr] or (lrUuid and photoById[lrUuid])
            if photo then
                table.insert(photosForStyle, photo)
            else
                log:warn("findPhotosBatchedByStyleMap: Photo ID " .. tostring(pidStr) .. " not found for style " .. tostring(entry.fullName))
            end
        end
        table.insert(results, {
            styleKey = entry.styleKey,
            fullName = entry.fullName,
            photos = photosForStyle
        })
    end

    return results
end

---
-- Exports a photo to a temporary location for processing.
-- @param photo The Lightroom photo object to export.
-- @return string|nil The path to the exported JPEG file, or nil on failure.
--
function SearchIndexAPI.exportPhotoForIndexing(photo, overrideSettings)
    if photo == nil then
        log:error("exportPhotoForIndexing: photo is nil. Probably it got deleted in the meantime.")
        return nil
    end

    local renderLease, leaseError = WorkCoordinator.acquire("render")
    if not renderLease then
        log:error("Could not acquire Lightroom render lane: " .. tostring(leaseError))
        return nil
    end

    local tempDir = makeOwnedExportDirectory()
    if not tempDir then
        WorkCoordinator.release(renderLease)
        return nil
    end
    local photoName = LrPathUtils.leafName(photo:getFormattedMetadata('fileName'))
    
    local settings = {}
    for k, v in pairs(EXPORT_SETTINGS) do settings[k] = v end
    settings.LR_export_destinationPathPrefix = tempDir
    
    if type(overrideSettings) == "table" then
        for k, v in pairs(overrideSettings) do settings[k] = v end
    end

    local exportSession = LrExportSession({
        photosToExport = { photo },
        exportSettings = settings
    })

    local renditions = {}
    for _, rendition in exportSession:renditions() do
        renditions[#renditions + 1] = rendition
    end

    if #renditions > 0 then
        local rendition = renditions[1]
        local success, path = rendition:waitForRender()
        log:trace("Export completed for photo: " ..
            photoName .. " Success: " .. tostring(success) .. " Path: " .. tostring(path))
        if success then -- Export successful
            WorkCoordinator.release(renderLease)
            return path
        else
            -- Error during export
            log:error("Failed to export photo for indexing. " .. (path or 'unknown error'))
            LrTasks.pcall(function() LrFileUtils.delete(tempDir) end)
            WorkCoordinator.release(renderLease)
            return nil
        end
    end

    LrTasks.pcall(function() LrFileUtils.delete(tempDir) end)
    WorkCoordinator.release(renderLease)
    return nil
end

function SearchIndexAPI.cleanupExportedPhoto(path)
    if not path or path == "" then return end
    local parent = LrPathUtils.parent(path)
    LrTasks.pcall(function()
        if LrFileUtils.exists(path) then
            LrFileUtils.delete(path)
        end
    end)
    if parent and string.match(LrPathUtils.leafName(parent) or "", "^styleai%-export%-%d+%-%d+$") then
        LrTasks.pcall(function()
            if LrFileUtils.exists(parent) then
                LrFileUtils.delete(parent)
            end
        end)
    end
end

function SearchIndexAPI.exportPhotosForIndexing(photos)
    if not photos or #photos == 0 then return {} end

    local tempDir = LrPathUtils.getStandardFilePath('temp')

    EXPORT_SETTINGS.LR_export_destinationPathPrefix = tempDir

    local exportSession = LrExportSession({
        photosToExport = photos,
        exportSettings = EXPORT_SETTINGS
    })

    local photoPaths = {}
    local photoIndex = 1
    for _, rendition in exportSession:renditions() do
        local success, path = rendition:waitForRender()
        local photo = photos[photoIndex]
        if photo ~= nil then
            local photoName = LrPathUtils.leafName(photo:getFormattedMetadata('fileName'))
            log:trace("Export completed for photo: " ..
                photoName .. " Success: " .. tostring(success) .. " Path: " .. tostring(path))
            if success then
                photoPaths[photo] = path
            else
                log:error("Failed to export photo for indexing. " .. (path or 'unknown error'))
                photoPaths[photo] = nil
            end
        else
            log:error("Photo is nil in exportPhotosForIndexing, probably it got deleted in the meantime.")
        end
        photoIndex = photoIndex + 1
    end
    return photoPaths
end

---
-- Gets a JPEG thumbnail from Lightroom's preview system (must be called from LrTasks async context).
-- Uses photo:requestJpegThumbnail(width, height, callback) and waits for the callback with a timeout.
-- @param photo LrPhoto
-- @param minWidth number Minimum width (long edge); nil = smallest preview.
-- @param minHeight number Optional; if minWidth is set, controls height of returned pixels.
-- @param requestState table|nil Optional state/config with timeoutSeconds.
-- @return string|nil JPEG data string, or nil on failure.
-- @return string|nil Error message when JPEG is nil.
--
function SearchIndexAPI.getJpegThumbnailForPhoto(photo, minWidth, minHeight, requestState)
    if not photo then
        return nil, "Photo is nil"
    end
    local result = nil
    local errResult = nil
    local done = false
    local callbackCount = 0
    local timeoutSeconds = tonumber(requestState and requestState.timeoutSeconds) or
        tonumber(prefs and prefs.previewThumbnailTimeoutSeconds) or 12
    local deadline = LrDate.currentTime() + timeoutSeconds

    local requestRef = nil
    local callback = function(jpegData, err)
        if done then return end
        callbackCount = callbackCount + 1

        -- Adobe reports that the callback may fire more than once. Prefer the
        -- first non-empty JPEG payload and otherwise keep waiting until timeout.
        if jpegData and type(jpegData) == "string" and #jpegData > 0 then
            result = jpegData
            jpegData = nil -- release reference as per SDK guidelines
            errResult = nil
            done = true
            return
        end

        if err and err ~= "" then
            errResult = err
        elseif not errResult then
            errResult = "No thumbnail data"
        end
    end

    local requestObj = photo:requestJpegThumbnail(minWidth, minHeight, callback)
    requestRef = requestObj
    if not requestObj then
        return nil, "requestJpegThumbnail failed to start"
    end

    while not done and LrDate.currentTime() < deadline do
        LrTasks.yield()
        LrTasks.sleep(0.05)
    end

    -- Explicitly instruct Adobe's internal engine to release the preview memory
    -- Without this, processing 7200 photos will rapidly exhaust memory
    if requestRef then
        if type(requestRef.cancel) == "function" then
            requestRef:cancel()
        end
        requestRef = nil
    end

    if not done then
        return nil,
            string.format("Thumbnail request timed out after %.1fs (callbacks=%d)", timeoutSeconds, callbackCount)
    end
    if result and type(result) == "string" and #result > 0 then
        return result, nil
    end
    return nil, errResult or "No thumbnail data"
end

---
-- Analyzes and indexes a single photo using base64-encoded JPEG (e.g. from requestJpegThumbnail).
-- Uses the /index_base64 endpoint; same options as analyzeAndIndexPhoto.
-- @param photoId string
-- @param jpegData string Raw JPEG bytes.
-- @param filename string Display filename for logging.
-- @param options table Same as analyzeAndIndexPhoto.
-- @return boolean success, table|string response or error.
--
function SearchIndexAPI.analyzeAndIndexPhotoBase64(photoId, jpegData, filename, options)
    if not jpegData or type(jpegData) ~= "string" or #jpegData == 0 then
        log:error("analyzeAndIndexPhotoBase64: no JPEG data")
        return false, "No image data provided"
    end
    if not photoId or photoId == "" then
        log:error("Photo ID is missing")
        return false, "No photo ID provided"
    end

    options = options or {}
    local base64Image = LrStringUtils.encodeBase64(jpegData)
    local url = getBaseUrl() .. ENDPOINTS.INDEX_BASE64

    local body = {
        image = base64Image,
        photo_id = photoId,
        filename = filename or "photo.jpg",
        tasks = options.tasks or {},
        provider = options.provider,
        model = options.model,
        language = options.language or (prefs and prefs.generateLanguage) or "English",
        temperature = tostring(options.temperature or (prefs and prefs.temperature) or 0.2),
        replace_ss = tostring(options.replace_ss or false),
        generate_keywords = tostring(options.generate_keywords or false),
        generate_caption = tostring(options.generate_caption or false),
        generate_title = tostring(options.generate_title or false),
        generate_alt_text = tostring(options.generate_alt_text or false),
        submit_gps = tostring(options.submit_gps or false),
        submit_keywords = tostring(options.submit_keywords or false),
        submit_folder_names = tostring(options.submit_folder_names or false),
        user_context = options.user_context,
        gps_coordinates = options.gps_coordinates and JSON:encode(options.gps_coordinates) or nil,
        existing_keywords = options.existing_keywords and JSON:encode(options.existing_keywords) or nil,
        folder_names = options.folder_names,
        prompt = options.prompt,
        keyword_categories = options.keyword_categories and JSON:encode(options.keyword_categories) or "[]",
        bilingual_keywords = tostring(options.bilingual_keywords or false),
        keyword_secondary_language = options.keyword_secondary_language or (prefs and prefs.keywordSecondaryLanguage) or
            "English",
        date_time = options.date_time,
        regenerate_metadata = (options.regenerate_metadata == true),
    }

    log:trace("Analyzing and indexing photo (base64): " .. tostring(filename) .. " id " .. photoId)

    local response, err = _request('POST', url, body, 720)

    if not response then
        log:error("Failed to analyze/index photo (base64): " .. tostring(err))
        return false, err or "Unknown error"
    end
    if response.status == "processed" then
        local success_count = response.success_count or 0
        if success_count > 0 then
            log:trace("Successfully processed photo (base64): " .. tostring(filename))
            return true, response
        else
            log:error("Photo processing failed (base64): " .. tostring(filename))
            return false, response.error or "Processing failed"
        end
    end
    log:error("Unexpected response status (base64): " .. tostring(response.status))
    return false, "Unexpected response status"
end

function SearchIndexAPI.enqueuePhotosBase64Batch(batch, globalOptions)
    local url = getBaseUrl() .. "/index_queue"
    local prefs = LrPrefs.prefsForPlugin()

    local bodyOptions = {
        regenerate_metadata = (globalOptions.regenerate_metadata == true),
        cache_images = globalOptions.cache_images == true
    }

    local bodyImages = {}
    for _, item in ipairs(batch) do
        local itemOptions = item.options or {}
        local encodedItemOptions = {
            submit_gps = tostring(itemOptions.submit_gps or false),
            submit_keywords = tostring(itemOptions.submit_keywords or false),
            submit_folder_names = tostring(itemOptions.submit_folder_names or false),
            gps_coordinates = itemOptions.gps_coordinates and JSON:encode(itemOptions.gps_coordinates) or nil,
            existing_keywords = itemOptions.existing_keywords and JSON:encode(itemOptions.existing_keywords) or nil,
            folder_names = itemOptions.folder_names,
            user_context = itemOptions.user_context,
            date_time = itemOptions.date_time,
            date_time_unix = itemOptions.date_time_unix,
            -- Preserve the catalog facts used by training, style discovery, and
            -- recommendations.  Dropping these here silently made queued
            -- indexing behave differently from the synchronous endpoint.
            raw_filepath = itemOptions.raw_filepath,
            camera_profile = itemOptions.camera_profile,
            camera_make = itemOptions.camera_make,
            camera_model = itemOptions.camera_model,
            focal_length = itemOptions.focal_length,
            lens = itemOptions.lens,
            iso = itemOptions.iso,
            aperture = itemOptions.aperture,
            shutter_speed = itemOptions.shutter_speed,
            rating = itemOptions.rating,
            pick_status = itemOptions.pick_status,
            is_edited = itemOptions.is_edited,
            is_hdr = itemOptions.is_hdr,
        }
        table.insert(bodyImages, {
            image = item.image,
            photo_id = item.photo_id,
            lr_uuid = item.lr_uuid,
            filename = item.filename or "photo.jpg",
            options = encodedItemOptions
        })
    end

    local body = {
        images = bodyImages,
        options = bodyOptions,
        job_id = globalOptions.operation_id,
    }

    local response, err = _request('POST', url, body, 15) -- Short timeout, just enqueueing

    if not response then
        log:error("Failed to enqueue photos batch: " .. tostring(err))
        return false, err or "Unknown error"
    end

    if response.status == "accepted" or response.status == "backpressure" then
        return true, response
    else
        log:error("Unexpected enqueue response status: " .. tostring(response.status))
        return false, response.error or "Enqueue failed"
    end
end

function SearchIndexAPI.getIndexQueueStatus()
    return _request('GET', getBaseUrl() .. ENDPOINTS.INDEX_QUEUE_STATUS, nil, 3)
end

function SearchIndexAPI.startOperation(kind, itemIds, details, requestFingerprint, priority, coalesce)
    local response, err = _request('POST', getBaseUrl() .. ENDPOINTS.OPERATIONS, {
        kind = kind,
        item_ids = itemIds or {},
        details = details or {},
        request_fingerprint = requestFingerprint,
        priority = priority or 0,
        coalesce = coalesce == true,
    }, 30)
    if not response or not response.job or not response.job.job_id then
        return false, err or "Backend did not create an operation job"
    end
    return true, response.job
end

function SearchIndexAPI.getOperation(jobId, includeItems)
    if not jobId or jobId == "" then return false, "Missing operation job ID" end
    local suffix = includeItems == false and "?include_items=false" or ""
    local response, err = _request('GET', getBaseUrl() .. ENDPOINTS.OPERATIONS .. "/" .. tostring(jobId) .. suffix, nil, 15)
    if not response or not response.job then
        return false, err or "Operation status unavailable"
    end
    return true, response.job
end

function SearchIndexAPI.cancelOperation(jobId)
    if not jobId or jobId == "" then return false, "Missing operation job ID" end
    local response, err = _request('POST', getBaseUrl() .. ENDPOINTS.OPERATIONS .. "/" .. tostring(jobId) .. "/cancel", {}, 15)
    if not response or not response.job then
        return false, err or "Operation cancellation failed"
    end
    return true, response.job
end

function SearchIndexAPI.updateOperationItems(jobId, items)
    if not jobId or jobId == "" then return false, "Missing operation job ID" end
    if not items or #items == 0 then return true, nil end
    local response, err = _request('POST', getBaseUrl() .. ENDPOINTS.OPERATIONS .. "/" .. tostring(jobId) .. "/items", {
        items = items,
    }, 30)
    if not response then return false, err or "Operation item update failed" end
    return true, response.job
end

function SearchIndexAPI.completeOperation(jobId)
    if not jobId or jobId == "" then return false, "Missing operation job ID" end
    local response, err = _request('POST', getBaseUrl() .. ENDPOINTS.OPERATIONS .. "/" .. tostring(jobId) .. "/complete", {}, 30)
    if not response or not response.job then
        return false, err or "Operation completion failed"
    end
    return true, response.job
end


---
-- Calls the /metadata/generate endpoint for a single photo.
-- Designed for Stage 2 of the decoupled pipeline. 
-- Sends either the base64 image (if skipped stage 1) or relies on server cache.
function SearchIndexAPI.generateMetadataSingle(photoId, base64Image, filename, options)
    if not photoId then return false, "No photoId provided" end

    options = options or {}
    local url = getBaseUrl() .. ENDPOINTS.METADATA_GENERATE

    local body = {
        image = base64Image, -- Can be nil if cached on server
        photo_id = photoId,
        filename = filename or "photo.jpg",
        tasks = options.tasks or {},
        provider = options.provider,
        model = options.model,
        language = options.language or (prefs and prefs.generateLanguage) or "English",
        temperature = tostring(options.temperature or (prefs and prefs.temperature) or 0.2),
        replace_ss = tostring(options.replace_ss or false),
        generate_keywords = tostring(options.generate_keywords or false),
        generate_caption = tostring(options.generate_caption or false),
        generate_title = tostring(options.generate_title or false),
        generate_alt_text = tostring(options.generate_alt_text or false),
        submit_gps = tostring(options.submit_gps or false),
        submit_keywords = tostring(options.submit_keywords or false),
        submit_folder_names = tostring(options.submit_folder_names or false),
        user_context = options.user_context,
        gps_coordinates = options.gps_coordinates and JSON:encode(options.gps_coordinates) or nil,
        existing_keywords = options.existing_keywords and JSON:encode(options.existing_keywords) or nil,
        folder_names = options.folder_names,
        prompt = options.prompt,
        keyword_categories = options.keyword_categories and JSON:encode(options.keyword_categories) or "[]",
        bilingual_keywords = tostring(options.bilingual_keywords or false),
        keyword_secondary_language = options.keyword_secondary_language or (prefs and prefs.keywordSecondaryLanguage) or "English",
        date_time = options.date_time,
        date_time_unix = options.date_time_unix,
        regenerate_metadata = tostring(options.regenerate_metadata ~= false),
    }

    log:trace("Generating metadata for single photo: " .. tostring(filename) .. " id " .. photoId)

    local response, err = _request('POST', url, body, 720)

    if not response then
        log:error("Failed to generate metadata single: " .. tostring(err))
        return false, err or "Unknown error"
    end
    if response.status == "processed" then
        local success_count = response.success_count or 0
        if success_count > 0 then
            log:trace("Successfully generated metadata for: " .. tostring(filename))
            return true, response
        else
            log:error("Metadata generation failed for: " .. tostring(filename))
            return false, response.error or "Processing failed"
        end
    end
    log:error("Unexpected response status for metadata generate: " .. tostring(response.status))
    return false, "Unexpected response status"
end

---
-- Calls the /metadata/generate_batch endpoint for a batch of photos.
-- Each item is inferred independently; batching controls transport and GPU scheduling only.
function SearchIndexAPI.generateMetadataBatch(items, options)
    if not items or type(items) ~= "table" or #items == 0 then
        return false, "No items provided for batch metadata generation"
    end

    options = options or {}
    local url = getBaseUrl() .. "/metadata/generate_batch"

    local bodyOptions = {
        tasks = options.tasks or {},
        provider = options.provider,
        model = options.model,
        language = options.language or (prefs and prefs.generateLanguage) or "English",
        temperature = tostring(options.temperature or (prefs and prefs.temperature) or 0.2),
        replace_ss = tostring(options.replace_ss or false),
        generate_keywords = tostring(options.generate_keywords or false),
        generate_caption = tostring(options.generate_caption or false),
        generate_title = tostring(options.generate_title or false),
        generate_alt_text = tostring(options.generate_alt_text or false),
        submit_gps = tostring(options.submit_gps or false),
        submit_keywords = tostring(options.submit_keywords or false),
        submit_folder_names = tostring(options.submit_folder_names or false),
        user_context = options.user_context,
        gps_coordinates = options.gps_coordinates and JSON:encode(options.gps_coordinates) or nil,
        existing_keywords = options.existing_keywords and JSON:encode(options.existing_keywords) or nil,
        folder_names = options.folder_names,
        prompt = options.prompt,
        keyword_categories = options.keyword_categories and JSON:encode(options.keyword_categories) or "[]",
        bilingual_keywords = tostring(options.bilingual_keywords or false),
        keyword_secondary_language = options.keyword_secondary_language or (prefs and prefs.keywordSecondaryLanguage) or "English",
        date_time = options.date_time,
        date_time_unix = options.date_time_unix,
        regenerate_metadata = tostring(options.regenerate_metadata ~= false),
    }

    local body = {
        options = bodyOptions,
        tasks = {},
        job_id = options.operation_id,
    }

    for _, item in ipairs(items) do
        table.insert(body.tasks, {
            photo_id = item.photo_id,
            uuid = item.photo_id,
            filename = item.filename or "photo.jpg",
            -- Metadata-only regeneration skips the embedding queue, so its
            -- JPEG has not been staged in the backend cache. Embedding-backed
            -- jobs clear this field after cache admission and send only IDs.
            image = item.image,
            options = item.options or {}
        })
    end

    log:trace("Generating metadata for batch of " .. tostring(#items) .. " photos.")

    local response, err = _request('POST', url, body, 1800) -- Very large timeout for LLM batch

    if not response then
        log:error("Failed to generate metadata batch: " .. tostring(err))
        return false, err or "Unknown error"
    end
    
    if response.status == "processed" then
        local success_count = response.success_count or 0
        if success_count > 0 then
            log:trace("Successfully generated metadata for " .. tostring(success_count) .. " photos in batch.")
            return true, response
        else
            log:error("Metadata generation failed for batch.")
            return false, response.error or "Processing failed"
        end
    end
    
    log:error("Unexpected response status for metadata generate_batch: " .. tostring(response.status))
    return false, "Unexpected response status"
end

---
-- Analyzes and indexes a batch of photos using base64-encoded JPEGs.
-- Uses the /index_base64_batch endpoint.
-- @param batch table Array of tables containing { photo_id, image, filename, options }
-- @param globalOptions table Options passed globally for all photos in the batch (e.g. tasks, provider, etc.)
-- @return boolean success, table|string response or error.
--
function SearchIndexAPI.analyzeAndIndexPhotosBatch(batch, globalOptions)
    if not batch or type(batch) ~= "table" or #batch == 0 then
        log:error("analyzeAndIndexPhotosBatch: no batch data")
        return false, "No batch data provided"
    end

    globalOptions = globalOptions or {}
    local url = getBaseUrl() .. ENDPOINTS.INDEX_BASE64_BATCH
	local diagnosticCaptureEnabled = (prefs and prefs.debugMode) == true
		and (prefs and prefs.captureLlmInputs) == true

    -- Construct global options table to send in JSON body
    local bodyOptions = {
        tasks = globalOptions.tasks or {},
        provider = globalOptions.provider,
        model = globalOptions.model,
        language = globalOptions.language or (prefs and prefs.generateLanguage) or "English",
        temperature = tostring(globalOptions.temperature or (prefs and prefs.temperature) or 0.2),
        replace_ss = tostring(globalOptions.replace_ss or false),
        generate_keywords = tostring(globalOptions.generate_keywords or false),
        generate_caption = tostring(globalOptions.generate_caption or false),
        generate_title = tostring(globalOptions.generate_title or false),
        generate_alt_text = tostring(globalOptions.generate_alt_text or false),
        submit_gps = tostring(globalOptions.submit_gps or false),
        submit_keywords = tostring(globalOptions.submit_keywords or false),
        submit_folder_names = tostring(globalOptions.submit_folder_names or false),
        user_context = globalOptions.user_context,
        prompt = globalOptions.prompt,
        keyword_categories = globalOptions.keyword_categories and JSON:encode(globalOptions.keyword_categories) or "[]",
        bilingual_keywords = tostring(globalOptions.bilingual_keywords or false),
        keyword_secondary_language = globalOptions.keyword_secondary_language or (prefs and prefs.keywordSecondaryLanguage) or "English",
        regenerate_metadata = (globalOptions.regenerate_metadata == true),
        cache_images = globalOptions.cache_images == true,
        diagnostic_mode = tostring((prefs and prefs.debugMode) == true),
		audit_llm_inputs = tostring(diagnosticCaptureEnabled),
		audit_llm_inputs_path = diagnosticCaptureEnabled
			and (globalOptions.audit_llm_inputs_path or (prefs and prefs.captureLlmInputsPath))
			or nil,
    }

    local bodyImages = {}
    for _, item in ipairs(batch) do
        local itemOptions = item.options or {}
        local encodedItemOptions = {
            submit_gps = tostring(itemOptions.submit_gps or false),
            submit_keywords = tostring(itemOptions.submit_keywords or false),
            submit_folder_names = tostring(itemOptions.submit_folder_names or false),
            gps_coordinates = itemOptions.gps_coordinates and JSON:encode(itemOptions.gps_coordinates) or nil,
            existing_keywords = itemOptions.existing_keywords and JSON:encode(itemOptions.existing_keywords) or nil,
            folder_names = itemOptions.folder_names,
            user_context = itemOptions.user_context,
            date_time = itemOptions.date_time,
            date_time_unix = itemOptions.date_time_unix,
        }

        table.insert(bodyImages, {
            image = item.image,
            photo_id = item.photo_id,
            lr_uuid = item.lr_uuid,
            filename = item.filename or "photo.jpg",
            options = encodedItemOptions
        })
    end

    local body = {
        images = bodyImages,
        options = bodyOptions
    }

    log:trace("Analyzing and indexing batch of " .. tostring(#batch) .. " photos via base64 batch API")

    local response, err = _request('POST', url, body, 1200)

    if not response then
        log:error("Failed to analyze/index batch: " .. tostring(err))
        return false, err or "Unknown error"
    end

    if response.status == "processed" then
        local success_count = response.success_count or 0
        if success_count > 0 then
            log:trace("Successfully processed batch: success=" .. tostring(success_count) .. ", failure=" .. tostring(response.failure_count or 0))
            return true, response
        else
            log:error("Batch processing failed completely")
            return false, response.error or "Processing failed"
        end
    end

    log:error("Unexpected batch response status: " .. tostring(response.status))
    return false, "Unexpected response status"
end

---
-- Unified function to analyze and index photos with metadata and embeddings.
-- Replaces the old separate analyze and index workflows.
-- @param photoId string The ID of the photo.
-- @param filename string The filename of the photo.
-- @param jpeg string The JPEG data of the photo.
-- @param options table Optional parameters for the analysis:
--   - tasks table: Array of tasks to perform (default: {"embeddings", "metadata", "quality"})
--   - provider string: AI provider to use (default: "qwen")
--   - language string: Language for generated content (default: "English")
--   - temperature number: Temperature for AI generation (default: 0.2)
--   - generate_keywords boolean: Generate keywords (default: true)
--   - generate_caption boolean: Generate caption (default: true)
--   - generate_title boolean: Generate title (default: true)
--   - generate_alt_text boolean: Generate alt text (default: false)
--   - submit_gps boolean: Submit GPS coordinates (default: false)
--   - gps_coordinates table: GPS coordinates {latitude, longitude}
--   - submit_keywords boolean: Submit existing keywords (default: false)
--   - existing_keywords table: Array of existing keywords
--   - submit_folder_names boolean: Submit folder names (default: false)
--   - folder_names string: Folder path
--   - user_context string: Additional context for the photo
-- @return boolean success, table|string response - Returns success status and response data or error message
---


function SearchIndexAPI.analyzeAndIndexPhoto(photoId, filepath, options)
    if filepath == nil then
        log:error("JPEG is nil")
        return false, "No image data provided"
    end
    if not photoId or photoId == "" then
        log:error("Photo ID is missing")
        return false, "No photo ID provided"
    end

    local filename = LrPathUtils.leafName(filepath)

    options = options or {}

    local url = getBaseUrl() .. ENDPOINTS.INDEX

    -- Prepare multipart content chunks
    local mimeChunks = {}

    -- Add form fields
    table.insert(mimeChunks, { name = "photo_id", value = photoId })

    table.insert(mimeChunks, { name = "tasks", value = JSON:encode(options.tasks or {}) })

    if options.provider then
        table.insert(mimeChunks, { name = "provider", value = options.provider })
    end
    if options.model then
        table.insert(mimeChunks, { name = "model", value = options.model })
    end


    table.insert(mimeChunks, { name = "language", value = options.language or prefs.generateLanguage or "English" })
    table.insert(mimeChunks, { name = "temperature", value = tostring(options.temperature or prefs.temperature or 0.2) })
    table.insert(mimeChunks, { name = "replace_ss", value = tostring(options.replace_ss or false) })

    -- Metadata generation options
    table.insert(mimeChunks, { name = "generate_keywords", value = tostring(options.generate_keywords or false) })
    table.insert(mimeChunks, { name = "generate_caption", value = tostring(options.generate_caption or false) })
    table.insert(mimeChunks, { name = "generate_title", value = tostring(options.generate_title or false) })
    table.insert(mimeChunks, { name = "generate_alt_text", value = tostring(options.generate_alt_text or false) })

    -- Context options
    table.insert(mimeChunks, { name = "submit_gps", value = tostring(options.submit_gps or false) })
    table.insert(mimeChunks, { name = "submit_keywords", value = tostring(options.submit_keywords or false) })
    table.insert(mimeChunks, { name = "submit_folder_names", value = tostring(options.submit_folder_names or false) })

    if options.user_context then
        table.insert(mimeChunks, { name = "user_context", value = options.user_context })
    end
    if options.gps_coordinates then
        table.insert(mimeChunks, { name = "gps_coordinates", value = JSON:encode(options.gps_coordinates) })
    end
    if options.existing_keywords then
        table.insert(mimeChunks, { name = "existing_keywords", value = JSON:encode(options.existing_keywords) })
    end
    if options.folder_names then
        table.insert(mimeChunks, { name = "folder_names", value = options.folder_names })
    end
    if options.prompt then
        table.insert(mimeChunks, { name = "prompt", value = options.prompt })
    end

    table.insert(mimeChunks, { name = "keyword_categories", value = JSON:encode(options.keyword_categories or {}) })
    table.insert(mimeChunks, { name = "bilingual_keywords", value = tostring(options.bilingual_keywords or false) })
    table.insert(mimeChunks,
        {
            name = "keyword_secondary_language",
            value = options.keyword_secondary_language or
                (prefs and prefs.keywordSecondaryLanguage) or "English"
        })

    if options.date_time then
        table.insert(mimeChunks, { name = "date_time", value = options.date_time })
    end
    -- Regeneration control: if false, server will only fill missing fields
    table.insert(mimeChunks, { name = "regenerate_metadata", value = tostring(options.regenerate_metadata ~= false) })

    if prefs and prefs.debugMode == true and prefs.captureLlmInputs == true then
        table.insert(mimeChunks, { name = "diagnostic_mode", value = "true" })
        table.insert(mimeChunks, { name = "audit_llm_inputs", value = "true" })
        if prefs.captureLlmInputsPath then
            table.insert(mimeChunks, { name = "audit_llm_inputs_path", value = prefs.captureLlmInputsPath })
        end
    end

    if options.raw_filepath then
        table.insert(mimeChunks, { name = "filepath", value = options.raw_filepath })
    end

    -- Add file
    table.insert(mimeChunks, {
        name = "image",
        fileName = filename,
        filePath = filepath,
        contentType = "image/jpeg"
    })

    log:trace("Analyzing and indexing photo: " ..
        filename ..
        " with id " .. photoId .. " and tasks: " .. (options.tasks and table.concat(options.tasks, ", ") or "none"))

    local response, err = _requestMultipart(url, mimeChunks, 720)

    if not response then
        log:error("Failed to analyze/index photo: " .. tostring(err))
        return false, err or "Unknown error"
    end

    if response.status == "processed" then
        local success_count = response.success_count or 0
        if success_count > 0 then
            log:trace("Successfully processed photo: " .. filename)
            return true, response
        else
            log:error("Photo processing failed: " .. filename)
            return false, response.error or "Processing failed"
        end
    else
        log:error("Unexpected response status: " .. tostring(response.status))
        return false, "Unexpected response status"
    end
end

---
-- Builds a URL with optional query parameters.
--
local function buildUrlWithParams(baseUrl, params)
    local queryParts = {}
    for key, value in pairs(params) do
        if value ~= nil then
            table.insert(queryParts, key .. "=" .. tostring(value))
        end
    end

    if #queryParts > 0 then
        return baseUrl .. "?" .. table.concat(queryParts, "&")
    else
        return baseUrl
    end
end


function SearchIndexAPI.getStats()
    local url = getBaseUrl() .. ENDPOINTS.STATS
    return _request('GET', url)
end

function SearchIndexAPI.isServerEmpty()
    local stats = SearchIndexAPI.getStats()
    if stats and stats.photos and stats.photos.total == 0 then
        return true
    end
    return false
end

function SearchIndexAPI.cancelBackendTasks()
    log:info("Sending /cancel_all_tasks signal to backend...")
    local url = getBaseUrl() .. ENDPOINTS.CANCEL_ALL_TASKS
    -- Short timeout since it's just setting an event flag
    _request('POST', url, nil, 3)
end

function SearchIndexAPI.clearBackendCancelTasks()
    log:trace("Clearing backend cancellation flag...")
    local url = getBaseUrl() .. ENDPOINTS.CLEAR_CANCEL_TASKS
    _request('POST', url, nil, 3)
end


function SearchIndexAPI.getBackendVersion()
    return _request('GET', getBaseUrl() .. ENDPOINTS.VERSION)
end

function SearchIndexAPI.checkVersionCompatibility()
    local pluginVersion = tostring(Info.MAJOR) .. "." .. tostring(Info.MINOR) .. "." .. tostring(Info.REVISION)
    local pluginReleaseTag = "v" .. pluginVersion
    local body = {
        plugin_version = pluginVersion,
        plugin_release_tag = pluginReleaseTag,
        plugin_build = tonumber(Info.BUILD) or 0
    }
    return _request('POST', getBaseUrl() .. ENDPOINTS.VERSION_CHECK, body)
end

function SearchIndexAPI.ensureVersionCompatibility()
    local result, err = SearchIndexAPI.checkVersionCompatibility()
    if err then
        return false, "Version check request failed: " .. tostring(err), nil
    end
    if type(result) ~= "table" then
        return false, "Version check failed: invalid response from backend.", nil
    end
    if result.compatible then
        return true, nil, result
    end

    local pluginTag = tostring(result.plugin_release_tag or ("v" .. tostring(result.plugin_version or "unknown")))
    local backendTag = tostring(result.backend_release_tag or ("v" .. tostring(result.backend_version or "unknown")))
    local reason = tostring(result.reason or "plugin and backend version differ")
    local message = "Plugin and backend versions are not compatible.\n" ..
        "Plugin: " .. pluginTag .. "\n" ..
        "Backend: " .. backendTag .. "\n" ..
        "Reason: " .. reason
    return false, message, result
end

function SearchIndexAPI.formatStats(stats)
    if type(stats) ~= "table" then
        return "No statistics available."
    end

    local photos = stats.photos or {}
    local faces = stats.faces or {}

    return table.concat({
        "Photos total: " .. tostring(photos.total or 0),
        "Photos with embeddings: " .. tostring(photos.with_embedding or 0),
        "Photos with title: " .. tostring(photos.with_title or 0),
        "Photos with caption: " .. tostring(photos.with_caption or 0),
        "Photos with keywords: " .. tostring(photos.with_keywords or 0),
    }, "\n")
end

function SearchIndexAPI.getAllIndexedPhotoIds(requireEmbeddings)
    local url = getBaseUrl() .. ENDPOINTS.GET_IDS
    local params = {}
    if requireEmbeddings then
        params.has_embedding = "true"
    end
    if next(params) then
        local sep = "?"
        for k, v in pairs(params) do
            url = url .. sep .. k .. "=" .. v
            sep = "&"
        end
    end
    return _request('GET', url)
end

function SearchIndexAPI.getAllIndexedPhotoUUIDs(requireEmbeddings)
    return SearchIndexAPI.getAllIndexedPhotoIds(requireEmbeddings)
end

---
-- Retrieves stored metadata for a photo by ID.
-- @param photoId The photo ID to retrieve.
-- @return table|nil Response containing metadata and quality fields, or nil on error.
-- Response structure:
--   {
--     status = "success",
--     photo_id = "...",
--     metadata = { title = "...", caption = "...", keywords = {...}, alt_text = "..." },
--   }
--
function SearchIndexAPI.getPhotoData(photoId)
    if not photoId then
        log:error("getPhotoData: photo_id is required")
        return nil
    end

    local url = getBaseUrl() .. "/get"
    local body = { photo_id = photoId }

    log:trace("Retrieving photo data for photo_id: " .. photoId)

    local result, err = _request('POST', url, body)
    if err then
        log:error("Failed to retrieve photo data: " .. err)
        return nil
    end

    if result and result.status == "success" then
        log:trace("Successfully retrieved photo data for photo_id: " .. photoId)
        return result
    else
        log:warn("Photo data not found for photo_id: " .. photoId)
        return nil
    end
end


function SearchIndexAPI.removePhotoId(photoId)
    local url = getBaseUrl() .. ENDPOINTS.REMOVE
    local body = { photo_id = photoId }
    log:trace("Removing photo_id: " .. photoId)

    local _, err = _request('POST', url, body)
    if not err then
        return true
    else
        ErrorHandler.handleError("Remove UUID failed", err)
        return false
    end
end

function SearchIndexAPI.removeUUID(uuid)
    return SearchIndexAPI.removePhotoId(uuid)
end

--- Remove only AI-generated metadata for a photo (keeps embeddings so the photo stays in the index).
--- Use when the user discards a suggestion in the review dialog so they can regenerate later.
function SearchIndexAPI.removePhotoMetadata(photoId)
    local url = getBaseUrl() .. ENDPOINTS.REMOVE_METADATA
    local body = { photo_id = photoId }
    log:trace("Removing metadata for photo_id: " .. photoId)

    local _, err = _request('POST', url, body)
    if not err then
        return true
    else
        ErrorHandler.handleError("Remove metadata failed", err)
        return false
    end
end


-- Analyzes and indexes selected photos with LLM processing (metadata, embeddings).
-- Uses JPEG export instead of thumbnails for better reliability.
-- @param selectedPhotos table Array of LrPhoto objects to process.
-- @param progressScope LrProgressScope Progress scope for UI updates.
-- @param options table Processing options (tasks, provider, language, temperature, etc.).
--                Optional options.onPhotoAnalyzed(photo, photoId, progressScope): if provided,
--                invoked inside the worker loop immediately after each photo is successfully
--                analyzed. Lets callers write metadata per-photo as the batch progresses
--                instead of waiting for all photos to finish. Errors in the callback are
--                caught with LrTasks.pcall and logged; the batch continues.
-- @param closeProgressScope boolean|nil When false, does not call :done() on the scope (caller must close).
-- @return string status Status: "success", "canceled", "somefailed", or "allfailed".
-- @return number processed Number of photos processed.
-- @return number failed Number of photos that failed.
-- @return table responses Array of response data from the server for each photo.
-- @return string|nil warnings Combined warnings from the server.
--
function SearchIndexAPI.analyzeAndIndexSelectedPhotos(selectedPhotos, progressScope, options, closeProgressScope)
    local numPhotos = #selectedPhotos
    if numPhotos == 0 then
        return "success", 0, 0, {}
    end

    if not SearchIndexAPI.pingServer() then
        return "allfailed", numPhotos, numPhotos, {}
    end

    options = options or {}
    local shouldCloseScope = (closeProgressScope ~= false)

    local enableEmbeddings = false
    local enableMetadata = false
    if options.tasks then
        for _, t in ipairs(options.tasks) do
            if t == "embeddings" then enableEmbeddings = true end
            if t == "metadata" then enableMetadata = true end
        end
    end
    if options.enableMetadata then enableMetadata = true end
    if options.enableEmbeddings then enableEmbeddings = true end

	local hardwareMax = 4
	local hardwareBatchSize = 12
	local success, msg, versionInfo = SearchIndexAPI.ensureVersionCompatibility()
	if success and versionInfo and versionInfo.recommended_parallel_tasks then
		hardwareMax = tonumber(versionInfo.recommended_parallel_tasks) or 4
		hardwareBatchSize = tonumber(versionInfo.recommended_index_batch_size) or hardwareBatchSize
	end

    local profile = tonumber(prefs.indexingPerformanceProfile) or 2
    local maxSenderWorkers = 1
    local maxAnalyzeWorkers = 8
	local calculatedBatchSize = hardwareBatchSize

    -- HardwareMax is typically the CPU core count (e.g., 8 to 12 on Apple Silicon)
    if profile == 1 then
        maxAnalyzeWorkers = math.max(2, math.floor(hardwareMax * 0.25))
        maxSenderWorkers = 1
		calculatedBatchSize = math.min(8, hardwareBatchSize)
    elseif profile == 2 then
        maxAnalyzeWorkers = math.max(4, math.floor(hardwareMax * 0.5))
        maxSenderWorkers = 1
		calculatedBatchSize = hardwareBatchSize
    elseif profile == 3 then
        maxAnalyzeWorkers = math.min(6, math.max(4, hardwareMax))
		calculatedBatchSize = hardwareBatchSize
    elseif profile == 4 then
        -- Optimal max performance: push Lightroom hard but cap senders to prevent backend GIL thrashing
        maxAnalyzeWorkers = math.min(8, math.floor(hardwareMax * 1.0))
		calculatedBatchSize = hardwareBatchSize
    end

    local maxWorkers = maxSenderWorkers

    if options.benchmarkConfig then
        maxSenderWorkers = options.benchmarkConfig.senders or options.benchmarkConfig.workers
        maxAnalyzeWorkers = options.benchmarkConfig.analyzers or options.benchmarkConfig.workers
        maxWorkers = maxSenderWorkers
    end

    if not enableMetadata and enableEmbeddings then
        -- Embedding-only path: Sender workers matched dynamically
        log:info("Embedding-only path: Setting sender workers to " .. maxSenderWorkers .. " to optimize PyTorch batching.")
    end

    log:info(string.format("Performance Tracking: Profile=%d, HardwareRec=%d -> Producers=%d, Consumers=%d, Batch=%d", 
             profile, hardwareMax, maxAnalyzeWorkers, maxSenderWorkers, calculatedBatchSize))

    local batchStartTime = LrDate.currentTime()
    local stats = { processed = 0, success = 0, failed = 0 }

    local photoToProcessStack = {}
    local photoIdByPhoto = {}
    
    local isServerEmpty = SearchIndexAPI.isServerEmpty()

    if options.regenerate_metadata ~= true and not isServerEmpty then
        progressScope:setCaption(LOC("$$$/StyleAI/AnalyzeAndIndex/PreflightCheck=Verifying existing index..."))
        local allPhotoIds = {}
        local photoIdToPhotoMap = {}
        local totalSelected = #selectedPhotos
        local updateInterval = math.max(1, math.floor(totalSelected / 50))
        for i, photo in ipairs(selectedPhotos) do
            if progressScope and progressScope:isCanceled() then
                return "canceled", 0, 0, {}
            end
            local photoId = getPhotoIdForPhoto(photo, options)
            if photoId then
                table.insert(allPhotoIds, photoId)
                photoIdToPhotoMap[photoId] = photo
                photoIdByPhoto[photo] = photoId
            end
            if i % updateInterval == 0 then
                progressScope:setPortionComplete(i, totalSelected)
                LrTasks.yield()
            end
        end
        
        local body = {
            photo_ids = allPhotoIds,
            tasks = options.tasks,
            regenerate_metadata = false
        }
        
        local result, err = _request('POST', getBaseUrl() .. ENDPOINTS.CHECK_UNPROCESSED, body)
        if not err and result and (result.photo_ids or result.uuids) then
            local needingIds = result.photo_ids or result.uuids
            local needingSet = {}
            for _, pid in ipairs(needingIds) do needingSet[pid] = true end
            
            for _, pid in ipairs(allPhotoIds) do
                if needingSet[pid] then
                    table.insert(photoToProcessStack, photoIdToPhotoMap[pid])
                else
                    stats.processed = stats.processed + 1
                    stats.success = stats.success + 1
                end
            end
            log:info(string.format(
                "Index preflight: scope contained %d photo(s); %d require processing and %d are already complete",
                #selectedPhotos,
                #photoToProcessStack,
                stats.success
            ))
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

    local operationItemIds = {}
    local operationPhotoById = {}
	for _, photo in ipairs(photoToProcessStack) do
        local photoId = photoIdByPhoto[photo]
        if not photoId then
            photoId = getPhotoIdForPhoto(photo, options)
            if photoId then photoIdByPhoto[photo] = photoId end
        end
        if photoId then
            table.insert(operationItemIds, photoId)
            operationPhotoById[photoId] = photo
		end
	end
	if #operationItemIds == 0 then
		return "allfailed", #photoToProcessStack, #photoToProcessStack, {},
			"No stable Lightroom photo IDs could be resolved."
	end
	local fingerprintIds = {}
    for _, photoId in ipairs(operationItemIds) do table.insert(fingerprintIds, photoId) end
    table.sort(fingerprintIds)
    local requestFingerprint = LrMD5.digest(JSON:encode({
        kind = "index",
        photo_ids = fingerprintIds,
        tasks = options.tasks or {},
        regenerate_metadata = options.regenerate_metadata == true,
        provider = options.provider,
        model = options.model,
    }))
    local operationOk, operation = SearchIndexAPI.startOperation(
        "index",
        operationItemIds,
        { tasks = options.tasks or {}, photo_count = #operationItemIds },
        requestFingerprint,
        enableMetadata and 5 or 0,
        false
    )
    if not operationOk then
        log:error("Could not create indexing operation: " .. tostring(operation))
        return "allfailed", 0, numPhotos, {}, tostring(operation)
    end
    local operationId = operation.job_id
    options.operation_id = operationId
    local preOperationProcessed = stats.processed
    local preOperationSuccess = stats.success
    local untrackedOperationItems = #photoToProcessStack - #operationItemIds



    local modelDisplay = ""
    if enableEmbeddings and enableMetadata then
        modelDisplay = "SigLIP2 & " .. tostring(options.model or "LLM")
    elseif enableEmbeddings then
        modelDisplay = "SigLIP2"
    else
        modelDisplay = tostring(options.model or "AI")
    end

    progressScope:setCaption(LOC("$$$/StyleAI/AnalyzeAndIndex/ProcessingPhotos=Processing ^1 photos with ^2...",
        #photoToProcessStack, modelDisplay))
    progressScope:setPortionComplete(stats.processed, numPhotos)
    local processedPhotos = {}
    local activeWorkers = 0
    local keepRunning = true

    -- Cancellation is scoped to this operation. Other Lightroom windows and
    -- their cached image payloads must continue independently.
    LrTasks.startAsyncTask(function()
        while keepRunning do
            if progressScope and progressScope:isCanceled() then
                SearchIndexAPI.cancelOperation(operationId)
                break
            end
            LrTasks.yield()
            LrTasks.sleep(0.5)
        end
    end)

    local previewRequestState = {
        enabled = (prefs and prefs.usePreviewThumbnails ~= false),
        timeoutSeconds = tonumber(prefs and prefs.previewThumbnailTimeoutSeconds) or 30,
        cooldownSeconds = tonumber(prefs and prefs.previewThumbnailCooldownSeconds) or 1,
        disableAfterConsecutiveTimeouts = tonumber(prefs and prefs.previewThumbnailDisableAfterTimeouts) or 10,
        consecutiveTimeouts = 0,
        disabledForRun = false,
    }

    local errorMessages = {}
    local warningsList = {}
    
    
    local llmQueue = {}
    local maxLlmQueueCapacity = math.max(12, math.min(calculatedBatchSize * 2, 24))
    local activeLlmWorkers = 0

    local readFileBinary = function(filepath)
        local f, err = io.open(filepath, "rb")
        if not f then
            return nil, err
        end
        local data = f:read("*all")
        f:close()
        return data
    end

    local preparationDone = false
    local catalog = LrApplication.activeCatalog()
    local batchRawMetaMap = {}
    if catalog and catalog.batchGetRawMetadata then
        LrTasks.pcall(function()
            batchRawMetaMap = catalog:batchGetRawMetadata(selectedPhotos, { "path", "dateTime", "rating", "pickStatus", "gps" }) or {}
        end)
    end
    local function getPhotoRawMeta(photo, key)
        if batchRawMetaMap[photo] and batchRawMetaMap[photo][key] ~= nil then
            return batchRawMetaMap[photo][key]
        end
        return photo:getRawMetadata(key)
    end

    local preparedQueue = {}
    local activeSenderWorkers = 0

    local analyzeWorker = function()
        local batchSize = (options.benchmarkConfig and options.benchmarkConfig.batch) or calculatedBatchSize
        local maxQueueCapacity = math.min(batchSize * 2, 24)

        while #photoToProcessStack > 0 do
            if progressScope:isCanceled() then break end
            if not keepRunning then break end

            if #preparedQueue >= maxQueueCapacity then
                if MAC_ENV then LrTasks.yield() else LrTasks.sleep(0.1) end
            else
                local photo = table.remove(photoToProcessStack)
                if photo then
                    local filename = photo:getFormattedMetadata("fileName")
                local hashStart = LrDate.currentTime()
                local photoId = photoIdByPhoto[photo]
                local photoIdErr
                if not photoId then
                    photoId, photoIdErr = getPhotoIdForPhoto(photo, options)
                    if photoId then photoIdByPhoto[photo] = photoId end
                end
                if photoId then
                    log:trace("Using photo_id for " ..
                        filename ..
                        " (hashing_ms=" .. tostring(math.floor((LrDate.currentTime() - hashStart) * 1000)) .. ")")

                    -- Prepare analysis options with photo-specific context
                    local photoOptions = {}
                    if options.submit_gps then
                        local gps = getPhotoRawMeta(photo, 'gps')
                        if gps then
                            photoOptions.gps_coordinates = gps
                            photoOptions.submit_gps = true
                        end
                    end
                    if options.submit_keywords then
                        local keywords = photo:getFormattedMetadata("keywordTagsForExport")
                        if keywords then
                            if type(keywords) == "string" then
                                photoOptions.existing_keywords = Util.string_split(keywords, ",")
                            else
                                photoOptions.existing_keywords = keywords
                            end
                            photoOptions.submit_keywords = true
                        end
                    end
                    if options.submit_folder_names then
                        local originalFilePath = getPhotoRawMeta(photo, "path")
                        if originalFilePath then
                            photoOptions.folder_names = Util.getStringsFromRelativePath(originalFilePath)
                            photoOptions.submit_folder_names = true
                        end
                    end
                    -- Always submit catalog capture time.
                    local datetime = getPhotoRawMeta(photo, "dateTime")
                    if datetime ~= nil and type(datetime) == "number" then
                        photoOptions.date_time = LrDate.timeToW3CDate(datetime)
                        photoOptions.date_time_unix = LrDate.timeToPosixDate(datetime)
                    end
                    photoOptions.user_context = photo:getPropertyForPlugin(_PLUGIN, 'photoContext') or ""
                    photoOptions.raw_filepath = getPhotoRawMeta(photo, "path")

                    local exifInfo = Util.getPhotoExif(photo)
                    if exifInfo then
                        photoOptions.camera_profile = exifInfo.camera_profile
						photoOptions.is_hdr = exifInfo.is_hdr
                        photoOptions.camera_make = exifInfo.camera_make
                        photoOptions.camera_model = exifInfo.camera_model
                        photoOptions.focal_length = exifInfo.focal_length
                        photoOptions.lens = exifInfo.lens
                        photoOptions.iso = exifInfo.iso
                        photoOptions.aperture = exifInfo.aperture
                        photoOptions.shutter_speed = exifInfo.shutter_speed
                    end
                    photoOptions.rating = tonumber(getPhotoRawMeta(photo, "rating")) or 0
                    photoOptions.pick_status = tonumber(getPhotoRawMeta(photo, "pickStatus")) or 0

                    local okDev, devSettings = LrTasks.pcall(function()
                        return photo:getDevelopSettings()
                    end)
                    if okDev and type(devSettings) == "table" then
                        local isEdited = false
                        for k, v in pairs(devSettings) do
                            if (k == "Exposure" and v ~= 0) or (k == "Contrast" and v ~= 0) or (k == "Highlights" and v ~= 0) or (k == "Shadows" and v ~= 0) or (k == "ParametricDarks" and v ~= 0) or (k == "ParametricLights" and v ~= 0) or (k == "ParametricShadows" and v ~= 0) or (k == "ParametricHighlights" and v ~= 0) or (k == "Saturation" and v ~= 0) or (k == "Vibrance" and v ~= 0) then
                                isEdited = true
                                break
                            end
                        end
                        photoOptions.is_edited = isEdited
                    end

                    local jpegData
                    local usePreviewThumbnails = previewRequestState.enabled and not previewRequestState.disabledForRun
                    local thumbnailSize = 1024
                    local leafName = LrPathUtils.leafName(filename or "photo.jpg")

                    if usePreviewThumbnails then
                        local thumbErr
                        jpegData, thumbErr = SearchIndexAPI.getJpegThumbnailForPhoto(photo, thumbnailSize,
                            thumbnailSize, previewRequestState)
                        if jpegData and #jpegData > 0 then
                            previewRequestState.consecutiveTimeouts = 0
                            log:trace("Using Lightroom preview for " .. filename)
                        else
                            log:trace("Preview unavailable for " ..
                                filename .. ", falling back to export: " .. tostring(thumbErr))
                            if thumbErr and string.find(thumbErr, "timed out", 1, true) then
                                previewRequestState.consecutiveTimeouts = previewRequestState.consecutiveTimeouts + 1
                                if previewRequestState.consecutiveTimeouts >= previewRequestState.disableAfterConsecutiveTimeouts then
                                    previewRequestState.disabledForRun = true
                                    log:warn("Disabling Lightroom preview thumbnails for the rest of this batch after " ..
                                        tostring(previewRequestState.consecutiveTimeouts) .. " consecutive timeouts.")
                                else
                                    log:trace("Cooling down preview requests after timeout (" ..
                                        tostring(previewRequestState.consecutiveTimeouts) .. "/" ..
                                        tostring(previewRequestState.disableAfterConsecutiveTimeouts) .. ")")
                                end

                                if previewRequestState.cooldownSeconds > 0 then
                                    LrTasks.sleep(previewRequestState.cooldownSeconds)
                                end
                            else
                                previewRequestState.consecutiveTimeouts = 0
                            end
                        end
                    end

                    if not jpegData or #jpegData == 0 then
                        local exportedPhotoPath = SearchIndexAPI.exportPhotoForIndexing(photo)
                        if exportedPhotoPath then
                            log:trace("Using exported JPEG for " .. filename)
                            local fileData, readErr = readFileBinary(exportedPhotoPath)
                            if fileData then
                                jpegData = fileData
                            else
                                log:error("Failed to read exported JPEG file: " .. tostring(readErr))
                            end
                            SearchIndexAPI.cleanupExportedPhoto(exportedPhotoPath)
                        end
                    end

                    if jpegData and #jpegData > 0 then
                        local base64Image = LrStringUtils.encodeBase64(jpegData)
                        local lrUuid = photo:getRawMetadata("uuid")
                        local item = {
                            type = "item",
                            photo_id = photoId,
                            lr_uuid = lrUuid,
                            image = base64Image,
                            filename = leafName,
                            options = photoOptions,
                            photo = photo
                        }

                        table.insert(preparedQueue, item)
                    else
                        local item = {
                            type = "error",
                            photo = photo,
                            photo_id = photoId,
                            filename = filename,
                            errorMsg = "Could not obtain image data (preview or export failed)"
                        }
                        table.insert(preparedQueue, item)
                    end
                else
                    local item = {
                        type = "error",
                        photo = photo,
                        filename = filename,
                        errorMsg = "Could not compute photo ID: " .. tostring(photoIdErr)
                    }
                    table.insert(preparedQueue, item)
                end
            else
                log:error("Photo is nil in analyze worker, probably it got deleted in the meantime.")
            end
            end
        end
        
	end

	local senderWorker = function()
		local batchSize = (options.benchmarkConfig and options.benchmarkConfig.batch) or calculatedBatchSize
        
        while keepRunning and not progressScope:isCanceled() do
            if enableMetadata and #llmQueue >= maxLlmQueueCapacity then
                -- Keep base64 previews bounded while the serial local LLM
                -- drains prior work. This is especially important for
                -- metadata-only regeneration, where images are sent inline.
                LrTasks.yield()
                LrTasks.sleep(0.05)
            elseif #preparedQueue == 0 then
                if preparationDone then
                    break
                else
                    if MAC_ENV then LrTasks.yield() else LrTasks.sleep(0.1) end
                end
            else
                local waitLoops = 0
                while #preparedQueue < batchSize and not preparationDone and waitLoops < 20 do
                    LrTasks.yield()
                    LrTasks.sleep(0.05)
                    waitLoops = waitLoops + 1
                end

                local batchItemsToSend = {}
                local localFailures = {}
                local subBatchPhotos = {}

                while #batchItemsToSend + #localFailures < batchSize and #preparedQueue > 0 do
                    local item = table.remove(preparedQueue, 1)
                    if item.type == "item" then
                        table.insert(batchItemsToSend, item)
                        table.insert(subBatchPhotos, item.photo)
                    elseif item.type == "error" then
                        table.insert(localFailures, item)
                        table.insert(subBatchPhotos, item.photo)
                    end
                end

                if #localFailures > 0 then
                    local operationFailures = {}
                    for _, failItem in ipairs(localFailures) do
                        stats.failed = stats.failed + 1
                        stats.processed = stats.processed + 1
                        table.insert(errorMessages, failItem.errorMsg)
                        log:error("Failed to prepare photo: " .. failItem.filename .. " Error: " .. failItem.errorMsg)
                        if failItem.photo_id then
                            table.insert(operationFailures, {
                                item_id = failItem.photo_id,
                                state = "failed",
                                error = failItem.errorMsg,
                            })
                        end
                    end
                    if #operationFailures > 0 then
                        SearchIndexAPI.updateOperationItems(operationId, operationFailures)
                    end
                end

                if #batchItemsToSend > 0 then
                    if enableEmbeddings then
                        options.cache_images = enableMetadata
                        local success, enqueueResponse = SearchIndexAPI.enqueuePhotosBase64Batch(batchItemsToSend, options)
                        if success then
                            local acceptedCount = math.min(
                                tonumber(enqueueResponse and enqueueResponse.enqueued) or 0,
                                #batchItemsToSend
                            )
                            -- The backend accepts queue items in request order. Keep
                            -- backpressured items for retry rather than treating a
                            -- 202 response as proof that every item was accepted.
                            for i = #batchItemsToSend, acceptedCount + 1, -1 do
                                table.insert(preparedQueue, 1, batchItemsToSend[i])
                            end
                            for i = 1, acceptedCount do
                                local batchItem = batchItemsToSend[i]
                                if enableMetadata then
                                    batchItem.image = nil
                                    table.insert(llmQueue, batchItem)
                                else
                                    -- Queue admission is not completion. The operation
                                    -- item becomes successful only after GPU inference
                                    -- and the Chroma commit both finish.
                                end
                            end
                            if acceptedCount < #batchItemsToSend then
                                local queueStatus = SearchIndexAPI.getIndexQueueStatus()
                                local queued = queueStatus and queueStatus.queued or "?"
                                local capacity = queueStatus and queueStatus.capacity or "?"
                                log:trace("Backend index queue backpressure (" .. tostring(queued) .. "/" .. tostring(capacity) .. "); retrying " .. tostring(#batchItemsToSend - acceptedCount) .. " photo(s).")
                                LrTasks.sleep(0.15)
                            end
                            if not enableMetadata then
                                progressScope:setPortionComplete(stats.processed, numPhotos)
                                progressScope:setCaption(
                                    LOC("$$$/StyleAI/AnalyzeAndIndex/ProcessingPhoto=Processing ^1 successful (^2 total/^3 failed)",
                                        stats.success, numPhotos, stats.failed)
                                )
                            end
                        else
                            local operationFailures = {}
                            for _, failItem in ipairs(batchItemsToSend) do
                                stats.failed = stats.failed + 1
                                stats.processed = stats.processed + 1
                                table.insert(errorMessages, failItem.filename .. ": " .. tostring(enqueueResponse))
                                log:error("Failed to enqueue photo: " .. failItem.filename .. " Error: " .. tostring(enqueueResponse))
                                table.insert(operationFailures, {
                                    item_id = failItem.photo_id,
                                    state = "failed",
                                    error = tostring(enqueueResponse),
                                })
                            end
                            if #operationFailures > 0 then
                                SearchIndexAPI.updateOperationItems(operationId, operationFailures)
                            end
                        end
                    else
                        for _, batchItem in ipairs(batchItemsToSend) do
                            table.insert(llmQueue, batchItem)
                        end
                    end
                end
            end
        end
	end

    

	local llmWorker = function()
		while keepRunning and not progressScope:isCanceled() do
            if #llmQueue == 0 then
                if preparationDone and activeSenderWorkers == 0 then
                    break
                else
                    LrTasks.yield()
                    LrTasks.sleep(0.1)
                end
            else
                local waitLoops = 0
                while #llmQueue < 12 and not preparationDone and waitLoops < 20 do
                    LrTasks.yield()
                    LrTasks.sleep(0.05)
                    waitLoops = waitLoops + 1
                end

                local batch = {}
                while #llmQueue > 0 and #batch < 12 do
                    local item = table.remove(llmQueue)
                    if item then
                        table.insert(batch, item)
                    end
                end
                
                if #batch > 0 then
                    local success, llmResponse = SearchIndexAPI.generateMetadataBatch(batch, options)
                    local itemStatuses = {}
                    local successfulBatch = {}
                    if success and type(llmResponse) == "table" and type(llmResponse.items) == "table" then
                        for _, result in ipairs(llmResponse.items) do
                            if result.photo_id then
                                itemStatuses[result.photo_id] = result
                            end
                        end
                        for _, item in ipairs(batch) do
                            local result = itemStatuses[item.photo_id]
                            if result and result.status == "succeeded" then
                                table.insert(successfulBatch, item)
                            end
                        end
                    elseif success then
                        -- Compatibility with an older backend during a rolling redeploy.
                        successfulBatch = batch
                    end

                    if #successfulBatch > 0 and options.onBatchAnalyzed then
                        LrTasks.yield()
                        LrTasks.sleep(0.01)
                        local okCb, cbErr = LrTasks.pcall(function()
                            options.onBatchAnalyzed(successfulBatch, progressScope)
                        end)
                        if not okCb then
                            log:error("onBatchAnalyzed callback failed: " .. tostring(cbErr))
                            for _, item in ipairs(successfulBatch) do
                                itemStatuses[item.photo_id] = {
                                    photo_id = item.photo_id,
                                    status = "failed",
                                    error = "Batch metadata callback failed: " .. tostring(cbErr),
                                }
                            end
                            successfulBatch = {}
                        end
                    end

					for _, item in ipairs(batch) do
						stats.processed = stats.processed + 1
						local result = itemStatuses[item.photo_id]
                        local itemSucceeded = success and (
                            result == nil or result.status == "succeeded"
                        )

                        if itemSucceeded then
                            stats.success = stats.success + 1
                            table.insert(processedPhotos, item.photo)
                        else
                            stats.failed = stats.failed + 1
                            local errText = "Metadata generation failed for batch"
                            if result and result.error then
                                errText = tostring(result.error)
                            elseif type(llmResponse) == "string" then
                                errText = llmResponse
                            elseif type(llmResponse) == "table" and llmResponse.error then
                                errText = tostring(llmResponse.error)
                            end
                            table.insert(errorMessages, item.filename .. ": " .. errText)
                            log:error("LLM Generation failed for " .. item.filename .. ": " .. errText)
						end
					end

					-- Backend metadata success is a handoff, not terminal success: the
					-- Lightroom callback above may still fail while writing catalog data.
					local operationUpdates = {}
					for _, item in ipairs(batch) do
						local result = itemStatuses[item.photo_id]
						local itemSucceeded = success and (
							result == nil or result.status == "succeeded"
						)
						local operationError = nil
						if not itemSucceeded then
							operationError = (result and result.error)
								or (type(llmResponse) == "string" and llmResponse)
								or "Metadata generation failed"
						end
						table.insert(operationUpdates, {
							item_id = item.photo_id,
							state = itemSucceeded and "succeeded" or "failed",
							error = operationError,
						})
					end
					SearchIndexAPI.updateOperationItems(operationId, operationUpdates)

					progressScope:setPortionComplete(stats.processed, numPhotos)
                    progressScope:setCaption(
                        LOC("$$$/StyleAI/AnalyzeAndIndex/ProcessingPhoto=Processing ^1 successful (^2 total/^3 failed)",
                            stats.success, numPhotos, stats.failed)
                    )
                end
            end
        end
	end

	local function recordWorkerFailure(workerName, workerError)
		keepRunning = false
		local message = workerName .. " failed: " .. tostring(workerError)
		log:error(message)
		table.insert(errorMessages, message)
	end

	-- Start worker threads
	-- Scale analyze workers according to the performance profile (controlled via prefs)
	for i = 1, maxAnalyzeWorkers do
		activeWorkers = activeWorkers + 1
		LrTasks.startAsyncTask(function()
			local workerOk, workerError = LrTasks.pcall(analyzeWorker)
			activeWorkers = activeWorkers - 1
			if not workerOk then recordWorkerFailure("Analyze worker", workerError) end
			log:trace("Analyze worker thread finished. activeWorkers=" .. tostring(activeWorkers))
			if activeWorkers == 0 then preparationDone = true end
		end)
		log:trace("Started analyze worker #" .. tostring(i))
	end

	maxSenderWorkers = 1
	for i = 1, maxSenderWorkers do
		activeSenderWorkers = activeSenderWorkers + 1
		LrTasks.startAsyncTask(function()
			local workerOk, workerError = LrTasks.pcall(senderWorker)
			activeSenderWorkers = activeSenderWorkers - 1
			if not workerOk then recordWorkerFailure("Sender worker", workerError) end
			log:trace("Sender worker thread finished. activeSenderWorkers=" .. tostring(activeSenderWorkers))
		end)
		log:trace("Started sender worker #" .. tostring(i))
	end

    -- Local models own a single GPU/unified-memory context.  Serializing LLM
    -- batches avoids context thrash and leaves MPS capacity for embeddings.
    local maxLlmWorkers = 1

	if enableMetadata then
		for i = 1, maxLlmWorkers do
			activeLlmWorkers = activeLlmWorkers + 1
			LrTasks.startAsyncTask(function()
				local workerOk, workerError = LrTasks.pcall(llmWorker)
				activeLlmWorkers = activeLlmWorkers - 1
				if not workerOk then recordWorkerFailure("Metadata worker", workerError) end
				log:trace("LLM worker thread finished. activeLlmWorkers=" .. tostring(activeLlmWorkers))
			end)
			log:trace("Started LLM worker #" .. tostring(i))
        end
    end

    -- Monitor workers and server availability
    while activeWorkers > 0 or activeSenderWorkers > 0 or activeLlmWorkers > 0 do
        if progressScope:isCanceled() then break end
        LrTasks.yield()
        LrTasks.sleep(0.1)
    end


    -- Wait for workers to stop in case of server failure
	if not keepRunning then
		while activeWorkers > 0 or activeSenderWorkers > 0 do
            LrTasks.yield()
			LrTasks.sleep(0.5)
		end
		SearchIndexAPI.cancelOperation(operationId)
	end

    local operationJob
    if progressScope:isCanceled() then
        SearchIndexAPI.cancelOperation(operationId)
    else
        local completed, completionResult = SearchIndexAPI.completeOperation(operationId)
        if not completed then
            table.insert(errorMessages, tostring(completionResult))
        else
            operationJob = completionResult
            local terminal = {
                succeeded = true,
                failed = true,
                canceled = true,
                interrupted = true,
            }
            local pollCount = 0
            while operationJob and not terminal[operationJob.state] and pollCount < 7200 do
                if progressScope:isCanceled() then
                    SearchIndexAPI.cancelOperation(operationId)
                    break
                end
                local statusOk, statusResult = SearchIndexAPI.getOperation(operationId, false)
                if statusOk then
                    operationJob = statusResult
                    local counts = operationJob.details and operationJob.details.item_state_counts or {}
                    progressScope:setCaption(
                        LOC("$$$/StyleAI/AnalyzeAndIndex/WaitingForBackend=Waiting for backend completion (^1 succeeded, ^2 failed)...",
                            tostring(counts.succeeded or 0),
                            tostring((counts.failed or 0) + (counts.canceled or 0)))
                    )
                end
                pollCount = pollCount + 1
                LrTasks.yield()
                LrTasks.sleep(0.25)
            end
            if operationJob and not terminal[operationJob.state] then
                table.insert(errorMessages, "Backend operation status timed out before terminal completion")
            end
            local detailOk, detailResult = SearchIndexAPI.getOperation(operationId, true)
            if detailOk then operationJob = detailResult end
        end
    end
    keepRunning = false

    if not enableMetadata and operationJob and type(operationJob.items) == "table" then
        local succeeded = 0
        local failed = untrackedOperationItems
        for _, result in ipairs(operationJob.items) do
            if result.state == "succeeded" then
                succeeded = succeeded + 1
                local photo = operationPhotoById[result.item_id]
                if photo then table.insert(processedPhotos, photo) end
            elseif result.state == "failed" or result.state == "canceled" or result.state == "interrupted" then
                failed = failed + 1
                if result.error and result.error ~= "" then
                    table.insert(errorMessages, tostring(result.item_id) .. ": " .. tostring(result.error))
                end
            end
        end
        stats.success = preOperationSuccess + succeeded
        stats.failed = failed
        stats.processed = preOperationProcessed + succeeded + failed
    end

    if shouldCloseScope then
        progressScope:done()
    end

    if progressScope:isCanceled() then
        return "canceled", stats.processed, stats.failed, processedPhotos
    end

    local status
    if stats.failed == 0 then
        status = "success"
    elseif stats.failed >= stats.processed and stats.processed > 0 then
        status = "allfailed"
    else
        status = "somefailed"
    end

    local combinedError
    if #errorMessages > 0 then
        local uniqueErrors = {}
        local errorList = {}
        for _, msg in ipairs(errorMessages) do
            if not uniqueErrors[msg] then
                uniqueErrors[msg] = true
                table.insert(errorList, msg)
                if #errorList >= 5 then break end
            end
        end
        combinedError = table.concat(errorList, "\n")
    end

    local combinedWarnings
    if #warningsList > 0 then
        local uniqueWarnings = {}
        local warningListStrings = {}
        for _, w in ipairs(warningsList) do
            if not uniqueWarnings[w] then
                uniqueWarnings[w] = true
                table.insert(warningListStrings, w)
            end
        end
        combinedWarnings = table.concat(warningListStrings, "\n")
    end

    local batchDuration = LrDate.currentTime() - batchStartTime
    local avgTimePerPhoto = 0
    if stats.processed > 0 then avgTimePerPhoto = batchDuration / stats.processed end
    log:info(string.format("Performance Tracking: Processed %d photos in %.2f seconds (%.2f s/photo).", stats.processed, batchDuration, avgTimePerPhoto))

    return status, stats.processed, stats.failed, processedPhotos, combinedError, combinedWarnings
end



function SearchIndexAPI.pingServer(timeoutSeconds)
    timeoutSeconds = timeoutSeconds or 2
    local url = getBaseUrl() .. "/ping"
    local result, hdrs = LrHttp.get(url, nil, timeoutSeconds)
    local status = (type(hdrs) == "number") and hdrs or (type(hdrs) == "table" and hdrs.status) or nil
    if status == 200 and result == "pong" then
        return true
    else
        return false
    end
end

function SearchIndexAPI.isBackendOnLocalhost()
    local url = getBaseUrl()
    return not not (url:match("^https?://127%.0%.0%.1") or url:match("^https?://localhost"))
end

function SearchIndexAPI.downloadDatabaseBackup()
    local url = getBaseUrl() .. ENDPOINTS.DB_BACKUP
    log:info("downloadDatabaseBackup: start, url=" .. tostring(url))
    local catalog = LrApplication.activeCatalog()
    local catalogPath = catalog:getPath()
    local catalogDir = LrPathUtils.parent(catalogPath)
    local defaultBackupDir = LrPathUtils.child(catalogDir, "Backups")
    if not LrFileUtils.exists(defaultBackupDir) then
        defaultBackupDir = catalogDir
    end

    local initialFilename = "styleai-backup-" .. os.date("%Y%m%d-%H%M%S") .. ".zip"

    local outputPath = LrDialogs.runSavePanel({
        title = LOC("$$$/StyleAI/common/SaveDatabaseBackup=Save database backup"),
        prompt = "Save Backup",
        canCreateDirectories = true,
        requiredFileType = "zip",
        initialDirectory = defaultBackupDir,
        initialFilename = initialFilename,
    })
    log:info("downloadDatabaseBackup: save panel returned type=" ..
        tostring(type(outputPath)) .. " value=" .. tostring(outputPath))

    if not outputPath or outputPath == "" then
        log:info("Database backup download canceled by user")
        return nil, "canceled"
    end

    if type(outputPath) ~= "string" then
        local err = "Save panel returned unexpected type for outputPath: " .. tostring(type(outputPath))
        log:error("downloadDatabaseBackup: " .. err)
        return false, err
    end

    if not outputPath:lower():match("%.zip$") then
        outputPath = outputPath .. ".zip"
    end

    log:info("Requesting backend to write database backup to " .. outputPath)

    local payload = {
        output_path = outputPath
    }

    local results, err = _request('POST', url, payload, 300) -- give it 5 minutes just in case
    if not results then
        log:error("downloadDatabaseBackup failed: " .. tostring(err))
        return false, err
    end

    if not LrFileUtils.exists(outputPath) then
        local existErr = "Backend reported success, but backup file was not found at " .. outputPath
        log:error(existErr)
        return false, existErr
    end

    log:info("Database backup created successfully by backend at: " .. outputPath)
    return true, outputPath
end

function SearchIndexAPI.restoreDatabaseBackup()
    local selected = LrDialogs.runOpenPanel({
        title = LOC("$$$/StyleAI/PluginInfo/ChooseDbBackup=Choose a StyleAI database backup"),
        canChooseFiles = true,
        canChooseDirectories = false,
        allowsMultipleSelection = false,
        fileTypes = { "zip" },
    })
    if not selected or not selected[1] then
        return nil, "canceled"
    end
    local archivePath = selected[1]
    local results, err = _request(
        'POST',
        getBaseUrl() .. ENDPOINTS.DB_RESTORE,
        { archive_path = archivePath },
        600
    )
    if not results then
        return false, err or "Unknown restore error"
    end
    return true, results
end

-- -----------------------------
-- Structured backend lifecycle
-- -----------------------------
local SERVER_PID_FILENAME = "styleai-server.pid"
local SERVER_OK_FILENAME = "styleai-server.OK"
local SERVER_LOCK_FILENAME = "styleai-server.lock"

local serverStartInProgress = false

local function getServerControlDir()
    -- Backend uses --db-path = "<catalogParent>/styleai.db", and writes pid/OK files next to it.
    return LrPathUtils.parent(LrApplication.activeCatalog():getPath())
end

local function getServerPidFilePath()
    return LrPathUtils.child(getServerControlDir(), SERVER_PID_FILENAME)
end

local function getServerOkFilePath()
    return LrPathUtils.child(getServerControlDir(), SERVER_OK_FILENAME)
end

local function getServerLockFilePath()
    return LrPathUtils.child(getServerControlDir(), SERVER_LOCK_FILENAME)
end

local readPidFromPidFile

local function cleanupServerPidAndOkFiles(expectedPid)
	if expectedPid and readPidFromPidFile() ~= expectedPid then
		log:warn("Skipping lifecycle marker cleanup because backend ownership changed")
		return false
	end
    local pidPath = getServerPidFilePath()
    local okPath = getServerOkFilePath()
    if LrFileUtils.exists(pidPath) then LrTasks.pcall(function() LrFileUtils.delete(pidPath) end) end
    if LrFileUtils.exists(okPath) then LrTasks.pcall(function() LrFileUtils.delete(okPath) end) end
	return true
end

readPidFromPidFile = function()
    local pidFilePath = getServerPidFilePath()
    local pidFile = io.open(pidFilePath, "r")
    if not pidFile then return nil end
    local pid = pidFile:read("*l")
    pidFile:close()
    if not pid then return nil end
    return tonumber(pid)
end

local function isPidAlive(pid)
    if not pid then return false end
    if MAC_ENV then
        -- Exit code 0 => process exists
        local cmd = "ps -p " .. tostring(pid) .. " >/dev/null 2>&1"
        local rc = LrTasks.execute(cmd)
        return rc == 0
    end
    if WIN_ENV then
        -- Best-effort (avoid brittle parsing of tasklist output)
        local cmd = "tasklist /FI \"PID eq " .. tostring(pid) .. "\" | findstr /I \"" .. tostring(pid) .. "\" >NUL"
        local rc = LrTasks.execute(cmd)
        return rc == 0
    end
    return false
end

local function acquireStartLock(lockStaleSeconds)
    if serverStartInProgress then return false end
    lockStaleSeconds = lockStaleSeconds or 120

    local lockPath = getServerLockFilePath()
    if LrFileUtils.exists(lockPath) then
        local lockFile = io.open(lockPath, "r")
        local content = lockFile and lockFile:read("*a") or ""
        if lockFile then lockFile:close() end

        local ts = content:match("ts=(%d+)")
        local tsN = tonumber(ts)
        if tsN and (os.time() - tsN) < lockStaleSeconds then
            -- Another start attempt is still considered fresh.
            return false
        else
            -- Stale lock: remove it.
            LrTasks.pcall(function() LrFileUtils.delete(lockPath) end)
        end
    end

    local f = io.open(lockPath, "w")
    if not f then return false end
    f:write("ts=" .. tostring(os.time()))
    f:close()

    serverStartInProgress = true
    return true
end

local function releaseStartLock()
    serverStartInProgress = false
    local lockPath = getServerLockFilePath()
    if LrFileUtils.exists(lockPath) then LrTasks.pcall(function() LrFileUtils.delete(lockPath) end) end
end

function SearchIndexAPI.shutdownServer(opts)
    opts = opts or {}
    local graceSeconds = opts.graceSeconds or 10
    local pollIntervalSeconds = opts.pollIntervalSeconds or 0.5
    local shutdownRequestTimeoutSeconds = opts.shutdownRequestTimeoutSeconds or 5
	local shutdownPid = readPidFromPidFile()

    -- Cancel any active tasks so Waitress threads can process the shutdown
    LrTasks.pcall(function()
        SearchIndexAPI.cancelBackendTasks()
    end)

    if not SearchIndexAPI.pingServer() then
        log:trace("Search index server is not running (or unreachable)")
        cleanupServerPidAndOkFiles(shutdownPid)
        return true
    end

    local url = getBaseUrl() .. ENDPOINTS.SHUTDOWN
    log:trace("Requesting graceful backend shutdown")

    -- /shutdown returns JSON, so we can go through _request() decoding.
    LrTasks.pcall(function()
        _request("POST", url, {}, shutdownRequestTimeoutSeconds)
    end)

    if opts.skipWait then
        log:trace("Skipping shutdown wait loop (skipWait enabled)")
        cleanupServerPidAndOkFiles(shutdownPid)
        return true
    end

    local deadline = LrDate.currentTime() + graceSeconds
    while LrDate.currentTime() < deadline do
        if not SearchIndexAPI.pingServer() then
            cleanupServerPidAndOkFiles(shutdownPid)
            return true
        end
        LrTasks.sleep(pollIntervalSeconds)
    end

    log:trace("Graceful shutdown timed out; escalating to kill")
    return SearchIndexAPI.killServer({
		killMode = "force",
		forceWaitSeconds = opts.forceWaitSeconds or 10,
		expectedPid = shutdownPid,
	})
end

function SearchIndexAPI.unloadResources(opts)
    local url = getBaseUrl() .. ENDPOINTS.UNLOAD
    log:trace("Requesting backend model unload")
    local status, response = LrTasks.pcall(function()
        return _request("POST", url, {}, 10) -- 10s timeout
    end)
    if status and response then
        log:trace("Backend models unloaded successfully")
        return true
    else
        log:warn("Failed to unload backend models: " .. tostring(response))
        return false
    end
end

function SearchIndexAPI.restartBackend()
    local url = getBaseUrl() .. ENDPOINTS.RESTART
    log:info("Requesting backend restart via API")
    local _, err = _request('POST', url, {}, 5)
    if err then
        log:error("Failed to request backend restart: " .. tostring(err))
        return false, err
    end

    -- Wait a bit and then ping until back
    LrTasks.sleep(2)
    local deadline = LrDate.currentTime() + 60
    while LrDate.currentTime() < deadline do
        if SearchIndexAPI.pingServer() then
            log:info("Backend restarted successfully")
            local dbPath = LrPathUtils.child(getServerControlDir(), "styleai.db")
            SearchIndexAPI.initializeCatalog(dbPath)
            return true
        end
        LrTasks.sleep(1)
    end
    return false, "Restart timeout"
end

function SearchIndexAPI.initializeCatalog(dbPath)
    if not dbPath then
        dbPath = LrPathUtils.child(getServerControlDir(), "styleai.db")
    end

    local url = getBaseUrl() .. ENDPOINTS.INITIALIZE
    log:info("Initializing catalog database at backend: " .. tostring(dbPath))
    local response, err = _request("POST", url, { db_path = dbPath }, 10)

    if response and (response.status == "success" or response.status == "already_initialized") then
        log:info("Backend initialized successfully for database: " .. tostring(dbPath))
        return true
    else
        log:error("Failed to initialize backend for catalog: " ..
            tostring(err or (response and response.error) or "Unknown error"))
        return false, err or (response and response.error)
    end
end

function SearchIndexAPI.killServer(opts)
    opts = opts or {}
    local killMode = opts.killMode or "force" -- "force" => SIGKILL on unix
    local forceWaitSeconds = opts.forceWaitSeconds or 10
    local pollIntervalSeconds = opts.pollIntervalSeconds or 0.5

    local pid = readPidFromPidFile()
	if opts.expectedPid and pid ~= opts.expectedPid then
		log:error("killServer: backend PID ownership changed; refusing to kill replacement process.")
		return false
	end
    if not pid then
        -- Without a pid file, we can only do a best-effort ping check.
        if not SearchIndexAPI.pingServer() then
            cleanupServerPidAndOkFiles()
            return true
        end
        log:error("killServer: no PID available; cannot force kill safely.")
        return false
    end

    if not isPidAlive(pid) then
        cleanupServerPidAndOkFiles(pid)
        return true
    end

    local killCmd
    if WIN_ENV then
        killCmd = "taskkill /PID " .. tostring(pid) .. " /F"
    elseif MAC_ENV then
        if killMode == "force" then
            killCmd = "kill -9 " .. tostring(pid)
        else
            killCmd = "kill " .. tostring(pid)
        end
    else
        log:error("killServer: unsupported platform for pid kill")
        return false
    end

    log:trace("Forcing backend process kill: " .. tostring(killCmd))
    local rc = LrTasks.execute(killCmd)
    if rc ~= 0 then
        log:error("killServer: kill command exit code: " .. tostring(rc))
    end

    local deadline = LrDate.currentTime() + forceWaitSeconds
    while LrDate.currentTime() < deadline do
        if not SearchIndexAPI.pingServer() then
            cleanupServerPidAndOkFiles(pid)
            return true
        end
        LrTasks.sleep(pollIntervalSeconds)
    end

    cleanupServerPidAndOkFiles(pid)
    return false
end

function SearchIndexAPI.startServer(opts)
    opts = opts or {}
    local readyTimeoutSeconds = opts.readyTimeoutSeconds or 60
    local lockStaleSeconds = opts.lockStaleSeconds or 120

    if SearchIndexAPI.pingServer() then
        log:trace("Search index server is already running, triggering initialization")
        local catalog = LrApplication.activeCatalog()
        local catalogPath = catalog:getPath()
        local catalogDir = LrPathUtils.parent(catalogPath)
        local dbPath = LrPathUtils.child(catalogDir, "styleai.db")
        if SearchIndexAPI.initializeCatalog(dbPath) then
            return true
        end
        return false
    end

    if not acquireStartLock(lockStaleSeconds) then
        log:trace("Backend start lock is active; another start attempt may be in progress")
        return false
    end

    local catalog = LrApplication.activeCatalog()
    local catalogPath = catalog:getPath()
    local catalogDir = LrPathUtils.parent(catalogPath)
    local dbPath = LrPathUtils.child(catalogDir, "styleai.db")

    -- Make sure we don't leave the lock behind on early returns.
    local ok, startResult = LrTasks.pcall(function()
        -- If pid/OK are stale, clean them before starting.
        local pid = readPidFromPidFile()
        if pid and not isPidAlive(pid) then
            cleanupServerPidAndOkFiles(pid)
        end

        -- Check standard system locations first (if installed via PKG/EXE)
        local serverBinary = nil
        if MAC_ENV then
            serverBinary = "/Applications/StyleAI/Server/styleai-server"
        elseif WIN_ENV then
            serverBinary = "C:\\Program Files\\StyleAI\\backend\\styleai-server.cmd"
        end

        -- Fallback to plugin-local binary (development or old installs)
        if not serverBinary or not LrFileUtils.exists(serverBinary) then
            local serverDir = LrPathUtils.child(LrPathUtils.parent(_PLUGIN.path), "styleai-server")
            serverBinary = LrPathUtils.child(serverDir, "styleai-server")
            if WIN_ENV then
                local serverLauncherCmd = serverBinary .. ".cmd"
                local serverExe = serverBinary .. ".exe"
                if LrFileUtils.exists(serverLauncherCmd) then
                    serverBinary = serverLauncherCmd
                else
                    serverBinary = serverExe
                end
            end
        end

        local startServerCmd
        local isDevMode = false
        local devServerDir = LrPathUtils.child(LrPathUtils.parent(LrPathUtils.parent(_PLUGIN.path)), "server")
        local devServerScript = LrPathUtils.child(LrPathUtils.child(devServerDir, "src"), "styleai_server.py")

        if not LrFileUtils.exists(devServerScript) then
            -- Fallback: If plugin was copied to Lightroom's Modules directory via a redeploy script,
            -- it loses its relative path to the server. Check the standard source location.
            local docs = LrPathUtils.getStandardFilePath("documents")
            if docs then
                local altDevServerDir
                if MAC_ENV then
                    altDevServerDir = docs .. "/Coding/StyleAI/server"
                else
                    altDevServerDir = docs .. "\\Coding\\StyleAI\\server"
                end
                local altDevServerScript = LrPathUtils.child(LrPathUtils.child(altDevServerDir, "src"), "styleai_server.py")
                if LrFileUtils.exists(altDevServerScript) then
                    devServerDir = altDevServerDir
                    devServerScript = altDevServerScript
                end
            end
        end

        if LrFileUtils.exists(devServerScript) then
            isDevMode = true
        end

        if not LrFileUtils.exists(serverBinary) and not isDevMode then
            log:error(tostring(serverBinary) .. " not found and not in dev mode. Not trying to start server")
            return false
        end

        if isDevMode and not LrFileUtils.exists(serverBinary) then
            local launchLogPath = LrPathUtils.child(getServerControlDir(), "styleai-server-launcher.log")
            if WIN_ENV then
                startServerCmd = string.format("cd /d \"%s\" && start /b \"\" uv run python src/styleai_server.py --db-path \"%s\" > \"%s\" 2>&1",
                    devServerDir, dbPath, launchLogPath)
            else
                startServerCmd = string.format("cd '%s' && nohup bash -c 'PATH=\"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH\" uv run python src/styleai_server.py --db-path \"%s\"' </dev/null > '%s' 2>&1 &",
                    devServerDir, dbPath, launchLogPath)
            end
        else
            local serverDir = LrPathUtils.parent(serverBinary)
            if WIN_ENV then
                -- The .cmd launcher handles environment variables and uses pythonw.exe for invisible execution.
                startServerCmd = "start /b /d \"" ..
                    serverDir .. "\" \"\" \"" .. tostring(serverBinary) .. "\" --db-path \"" .. dbPath .. "\""
            elseif MAC_ENV then
                if serverBinary:match("^/Applications") then
                    -- System install: use launchctl to trigger the system-wide service
                    startServerCmd = "launchctl kickstart -k gui/$(id -u)/com.styleai.server"
                else
                    -- Local/Dev fallback
                    local envPrefix = "KMP_DUPLICATE_LIB_OK=TRUE "
                    startServerCmd = envPrefix .. "bash \"" .. tostring(serverBinary) .. "\" --db-path \"" .. dbPath .. "\""
                end
            else
                -- Unknown platform fallback
                local envPrefix = "KMP_DUPLICATE_LIB_OK=TRUE "
                startServerCmd = envPrefix .. "bash \"" .. tostring(serverBinary) .. "\" --db-path \"" .. dbPath .. "\""
            end
        end

        log:trace("Trying to start search index server with command: " .. tostring(startServerCmd))
        LrTasks.startAsyncTask(function()
            local result = LrTasks.execute(startServerCmd)
            log:trace("Search index server start command exit code: " .. tostring(result))
        end)

        local deadline = LrDate.currentTime() + readyTimeoutSeconds
        while LrDate.currentTime() < deadline do
            if SearchIndexAPI.pingServer() then
                log:trace("Search index server is running")
                -- Initialize with current catalog
                if SearchIndexAPI.initializeCatalog(dbPath) then
                    SearchIndexAPI.checkServerHealth()
                    return true
                end
            end
            LrTasks.sleep(0.5)
        end

        log:trace("Search index server did not become ready or initialize within timeout")

        -- Diagnose failure
        local diag = SearchIndexAPI.diagnoseStartupFailure()
        if diag.binaryMissing then
            log:error(LOC "$$$/StyleAI/Diagnostics/BinaryMissing=The background service binary is missing from the plugin folder.")
        elseif diag.portBusy then
            log:error(LOC "$$$/StyleAI/Diagnostics/PortBusy=Port 19819 is already in use by another application.")
        end
        if diag.logSnippet then
            log:error(LOC "$$$/StyleAI/Diagnostics/LogSnippet=Recent service errors:" .. "\n" .. diag.logSnippet)
        end
        return false
    end)

    releaseStartLock()

    if not ok then
        log:error("startServer: unexpected error: " .. tostring(startResult))
        return false
    end

    return startResult == true
end

_requestMultipart = function(url, mimeChunks, timeout)
    log:trace("_requestMultipart start: url=" ..
        tostring(url) ..
        " timeout=" .. tostring(timeout) .. " chunks=" .. tostring(type(mimeChunks) == "table" and #mimeChunks or "n/a"))
    local result, hdrs = LrHttp.postMultipart(url, mimeChunks, nil, timeout)
    log:trace("_requestMultipart raw return: resultType=" ..
        tostring(type(result)) ..
        " resultLen=" .. tostring(type(result) == "string" and #result or "n/a") .. " hdrsType=" .. tostring(type(hdrs)))

    -- hdrs kann Tabelle mit .status oder (in einigen LR-Versionen) direkt die Status-Nummer sein
    local status = (type(hdrs) == "number") and hdrs or (type(hdrs) == "table" and hdrs.status) or nil
    log:trace("_requestMultipart interpreted status: " .. tostring(status))
    if status ~= nil and status >= 200 and status < 300 then
        if result and #result > 0 then
            local ok, decodedOrErr = LrTasks.pcall(function()
                return JSON:decode(result)
            end)
            if not ok then
                log:error("_requestMultipart JSON decode failed: " .. tostring(decodedOrErr))
                return nil, "Invalid JSON response from server"
            end

            -- Auto-unwrap standard API envelope if present
            if type(decodedOrErr) == "table" and (decodedOrErr.results ~= nil or decodedOrErr.error ~= nil or decodedOrErr.warning ~= nil) then
                if decodedOrErr.error and decodedOrErr.error ~= "" then
                    return nil, decodedOrErr.error
                end
                if decodedOrErr.warning and decodedOrErr.warning ~= "" then
                    log:warn("API Warning for multipart " .. tostring(url) .. ": " .. tostring(decodedOrErr.warning))
                end
                decodedOrErr = decodedOrErr.results
            end

            log:trace("_requestMultipart decode success: decodedType=" ..
                tostring(type(decodedOrErr)) ..
                " hasStatus=" .. tostring(type(decodedOrErr) == "table" and decodedOrErr.status or "n/a"))
            return decodedOrErr
        end
        log:trace("_requestMultipart success with empty body")
        return {} -- Return an empty table for successful but empty responses
    else
        local err_msg = "API request failed. HTTP status: " .. httpStatusForLog(status, hdrs)
        if result and #result > 0 then
            local ok, decoded_err = LrTasks.pcall(function()
                return JSON:decode(result)
            end)
            if ok and type(decoded_err) == "table" and decoded_err.error then
                err_msg = err_msg .. " - " .. decoded_err.error
            else
                err_msg = err_msg .. " Response: " .. tostring(result)
            end
        end
        log:error(err_msg)
        return nil, err_msg
    end
end

_request = function(method, url, body, timeout, options)
    options = options or {}
    local result, hdrs
    local bodyString = (body and type(body) == 'table') and JSON:encode(body) or nil

    local ok, err = LrTasks.pcall(function()
        if method == 'GET' then
            if timeout ~= nil then
                result, hdrs = LrHttp.get(tostring(url), nil, timeout)
            else
                result, hdrs = LrHttp.get(tostring(url))
            end
        else
            result, hdrs = LrHttp.post(tostring(url), bodyString or "",
                { { field = "Content-Type", value = "application/json" } }, method, timeout)
        end
    end)

    if not ok then
        log:error("_request network error: " .. tostring(err))
        return nil, tostring(err)
    end

    local status = (type(hdrs) == "number") and hdrs or (type(hdrs) == "table" and hdrs.status) or nil
    if status ~= nil and status >= 200 and status < 300 then
        if options.raw then
            return result, hdrs
        end
        if result and #result > 0 then
            log:trace("_request: decoding JSON result of length " .. #result)
            local ok2, decoded = LrTasks.pcall(JSON.decode, JSON, result)
            if ok2 then
                -- Auto-unwrap standard API envelope if present
                if type(decoded) == "table" and (decoded.results ~= nil or decoded.error ~= nil or decoded.warning ~= nil) then
                    if decoded.error and decoded.error ~= "" then
                        return nil, decoded.error
                    end
                    if decoded.warning and decoded.warning ~= "" then
                        log:warn("API Warning for " .. tostring(url) .. ": " .. tostring(decoded.warning))
                    end
                    return decoded.results
                end
                return decoded
            else
                local snippet = tostring(result):sub(1, 1000)
                log:error("_request: JSON decode failed: " ..
                    tostring(decoded) .. " | URL: " .. tostring(url) .. " | Raw Snippet: " .. snippet)
                return nil, "JSON decode failed: " .. tostring(decoded)
            end
        end
        return {}
    else
        log:trace("_request: status=" .. tostring(status) .. " type(hdrs)=" .. type(hdrs))
        local statusStr = httpStatusForLog(status, hdrs)
        local err_msg
        if status == nil then
            local urlFixed = tostring(url):gsub("%%?.*", "")
            err_msg = "API request failed (no response). URL: " .. urlFixed
            if type(hdrs) == "string" and hdrs ~= "" then
                err_msg = err_msg .. " - error: " .. hdrs
            end
        else
            err_msg = "API request failed. HTTP status: " .. statusStr
            if result and #result > 0 then
                local ok2, decoded_err = LrTasks.pcall(JSON.decode, JSON, result)
                if ok2 and type(decoded_err) == "table" and decoded_err.error then
                    err_msg = err_msg .. " - " .. decoded_err.error
                else
                    err_msg = err_msg .. " Response: " .. tostring(result):sub(1, 400)
                end
            end
        end
        log:error(err_msg)
        return nil, err_msg
    end
end


---
-- Gets photos that need processing for "New or unprocessed photos" scope.
-- When taskOptions is provided, uses backend to check which photos lack the selected tasks' data.
-- When taskOptions is nil, falls back to legacy behavior: photos not in index (with embeddings).
-- @param taskOptions table|nil { enableEmbeddings, enableMetadata, enableFaces, regenerateMetadata }
-- @param lookupProgressScope LrProgressScope|nil Optional progress for "looking up which photos need processing".
-- @return boolean success, table photosToProcess
--
---
-- Sends a comprehensive list of valid photo IDs to the backend to safely purge orphaned database entries.
-- This guarantees the ChromaDB embeddings match the active Lightroom catalog exactly.
-- @param catalogId string The unique identifier for the current catalog.
-- @param validPhotoIds table An array of globalPhotoIds that currently exist in Lightroom.
-- @return table|nil Result summary on success (deleted, disassociated, checked counts), nil on error.
-- @return string|nil Error message on failure, nil on success.
---
function SearchIndexAPI.pruneDatabase(validPhotoIds)
    local body = {
        valid_photo_ids = validPhotoIds
    }
    local url = getBaseUrl() .. ENDPOINTS.DB_PRUNE
    local res, err = _request('POST', url, body)
    if err then
        return nil, err
    end
    return res, nil
end

function SearchIndexAPI.getMissingPhotosFromIndex(taskOptions, lookupProgressScope)
    local allPhotos = PhotoSelector.getPhotosInScope('all')
    if allPhotos == nil then
        ErrorHandler.handleError("No photos found in catalog", "Something went wrong")
        return false, {}
    end

    local totalCatalog = #allPhotos
    local function updateLookupProgress(current, total)
        if lookupProgressScope and not lookupProgressScope:isCanceled() then
            lookupProgressScope:setPortionComplete(current, total)
            lookupProgressScope:setCaption(
                LOC("$$$/StyleAI/AnalyzeAndIndex/LookupProgress=Looking up which photos need processing... ^1/^2",
                    tostring(current), tostring(total)))
        end
    end

    local isServerEmpty = SearchIndexAPI.isServerEmpty()

    -- New behavior: use backend to check which photos need processing based on selected tasks
    if taskOptions and type(taskOptions) == "table" then
        if isServerEmpty then
            if lookupProgressScope then
                lookupProgressScope:setCaption(LOC "$$$/StyleAI/AnalyzeAndIndex/LookupPhase1Bypass=Bypassing lookup for empty service...")
                lookupProgressScope:setPortionComplete(totalCatalog, totalCatalog)
            end
            -- Since the server is empty, all photos are missing. Return all catalog photos instantly.
            return true, allPhotos
        end

        if lookupProgressScope then
            lookupProgressScope:setCaption(LOC "$$$/StyleAI/AnalyzeAndIndex/LookupPhase1=Preparing catalog photos for lookup...")
            lookupProgressScope:setPortionComplete(0, totalCatalog)
        end

        local photoIds = {}
        local updateInterval = math.max(1, math.floor(totalCatalog / 50))
        for i, photo in ipairs(allPhotos) do
            if lookupProgressScope and lookupProgressScope:isCanceled() then
                return false, {}
            end
            local photoId, idErr = getPhotoIdForPhoto(photo)
            if photoId then
                table.insert(photoIds, photoId)
            else
                log:error("Could not compute photo_id for missing-check: " .. tostring(idErr))
            end
            if i % updateInterval == 0 or i == totalCatalog then
                updateLookupProgress(i, totalCatalog)
                LrTasks.yield()
            end
        end
        if #photoIds == 0 then
            return true, {}
        end

        if lookupProgressScope then
            lookupProgressScope:setCaption(LOC "$$$/StyleAI/AnalyzeAndIndex/LookupPhase2=Checking service for unprocessed photos...")
        end

        local tasks = {}
        if taskOptions.enableEmbeddings then table.insert(tasks, "embeddings") end
        if taskOptions.enableMetadata then table.insert(tasks, "metadata") end
        if taskOptions.enableFaces then table.insert(tasks, "faces") end

        local body = {
            photo_ids = photoIds,
            tasks = tasks,
            regenerate_metadata = taskOptions.regenerateMetadata or false
        }
        local result, err = _request('POST', getBaseUrl() .. ENDPOINTS.CHECK_UNPROCESSED, body)
        if err then
            ErrorHandler.handleError("Failed to check unprocessed photos", err)
            return false, {}
        end

        local needingPhotoIds = result and (result.photo_ids or result.uuids) or {}
        local photoIdSet = {}
        for _, pid in ipairs(needingPhotoIds) do photoIdSet[pid] = true end

        if lookupProgressScope then
            lookupProgressScope:setCaption(LOC "$$$/StyleAI/AnalyzeAndIndex/LookupPhase3=Matching photos to process...")
            lookupProgressScope:setPortionComplete(0, totalCatalog)
        end

        local photosToProcess = {}
        for i, photo in ipairs(allPhotos) do
            if lookupProgressScope and lookupProgressScope:isCanceled() then
                return false, {}
            end
            local photoId = getPhotoIdForPhoto(photo)
            if photoId and photoIdSet[photoId] then
                table.insert(photosToProcess, photo)
            end
            if i % updateInterval == 0 or i == totalCatalog then
                updateLookupProgress(i, totalCatalog)
                LrTasks.yield()
            end
        end
        return true, photosToProcess
    end

    -- Legacy: photos not in index (optionally requiring real embeddings)
    local requireEmbeddings = (taskOptions == true)
    local indexedPhotoIds, err = SearchIndexAPI.getAllIndexedPhotoIds(requireEmbeddings)
    if err then
        ErrorHandler.handleError("Failed to retrieve indexed photos", err)
        return false, {}
    end

    local photosToProcess = {}
    local totalLegacy = #allPhotos
    local updateLegacy = math.max(1, math.floor(totalLegacy / 50))
    for i, photo in ipairs(allPhotos) do
        local photoId = getPhotoIdForPhoto(photo)
        if photoId and not Util.table_contains(indexedPhotoIds, photoId) then
            table.insert(photosToProcess, photo)
        end
        if i % updateLegacy == 0 then
            LrTasks.yield()
        end
    end
    return true, photosToProcess
end

function SearchIndexAPI.saveThumbnail(uuid, faceIndex, base64Data)
    local tempDir = LrPathUtils.getStandardFilePath('temp')
    local tempFile = LrPathUtils.child(tempDir, uuid .. "_" .. faceIndex .. ".jpg")
    local f = io.open(tempFile, "wb")
    if f then
        f:write(LrStringUtils.decodeBase64(base64Data))
        f:close()
        log:trace("Saved face thumbnail to: " .. tempFile)
        return tempFile
    end
    return nil
end

---
-- Retrieves all available multimodal models from all providers.
-- Always filters to vision-capable models only.
-- Dynamically checks Ollama and LM Studio availability on each call.
-- @return table|nil Response from server with format: { models = { qwen = {...}, ollama = {...}, ... } }
function SearchIndexAPI.getModels()
    local url = getBaseUrl() .. ENDPOINTS.MODELS
    local result = _request('GET', url)
    return result
end

function SearchIndexAPI.getDiagnosticCaptureInfo(path)
    local url = getBaseUrl() .. ENDPOINTS.DEBUG_CAPTURES
    if path and path ~= "" then
        url = url .. "?path=" .. LrHttp.encodeForUrl(path)
    end
    local result, err = _request('GET', url, nil, 15)
    return result, err
end

function SearchIndexAPI.clearDiagnosticCaptures(path)
    local result, err = _request('POST', getBaseUrl() .. ENDPOINTS.DEBUG_CAPTURES, {
        path = path,
    }, 30)
    return result, err
end

---
-- Downloads a raw log file from the server directly to a local path on disk.
-- Bypasses JSON parsing to avoid memory exhaustion for large logs.
-- @param logType string 'backend', 'ollama', or 'lmstudio'
-- @param targetPath string local file path to save to
-- @return boolean success
function SearchIndexAPI.downloadRawLog(logType, targetPath)
    if not logType or not targetPath then return false end

    local url = getBaseUrl() .. ENDPOINTS.LOGS_RAW .. "/" .. tostring(logType)
    log:trace("Downloading raw " .. logType .. " log from: " .. url)

    local ok, res, hdrs = LrTasks.pcall(function()
        return LrHttp.get(url, nil, 60)
    end)

    -- Status can be in hdrs.status (table) or hdrs itself (number) depending on LR version
    local status = (type(hdrs) == 'table' and hdrs.status) or (type(hdrs) == 'number' and hdrs) or nil

    if ok and status == 200 and res then
        local f, err = io.open(targetPath, "wb")
        if f then
            f:write(res)
            f:close()
            log:trace("Successfully downloaded and saved raw log to: " .. targetPath)
            return true
        else
            log:error("Failed to open target path for writing: " .. tostring(err))
        end
    else
        log:error("Failed to download raw log: status=" ..
            tostring(status) .. " ok=" .. tostring(ok) .. " hdrsType=" .. type(hdrs))
    end
    return false
end



---
-- Generates hash-based global photo IDs for all photos in the current catalog
-- and writes them to the catalog-only plugin fields, without touching the backend.
-- Uses Util.getGlobalPhotoIdForPhoto() which will reuse cached IDs when present.
-- @return boolean success, string message
--
function SearchIndexAPI.generateGlobalPhotoIdsForCatalog()
    local startedAt = LrDate.currentTime()
    log:info("generateGlobalPhotoIdsForCatalog: started")

    local catalog = LrApplication.activeCatalog()
    local photos = catalog:getAllPhotos() or {}
    local totalPhotos = #photos

    if totalPhotos == 0 then
        log:info("generateGlobalPhotoIdsForCatalog: no photos in catalog")
        return true, "No photos found in catalog."
    end

    log:info("generateGlobalPhotoIdsForCatalog: catalog photos to inspect: " .. tostring(totalPhotos))

    local progressScope = LrProgressScope({
        title = LOC("$$$/StyleAI/APISearchIndex/GeneratingIds=Generating hash-based photo IDs in catalog..."),
        functionContext = nil,
    })

    local generated = 0
    local reused = 0
    local errors = 0

    for i, photo in ipairs(photos) do
        if progressScope:isCanceled() then
            progressScope:done()
            log:info("generateGlobalPhotoIdsForCatalog: canceled by user at " ..
                tostring(i) .. "/" .. tostring(totalPhotos))
            return false, "Photo-ID generation canceled."
        end

        local hadExistingId = not Util.nilOrEmpty(photo:getPropertyForPlugin(_PLUGIN, "globalPhotoId"))

        local photoId, err = Util.getGlobalPhotoIdForPhoto(photo, {
            windowBytes = Util.getDefaultPartialHashWindowBytes(),
        })

        if photoId and photoId ~= "" then
            if hadExistingId then
                reused = reused + 1
            else
                generated = generated + 1
            end
        else
            errors = errors + 1
            log:warn("generateGlobalPhotoIdsForCatalog: failed to compute ID for photo: " .. tostring(err))
        end

        progressScope:setPortionComplete(i, totalPhotos)
        if i % 250 == 0 or i == totalPhotos then
            progressScope:setCaption("Generating hash-based photo IDs " .. tostring(i) .. "/" .. tostring(totalPhotos))
        end
    end

    progressScope:done()

    local elapsedMs = math.floor((LrDate.currentTime() - startedAt) * 1000)
    local msg = "Photo-ID generation finished.\n" ..
        "Catalog photos: " .. tostring(totalPhotos) .. "\n" ..
        "New IDs generated: " .. tostring(generated) .. "\n" ..
        "Existing IDs reused: " .. tostring(reused) .. "\n" ..
        "Errors: " .. tostring(errors)

    log:info(
        "generateGlobalPhotoIdsForCatalog: finished elapsedMs=" .. tostring(elapsedMs) ..
        " generated=" .. tostring(generated) ..
        " reused=" .. tostring(reused) ..
        " errors=" .. tostring(errors)
    )

    return errors == 0, msg
end

function SearchIndexAPI.startClipDownload()
    LrTasks.startAsyncTask(function()
        if SearchIndexAPI.isClipReady() then
            log:trace("CLIP model is already cached")
            return
        end

        local status, err = _request('GET', getBaseUrl() .. ENDPOINTS.STATUS_CLIP_DOWNLOAD)
        if not err and status ~= nil and status.status == "downloading" then
            log:trace("CLIP model download is already in progress")
            return
        end

        local progressScope = LrProgressScope({
            title = LOC "$$$/StyleAI/ClipDownload/ProgressTitle=Downloading CLIP AI model for advanced search",
            functionContext = nil,
        })

        local url = getBaseUrl() .. ENDPOINTS.START_CLIP_DOWNLOAD
        local body = {}

        local _, requestErr = _request('POST', url, body)

        if requestErr then
            log:error("startClipDownload failed: " .. requestErr)
            return nil, requestErr
        end

        while true do
            local pollStatus, pollErr = _request('GET', getBaseUrl() .. ENDPOINTS.STATUS_CLIP_DOWNLOAD)
                if pollErr then
                    ErrorHandler.handleError("Error downloading CLIP model", pollErr)
                    if progressScope ~= nil then
                        progressScope:setCaption(LOC "$$$/StyleAI/ClipDownload/Error=Error downloading CLIP model: ^1",
                            pollErr)
                        progressScope:done()
                    end
                    break
                end

                if pollStatus ~= nil then
                    if progressScope ~= nil then
                        progressScope:setCaption(LOC "$$$/StyleAI/ClipDownload/Downloading=Downloading CLIP model...")
                    end
                    if pollStatus.status == "downloading" then
                        progressScope:setPortionComplete(pollStatus.progress, pollStatus.total)
                    elseif pollStatus.status == "completed" then
                        log:trace("CLIP model download completed")
                        progressScope:done()
                        LrDialogs.message(LOC "$$$/StyleAI/ClipDownload/SuccessTitle=CLIP Download",
                            LOC "$$$/StyleAI/ClipDownload/SuccessMessage=CLIP model downloaded successfully.")
                        break
                    elseif pollStatus.status == "error" or (pollStatus.error and pollStatus.error ~= "null" and pollStatus.error ~= "") then
                        local error_msg = pollStatus.error or "Unknown download error"
                        ErrorHandler.handleError(LOC "$$$/StyleAI/ClipDownload/ErrorTitle=Error downloading CLIP model",
                            error_msg)
                        progressScope:done()
                        break
                    end
                end

            LrTasks.sleep(2)
        end
    end)
end

local lastClipReadyStatus = nil
function SearchIndexAPI.isClipReady()
    local url = getBaseUrl() .. ENDPOINTS.CLIP_STATUS
    local res, err = _request('GET', url, nil, 0.5)
    if err then
        local errStr = (type(err) == "string") and err or "unknown"
        log:error("isClipReady failed: " .. errStr)
        return false, errStr
    end
    if res ~= nil then
        local currentStatus = res.clip
        if currentStatus ~= lastClipReadyStatus then
            if currentStatus == "ready" then
                log:trace("CLIP model is ready")
            else
                log:trace("CLIP model is not ready: " .. tostring(res.message or "no message"))
            end
            lastClipReadyStatus = currentStatus
        end

        if currentStatus == "ready" then
            return true, res.message
        else
            return false, res.message
        end
    end
    log:error("isClipReady: Unknown error")
    return false, "Unknown error"
end

---
-- Checks the health of the backend server and its components (models, providers).
-- Surfaces critical loading failures to the user.
--
function SearchIndexAPI.checkServerHealth()
    local url = getBaseUrl() .. ENDPOINTS.HEALTH
    local res, err = _request('GET', url)
    if err then
        log:warn("checkServerHealth failed (could not reach /health): " .. tostring(err))
        return false, err
    end

    if res then
        -- 1. Check CLIP model
        if res.clip_model == "failed" then
            ErrorHandler.handleError(
                LOC "$$$/StyleAI/Health/ClipFailed=AI search model failed to load.",
                res.clip_error or "Unknown error loading CLIP model."
            )
        end

        -- 2. Check Face model
        if res.face_model == "failed" then
            log:warn("Face detection model failed to load on server: " .. tostring(res.face_error))
        end

        -- 3. Check LLM providers
        local providers = res.llm_providers or {}
        local hasAvailable = false
        local failedProviders = {}
        for provider, status in pairs(providers) do
            if status == "available" or status == "registered" then
                hasAvailable = true
            elseif status == "failed" then
                table.insert(failedProviders,
                    provider .. ": " .. (res.llm_errors and res.llm_errors[provider] or "unknown error"))
            end
        end

        if not hasAvailable and next(providers) ~= nil then
            ErrorHandler.handleError(
                LOC "$$$/StyleAI/Health/NoProviders=No AI metadata providers are available.",
                LOC "$$$/StyleAI/Health/NoProvidersDetail=Please configure Ollama or LM Studio in the plugin preferences."
            )
        elseif #failedProviders > 0 then
            log:warn("Some AI providers failed to initialize: " .. table.concat(failedProviders, ", "))
        end
    end

    return true
end

function SearchIndexAPI.getHealth()
    local url = getBaseUrl() .. ENDPOINTS.HEALTH
    local res, err = _request('GET', url)
    if err then
        return nil, err
    end
    return res
end

function SearchIndexAPI.diagnoseStartupFailure()
    local results = {
        binaryMissing = false,
        portBusy = false,
        logSnippet = nil
    }

    -- 1. Check binary existence
    local serverDir = LrPathUtils.child(LrPathUtils.parent(_PLUGIN.path), "styleai-server")
    local serverBinary = LrPathUtils.child(serverDir, "styleai-server")
    if WIN_ENV then
        local serverLauncherCmd = serverBinary .. ".cmd"
        local serverExe = serverBinary .. ".exe"
        if LrFileUtils.exists(serverLauncherCmd) then
            serverBinary = serverLauncherCmd
        else
            serverBinary = serverExe
        end
    end

    if not LrFileUtils.exists(serverBinary) then
        results.binaryMissing = true
        return results
    end

    -- 2. Check port 19819 (Mac only for now)
    if MAC_ENV then
        local status, output = LrTasks.pcall(function()
            return LrTasks.execute("bash -c \"lsof -i :19819 | grep LISTEN\"")
        end)
        if status and output and output ~= "" then
            results.portBusy = true
        end
    end

    -- 3. Check logs for errors
    local logPath = LrPathUtils.child(getServerControlDir(), "styleai-server.log")
    if LrFileUtils.exists(logPath) then
        local f = io.open(logPath, "r")
        if f then
            local content = f:read("*all")
            f:close()
            local lines = {}
            for line in content:gmatch("[^\r\n]+") do
                table.insert(lines, line)
            end
            local start = math.max(1, #lines - 10)
            local snippet = {}
            for i = start, #lines do
                table.insert(snippet, lines[i])
            end
            results.logSnippet = table.concat(snippet, "\n")
        end
    end

    return results
end

function SearchIndexAPI.getDetailedHealth()
    local health = {
        backend = SearchIndexAPI.pingServer() == true,
        clip = SearchIndexAPI.isClipReady() == true,
        ollama = false,
        lmstudio = false,
    }

    local _, ollamaHeaders = LrHttp.get("http://127.0.0.1:11434/api/tags", nil, 0.5)
    local ollamaStatus = (type(ollamaHeaders) == "number") and ollamaHeaders
        or (type(ollamaHeaders) == "table" and ollamaHeaders.status)
    if ollamaStatus == 200 then health.ollama = true end

    -- Fall back to backend provider health check for dynamic LM Studio port discovery
    if not health.lmstudio and health.backend then
        local bHealth = SearchIndexAPI.getBackendHealth()
        if bHealth and bHealth.llm_providers and bHealth.llm_providers.lmstudio == "available" then
            health.lmstudio = true
        end
    end

    return health
end

function SearchIndexAPI.getBackendHealth()
    local url = getBaseUrl() .. "/health"
    local response, err = _request("GET", url)
    if err then return nil, err end
    return response, nil
end

function SearchIndexAPI.getLogs()
    local url = getBaseUrl() .. "/logs"
    local response, err = _request("GET", url)
    if err then return nil, err end
    return response, nil
end

function SearchIndexAPI.renameStyle(styleId, newName)
    if not styleId or not newName then
        return false, "Missing style_id or new_name"
    end
    local url = getBaseUrl() .. "/styles/rename"
    local response, err = _request("POST", url, {
        style_id = styleId,
        new_name = newName
    })
    if err then return false, err end
    return true, nil
end

-- ---------------------------------------------------------------------------
-- Training API functions
-- ---------------------------------------------------------------------------

---
-- Add or update a training example on the backend.
-- @param photoId string        Stable photo identifier.
-- @param filepath string       Path to an exported JPEG for this photo.
-- @param developSettings table Lightroom develop settings (from photo:getDevelopSettings()).
-- @param options table         Optional: label, summary.
-- @return boolean success, table|string response or error message
---
function SearchIndexAPI.addTrainingExample(photoId, filepath, developSettings, options)
    if not photoId or photoId == "" then
        log:error("addTrainingExample: photo_id is missing")
        return false, "No photo ID provided"
    end
    options = options or {}
    local url = getBaseUrl() .. ENDPOINTS.TRAINING_ADD
    local mimeChunks = {}

    table.insert(mimeChunks, { name = "photo_id", value = photoId })
    table.insert(mimeChunks, { name = "develop_settings", value = JSON:encode(developSettings or {}) })

    if options.label and options.label ~= "" then
        table.insert(mimeChunks, { name = "label", value = options.label })
    end
    if options.summary and options.summary ~= "" then
        table.insert(mimeChunks, { name = "summary", value = options.summary })
    end

    -- Send EXIF fields for richer multi-criteria matching.
    if options.focal_length and type(options.focal_length) == "number" then
        table.insert(mimeChunks, { name = "focal_length", value = tostring(options.focal_length) })
    end
    if options.capture_time and type(options.capture_time) == "number" then
        table.insert(mimeChunks, { name = "capture_time", value = tostring(options.capture_time) })
    end
    if options.camera_make and options.camera_make ~= "" then
        table.insert(mimeChunks, { name = "camera_make", value = tostring(options.camera_make) })
    end
    if options.camera_model and options.camera_model ~= "" then
        table.insert(mimeChunks, { name = "camera_model", value = tostring(options.camera_model) })
    end
    if options.camera_profile and options.camera_profile ~= "" then
        table.insert(mimeChunks, { name = "camera_profile", value = tostring(options.camera_profile) })
    end
    if options.iso and type(options.iso) == "number" then
        table.insert(mimeChunks, { name = "iso", value = tostring(options.iso) })
    end
    if options.aperture and type(options.aperture) == "number" then
        table.insert(mimeChunks, { name = "aperture", value = tostring(options.aperture) })
    end
    if options.shutter_speed and options.shutter_speed ~= "" then
        table.insert(mimeChunks, { name = "shutter_speed", value = tostring(options.shutter_speed) })
    end
    if options.rating and type(options.rating) == "number" then
        table.insert(mimeChunks, { name = "rating", value = tostring(options.rating) })
    end
    if options.pick_status and type(options.pick_status) == "number" then
        table.insert(mimeChunks, { name = "pick_status", value = tostring(options.pick_status) })
    end

    if filepath and LrFileUtils.exists(filepath) then
        local filename = LrPathUtils.leafName(filepath)
        table.insert(mimeChunks, {
            name = "image",
            fileName = filename,
            filePath = filepath,
            contentType = "image/jpeg",
        })
    end

    log:trace("addTrainingExample: uploading photo_id=" .. tostring(photoId))
    local response, err = _requestMultipart(url, mimeChunks, 120)
    if not response then
        log:error("addTrainingExample failed: " .. tostring(err))
        return false, err or "Unknown error"
    end
    if response.status == "ok" then
        return true, response
    end
    log:error("addTrainingExample unexpected status: " .. tostring(response.status))
    return false, response.error or "Unexpected response"
end

---
-- Add multiple training examples in a single batch request to the backend.
-- @param examples table        List of training examples.
-- @return boolean success, table|string response or error message
---
function SearchIndexAPI.addTrainingBatch(examples, forceRetrain, rebuildPolicies, jobId)
    if not examples or #examples == 0 then
        return false, "No examples provided"
    end
    local url = getBaseUrl() .. ENDPOINTS.TRAINING_ADD_BATCH
    local body = {
        examples = examples,
        force_retrain = forceRetrain or false,
        rebuild_policies = rebuildPolicies ~= false,
		job_id = jobId,
    }
    log:trace("addTrainingBatch: uploading " .. tostring(#examples) .. " examples")
    local response, err = _request('POST', url, body, 120)
    if not response then
        log:error("addTrainingBatch failed: " .. tostring(err))
        return false, err or "Unknown error"
    end
    if response.status == "ok" then
        return true, response
    end
    log:error("addTrainingBatch unexpected status: " .. tostring(response.status))
    return false, response.error or "Unexpected response"
end

---
-- Fetch the list of all training examples from the backend.
-- @return boolean success, table|string examples list or error message
---
function SearchIndexAPI.listTrainingExamples()
    local url = getBaseUrl() .. ENDPOINTS.TRAINING_LIST
    local response, err = _request('GET', url)
    if not response then
        log:error("listTrainingExamples failed: " .. tostring(err))
        return false, err or "Unknown error"
    end
    return true, response.examples or {}
end

---
-- Get the count of stored training examples.
-- @return number|nil count, string|nil error
---
function SearchIndexAPI.getTrainingCount()
    local url = getBaseUrl() .. ENDPOINTS.TRAINING_COUNT
    local response, err = _request('GET', url)
    if not response then
        log:error("getTrainingCount failed: " .. tostring(err))
        return nil, err or "Unknown error"
    end
    return tonumber(response.count) or 0, nil
end

---
-- Delete one training example by photo_id.
-- @param photoId string
-- @return boolean success, string|nil error
---
function SearchIndexAPI.deleteTrainingExample(photoId)
    if not photoId or photoId == "" then
        return false, "No photo ID provided"
    end
    local url = getBaseUrl() .. ENDPOINTS.TRAINING_DELETE .. "/" .. photoId
    local response, err = _request('DELETE', url)
    if not response then
        log:error("deleteTrainingExample failed: " .. tostring(err))
        return false, err or "Unknown error"
    end
    if response.status == "ok" then
        return true, nil
    end
    return false, response.error or "Not found"
end

---
-- Clear ALL training examples from the backend.
-- @return boolean success, string|nil error
---
function SearchIndexAPI.clearAllTrainingExamples()
    local url = getBaseUrl() .. ENDPOINTS.TRAINING_CLEAR
    local response, err = _request('DELETE', url)
    if not response then
        log:error("clearAllTrainingExamples failed: " .. tostring(err))
        return false, err or "Unknown error"
    end
    if response.status == "ok" then
        return true, nil
    end
    return false, response.error or "Unexpected response"
end

---
-- Clear ALL training data including actual embeddings from the backend.
-- @return boolean success, string|nil error
---
function SearchIndexAPI.clearAllTrainingData()
    local url = getBaseUrl() .. ENDPOINTS.TRAINING_CLEAR_ALL
    local response, err = _request('DELETE', url)
    if not response then
        log:error("clearAllTrainingData failed: " .. tostring(err))
        return false, err or "Unknown error"
    end
    if response.status == "ok" then
        return true, nil
    end
    return false, response.error or "Unexpected response"
end

---
-- Get aggregate style-profile statistics from the backend.
-- @return table|nil { count, readiness, descriptor_distribution, exposure, focal_buckets, time_of_day, ... }
-- @return string|nil error message
---
function SearchIndexAPI.getTrainingStats()
    local url = getBaseUrl() .. ENDPOINTS.TRAINING_STATS
    local response, err = _request('GET', url)
    if not response then
        log:error("getTrainingStats failed: " .. tostring(err))
        return nil, err or "Unknown error"
    end
    return response, nil
end

---
-- Generate a style-matched edit recipe using the LLM-free style engine.
-- Falls back to LLM if use_llm_fallback=true and confidence is low.
-- @param photoId   string  Stable photo ID.
-- @param filepath  string  Path to an exported JPEG preview.
-- @param options   table   Same options as generateEditRecipe; extra keys:
--                           use_llm_fallback (bool), focal_length (number),
--                           capture_time (number unix), camera_make, camera_model,
--                           iso, aperture, shutter_speed.
-- @return boolean success, table|string response or error message
---
function SearchIndexAPI.styleEdit(photoId, filepath, options)
    if not photoId or photoId == "" then
        log:error("styleEdit: photo_id missing")
        return false, "No photo ID provided"
    end
    options = options or {}
    local url = getBaseUrl() .. ENDPOINTS.STYLE_EDIT
    local mimeChunks = {}

    table.insert(mimeChunks, { name = "photo_id", value = photoId })

    -- Optional extra EXIF context for the style engine
    local function addStr(key)
        if options[key] and tostring(options[key]) ~= "" then
            table.insert(mimeChunks, { name = key, value = tostring(options[key]) })
        end
    end
    addStr("use_llm_fallback")
    addStr("focal_length")
    addStr("capture_time")
    addStr("camera_make")
    addStr("camera_model")
    addStr("camera_profile")
    addStr("profile_mode")
    addStr("hdr_mode")
    addStr("job_id")
    addStr("iso")
    addStr("aperture")
    addStr("shutter_speed")
    
    if options.current_settings then
        table.insert(mimeChunks, { name = "current_settings", value = JSON:encode(options.current_settings) })
    end
    if options.raw_filepath then
        table.insert(mimeChunks, { name = "filepath", value = options.raw_filepath })
    end

    -- Standard edit options forwarded for LLM fallback compatibility
    local function addEditOpt(key, value)
        if value ~= nil then
            table.insert(mimeChunks, { name = key, value = tostring(value) })
        end
    end
    addEditOpt("provider", options.provider)
    addEditOpt("model", options.model)
    addEditOpt("language", options.language)
    addEditOpt("temperature", options.temperature)
    addEditOpt("include_masks", options.include_masks)
    addEditOpt("adjust_white_balance", options.adjust_white_balance)
    addEditOpt("adjust_basic_tone", options.adjust_basic_tone)
    addEditOpt("adjust_presence", options.adjust_presence)
    addEditOpt("adjust_color_mix", options.adjust_color_mix)
    addEditOpt("do_color_grading", options.do_color_grading)
    addEditOpt("use_tone_curve", options.use_tone_curve)
    addEditOpt("adjust_detail", options.adjust_detail)
    addEditOpt("adjust_effects", options.adjust_effects)
    addEditOpt("allow_auto_crop", options.allow_auto_crop)
    addEditOpt("allow_auto_rotate", options.allow_auto_rotate)
    addEditOpt("style_strength", options.style_strength)

    if filepath and LrFileUtils.exists(filepath) then
        local filename = LrPathUtils.leafName(filepath)
        table.insert(mimeChunks, {
            name = "image",
            fileName = filename,
            filePath = filepath,
            contentType = "image/jpeg",
        })
    end
    if options.darkPath and LrFileUtils.exists(options.darkPath) then
        table.insert(mimeChunks, {
            name = "image_dark",
            fileName = LrPathUtils.leafName(options.darkPath),
            filePath = options.darkPath,
            contentType = "image/jpeg"
        })
    end
    if options.brightPath and LrFileUtils.exists(options.brightPath) then
        table.insert(mimeChunks, {
            name = "image_bright",
            fileName = LrPathUtils.leafName(options.brightPath),
            filePath = options.brightPath,
            contentType = "image/jpeg"
        })
    end

    log:trace("styleEdit: uploading photo_id=" .. tostring(photoId))
    local response, err = _requestMultipart(url, mimeChunks, 180)
    if not response then
        log:error("styleEdit failed: " .. tostring(err))
        return false, err or "Unknown error"
    end
    if response.status == "success" then
        return true, response
    end
    if response.status == "error" then
        return false, response.error or "Style engine error"
    end
    log:error("styleEdit unexpected status: " .. tostring(response.status))
    return false, response.error or "Unexpected response"
end

function SearchIndexAPI.submitStyleEditApplicationEvents(events)
    if type(events) ~= "table" or #events == 0 then
        return true, { stored = 0, events = {} }
    end
    local url = getBaseUrl() .. ENDPOINTS.STYLE_EDIT_APPLICATION_EVENTS
    local combined = { stored = 0, events = {} }
    for first = 1, #events, 250 do
        local batch = {}
        local last = math.min(first + 249, #events)
        for index = first, last do
            table.insert(batch, events[index])
        end
        local response, err = _request('POST', url, { events = batch }, 30)
        if not response then
            return false, err or "Unknown error"
        end
        combined.stored = combined.stored + (response.stored or 0)
        for _, storedEvent in ipairs(response.events or {}) do
            table.insert(combined.events, storedEvent)
        end
    end
    return true, combined
end

function SearchIndexAPI.reconcileStyleEditStates(items)
    if type(items) ~= "table" or #items == 0 then
        return true, { checked = 0, photos = {} }
    end
    local url = getBaseUrl() .. ENDPOINTS.STYLE_EDIT_RECONCILE_EVENTS
    local response, err = _request('POST', url, { items = items }, 30)
    if not response then
        return false, err or "Unknown error"
    end
    if response.checked ~= nil and type(response.photos) == "table" then
        return true, response
    end
    return false, response.error or "Unexpected response"
end

function SearchIndexAPI.recordStyleEditOutcomes(items)
    if type(items) ~= "table" or #items == 0 then
        return true, { stored = 0, photos = {} }
    end
    local url = getBaseUrl() .. ENDPOINTS.STYLE_EDIT_OUTCOMES
    local response, err = _request('POST', url, { items = items }, 30)
    if not response then
        return false, err or "Unknown error"
    end
    if response.stored ~= nil and type(response.photos) == "table" then
        return true, response
    end
    return false, response.error or "Unexpected response"
end

---
-- List all styles from the style catalog.
-- @return boolean success, table styles list or error message
---
function SearchIndexAPI.listStyles()
    local url = getBaseUrl() .. ENDPOINTS.STYLE_LIST
    local response, err = _request('GET', url, {}, 30)
    if not response then
        return false, err or "Unknown error"
    end
    if response.status == "ok" then
        return true, response.styles or {}
    end
    return false, response.error or "Unexpected response"
end

---
-- Discover new styles from the given photo IDs.
-- @param photoIds table List of photo IDs to process.
-- @return boolean success, table styles or error message
---
function SearchIndexAPI.discoverStyles(photoIds)
    local url = getBaseUrl() .. ENDPOINTS.STYLE_DISCOVER
    local body = { photo_ids = photoIds or {} }
    -- Discovery runs on the backend worker.  Keep this acknowledgement short;
    -- callers poll discoveryStatus() instead of holding a Lightroom HTTP
    -- connection through CPU-intensive policy fitting.
    local response, err = _request('POST', url, body, 30)
    if not response then
        return false, err or "Unknown error"
    end
    if response.status == "accepted" then
        return true, response
    end
    return false, response.error or "Unexpected response"
end

---
-- Get the current catalog-local policy discovery job state.
-- @return boolean success, table|string status payload or error message
---
function SearchIndexAPI.discoveryStatus()
    local url = getBaseUrl() .. ENDPOINTS.STYLE_DISCOVER_STATUS
    local response, err = _request('GET', url, {}, 15)
    if not response then
        return false, err or "Unknown error"
    end
    if response.status == "ok" and response.discovery then
        return true, response.discovery
    end
    return false, response.error or "Unexpected response"
end

---
-- Reset all styles.
-- @return boolean success, string error message if any
---
function SearchIndexAPI.resetAllStyles()
    local url = getBaseUrl() .. ENDPOINTS.STYLE_RESET_ALL
    local response, err = _request('POST', url, {}, 30)
    if not response then
        return false, err or "Unknown error"
    end
    if response.status == "ok" then
        return true, nil
    end
    return false, response.error or "Unexpected response"
end

---
-- Get details for a specific style including its trained photos.
-- @param styleId string The style ID.
-- @return boolean success, table style details or error message
---
function SearchIndexAPI.getStyleDetails(styleId)
    local url = getBaseUrl() .. "/styles/" .. tostring(styleId)
    local response, err = _request('GET', url, {}, 30)
    if not response then
        return false, err or "Unknown error"
    end
    if response.status == "ok" and response.style then
        return true, response.style
    end
    return false, response.error or "Unexpected response"
end

---
-- Get all styles with their associated photo IDs attached.
-- @return boolean success, table styles list or error message
---
function SearchIndexAPI.getAllStylesWithExamples()
    local url = getBaseUrl() .. "/styles/all_examples"
    local response, err = _request('GET', url, {}, 60)
    if not response then
        return false, err or "Unknown error"
    end
    if response.status == "ok" and response.styles then
        return true, response.styles
    end
    return false, response.error or "Unexpected response"
end

function SearchIndexAPI.getUpgradeRecommendations(limit)
    local url = getBaseUrl() .. ENDPOINTS.STYLE_UPGRADES_RECOMMENDATIONS
    if limit and tonumber(limit) then
        url = url .. "?limit=" .. tostring(limit)
    end
    local response, err = _request('GET', url, {}, 60)
    if not response then
        return false, err or "Unknown error"
    end
    if type(response) == "table" then
        return true, response
    end
    return false, response.error or "Unexpected response"
end

function SearchIndexAPI.submitUpgradeFeedback(reviewId, policyId, labels)
    local url = getBaseUrl() .. ENDPOINTS.STYLE_UPGRADES_FEEDBACK
    local response, err = _request('POST', url, {
        review_id = reviewId,
        policy_id = policyId,
        labels = labels or {},
    }, 30)
    if not response then
        return false, err or "Unknown error"
    end
    if response.updated ~= nil or response.requested ~= nil then
        return true, response
    end
    return false, response.error or "Unexpected response"
end

return SearchIndexAPI
