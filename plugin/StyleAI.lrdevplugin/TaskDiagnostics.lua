local LrTasks = import("LrTasks")

local Util = require("Util")
require("APISearchIndex")

local TaskDiagnostics = {}

function TaskDiagnostics.generateReport()
	LrTasks.startAsyncTask(function()
		local health = SearchIndexAPI.getDetailedHealth() or {}
		local backendHealth = SearchIndexAPI.getBackendHealth() or {}
		local versionInfo = SearchIndexAPI.getBackendVersion() or {}
		local details = table.concat({
			"Plugin version: " .. string.format(
				"%d.%d.%d (%d)",
				Info.MAJOR or 0,
				Info.MINOR or 0,
				Info.REVISION or 0,
				Info.BUILD or 0
			),
			"Service version: " .. tostring(versionInfo.backend_version or versionInfo.version or "Unavailable"),
			"Background service: " .. (health.backend and "Ready" or "Unavailable"),
			"Vision model: " .. (health.clip and "Ready" or "Unavailable"),
			"Ollama: " .. (health.ollama and "Available" or "Not detected"),
			"LM Studio: " .. (health.lmstudio and "Available" or "Not detected"),
			"Backend health: " .. JSON:encode(backendHealth),
		}, "\n")

		-- This creates a timestamped support folder containing report.txt plus
		-- every available StyleAI/plugin/provider log. It does not include the
		-- Lightroom catalog or original photos.
		Util.copyLogfilesToDesktop({ details = details })
	end)
end

return TaskDiagnostics
