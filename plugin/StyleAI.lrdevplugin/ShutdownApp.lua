local LrPrefs = import("LrPrefs")

-- Removed redundant getIsMac() function since MAC_ENV is globally available.

local function safeLogTrace(msg)
	if _G.log and type(_G.log.trace) == "function" then
		_G.log:trace(msg)
	end
end

local LrSystemInfo = import("LrSystemInfo")
local MAC_ENV = false
pcall(function()
	MAC_ENV = LrSystemInfo.osVersion():sub(1, 3):lower() == "mac"
end)

local function shutdownApp(doneFunc, progressFunc)
	-- Lightroom's teardown callback must acknowledge completion immediately. Never
	-- use LrHttp, LrTasks, polling, or a synchronous backend request here.
	-- The backend owns its own bounded shutdown after receiving this signal.
	local port = 19819
	local shutdownOnExit = true
	if _G.prefs and _G.prefs.serverPort then
		port = _G.prefs.serverPort
		shutdownOnExit = _G.prefs.shutdownServerOnExit ~= false
	else
		local pcallOk, pcallPrefs = pcall(LrPrefs.prefsForPlugin)
		if pcallOk and pcallPrefs then
			port = pcallPrefs.serverPort or port
			shutdownOnExit = pcallPrefs.shutdownServerOnExit ~= false
		end
	end

	if shutdownOnExit then
		local url = "http://127.0.0.1:" .. tostring(port) .. "/shutdown"
		if MAC_ENV then
			-- The outer shell exits immediately; the detached curl has a strict
			-- sub-second budget even if the local backend is already unhealthy.
			os.execute("(nohup /usr/bin/curl -X POST -s --connect-timeout 0.1 --max-time 0.25 " .. url .. " </dev/null >/dev/null 2>&1 &) >/dev/null 2>&1")
		else
			os.execute('start /B powershell -NoProfile -Command "Invoke-WebRequest -Method POST -Uri ' .. url .. ' -UseBasicParsing -TimeoutSec 1" >NUL 2>&1')
		end
		safeLogTrace("ShutdownApp: Detached backend shutdown signal launched.")
	else
		safeLogTrace("ShutdownApp: Backend shutdown disabled by preference.")
	end

	if type(doneFunc) == "function" then
		-- Native pcall does not involve Lightroom's task scheduler. This must be
		-- the final teardown operation and must never wait for the backend.
		pcall(doneFunc)
	end
end

return {
	LrShutdownFunction = shutdownApp,
}
