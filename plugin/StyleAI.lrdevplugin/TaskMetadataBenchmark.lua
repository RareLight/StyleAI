local MetadataBenchmark = require("MetadataBenchmark")

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("metadataBenchmarkTask", function(ctx)
		local ok, err = LrTasks.pcall(function() MetadataBenchmark.run(ctx) end)
		if not ok then
			log:error("Metadata benchmark failed unexpectedly: " .. tostring(err))
			LrDialogs.message(
				LOC("$$$/StyleAI/MetadataBenchmark/UnexpectedTitle=Benchmark Error"),
				LOC("$$$/StyleAI/MetadataBenchmark/Unexpected=The benchmark stopped safely because of an unexpected error. No metadata was written to Lightroom.\n\n^1\n\nRedeploy the current plug-in if this message identifies an unavailable Lua or Lightroom function.", tostring(err)),
				"critical"
			)
		end
	end)
end)
