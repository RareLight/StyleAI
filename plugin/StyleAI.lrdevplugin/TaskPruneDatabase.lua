---
-- @module TaskPruneDatabase
-- @description Iterates through the current Lightroom catalog, collects all active globalPhotoIds, 
-- and sends them to the backend to safely purge any orphaned metadata and vector embeddings.
-- This ensures the ChromaDB vector index remains a 1:1 match with the Lightroom catalog.
---

local LrTasks = import 'LrTasks'
local LrApplication = import 'LrApplication'
local LrDialogs = import 'LrDialogs'
local LrProgressScope = import 'LrProgressScope'

local ErrorHandler = require 'ErrorHandler'
local SearchIndexAPI = require 'APISearchIndex'
local Util = require 'Util'

local TaskPruneDatabase = {}

---
-- Executes the database pruning task.
-- Prompts the user for confirmation, then kicks off a background task that safely walks 
-- the catalog and streams a massive list of valid IDs to the Python backend API.
---
function TaskPruneDatabase.process()
    LrTasks.startAsyncTask(function()
        local ok, err = SearchIndexAPI.ensureVersionCompatibility()
        if not ok then
            ErrorHandler.handleError(LOC "$$$/StyleAI/PruneDatabase/VersionMismatch=Service Version Mismatch", err)
            return
        end

        local confirm = LrDialogs.confirm(
			LOC "$$$/StyleAI/PruneDatabase/ConfirmTitle=Clean StyleAI Database?",
            LOC "$$$/StyleAI/PruneDatabase/ConfirmMessage=This will remove any AI metadata and embeddings from the backend for photos that are no longer in this Lightroom catalog. This cannot be undone.\n\n(A database backup will be automatically generated before pruning).",
			LOC "$$$/StyleAI/common/Continue=Continue",
            LOC "$$$/StyleAI/common/Cancel=Cancel"
        )
        if confirm ~= "ok" then
            return
        end

        if not Util.waitForServerDialog({ suppressProgressDialog = false }) then
            return
        end

        local progressScope = LrProgressScope({
            title = LOC "$$$/StyleAI/PruneDatabase/ProgressTitle=Cleaning Database...",
            functionContext = nil,
        })
        progressScope:setPortionComplete(0, 100)

        local catalog = LrApplication.activeCatalog()


        progressScope:setCaption(LOC "$$$/StyleAI/PruneDatabase/Gathering=Checking photos in the Lightroom catalog...")
        local allPhotos = catalog:getAllPhotos()
        local validPhotoIds = {}

        for i, photo in ipairs(allPhotos) do
            if progressScope:isCanceled() then
                progressScope:done()
                return
            end

            -- Update progress every 500 photos to avoid slowing down Lightroom
            if i % 500 == 0 then
                progressScope:setCaption(LOC(
                    "$$$/StyleAI/PruneDatabase/GatheringProgress=Checking photos (^1 of ^2)...",
                    tostring(i),
                    tostring(#allPhotos)
                ))
                LrTasks.yield()
                LrTasks.sleep(0.01)
            end

            -- Uses the robust stable ID logic
            local globalPhotoId = Util.getGlobalPhotoIdForPhoto(photo)
            if globalPhotoId then
                table.insert(validPhotoIds, globalPhotoId)
            end
        end

        if #validPhotoIds == 0 then
            progressScope:done()
            LrDialogs.message(
                LOC "$$$/StyleAI/PruneDatabase/AbortedTitle=Aborted",
                LOC "$$$/StyleAI/PruneDatabase/AbortedMsg=No valid photos found in catalog to retain. Aborting prune to prevent accidental data loss.",
                "critical"
            )
            return
        end

        progressScope:setCaption(LOC "$$$/StyleAI/PruneDatabase/Sending=Cleaning StyleAI records...")
        progressScope:setPortionComplete(50, 100)

        local results, apiErr = SearchIndexAPI.pruneDatabase(validPhotoIds)
        progressScope:done()

        if apiErr then
            ErrorHandler.handleError(LOC "$$$/StyleAI/PruneDatabase/FailedTitle=Database Cleanup Failed", apiErr)
            return
        end

        local msg
        if results and type(results) == "table" then
            local deleted = results.deleted or 0
            local disassociated = results.disassociated or 0
            local checked = results.checked or 0
            msg = LOC(
                "$$$/StyleAI/PruneDatabase/CompleteMessage=A backup was created before cleanup.\n\nChecked: ^1 photos\nRemoved: ^2 orphaned records\nDisassociated: ^3 records from this catalog",
                tostring(checked),
                tostring(deleted),
                tostring(disassociated)
            )
        else
            msg = LOC "$$$/StyleAI/PruneDatabase/CompleteSimple=Database cleanup completed successfully. A backup was created before cleanup."
        end

        LrDialogs.message(LOC "$$$/StyleAI/PruneDatabase/CompleteTitle=Cleanup Complete", msg, "info")
    end)
end

return TaskPruneDatabase
