---@diagnostic disable: undefined-global
---luacheck: globals UpdateCheck TaskUpdate log Info JSON MAC_ENV SearchIndexAPI LrHttp LrTasks LrDialogs

require("Info")

UpdateCheck = {}

-- ─────────────────────────────────────────────────────────────────────────────
-- Constants
-- ─────────────────────────────────────────────────────────────────────────────

UpdateCheck.releaseTagName = "v"
	.. tostring(Info.MAJOR)
	.. "."
	.. tostring(Info.MINOR)
	.. "."
	.. tostring(Info.REVISION)

-- ─────────────────────────────────────────────────────────────────────────────
-- Internal helpers
-- ─────────────────────────────────────────────────────────────────────────────

--- Parse a semver tag like "v2.15.0" into (major, minor, patch) numbers.
--- Returns nil, nil, nil if the tag cannot be parsed.
local function parseSemver(tag)
	local v = tag:gsub("^v", "")
	local major, minor, patch = v:match("^(%d+)%.(%d+)%.(%d+)")
	if major then
		return tonumber(major), tonumber(minor), tonumber(patch)
	end
	return nil, nil, nil
end

--- Returns true if latestTag is strictly newer than currentTag using semver.
--- Falls back to string inequality when either tag cannot be parsed.
local function semverIsNewer(latestTag, currentTag)
	if latestTag == currentTag then
		return false
	end
	local lMaj, lMin, lPat = parseSemver(latestTag)
	local cMaj, cMin, cPat = parseSemver(currentTag)
	if not lMaj or not cMaj then
		return latestTag ~= currentTag
	end
	if lMaj ~= cMaj then
		return lMaj > cMaj
	end
	if lMin ~= cMin then
		return lMin > cMin
	end
	return lPat > cPat
end

--- Fetch and parse JSON from a URL. Returns decoded table or nil.
local function fetchJson(url)
	local response, headers = LrHttp.get(url)
	if not headers or headers.status ~= 200 then
		log:warn("fetchJson: HTTP " .. tostring(headers and headers.status) .. " for " .. url)
		return nil
	end
	if not response or response == "" then
		log:warn("fetchJson: empty response for " .. url)
		return nil
	end
	local ok, decoded = LrTasks.pcall(function()
		return JSON:decode(response)
	end)
	if not ok or type(decoded) ~= "table" then
		log:warn("fetchJson: JSON parse failed for " .. url)
		return nil
	end
	return decoded
end

-- ─────────────────────────────────────────────────────────────────────────────
-- Public API
-- ─────────────────────────────────────────────────────────────────────────────

--- Fetch information about the latest release directly from GitHub.
--- Release builds are full platform ZIPs; never apply a remote patch manifest.
function UpdateCheck.getLatestReleaseInfo()
	local release = fetchJson("https://api.github.com/repos/RareLight/StyleAI/releases/latest")
	if not release then
		return nil
	end

	local tag = release.tag_name
	if not tag then
		return nil
	end

	local isNewer = semverIsNewer(tag, UpdateCheck.releaseTagName)

	return {
		tag_name = tag,
		release_url = release.html_url,
		is_newer = isNewer,
	}
end

-- ─────────────────────────────────────────────────────────────────────────────
-- UI Helpers
-- ─────────────────────────────────────────────────────────────────────────────

--- Manual update check trigger.
function UpdateCheck.checkForNewVersion()
	local info = UpdateCheck.getLatestReleaseInfo()
	if not info then
		LrDialogs.message(LOC("$$$/StyleAI/UpdateCheck/Error=Could not check for updates."))
		return nil
	end

	if info.is_newer then
		LrHttp.openUrlInBrowser(info.release_url or "https://github.com/RareLight/StyleAI/releases/latest")
	else
		LrDialogs.message(
			LOC(
				"$$$/StyleAI/UpdateCheck/LatestVersion=You're on the latest plugin version: ^1",
				UpdateCheck.releaseTagName
			)
		)
	end
	return nil
end

--- Non-blocking background check.
function UpdateCheck.checkForNewVersionInBackground()
	local info = UpdateCheck.getLatestReleaseInfo()
	if not info or not info.is_newer or prefs.lastPromptedRelease == info.tag_name then
		return nil
	end

	prefs.lastPromptedRelease = info.tag_name
	local btn = LrDialogs.confirm(
		LOC("$$$/StyleAI/UpdateCheck/NewVersionAvailableTitle=New Version Available"),
		LOC("$$$/StyleAI/UpdateCheck/CodeOnlyAvailableTitle=Update Available: ^1", info.tag_name),
		LOC("$$$/StyleAI/PluginInfo/DownloadNow=Download now"),
		LOC("$$$/StyleAI/common/Later=Later")
	)
	if btn == "ok" then
		LrHttp.openUrlInBrowser(info.release_url or "https://github.com/RareLight/StyleAI/releases/latest")
	end
	return nil
end

return UpdateCheck
