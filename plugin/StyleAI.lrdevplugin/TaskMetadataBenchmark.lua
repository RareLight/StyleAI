local MetadataBenchmark = require("MetadataBenchmark")
local DeveloperOptions = require("DeveloperOptions")

local TaskMetadataBenchmark = {}

function TaskMetadataBenchmark.run()
	LrTasks.startAsyncTask(function()
		LrFunctionContext.callWithContext("metadataBenchmarkTask", function(ctx)
			if not DeveloperOptions.requireEnabled() then return end
			MetadataBenchmark.run(ctx)
		end)
	end)
end

return TaskMetadataBenchmark
