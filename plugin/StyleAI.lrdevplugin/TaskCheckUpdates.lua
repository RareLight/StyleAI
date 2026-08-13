local LrTasks = import("LrTasks")

local UpdateCheck = require("UpdateCheck")

LrTasks.startAsyncTask(function()
	UpdateCheck.checkForNewVersion()
end)
