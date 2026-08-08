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

			local props = LrBinding.makePropertyTable(ctx)
			props.outcome = "accepted"
			local f = LrView.osFactory()
			local contents = f:column({
				bind_to_object = props,
				spacing = f:control_spacing(),
				f:static_text({
					title = LOC(
						"$$$/StyleAI/EditOutcome/Prompt=Choose one outcome for ^1 tracked selected photo(s). The same outcome will be recorded for all of them. This records feedback only and never changes or undoes Develop settings.",
						tostring(#tracked)
					),
					fill_horizontal = 1,
					wrap = true,
				}),
				f:radio_button({
					value = LrView.bind("outcome"),
					checked_value = "accepted",
					title = LOC("$$$/StyleAI/EditOutcome/Accepted=Keep AI Edit — I kept the modeled edit unchanged"),
				}),
				f:radio_button({
					value = LrView.bind("outcome"),
					checked_value = "modified_and_kept",
					title = LOC("$$$/StyleAI/EditOutcome/Modified=Modified and Kept — I adjusted the edit and kept the result"),
				}),
				f:radio_button({
					value = LrView.bind("outcome"),
					checked_value = "rejected",
					title = LOC("$$$/StyleAI/EditOutcome/Rejected=Not Useful — the result did not work for these photos"),
				}),
				f:static_text({
					title = LOC(
						"$$$/StyleAI/EditOutcome/Guidance=Not Useful is a feedback label. It does not undo an edit; use Lightroom Undo or History separately when needed."
					),
					fill_horizontal = 1,
					wrap = true,
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
				table.insert(items, {
					edit_inference_id = trackedPhoto.inferenceId,
					outcome = props.outcome,
					current_settings = trackedPhoto.photo:getDevelopSettings(),
				})
			end
			local apiOk, response = SearchIndexAPI.recordStyleEditOutcomes(items)
			if not apiOk then
				error(tostring(response))
			end

			local successfulByInference = {}
			for _, stored in ipairs(response.photos or {}) do
				successfulByInference[stored.inference_id] = stored.outcome
			end
			if (response.stored or 0) > 0 then
				catalog:withPrivateWriteAccessDo(function()
					for _, trackedPhoto in ipairs(tracked) do
						local outcome = successfulByInference[trackedPhoto.inferenceId]
						if outcome then
							trackedPhoto.photo:setPropertyForPlugin(_PLUGIN, "aiEditStatus", outcome)
						end
					end
				end, Defaults.catalogWriteAccessOptions)
			end

			local summary = LOC(
				"$$$/StyleAI/EditOutcome/Summary=Saved ^1 review(s). ^2 could not be saved.",
				tostring(response.stored or 0),
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
