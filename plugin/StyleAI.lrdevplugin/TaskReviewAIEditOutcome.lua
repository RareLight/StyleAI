-- Records explicit user judgments for selected AI-edited photos. This task
-- never changes Develop settings; Undo and rejection remain separate actions.

local LrApplication = import("LrApplication")
local LrBinding = import("LrBinding")
local LrDialogs = import("LrDialogs")
local LrFunctionContext = import("LrFunctionContext")
local LrTasks = import("LrTasks")
local LrView = import("LrView")

local SearchIndexAPI = require("APISearchIndex")
local Util = require("Util")
local UIFactory = require("UIFactory")

local function reviewDisplayValue(outcome)
	if outcome == "accepted" then return "Kept" end
	if outcome == "modified_and_kept" then return "Modified and Kept" end
	if outcome == "rejected" then return "Not Useful" end
	return "Unreviewed"
end

local function stateDisplayValue(state)
	if state == "apply_confirmed" then return "Applied" end
	if state == "reverted" then return "Reverted" end
	if state == "diverged" then return "Modified" end
	return nil
end

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("reviewAIEditOutcome", function(ctx)
		local ok, taskError = LrTasks.pcall(function()
			if not Util.waitForServerDialog({ suppressProgressDialog = false }) then
				return
			end
			local catalog = LrApplication.activeCatalog()
			local photos = catalog:getTargetPhotos()
			if not photos or #photos == 0 then
				LrDialogs.message(
					LOC("$$$/StyleAI/EditOutcome/Title=Rate Selected AI Edits"),
					LOC("$$$/StyleAI/EditOutcome/NoPhotos=Select at least one AI-edited photo first."),
					"info"
				)
				return
			end

			local tracked = {}
			for index = 1, math.min(#photos, 100) do
				local photo = photos[index]
				local inferenceId = photo:getPropertyForPlugin(_PLUGIN, "aiEditInferenceId")
				if inferenceId and inferenceId ~= "" then
					table.insert(tracked, { photo = photo, inferenceId = tostring(inferenceId) })
				end
			end
			if #tracked == 0 then
				LrDialogs.message(
					LOC("$$$/StyleAI/EditOutcome/Title=Rate Selected AI Edits"),
					LOC("$$$/StyleAI/EditOutcome/NoTracked=The selected photos do not have tracked AI edits."),
					"info"
				)
				return
			end

			local inferenceIds = {}
			for _, trackedPhoto in ipairs(tracked) do
				table.insert(inferenceIds, trackedPhoto.inferenceId)
			end
			local statusOk, statusResponse = SearchIndexAPI.getStyleEditOutcomeStatuses(inferenceIds)
			if not statusOk then error(tostring(statusResponse)) end
			local statusByInference = {}
			local alreadyReviewedCount = 0
			for _, status in ipairs(statusResponse.photos or {}) do
				statusByInference[status.inference_id] = status
				if status.reviewed then alreadyReviewedCount = alreadyReviewedCount + 1 end
			end
			-- Opportunistically backfill the separated metadata for older tracked edits.
			catalog:withPrivateWriteAccessDo(function()
				for _, trackedPhoto in ipairs(tracked) do
					local status = statusByInference[trackedPhoto.inferenceId]
					if status and status.applied then
						trackedPhoto.photo:setPropertyForPlugin(_PLUGIN, "aiEditApplied", "Yes")
					end
					if status and status.reviewed then
						trackedPhoto.photo:setPropertyForPlugin(
							_PLUGIN,
							"aiEditReview",
							reviewDisplayValue(status.outcome)
						)
					end
				end
			end, Defaults.catalogWriteAccessOptions)

			local props = LrBinding.makePropertyTable(ctx)
			props.outcome = "accepted"
			props.includePreviouslyReviewed = false
			local f = LrView.osFactory()
			local outcomeControls = {
				title = LOC("$$$/StyleAI/EditOutcome/Outcome=Outcome"),
				f:radio_button({
					value = LrView.bind("outcome"),
					checked_value = "accepted",
					title = LOC("$$$/StyleAI/EditOutcome/Accepted=Keep AI Edit — unchanged"),
				}),
				f:radio_button({
					value = LrView.bind("outcome"),
					checked_value = "modified_and_kept",
					title = LOC("$$$/StyleAI/EditOutcome/Modified=Modified and Kept — adjusted, then kept"),
				}),
				f:radio_button({
					value = LrView.bind("outcome"),
					checked_value = "rejected",
					title = LOC("$$$/StyleAI/EditOutcome/Rejected=Not Useful — did not work for these photos"),
				}),
			}
			local contents = UIFactory.DialogColumn(f, {
				bind_to_object = props,
				width = 620,
				spacing = f:control_spacing(),
				UIFactory.Notice(f, {
					kind = "info",
					title = LOC(
						"$$$/StyleAI/EditOutcome/Prompt=^1 tracked photo(s) selected; ^2 already reviewed. Previously reviewed photos are skipped by default. This saves feedback only and does not change Develop settings.",
						tostring(#tracked),
						tostring(alreadyReviewedCount)
					),
				}),
				UIFactory.SettingsGroup(f, outcomeControls),
				alreadyReviewedCount > 0 and f:checkbox({
					value = LrView.bind("includePreviouslyReviewed"),
					title = LOC(
						"$$$/StyleAI/EditOutcome/IncludeReviewed=Include ^1 previously reviewed photo(s) and record a corrected review",
						tostring(alreadyReviewedCount)
					),
				}) or f:spacer({ height = 0 }),
				UIFactory.HelpText(f, {
					title = LOC(
						"$$$/StyleAI/EditOutcome/Guidance=Not Useful does not undo an edit. Use Lightroom Undo or History when needed."
					),
				}),
			})
			local result = LrDialogs.presentModalDialog({
				title = LOC("$$$/StyleAI/EditOutcome/Title=Rate Selected AI Edits"),
				contents = contents,
				actionVerb = LOC("$$$/StyleAI/EditOutcome/Save=Save Review"),
				resizable = true,
			})
			if result ~= "ok" then
				return
			end

			local items = {}
			for _, trackedPhoto in ipairs(tracked) do
				local status = statusByInference[trackedPhoto.inferenceId]
				if props.includePreviouslyReviewed or not (status and status.reviewed) then
					table.insert(items, {
						edit_inference_id = trackedPhoto.inferenceId,
						outcome = props.outcome,
						current_settings = trackedPhoto.photo:getDevelopSettings(),
					})
				end
			end
			if #items == 0 then
				LrDialogs.message(
					LOC("$$$/StyleAI/EditOutcome/Title=Rate Selected AI Edits"),
					LOC("$$$/StyleAI/EditOutcome/AllReviewed=All selected tracked photos are already reviewed. Enable Include previously reviewed photos only when you intend to record corrected reviews."),
					"info"
				)
				return
			end
			local apiOk, response = SearchIndexAPI.recordStyleEditOutcomes(
				items,
				not props.includePreviouslyReviewed
			)
			if not apiOk then
				error(tostring(response))
			end

			if #(response.photos or {}) > 0 then
				catalog:withPrivateWriteAccessDo(function()
					local trackedByInference = {}
					for _, trackedPhoto in ipairs(tracked) do
						trackedByInference[trackedPhoto.inferenceId] = trackedPhoto.photo
					end
					for _, stored in ipairs(response.photos or {}) do
						local photo = trackedByInference[stored.inference_id]
						if photo and stored.outcome then
							photo:setPropertyForPlugin(_PLUGIN, "aiEditStatus", stored.outcome)
							photo:setPropertyForPlugin(_PLUGIN, "aiEditApplied", "Yes")
							photo:setPropertyForPlugin(
								_PLUGIN,
								"aiEditReview",
								reviewDisplayValue(stored.outcome)
							)
							local displayState = stateDisplayValue(stored.state)
							if displayState then
								photo:setPropertyForPlugin(_PLUGIN, "aiEditState", displayState)
							end
						end
					end
				end, Defaults.catalogWriteAccessOptions)
			end

			local summary = LOC(
				"$$$/StyleAI/EditOutcome/Summary=New reviews: ^1\nCorrected reviews: ^2\nUnchanged duplicates: ^3\nPreviously reviewed and skipped: ^4\nCould not be saved: ^5",
				tostring(response.new_reviews or 0),
				tostring(response.corrected_reviews or 0),
				tostring(response.unchanged_reviews or 0),
				tostring(response.skipped_reviewed or 0),
				tostring(response.failed or 0)
			)
			if response.failures and response.failures[1] then
				summary = summary
					.. "\n\n"
					.. LOC(
						"$$$/StyleAI/EditOutcome/FirstIssue=First issue: ^1",
						tostring(response.failures[1].error or "")
					)
			end
			if #photos > 100 then
				summary = summary
					.. "\n\n"
					.. LOC("$$$/StyleAI/EditOutcome/Bounded=Only the first 100 selected photos were reviewed.")
			end
			LrDialogs.message(LOC("$$$/StyleAI/EditOutcome/Title=Rate Selected AI Edits"), summary, "info")
		end)

		if not ok then
			log:error("AI edit outcome review failed: " .. tostring(taskError))
			LrDialogs.message(
				LOC("$$$/StyleAI/EditOutcome/Failed=Could Not Save Edit Review"),
				tostring(taskError),
				"critical"
			)
		end
	end)
end)
