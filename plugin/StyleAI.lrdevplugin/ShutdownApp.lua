local function shutdownApp(doneFunc, progressFunc)
	LrTasks.startAsyncTask(function()
		-- Gracefully shut down the entire backend server when Lightroom exits.
		-- The server will be restarted automatically the next time Lightroom opens.
		LrTasks.pcall(function()
			SearchIndexAPI.shutdownServer({
				graceSeconds = 8,
				forceWaitSeconds = 5,
				pollIntervalSeconds = 0.5,
				shutdownRequestTimeoutSeconds = 1, -- Don't hang if backend is slow
				skipWait = true -- Let Lightroom quit immediately
			})
		end)
		doneFunc()
	end)
end

return {
	LrShutdownFunction = shutdownApp,
}
