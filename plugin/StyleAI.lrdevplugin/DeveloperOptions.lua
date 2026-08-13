local DeveloperOptions = {}

local developerPrefs = import("LrPrefs").prefsForPlugin()
local DeveloperDialogs = import("LrDialogs")

function DeveloperOptions.isEnabled()
	return developerPrefs.enableDeveloperOptions == true
end

function DeveloperOptions.requireEnabled()
	if DeveloperOptions.isEnabled() then return true end
	DeveloperDialogs.message(
		LOC("$$$/StyleAI/DeveloperOptions/DisabledTitle=Developer Options Disabled"),
		LOC("$$$/StyleAI/DeveloperOptions/Disabled=Enable Developer Options in File > Plug-in Manager > StyleAI to reveal the developer tools."),
		"warning"
	)
	return false
end

return DeveloperOptions
