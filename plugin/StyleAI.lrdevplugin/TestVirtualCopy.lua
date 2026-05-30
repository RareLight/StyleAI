local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'

LrTasks.startAsyncTask(function()
    local catalog = LrApplication.activeCatalog()
    local photo = catalog:getTargetPhoto()
    if not photo then return end
    
    catalog:withWriteAccessDo("Test Virtual Copy", function()
        catalog:createVirtualCopies({photo})
    end)
    
    -- Try to find it
    local target = catalog:getTargetPhoto()
    LrDialogs.message("Done", "Selected: " .. tostring(target:getFormattedMetadata('fileName')))
end)
