local function shutdownApp(doneFunc, progressFunc)
	LrTasks.startAsyncTask(function()
		if prefs.shutdownServerOnExit then
			-- Gracefully shut down the entire backend server when Lightroom exits.
			-- The server will be restarted automatically the next time Lightroom opens.
			LrTasks.pcall(function()
				SearchIndexAPI.shutdownServer({
					graceSeconds = 8,
					forceWaitSeconds = 5,
					pollIntervalSeconds = 0.5,
					shutdownRequestTimeoutSeconds = 5,
				})
			end)
		else
			-- Keep server alive but free heavy models from memory.
			LrTasks.pcall(function()
				SearchIndexAPI.unloadResources()
			end)
		end
		doneFunc()
	end)
end

return {
	LrShutdownFunction = shutdownApp,
}
