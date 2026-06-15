local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'

LrTasks.startAsyncTask(function()
    local catalog = LrApplication.activeCatalog()
    local photo = catalog:getTargetPhoto()
    if not photo then return end
    
    catalog:withWriteAccessDo("Test Snapshot", function()
        photo:applyDevelopSettings({Exposure = 1.0})
    end)
    LrDialogs.message("Done", "Tested.")
end)
