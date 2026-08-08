-- Shared polling for catalog-local editing-policy rebuilds.

local SearchIndexAPI = require("APISearchIndex")

local StyleDiscovery = {}

function StyleDiscovery.waitForCompletion(onUpdate, maxPolls)
	for _ = 1, (maxPolls or 3600) do
		local success, discovery = SearchIndexAPI.discoveryStatus()
		if success then
			if onUpdate then onUpdate(discovery) end
			if discovery.status == "succeeded" then
				return true, discovery.generation or {}
			end
			if discovery.status == "failed" then
				return false, discovery.error or "Unknown discovery error"
			end
		end
		LrTasks.sleep(1)
	end
	return false, "Discovery status timed out"
end

return StyleDiscovery
