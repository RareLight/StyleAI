-- TaskDiscoverUpgradeCandidates.lua
-- Discover Style Upgrade Candidates using Active Style Upgrade Assistant.
-- Surfaces under-trained styles and recommends candidate photos to train them.

local LrApplication = import("LrApplication")
local LrDialogs = import("LrDialogs")
local LrFunctionContext = import("LrFunctionContext")
local LrProgressScope = import("LrProgressScope")
local LrTasks = import("LrTasks")
local LrView = import("LrView")
local LrBinding = import("LrBinding")
local LrDate = import("LrDate")

local SearchIndexAPI = require("APISearchIndex")
local Util = require("Util")
local ErrorHandler = require("ErrorHandler")

local log = import("LrLogger")("StyleAI")

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("DiscoverUpgradeCandidatesTask", function(ctx)
		LrDialogs.attachErrorDialogToFunctionContext(ctx)
		log:info("Discover Style Upgrade Candidates task started")

		if not Util.waitForServerDialog() then
			log:warn("Discover Style Upgrade Candidates aborted: backend server unavailable")
			return
		end

		local progressScope = LrProgressScope({
			title = LOC("$$$/StyleAI/UpgradeAssistant/Progress=Discovering style upgrade candidates...")
		})
		progressScope:setIndeterminate()

		local success, results = SearchIndexAPI.getUpgradeRecommendations(15)
		progressScope:done()

		if not success then
			ErrorHandler.handleError("Failed to get style upgrade recommendations: " .. tostring(results))
			return
		end

		local styles = (results and results.styles) or {}
		if #styles == 0 then
			LrDialogs.message(
				LOC("$$$/StyleAI/UpgradeAssistant/NoStylesTitle=No Styles Found"),
				LOC("$$$/StyleAI/UpgradeAssistant/NoStylesMsg=No signature styles were found in your database. Use 'Learn My Styles' to create your first style!"),
				"info"
			)
			return
		end

		local f = LrView.osFactory()
		local bind = LrView.bind
		local share = LrView.share
		local props = LrBinding.makePropertyTable(ctx)

		props.styles = styles
		props.listItems = {}
		props.selectedStyleIndex = 0

		props.detailName = ""
		props.detailProfile = ""
		props.detailTier = ""
		props.detailNeeded = ""
		props.detailExplanation = ""
		props.detailButtonTitle = ""
		props.detailButtonEnabled = false
		props.detailRecommendedIds = {}

		local function updateDetailView()
			local rawIdx = props.selectedStyleIndex
			local idx = tonumber(rawIdx)
			if type(rawIdx) == "table" then
				if rawIdx.value then
					idx = tonumber(rawIdx.value)
				elseif rawIdx[1] ~= nil then
					idx = tonumber(rawIdx[1])
				else
					for _, item in ipairs(props.listItems or {}) do
						if item == rawIdx or item.title == rawIdx.title then
							idx = item.value
							break
						end
					end
				end
			end
			if not idx or idx < 1 or not props.styles or idx > #props.styles then
				props.detailName = LOC("$$$/StyleAI/UpgradeAssistant/SelectPrompt=Select a style to view details.")
				props.detailProfile = ""
				props.detailTier = ""
				props.detailNeeded = ""
				props.detailExplanation = ""
				props.detailButtonTitle = ""
				props.detailButtonEnabled = false
				props.detailRecommendedIds = {}
				return
			end

			local s = props.styles[idx]
			props.detailName = s.style_name or "Unknown Style"
			props.detailProfile = string.format(LOC("$$$/StyleAI/UpgradeAssistant/ProfileFmt=Camera Profile: %s"), s.camera_profile or "Default")
			
			local current = tonumber(s.current_count) or 0
			local tierName = "🔴 Undertrained / Pillar 1 (PCA Baseline)"
			if current >= 50 then
				tierName = "🌟 ML Predictive (Best) / Pillar 3 (Elastic Net)"
			elseif current >= 15 then
				tierName = "⭐️ ML Predictive (Good) / Pillar 2 (Supervised PLS)"
			elseif current >= 3 then
				tierName = "🟡 Basic / Pillar 1 (PCA Baseline)"
			end
			props.detailTier = string.format(LOC("$$$/StyleAI/UpgradeAssistant/TierFmt=Current ML Tier: %s (%d examples)"), tierName, current)

			props.detailRecommendedIds = s.recommended_photo_ids or {}
			local recCount = #props.detailRecommendedIds

			if s.is_highest_tier or current >= 50 then
				props.detailNeeded = LOC("$$$/StyleAI/UpgradeAssistant/FullyUpgraded=Status: Fully Upgraded! (50+ training examples)")
				props.detailExplanation = LOC("$$$/StyleAI/UpgradeAssistant/HighestTierExpl=This style has reached the highest ML tier (Pillar 3 Elastic Net with L1 sparsity). No further upgrade is required!")
				props.detailButtonTitle = LOC("$$$/StyleAI/UpgradeAssistant/BtnHighest=Highest Tier Reached")
				props.detailButtonEnabled = false
			else
				props.detailNeeded = string.format(LOC("$$$/StyleAI/UpgradeAssistant/NextTierFmt=Target: %s (needs %d more)"), s.target_tier or "", tonumber(s.needed_count) or 0)
				if recCount > 0 then
					props.detailExplanation = string.format(LOC("$$$/StyleAI/UpgradeAssistant/RecsFoundExpl=Found %d recommended candidate photos in your search index using Farthest Point Sampling (Max-Min diversity) and Star Rating quality scoring! Click below to select them in Lightroom Library to review and train."), recCount)
					props.detailButtonTitle = string.format(LOC("$$$/StyleAI/UpgradeAssistant/BtnSelectFmt=Select %d Recommended Photos in Library"), recCount)
					props.detailButtonEnabled = true
				else
                    props.detailExplanation = LOC("$$$/StyleAI/UpgradeAssistant/NoRecsExpl=We need more examples for this style, but no suitable candidate photos matching this camera profile were found in your database. Try indexing more photos in Lightroom!")
					props.detailButtonTitle = LOC("$$$/StyleAI/UpgradeAssistant/BtnNoRecs=No Candidate Photos Found")
					props.detailButtonEnabled = false
				end
			end
		end

		props:addObserver("selectedStyleIndex", updateDetailView)
		props:addObserver("styles", updateDetailView)

		local items = {}
		for i, s in ipairs(styles) do
			local count = tonumber(s.current_count) or 0
			local badge = "🔴 Undertrained"
			if count >= 50 then
				badge = "🌟 ML Predictive (Best)"
			elseif count >= 15 then
				badge = "⭐️ ML Predictive (Good)"
			elseif count >= 3 then
				badge = "🟡 Basic"
			end
			local recCount = #(s.recommended_photo_ids or {})
			local label = string.format("%s • %s [%s • N=%d] (+%d recs)", s.style_name or "Unknown", s.camera_profile or "Default", badge, count, recCount)
			table.insert(items, { title = label, value = i })
		end
		props.listItems = items
		if #items > 0 then
			props.selectedStyleIndex = items[1].value
		else
			props.selectedStyleIndex = 0
		end
		updateDetailView()

		local function buildDialog()
			return f:column({
				bind_to_object = props,
				spacing = f:control_spacing(),
				f:group_box({
					title = LOC("$$$/StyleAI/UpgradeAssistant/DialogSubTitle=Style Upgrade Recommendations"),
					fill_horizontal = 1,
					fill_vertical = 1,
					f:row({
						spacing = f:control_spacing(),
						f:column({
							spacing = f:control_spacing(),
							f:static_text({ title = LOC("$$$/StyleAI/UpgradeAssistant/StylesListHeader=Styles Needing Upgrades:"), font = "<system/bold>" }),
							f:simple_list({
                                items = bind("listItems"),
                                value = bind("selectedStyleIndex"),
                                allows_multiple_selection = false,
                                height_in_lines = 12,
                                width = 400,
							}),
						}),
						f:column({
							spacing = f:control_spacing(),
							f:static_text({ title = LOC("$$$/StyleAI/UpgradeAssistant/DetailsHeader=Upgrade Details:"), font = "<system/bold>" }),
							f:static_text({ title = bind("detailName"), font = "<system/bold>", width = 400 }),
							f:static_text({ title = bind("detailProfile"), width = 400 }),
							f:static_text({ title = bind("detailTier"), width = 400 }),
							f:static_text({ title = bind("detailNeeded"), width = 400, font = "<system/bold>" }),
							f:static_text({ title = bind("detailExplanation"), width = 400, height_in_lines = 5, wrap = true }),
							f:spacer({ height = 10 }),
							f:push_button({
								title = bind("detailButtonTitle"),
								enabled = bind("detailButtonEnabled"),
								width = 400,
								action = function()
									if not props.detailRecommendedIds or #props.detailRecommendedIds == 0 then
										return
									end
									LrTasks.startAsyncTask(function()
										local selectProgress = LrProgressScope({
											title = LOC("$$$/StyleAI/UpgradeAssistant/SelectingProgress=Selecting photos in Lightroom Library...")
										})
										local photos = SearchIndexAPI.findPhotosByPhotoIds(props.detailRecommendedIds, selectProgress)
										selectProgress:done()

										if #photos > 0 then
											local catalog = LrApplication.activeCatalog()
											catalog:setSelectedPhotos(photos[1], photos)
											LrDialogs.message(
												LOC("$$$/StyleAI/UpgradeAssistant/SelectedTitle=Photos Selected"),
												string.format(LOC("$$$/StyleAI/UpgradeAssistant/SelectedMsg=Selected %d recommended photos in Lightroom Library. You can now review them or go to File > Plug-in Extras > Learn My Styles to train!"), #photos),
												"info"
											)
											LrDialogs.stopModalWithResult(ctx, "ok")
										else
											LrDialogs.message(
												LOC("$$$/StyleAI/UpgradeAssistant/NoneFound=No Photos Found"),
												LOC("$$$/StyleAI/UpgradeAssistant/NoneFoundMsg=Could not locate the recommended photos in the active catalog. They may have been deleted or moved."),
												"warning"
											)
										end
									end)
								end
							}),
						}),
					}),
				}),
			})
		end

		LrDialogs.presentModalDialog({
			title = LOC("$$$/StyleAI/UpgradeAssistant/DialogTitle=AI Style Upgrade Assistant"),
			contents = buildDialog(),
			actionVerb = LOC("$$$/StyleAI/common/Close=Close"),
		})

		log:info("Discover Style Upgrade Candidates task finished")
	end)
end)
