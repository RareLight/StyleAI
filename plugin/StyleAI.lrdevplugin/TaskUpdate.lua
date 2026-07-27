---@diagnostic disable: undefined-global
---luacheck: globals TaskUpdate UpdateCheck SearchIndexAPI log LrFunctionContext LrProgressScope LrDialogs LrTasks LrPathUtils LrApplication LrHttp

--[[
TaskUpdate.lua

Handles the code-only in-place update flow for StyleAI.
Delegates the actual file operations to the Backend server and the external Updater GUI.

Flow:
  1. Fetch and validate the update manifest
  2. Show a confirmation dialog
  3. Send update request to backend (triggers the external Updater GUI)
  4. Inform user to close Lightroom — no public SDK API exists to quit Lightroom.
--]]

require("UpdateCheck")

TaskUpdate = {}

--- Formats a byte size for human display (KB / MB).
local function formatSize(bytes)
	if bytes >= 1024 * 1024 then
		return string.format("%.1f MB", bytes / (1024 * 1024))
	end
	return string.format("%.0f KB", bytes / 1024)
end

--- Main entry point. Accepts the release info table from UpdateCheck.getLatestReleaseInfo().
function TaskUpdate.runUpdate(releaseInfo)
	if not releaseInfo or not releaseInfo.manifest_url then
		LrDialogs.message(
			LOC("$$$/StyleAI/TaskUpdate/ErrorTitle=Update Error"),
			LOC(
				"$$$/StyleAI/TaskUpdate/NoManifestError=No code-only update manifest found. Please download the full installer from the releases page."
			),
			"critical"
		)
		return
	end

	LrTasks.startAsyncTask(function()
		-- Step 1: Fetch manifest
		local manifest = UpdateCheck.fetchManifest(releaseInfo.manifest_url)
		if not manifest then
			LrDialogs.message(
				LOC("$$$/StyleAI/TaskUpdate/ErrorTitle=Update Error"),
				LOC(
					"$$$/StyleAI/TaskUpdate/ManifestFetchError=Could not download the update manifest. Please check your internet connection."
				),
				"critical"
			)
			return
		end

		-- Step 2b: Breaking-changes guard — full installer required
		if manifest.breaking_changes then
			local version = manifest.version or releaseInfo.tag_name or "?"
			local releaseUrl = manifest.release_url
			local btn = LrDialogs.confirm(
				LOC("$$$/StyleAI/TaskUpdate/BreakingChangesTitle=Full Installer Required"),
				LOC(
					"$$$/StyleAI/TaskUpdate/BreakingChangesRequired=Version ^1 requires a full reinstall because it includes changes to the backend dependencies. Please download the installer for your platform from the releases page.",
					version
				),
				LOC("$$$/StyleAI/PluginInfo/DownloadNow=Download now"),
				LOC("$$$/StyleAI/common/Cancel=Cancel")
			)
			if btn == "ok" and releaseUrl then
				LrHttp.openUrlInBrowser(releaseUrl)
			end
			return
		end

		local version = manifest.version or releaseInfo.tag_name or "?"
		local totalSize = manifest.total_size_bytes or 0
		local pluginCount = (manifest.file_counts or {}).plugin or 0
		local backendCount = (manifest.file_counts or {}).backend_src or 0

		-- Step 3: Confirmation dialog
		local detail = LOC(
			"$$$/StyleAI/TaskUpdate/ConfirmMsgBackend=The backend will download and replace the code files. You should close Lightroom once the process finishes."
		) .. "\n\n" .. LOC("$$$/StyleAI/TaskUpdate/PluginFiles=Plugin files:") .. " " .. tostring(pluginCount) .. "   " .. LOC(
			"$$$/StyleAI/TaskUpdate/BackendFiles=Backend files:"
		) .. " " .. tostring(backendCount) .. "   (" .. formatSize(totalSize) .. ")"

		local btn = LrDialogs.confirm(
			LOC("$$$/StyleAI/TaskUpdate/ConfirmTitle=Install Update ^1?", version),
			detail,
			LOC("$$$/StyleAI/TaskUpdate/Install=Install"),
			LOC("$$$/StyleAI/common/Cancel=Cancel")
		)

		if btn ~= "ok" then
			return
		end

		-- Step 4: Apply update via backend (triggers the external GUI)
		local ok, result = SearchIndexAPI.applyUpdate(manifest)

		if not ok then
			log:error("TaskUpdate: backend update failed to start: " .. tostring(result))
			LrDialogs.message(
				LOC("$$$/StyleAI/TaskUpdate/ErrorTitle=Update Error"),
				LOC(
					"$$$/StyleAI/TaskUpdate/UpdateFailed=The update could not be started:\n\n^1",
					tostring(result or LOC("$$$/StyleAI/common/UnknownError=Unknown error"))
				),
				"critical"
			)
			return
		end

		-- Step 5: Brief heads-up before Lightroom shuts down automatically.
		log:info("TaskUpdate: update to " .. version .. " triggered successfully")
		LrDialogs.message(
			LOC("$$$/StyleAI/TaskUpdate/SuccessTitle=Update Starting"),
			LOC(
				"$$$/StyleAI/TaskUpdate/ExternalUpdaterMsg=Lightroom will now close to allow the update to complete. Restart it once the updater window shows 'Finished'."
			)
		)
		LrApplication.shutdown()
	end)
end

return TaskUpdate
