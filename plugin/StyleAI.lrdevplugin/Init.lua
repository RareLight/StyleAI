---@diagnostic disable: undefined-global

-- Global imports
_G.LrHttp = import("LrHttp")
_G.LrDate = import("LrDate")
_G.LrPathUtils = import("LrPathUtils")
_G.LrFileUtils = import("LrFileUtils")
_G.LrTasks = import("LrTasks")
_G.LrErrors = import("LrErrors")
_G.LrDialogs = import("LrDialogs")
_G.LrView = import("LrView")
_G.LrBinding = import("LrBinding")
_G.LrColor = import("LrColor")
_G.LrFunctionContext = import("LrFunctionContext")
_G.LrApplication = import("LrApplication")
_G.LrPrefs = import("LrPrefs")
_G.LrProgressScope = import("LrProgressScope")
_G.LrExportSession = import("LrExportSession")
_G.LrStringUtils = import("LrStringUtils")
_G.LrMD5 = import("LrMD5")
_G.LrLocalization = import("LrLocalization")
_G.LrShell = import("LrShell")
_G.LrSystemInfo = import("LrSystemInfo")
_G.LrApplicationView = import("LrApplicationView")
_G.LrDevelopController = import("LrDevelopController")

-- Global initializations (move early)
_G.prefs = _G.LrPrefs.prefsForPlugin()

_G.log = import("LrLogger")("StyleAI")
_G.prefs.logging = true
_G.log:enable("logfile")

-- OS environment detection
if _G.MAC_ENV == nil then
	local ok, isMac = pcall(function()
		return _G.LrSystemInfo.osVersion():sub(1, 3):lower() == "mac"
	end)
	_G.MAC_ENV = ok and isMac or false
end
if _G.WIN_ENV == nil then
	_G.WIN_ENV = not _G.MAC_ENV
end

-- Load modules early
_G.JSON = require("JSON")
require("Util")
require("Defaults")
require("MetadataManager")
require("KeywordConfigProvider")
require("PromptConfigProvider")
require("UpdateCheck")
require("ErrorHandler")
require("APISearchIndex")
require("PhotoSelector")
require("OnboardingWizard")

-- Initialize Settings
local SettingsManager = require("SettingsManager")
SettingsManager.initializeDefaults()

function _G.JSON.assert(v, message)
	if not v then
		log:error("JSON error: " .. (message or "assertion failed!"))
		error(message or "assertion failed!")
	end
	return v
end

-- Update check is now handled via Backend + Util.waitForServerDialog

-- if prefs.onboardingCompleted == nil then
-- 	Do not set to false yet, let the wizard trigger
-- end

LrTasks.startAsyncTask(function()
	-- Check if onboarding is needed
	-- if not prefs.onboardingCompleted then
	--     OnboardingWizard.show()
	-- end

	if SearchIndexAPI.startServer() then
		SearchIndexAPI.checkServerHealth()
	end
end)
