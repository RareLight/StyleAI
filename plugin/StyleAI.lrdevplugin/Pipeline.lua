local LrTasks = import("LrTasks")
local LrApplication = import("LrApplication")

Pipeline = {}

--- Executes a function for each photo in a sequence, handling progress updates and error capture.
-- Ideal for simpler tasks that don't require complex parallel batching or user interruption.
--
-- @param photos table The array of LrPhoto objects.
-- @param progressScope LrProgressScope The active progress scope.
-- @param options table Configuration (e.g. { requireWriteAccess = false, titlePrefix = "Processing" })
-- @param processFn function The callback processFn(photo, index, total, catalog). Must return (success, resultOrError)
-- @return table Summary of execution: { successCount, errorCount, errors = { "PhotoName: err" } }
function Pipeline.runSequentialBatch(photos, progressScope, options, processFn)
    options = options or {}
    local total = #photos
    local catalog = LrApplication.activeCatalog()
    
    local summary = {
        successCount = 0,
        errorCount = 0,
        errors = {}
    }
    
    if total == 0 then return summary end
    
    local titlePrefix = options.titlePrefix or "Processing"
    
    for i, photo in ipairs(photos) do
        if progressScope and progressScope:isCanceled() then
            break
        end
        
        local fileName = "Photo"
        LrTasks.pcall(function() fileName = photo:getFormattedMetadata("fileName") or "Photo" end)
        
        if progressScope then
            progressScope:setCaption(string.format("%s %s (%d of %d)", titlePrefix, fileName, i, total))
            progressScope:setPortionComplete(i - 1, total)
        end
        
        local success, resultOrErr = false, "Unknown error"
        
        if options.requireWriteAccess then
            catalog:withPrivateWriteAccessDo(function()
                -- LrTasks.pcall allows yielding inside the write access block
                success, resultOrErr = LrTasks.pcall(processFn, photo, i, total, catalog)
            end, options.writeAccessTimeout or { timeout = 30 })
        else
            success, resultOrErr = LrTasks.pcall(processFn, photo, i, total, catalog)
        end
        
        if success and resultOrErr ~= false then
            summary.successCount = summary.successCount + 1
        else
            summary.errorCount = summary.errorCount + 1
            table.insert(summary.errors, fileName .. ": " .. tostring(resultOrErr))
        end
        
        -- Yield to ensure the Lightroom UI remains responsive even if processFn is purely synchronous
        LrTasks.yield()
    end
    
    if progressScope then
        progressScope:setPortionComplete(total, total)
    end
    
    return summary
end

return Pipeline
