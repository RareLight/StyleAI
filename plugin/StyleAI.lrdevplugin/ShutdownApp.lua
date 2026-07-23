local LrTasks = import("LrTasks")
local LrPrefs = import("LrPrefs")

local function getIsMac()
	if _G.MAC_ENV ~= nil then
		return _G.MAC_ENV
	end
	local ok, isMac = pcall(function()
		return import("LrSystemInfo").osVersion():sub(1, 3):lower() == "mac"
	end)
	return ok and isMac
end

local function safeLogTrace(msg)
	if _G.log and type(_G.log.trace) == "function" then
		_G.log:trace(msg)
	end
end

local function shutdownApp(doneFunc, progressFunc)
	-- Gracefully shut down the entire backend server when Lightroom exits.
	-- The server will be restarted automatically the next time Lightroom opens.
	-- Do a completely synchronous, non-yielding OS call to shut down the backend.
	-- LrHttp and LrTasks.startAsyncTask yield or wait on the scheduler, which causes deadlocks during Lightroom's teardown sequence.
	local port = 19819
	if _G.prefs and _G.prefs.serverPort then
		port = _G.prefs.serverPort
	else
		local pcallOk, pcallPrefs = pcall(LrPrefs.prefsForPlugin)
		if pcallOk and pcallPrefs and pcallPrefs.serverPort then
			port = pcallPrefs.serverPort
		end
	end

	local url = "http://127.0.0.1:" .. tostring(port) .. "/shutdown"

	local isMac = getIsMac()
	if isMac then
		LrTasks.execute("curl -X POST -s " .. url .. " >/dev/null 2>&1 &")
	else
		LrTasks.execute('powershell -Command "Invoke-WebRequest -Method POST -Uri ' .. url .. ' -UseBasicParsing" >NUL 2>&1')
	end

	safeLogTrace("ShutdownApp: Synchronous shutdown signal sent.")

	if type(doneFunc) == "function" then
		safeLogTrace("ShutdownApp: Calling doneFunc()")
		pcall(doneFunc)
	else
		safeLogTrace("ShutdownApp: doneFunc is " .. type(doneFunc) .. ", skipping")
	end
end

return {
	LrShutdownFunction = shutdownApp,
}
