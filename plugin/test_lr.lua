local LrApplication = import 'LrApplication'
local catalog = LrApplication.activeCatalog()
catalog:withReadAccessDo("test", function() end)
