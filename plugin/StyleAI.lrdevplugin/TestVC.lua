local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'

LrTasks.startAsyncTask(function()
    local catalog = LrApplication.activeCatalog()
    local photo = catalog:getTargetPhoto()
    if not photo then return end
    
    catalog:withWriteAccessDo("Test VC", function()
        local original = photo:getDevelopSettings()
        photo:applyDevelopSettings({Exposure = 2.0})
        catalog:createVirtualCopies({photo})
        photo:applyDevelopSettings(original)
    end)
    LrDialogs.message("Done", "Tested VC trick.")
end)
