-- Developer-only blinded comparison spike using caller-prepared disposable copies.

local LrApplication = import("LrApplication")
local LrBinding = import("LrBinding")
local LrDate = import("LrDate")
local LrDialogs = import("LrDialogs")
local LrFunctionContext = import("LrFunctionContext")
local LrTasks = import("LrTasks")
local LrView = import("LrView")

local Defaults = require("Defaults")
local DevelopEditManager = require("DevelopEditManager")
local SearchIndexAPI = require("APISearchIndex")
local UIFactory = require("UIFactory")
local Util = require("Util")
local WorkCoordinator = require("WorkCoordinator")

local function restoreCopies(catalog, left, leftSettings, right, rightSettings)
	local lease, leaseError = WorkCoordinator.acquire("catalog_write")
	if not lease then
		return false, leaseError
	end
	local ok, result = LrTasks.pcall(function()
		catalog:withWriteAccessDo(
			LOC("$$$/StyleAI/Refinement/Restore=Restore disposable refinement previews"),
			function()
				left:applyDevelopSettings(leftSettings)
				right:applyDevelopSettings(rightSettings)
			end,
			Defaults.catalogWriteAccessOptions
		)
	end)
	WorkCoordinator.release(lease)
	return ok, result
end

local function selectedPairs(catalog)
	local photos = catalog:getTargetPhotos() or {}
	local groups = {}
	local order = {}
	for _, photo in ipairs(photos) do
		local isCopy = photo:getRawMetadata("isVirtualCopy") == true
		local inferenceId = photo:getPropertyForPlugin(_PLUGIN, "aiEditInferenceId")
		if not isCopy or type(inferenceId) ~= "string" or inferenceId == "" then
			return nil,
				LOC(
					"$$$/StyleAI/Refinement/SelectionInvalid=Every selected item must be a disposable virtual copy carrying a tracked StyleAI edit."
				)
		end
		if not groups[inferenceId] then
			groups[inferenceId] = {}
			table.insert(order, inferenceId)
		end
		table.insert(groups[inferenceId], photo)
	end
	if #order == 0 then
		return nil, LOC("$$$/StyleAI/Refinement/SelectionEmpty=Select paired disposable virtual copies first.")
	end
	for _, inferenceId in ipairs(order) do
		if #groups[inferenceId] ~= 2 then
			return nil,
				LOC(
					"$$$/StyleAI/Refinement/SelectionPairs=Select exactly two disposable virtual copies for each tracked AI edit. Each pair must carry the same StyleAI inference ID."
				)
		end
	end
	return { groups = groups, inferenceIds = order }
end

local function setupDialog(ctx, pairCount)
	local f = LrView.osFactory()
	local props = LrBinding.makePropertyTable(ctx)
	props.mode = "quick"
	props.goal = math.min(pairCount, 10)
	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/Refinement/SetupTitle=Developer Style Refinement Test"),
		contents = UIFactory.DialogColumn(f, {
			bind_to_object = props,
			width = 680,
			UIFactory.Notice(f, {
				kind = "warning",
				title = LOC(
					"$$$/StyleAI/Refinement/DeveloperWarning=Developer prototype: use only paired disposable virtual copies. StyleAI restores their Develop settings after every comparison, records choices locally, and does not change active styles or training examples."
				),
			}),
			UIFactory.FormRow(f, {
				label = LOC("$$$/StyleAI/Refinement/Mode=Mode:"),
				f:popup_menu({
					value = LrView.bind("mode"),
					width = 430,
					items = {
						{
							title = LOC(
								"$$$/StyleAI/Refinement/Quick=Quick Refinement — compare learned-target strengths"
							),
							value = "quick",
						},
						{
							title = LOC(
								"$$$/StyleAI/Refinement/Correction=Correction Validation — compare your recorded correction with the model output"
							),
							value = "correction_validation",
						},
					},
				}),
			}),
			UIFactory.FormRow(f, {
				label = LOC("$$$/StyleAI/Refinement/Goal=Maximum comparisons:"),
				f:popup_menu({
					value = LrView.bind("goal"),
					items = {
						{ title = tostring(math.min(pairCount, 5)), value = math.min(pairCount, 5) },
						{ title = tostring(math.min(pairCount, 10)), value = math.min(pairCount, 10) },
						{ title = tostring(pairCount), value = pairCount },
					},
				}),
			}),
			UIFactory.HelpText(f, {
				title = LOC(
					"$$$/StyleAI/Refinement/PairCount=^1 eligible copy pair(s) are selected. Variants are blinded and choices are evaluation evidence only.",
					tostring(pairCount)
				),
			}),
		}),
		actionVerb = LOC("$$$/StyleAI/Refinement/Start=Start Comparisons"),
		cancelVerb = LOC("$$$/StyleAI/common/Cancel=Cancel"),
	})
	if result ~= "ok" then
		return nil
	end
	return { mode = props.mode, goal = tonumber(props.goal) or pairCount }
end

local function comparisonDialog(ctx, left, right, ordinal, total)
	local f = LrView.osFactory()
	local props = LrBinding.makePropertyTable(ctx)
	props.choice = "skip"
	props.keepable = false
	local result = LrDialogs.presentModalDialog({
		title = LOC(
			"$$$/StyleAI/Refinement/CompareTitle=Style Refinement Comparison ^1 of ^2",
			tostring(ordinal),
			tostring(total)
		),
		contents = UIFactory.DialogColumn(f, {
			bind_to_object = props,
			width = 820,
			UIFactory.Notice(f, {
				kind = "info",
				title = LOC(
					"$$$/StyleAI/Refinement/Blinded=Compare the rendered results without trying to identify which variant came from which source."
				),
			}),
			f:row({
				spacing = 20,
				f:column({
					f:static_text({ title = LOC("$$$/StyleAI/Refinement/SideA=Option A"), font = "<system/bold>" }),
					f:catalog_photo({ photo = left, width = 380, height = 380 }),
				}),
				f:column({
					f:static_text({ title = LOC("$$$/StyleAI/Refinement/SideB=Option B"), font = "<system/bold>" }),
					f:catalog_photo({ photo = right, width = 380, height = 380 }),
				}),
			}),
			f:row({
				spacing = 14,
				f:radio_button({
					title = LOC("$$$/StyleAI/Refinement/PreferA=Prefer A"),
					value = LrView.bind("choice"),
					checked_value = "prefer_a",
				}),
				f:radio_button({
					title = LOC("$$$/StyleAI/Refinement/PreferB=Prefer B"),
					value = LrView.bind("choice"),
					checked_value = "prefer_b",
				}),
				f:radio_button({
					title = LOC("$$$/StyleAI/Refinement/Equivalent=Both acceptable / no meaningful difference"),
					value = LrView.bind("choice"),
					checked_value = "equivalent",
				}),
			}),
			f:row({
				spacing = 14,
				f:radio_button({
					title = LOC("$$$/StyleAI/Refinement/Neither=Neither is satisfactory"),
					value = LrView.bind("choice"),
					checked_value = "neither",
				}),
				f:radio_button({
					title = LOC("$$$/StyleAI/Refinement/Skip=Skip"),
					value = LrView.bind("choice"),
					checked_value = "skip",
				}),
			}),
			f:checkbox({
				value = LrView.bind("keepable"),
				title = LOC(
					"$$$/StyleAI/Refinement/Keepable=I would keep my preferred result without further correction"
				),
			}),
			UIFactory.HelpText(f, {
				title = LOC(
					"$$$/StyleAI/Refinement/KeepableHelp=The keepable signal is saved only when A or B is preferred."
				),
			}),
		}),
		actionVerb = LOC("$$$/StyleAI/Refinement/SaveChoice=Save Choice"),
		cancelVerb = LOC("$$$/StyleAI/Refinement/SaveExit=Save and Exit"),
		resizable = true,
	})
	if result ~= "ok" then
		return nil
	end
	return {
		choice = props.choice,
		winner_keepable = (props.choice == "prefer_a" or props.choice == "prefer_b") and props.keepable or nil,
	}
end

local function currentSettings(groups, inferenceIds)
	local result = {}
	for _, inferenceId in ipairs(inferenceIds) do
		result[inferenceId] = groups[inferenceId][1]:getDevelopSettings()
	end
	return result
end

local function summaryMessage(summary)
	local counts = summary.choice_counts or {}
	return LOC(
		"$$$/StyleAI/Refinement/Summary=Saved ^1 of ^2 comparison(s).\n\nPrefer A: ^3\nPrefer B: ^4\nEquivalent: ^5\nNeither: ^6\nSkipped: ^7\n\nThis evidence is local and evaluation-only. No active style or training example changed.",
		tostring(summary.answered_count or 0),
		tostring(summary.pair_count or 0),
		tostring(counts.prefer_a or 0),
		tostring(counts.prefer_b or 0),
		tostring(counts.equivalent or 0),
		tostring(counts.neither or 0),
		tostring(counts.skip or 0)
	)
end

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("styleRefinementPrototype", function(ctx)
		local ok, taskError = LrTasks.pcall(function()
			local catalog = LrApplication.activeCatalog()
			local selection, selectionError = selectedPairs(catalog)
			if not selection then
				LrDialogs.message(
					LOC("$$$/StyleAI/Refinement/NotStarted=Style Refinement Not Started"),
					selectionError,
					"warning"
				)
				return
			end
			local options = setupDialog(ctx, #selection.inferenceIds)
			if not options then
				return
			end
			if not Util.waitForServerDialog({ suppressProgressDialog = false }) then
				return
			end
			local success, session = SearchIndexAPI.createStyleRefinementSession({
				mode = options.mode,
				inference_ids = selection.inferenceIds,
				current_settings = options.mode == "correction_validation"
						and currentSettings(selection.groups, selection.inferenceIds)
					or nil,
				requested_count = options.goal,
			})
			if not success then
				error(tostring(session))
			end
			if not session.session_id then
				LrDialogs.message(
					LOC("$$$/StyleAI/Refinement/NoPairsTitle=No Refinement Comparisons Available"),
					LOC(
						"$$$/StyleAI/Refinement/NoPairs=No selected edit produced a safe, authoritative comparison. Excluded: ^1",
						JSON:encode(session.exclusions or {})
					),
					"info"
				)
				return
			end
			while true do
				local pairOk, pair = SearchIndexAPI.getNextStyleRefinementPair(session.session_id)
				if not pairOk then
					error(tostring(pair))
				end
				if not pair then
					break
				end
				local copies = selection.groups[pair.inference_id]
				if not copies or #copies ~= 2 then
					error("Selected disposable copies no longer match this refinement pair")
				end
				local leftSettings = copies[1]:getDevelopSettings()
				local rightSettings = copies[2]:getDevelopSettings()
				local pairRunOk, choice = LrTasks.pcall(function()
					local leftOk, leftWarnings = DevelopEditManager.applyPreviewRecipe(copies[1], pair.recipe_a)
					local rightOk, rightWarnings = DevelopEditManager.applyPreviewRecipe(copies[2], pair.recipe_b)
					if not leftOk or not rightOk then
						error(table.concat(leftWarnings or rightWarnings or {}, "\n"))
					end
					LrTasks.yield()
					LrTasks.sleep(0.1)
					return comparisonDialog(ctx, copies[1], copies[2], pair.ordinal, session.pair_count)
				end)
				local restored, restoreError = restoreCopies(catalog, copies[1], leftSettings, copies[2], rightSettings)
				if not restored then
					error("Could not restore disposable preview copies: " .. tostring(restoreError))
				end
				if not pairRunOk then
					error(tostring(choice))
				end
				if not choice then
					break
				end
				choice.pair_id = pair.pair_id
				choice.idempotency_key = "lightroom:"
					.. tostring(session.session_id)
					.. ":"
					.. tostring(pair.pair_id)
					.. ":"
					.. tostring(LrDate.currentTime())
				local stored, storeError = SearchIndexAPI.recordStyleRefinementChoice(session.session_id, choice)
				if not stored then
					error(tostring(storeError))
				end
			end
			local finalized, summary = SearchIndexAPI.finalizeStyleRefinementSession(session.session_id)
			if not finalized then
				error(tostring(summary))
			end
			LrDialogs.message(
				LOC("$$$/StyleAI/Refinement/Complete=Refinement Evidence Saved"),
				summaryMessage(summary),
				"info"
			)
		end)
		if not ok then
			log:error("Style refinement prototype failed: " .. tostring(taskError))
			LrDialogs.message(
				LOC("$$$/StyleAI/Refinement/Failed=Style Refinement Failed"),
				tostring(taskError),
				"critical"
			)
		end
	end)
end)
