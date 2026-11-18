-- Compatibility entry point for older callers. Updates never replace running
-- plug-in or backend files: install a matching pair from the platform ZIP.
local LrHttp = import("LrHttp")

local function runUpdate()
	LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/releases/latest")
end

return { runUpdate = runUpdate }
