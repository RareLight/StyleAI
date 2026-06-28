local function shutdownApp(doneFunc, progressFunc)
	LrTasks.startAsyncTask(function()
		-- Gracefully shut down the entire backend server when Lightroom exits.
		-- The server will be restarted automatically the next time Lightroom opens.
		-- Do a completely synchronous, non-yielding OS call to shut down the backend.
		-- LrHttp yields, which causes deadlocks during Lightroom's teardown sequence.
		local port = prefs.serverPort or 19819
		local url = "http://127.0.0.1:" .. tostring(port) .. "/shutdown"
		
		if MAC_ENV then
			LrTasks.execute("curl -X POST -s " .. url .. " >/dev/null 2>&1 &")
		elseif WIN_ENV then
			LrTasks.execute('powershell -Command "Invoke-WebRequest -Method POST -Uri ' .. url .. ' -UseBasicParsing" >NUL 2>&1')
		end

		log:trace("ShutdownApp: Synchronous shutdown signal sent.")
		
		if type(doneFunc) == "function" then
			log:trace("ShutdownApp: Calling doneFunc()")
			doneFunc()
		else
			log:trace("ShutdownApp: doneFunc is " .. type(doneFunc) .. ", skipping")
		end
	end)
end

return {
	LrShutdownFunction = shutdownApp,
}
