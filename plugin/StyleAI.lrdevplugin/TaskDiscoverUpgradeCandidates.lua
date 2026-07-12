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

		local f = LrView.osFactory()
		local bind = LrView.bind
		local share = LrView.share
		local props = LrBinding.makePropertyTable(ctx)

		props.isLoading = false
		props.statusMessage = ""
		props.styles = {}
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
		props.findAllEnabled = false

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
			local sName = s.style_name or "Unknown Style"
			local sProf = s.camera_profile or ""
			local fullName = sName
			if sProf ~= "" and sProf ~= "Default" and not string.find(string.lower(sName), string.lower(sProf), 1, true) then
				fullName = string.format("%s (%s)", sName, sProf)
			end
			props.detailName = fullName
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

			local hasAnyRecs = false
			for _, st in ipairs(props.styles or {}) do
				if #(st.recommended_photo_ids or {}) > 0 then
					hasAnyRecs = true
					break
				end
			end
			props.findAllEnabled = hasAnyRecs
		end

		props:addObserver("selectedStyleIndex", updateDetailView)
		props:addObserver("styles", updateDetailView)

		local function loadRecommendations()
			LrTasks.startAsyncTask(function()
				props.isLoading = true
				props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/ProgressWait=Waiting for StyleAI backend to load...")

				if not Util.waitForServerDialog({ suppressProgressDialog = true }) then
					props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/ErrorNoServer=Backend server unavailable.")
					props.isLoading = false
					return
				end

				props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/Progress=Discovering style upgrade candidates...")

				local success, results = SearchIndexAPI.getUpgradeRecommendations(100)

				if not success then
					props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/Error=Failed to get recommendations: ^1", tostring(results))
					props.isLoading = false
					return
				end

				local styles = (results and results.styles) or {}
				if #styles == 0 then
					props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/NoStylesMsg=All signature styles in your database are already fully upgraded (50+ examples), or no styles were found yet. Great job!")
					props.isLoading = false
					return
				end

				props.styles = styles

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

				props.isLoading = false
				props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/Loaded=Successfully loaded recommendations.")
			end)
		end

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
                                enabled = LrBinding.negativeOfKey("isLoading"),
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
											local coll = Util.addPhotosToUpgradeCandidatesCollection(photos, props.detailName)

											-- Switch to Library module first so setActiveSources / setSelectedPhotos are effective
											LrTasks.pcall(function()
												LrApplicationView.switchToModule("library")
											end)
											LrTasks.yield()
											LrTasks.sleep(0.2)

											if coll then
												local sourceOk = LrTasks.pcall(function()
													catalog:setActiveSources({ coll })
													LrTasks.sleep(0.15)
													catalog:setSelectedPhotos(photos[1], photos)
												end)
												if not sourceOk then
													-- Fallback: navigate to All Photographs, then select
													LrTasks.pcall(function()
														catalog:setActiveSources({ catalog.kAllPhotos })
														LrTasks.sleep(0.15)
														catalog:setSelectedPhotos(photos[1], photos)
													end)
												end
											else
												-- No collection created — select directly via All Photographs
												LrTasks.pcall(function()
													catalog:setActiveSources({ catalog.kAllPhotos })
													LrTasks.sleep(0.15)
													catalog:setSelectedPhotos(photos[1], photos)
												end)
											end
											LrDialogs.message(
												LOC("$$$/StyleAI/UpgradeAssistant/SelectedTitle=Photos Selected & Added to Collection"),
												string.format(LOC("$$$/StyleAI/UpgradeAssistant/SelectedMsg=Added %d recommended candidate photos to collection '%s' (under set 'StyleAI') and selected them in Library. You can now easily review them or train!"), #photos, props.detailName or "Style"),
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
					f:spacer({ height = 10 }),
					f:push_button({
						title = LOC("$$$/StyleAI/UpgradeAssistant/BtnFindAll=Find All & Create Collections for All Styles"),
						enabled = LrBinding.negativeOfKey("isLoading"),
						fill_horizontal = 1,
						action = function()
							LrTasks.startAsyncTask(function()
								local totalPhotosAdded = 0
								local stylesProcessed = 0
								local findAllProgress = LrProgressScope({
									title = LOC("$$$/StyleAI/UpgradeAssistant/FindAllProgress=Creating upgrade collections for all styles...")
								})
								local styleEntries = {}
								for _, s in ipairs(props.styles or {}) do
									local recIds = s.recommended_photo_ids or {}
									if #recIds > 0 then
										local sName = s.style_name or "Unknown Style"
										local sProf = s.camera_profile or ""
										local fullName = sName
										if sProf ~= "" and sProf ~= "Default" and not string.find(string.lower(sName), string.lower(sProf), 1, true) then
											fullName = string.format("%s (%s)", sName, sProf)
										end
										table.insert(styleEntries, { fullName = fullName, photoIds = recIds })
									end
								end
								local batchedResults = SearchIndexAPI.findPhotosBatchedByStyleMap(styleEntries, findAllProgress)
								local stylesData = {}
								for _, entry in ipairs(batchedResults) do
									if #(entry.photos or {}) > 0 then
										table.insert(stylesData, { styleName = entry.fullName, photos = entry.photos })
										totalPhotosAdded = totalPhotosAdded + #entry.photos
										stylesProcessed = stylesProcessed + 1
									end
								end
								
								if #stylesData > 0 then
									Util.addMultipleUpgradePhotosToCollections(stylesData, nil, findAllProgress)
								end
								
								findAllProgress:done()
								if stylesProcessed > 0 then
									LrDialogs.message(
										LOC("$$$/StyleAI/UpgradeAssistant/FindAllSuccessTitle=Upgrade Collections Created"),
										string.format(LOC("$$$/StyleAI/UpgradeAssistant/FindAllSuccessMsg=Successfully created collections for %d styles (added %d total candidate photos) under set 'StyleAI'. You can now review them in Lightroom Library!"), stylesProcessed, totalPhotosAdded),
										"info"
									)
									LrDialogs.stopModalWithResult(ctx, "ok")
								else
									LrDialogs.message(
										LOC("$$$/StyleAI/UpgradeAssistant/NoneFound=No Photos Found"),
										LOC("$$$/StyleAI/UpgradeAssistant/FindAllNoneMsg=Could not locate any recommended candidate photos in the active catalog."),
										"warning"
									)
								end
							end)
						end,
					}),
					f:spacer({ height = 5 }),
					f:static_text({
						title = bind("statusMessage"),
						fill_horizontal = 1,
						font = "<system/bold>"
					}),
				}),
			})
		end

		loadRecommendations()

		LrDialogs.presentModalDialog({
			title = LOC("$$$/StyleAI/UpgradeAssistant/DialogTitle=ML Style Upgrade Assistant"),
			contents = buildDialog(),
			actionVerb = LOC("$$$/StyleAI/common/Close=Close"),
		})

		log:info("Discover Style Upgrade Candidates task finished")
	end)
end)
