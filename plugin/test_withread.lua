local LrApplication = import 'LrApplication'
local catalog = LrApplication.activeCatalog()
local ok, err = pcall(function()
    catalog:withReadAccessDo("test", function() end)
end)
print("ok:", ok, "err:", err)
