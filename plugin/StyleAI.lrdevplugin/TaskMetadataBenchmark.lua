local MetadataBenchmark = require("MetadataBenchmark")

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("metadataBenchmarkTask", function(ctx)
		MetadataBenchmark.run(ctx)
	end)
end)
