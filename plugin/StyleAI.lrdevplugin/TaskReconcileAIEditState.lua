-- Developer/manual QA task for reconciling selected Lightroom photos with
-- StyleAI's immutable edit inference history.

local LrApplication = import("LrApplication")
local LrDialogs = import("LrDialogs")
local LrTasks = import("LrTasks")

local SearchIndexAPI = require("APISearchIndex")
local Util = require("Util")

LrTasks.startAsyncTask(function()
	local ok, taskError = LrTasks.pcall(function()
		if not Util.waitForServerDialog({ suppressProgressDialog = false }) then
			return
		end

		local catalog = LrApplication.activeCatalog()
		local photos = catalog:getTargetPhotos()
		if not photos or #photos == 0 then
			LrDialogs.message(
				LOC("$$$/StyleAI/ReconcileEdits/Title=Reconcile AI Edit State"),
				LOC("$$$/StyleAI/ReconcileEdits/NoPhotos=Select at least one photo first."),
				"info"
			)
			return
		end

		local itemCount = math.min(#photos, 100)
		local items = {}
		local photoById = {}
		for index = 1, itemCount do
			local photo = photos[index]
			local photoId = Util.getGlobalPhotoIdForPhoto(photo, { skipCacheWrite = true })
			if photoId and photoId ~= "" then
				table.insert(items, {
					photo_id = photoId,
					current_settings = photo:getDevelopSettings(),
				})
				photoById[photoId] = photo
			end
		end

		if #items == 0 then
			error(LOC("$$$/StyleAI/ReconcileEdits/NoIds=No stable photo IDs could be resolved."))
		end
		local apiOk, response = SearchIndexAPI.reconcileStyleEditStates(items)
		if not apiOk then
			error(tostring(response))
		end

		local counts = { apply_confirmed = 0, reverted = 0, diverged = 0, untracked = 0 }
		local updates = {}
		for _, result in ipairs(response.photos or {}) do
			local state = result.state or "untracked"
			counts[state] = (counts[state] or 0) + 1
			local photo = photoById[result.photo_id]
			if photo and state ~= "untracked" then
				table.insert(updates, { photo = photo, state = state })
			end
		end

		if #updates > 0 then
			catalog:withPrivateWriteAccessDo(function()
				for _, update in ipairs(updates) do
					update.photo:setPropertyForPlugin(_PLUGIN, "aiEditStatus", update.state)
				end
			end, Defaults.catalogWriteAccessOptions)
		end

		local summary = LOC(
			"$$$/StyleAI/ReconcileEdits/Summary=Checked ^1 photo(s).\n\nStill applied: ^2\nReverted: ^3\nModified: ^4\nNo tracked edit: ^5",
			tostring(#items),
			tostring(counts.apply_confirmed),
			tostring(counts.reverted),
			tostring(counts.diverged),
			tostring(counts.untracked)
		)
		if #photos > itemCount then
			summary = summary
				.. "\n\n"
				.. LOC("$$$/StyleAI/ReconcileEdits/Bounded=Only the first 100 selected photos were checked.")
		end
		LrDialogs.message(LOC("$$$/StyleAI/ReconcileEdits/Title=Reconcile AI Edit State"), summary, "info")
	end)

	if not ok then
		log:error("AI edit reconciliation failed: " .. tostring(taskError))
		LrDialogs.message(
			LOC("$$$/StyleAI/ReconcileEdits/FailedTitle=Reconciliation Failed"),
			tostring(taskError),
			"critical"
		)
	end
end)
