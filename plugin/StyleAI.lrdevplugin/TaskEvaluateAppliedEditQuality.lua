-- Runs the catalog-local applied-edit evaluator and presents a bounded summary.

local LrBinding = import("LrBinding")
local LrDate = import("LrDate")
local LrDialogs = import("LrDialogs")
local LrFunctionContext = import("LrFunctionContext")
local LrProgressScope = import("LrProgressScope")
local LrShell = import("LrShell")
local LrTasks = import("LrTasks")
local LrView = import("LrView")

local SearchIndexAPI = require("APISearchIndex")
local UIFactory = require("UIFactory")
local Util = require("Util")

local function count(value)
	return tostring(math.floor(tonumber(value) or 0))
end

local function percent(value)
	if value == nil then return LOC("$$$/StyleAI/AppliedEditEvaluation/Unavailable=Not available") end
	return string.format("%.1f%%", (tonumber(value) or 0) * 100)
end

local function generationSummary(generations)
	local lines = {}
	for _, generation in ipairs(generations or {}) do
		local id = tostring(generation.generation_id or "")
		if #id > 12 then id = string.sub(id, 1, 12) end
		local evidence
		if (tonumber(generation.reviews_needed) or 0) > 0 then
			evidence = LOC(
				"$$$/StyleAI/AppliedEditEvaluation/NeedsReviews=needs ^1 more review(s)",
				count(generation.reviews_needed)
			)
		else
			evidence = LOC("$$$/StyleAI/AppliedEditEvaluation/Comparable=comparison evidence available")
		end
		table.insert(lines, LOC(
			"$$$/StyleAI/AppliedEditEvaluation/GenerationLine=^1: ^2 reviewed, ^3 accepted unchanged, ^4 modified and kept, ^5 rejected — ^6",
			id,
			count(generation.reviewed),
			count(generation.accepted),
			count(generation.modified_and_kept),
			count(generation.rejected),
			evidence
		))
	end
	if #lines == 0 then
		return LOC("$$$/StyleAI/AppliedEditEvaluation/NoGenerationEvidence=No reviewed generation evidence is available yet.")
	end
	return table.concat(lines, "\n")
end

local function correctionSummary(families)
	local lines = {}
	for _, family in ipairs(families or {}) do
		table.insert(lines, LOC(
			"$$$/StyleAI/AppliedEditEvaluation/CorrectionLine=^1: ^2 reviewed correction(s), normalized error ^3",
			tostring(family.family or ""),
			count(family.count),
			string.format("%.3f", tonumber(family.mean_normalized_absolute_error) or 0)
		))
	end
	if #lines == 0 then
		return LOC("$$$/StyleAI/AppliedEditEvaluation/NoCorrections=No accepted correction readbacks are available yet.")
	end
	return table.concat(lines, "\n")
end

local function presentSummary(ctx, response)
	local summary = response.summary or {}
	local dataset = summary.dataset or {}
	local outcomes = summary.user_outcomes or {}
	local reliability = summary.application_reliability or {}
	local calibration = summary.confidence_calibration or {}
	local warningText = table.concat(summary.warnings or {}, "\n")
	if warningText == "" then
		warningText = LOC("$$$/StyleAI/AppliedEditEvaluation/NoWarnings=No evaluation warnings were detected.")
	end
	local f = LrView.osFactory()
	local contents = UIFactory.DialogColumn(f, {
		width = 680,
		UIFactory.Notice(f, {
			kind = "info",
			title = LOC("$$$/StyleAI/AppliedEditEvaluation/EvaluationOnly=Evaluation only — this report did not retrain, activate, or change any learned style."),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/AppliedEditEvaluation/Evidence=Evidence"),
			UIFactory.HelpText(f, { title = LOC(
				"$$$/StyleAI/AppliedEditEvaluation/EvidenceText=Applied edits: ^1\nExplicitly reviewed: ^2\nReview coverage: ^3\nAll tracked inferences: ^4",
				count(dataset.applied_inferences), count(dataset.reviewed_inferences),
				percent(dataset.review_coverage), count(dataset.inferences)
			) }),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/AppliedEditEvaluation/Outcomes=Outcomes"),
			UIFactory.HelpText(f, { title = LOC(
				"$$$/StyleAI/AppliedEditEvaluation/OutcomesText=Accepted unchanged: ^1 (^2)\nModified and kept: ^3\nRejected: ^4 (^5)\nKept overall: ^6",
				count(outcomes.accepted), percent(outcomes.acceptance_rate),
				count(outcomes.modified_and_kept), count(outcomes.rejected),
				percent(outcomes.rejection_rate), percent(outcomes.kept_rate)
			) }),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/AppliedEditEvaluation/Reliability=Application and confidence reliability"),
			UIFactory.HelpText(f, { title = LOC(
				"$$$/StyleAI/AppliedEditEvaluation/ReliabilityText=Confirmed applications: ^1\nUnconfirmed applications: ^2\nApplication failures: ^3\nConfirmed rate: ^4\nConfidence samples: ^5\nCalibration error: ^6",
				count(reliability.apply_confirmed), count(reliability.apply_unconfirmed),
				count(reliability.apply_failed), percent(reliability.confirmed_rate),
				count(calibration.sample_count),
				calibration.expected_calibration_error == nil and LOC("$$$/StyleAI/AppliedEditEvaluation/Unavailable=Not available")
					or string.format("%.3f", tonumber(calibration.expected_calibration_error) or 0)
			) }),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/AppliedEditEvaluation/Generations=Generation evidence"),
			UIFactory.HelpText(f, { title = generationSummary(summary.generations) }),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/AppliedEditEvaluation/Corrections=Largest correction families"),
			UIFactory.HelpText(f, { title = correctionSummary(summary.top_correction_families) }),
		}),
		UIFactory.Notice(f, { kind = #(summary.warnings or {}) > 0 and "warning" or "info", title = warningText }),
	})
	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/AppliedEditEvaluation/Title=Applied Edit Quality"),
		contents = contents,
		actionVerb = LOC("$$$/StyleAI/AppliedEditEvaluation/Reveal=Reveal Full JSON Report"),
		cancelVerb = LOC("$$$/StyleAI/common/Close=Close"),
		resizable = true,
	})
	if result == "ok" and response.report_path then
		LrShell.revealInShell(response.report_path)
	end
end

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("evaluateAppliedEditQuality", function(ctx)
		local ok, taskError = LrTasks.pcall(function()
			if not Util.waitForServerDialog({ suppressProgressDialog = false }) then return end
			local props = LrBinding.makePropertyTable(ctx)
			props.scope = prefs.appliedEditEvaluationLastRunAt and "since_last" or "all"
			props.minimumReviews = 30
			local f = LrView.osFactory()
			local scopeItems = {
				{ title = LOC("$$$/StyleAI/AppliedEditEvaluation/AllHistory=All recorded edit history"), value = "all" },
			}
			if prefs.appliedEditEvaluationLastRunAt then
				table.insert(scopeItems, {
					title = LOC("$$$/StyleAI/AppliedEditEvaluation/SinceLast=Only edits recorded since the last successful report"),
					value = "since_last",
				})
			end
			local result = LrDialogs.presentModalDialog({
				title = LOC("$$$/StyleAI/AppliedEditEvaluation/RunTitle=Evaluate Applied Edit Quality"),
				contents = UIFactory.DialogColumn(f, {
					bind_to_object = props,
					width = 620,
					UIFactory.Notice(f, {
						kind = "info",
						title = LOC("$$$/StyleAI/AppliedEditEvaluation/RunExplanation=This developer assessment reads catalog-local edit history and writes a versioned report. It never changes active styles, thresholds, Lightroom metadata, or Develop settings."),
					}),
					UIFactory.FormRow(f, {
						label = LOC("$$$/StyleAI/AppliedEditEvaluation/History=History:"),
						f:popup_menu({ value = LrView.bind("scope"), items = scopeItems, width = 390 }),
					}),
					UIFactory.FormRow(f, {
						label = LOC("$$$/StyleAI/AppliedEditEvaluation/Minimum=Minimum reviews per generation:"),
						f:popup_menu({
							value = LrView.bind("minimumReviews"),
							items = {
								{ title = "15", value = 15 }, { title = "30", value = 30 },
								{ title = "50", value = 50 }, { title = "100", value = 100 },
							},
						}),
					}),
				}),
				actionVerb = LOC("$$$/StyleAI/AppliedEditEvaluation/Run=Run Evaluation"),
				cancelVerb = LOC("$$$/StyleAI/common/Cancel=Cancel"),
			})
			if result ~= "ok" then return end
			local runStarted = LrDate.timeToW3CDate(LrDate.currentTime())
			local progress = LrProgressScope({ title = LOC("$$$/StyleAI/AppliedEditEvaluation/Progress=Evaluating applied edits...") })
			progress:setCancelable(true)
			local success, response = SearchIndexAPI.evaluateAppliedEdits({
				created_after = props.scope == "since_last" and prefs.appliedEditEvaluationLastRunAt or nil,
				confidence_bin_count = 5,
				minimum_reviewed_per_generation = tonumber(props.minimumReviews) or 30,
			}, progress)
			progress:done()
			if not success then
				if response == "canceled" then return end
				error(tostring(response))
			end
			prefs.appliedEditEvaluationLastRunAt = runStarted
			presentSummary(ctx, response)
		end)
		if not ok then
			log:error("Applied-edit evaluation failed: " .. tostring(taskError))
			LrDialogs.message(
				LOC("$$$/StyleAI/AppliedEditEvaluation/Failed=Could Not Evaluate Applied Edits"),
				tostring(taskError),
				"critical"
			)
		end
	end)
end)
