-- TaskDiscoverUpgradeCandidates.lua
-- Discover Style Upgrade Candidates using Active Style Upgrade Assistant.
-- Surfaces under-trained styles and recommends candidate photos to train them.

local LrApplication = import("LrApplication")
local LrApplicationView = import("LrApplicationView")
local LrDialogs = import("LrDialogs")
local LrFunctionContext = import("LrFunctionContext")
local LrProgressScope = import("LrProgressScope")
local LrTasks = import("LrTasks")
local LrView = import("LrView")
local LrBinding = import("LrBinding")

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

		-- State
		props.styles = {}
		props.listItems = {}
		props.selectedStyleIndex = 0
		props.selectedProfileFilter = "All Profiles"
		props.profileFilters = { { title = LOC("$$$/StyleAI/StyleCatalog/AllProfiles=All Profiles"), value = "All Profiles" } }
		props.isLoading = false
		props.statusMessage = ""

		props.detailName = ""
		props.detailGenre = ""
		props.detailProfile = ""
		props.detailTier = ""
		props.detailNeeded = ""
		props.detailExplanation = ""
		props.detailButtonTitle = ""
		props.detailButtonEnabled = false
		props.detailRecommendedIds = {}
		props.findAllEnabled = false
		props.selectedActionEnabled = false
		props.allActionEnabled = false

		local function refreshActionState()
			props.selectedActionEnabled = props.detailButtonEnabled and not props.isLoading
			props.allActionEnabled = props.findAllEnabled and not props.isLoading
		end

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
				props.detailButtonTitle = LOC("$$$/StyleAI/UpgradeAssistant/BtnNoRecs=No Candidate Photos Found")
				props.detailButtonEnabled = false
				props.detailRecommendedIds = {}
				return
			end

			local s = props.styles[idx]
			local sName = s.style_name or "Unknown Style"
			local sGenre = s.genre or "Unknown"
			local sProf = s.camera_profile or "Default"
			
			props.detailName = sName
			props.detailGenre = sGenre
			props.detailProfile = sProf
			
			local current = tonumber(s.current_count) or 0
			local tierName = "🔴 Undertrained / Pillar 1 (PCA Baseline)"
			if current >= 50 then
				tierName = "🌟 ML Predictive (Best) / Pillar 3 (Elastic Net)"
			elseif current >= 15 then
				tierName = "⭐️ ML Predictive (Good) / Pillar 2 (Supervised PLS)"
			elseif current >= 3 then
				tierName = "🟡 Basic / Pillar 1 (PCA Baseline)"
			end
			props.detailTier = string.format("%s (%d examples)", tierName, current)

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
					props.detailExplanation = string.format(LOC("$$$/StyleAI/UpgradeAssistant/RecsFoundExpl=Found %d recommended candidate photos in your search index using Farthest Point Sampling (Max-Min diversity) and Star Rating quality scoring! Click 'Show Candidate Photos' in toolbar to select them in Lightroom Library."), recCount)
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
		props:addObserver("isLoading", refreshActionState)
		props:addObserver("detailButtonEnabled", refreshActionState)
		props:addObserver("findAllEnabled", refreshActionState)

		local function updateListItems()
			local items = {}
			local filter = props.selectedProfileFilter or "All Profiles"
			for i, s in ipairs(props.styles or {}) do
				local profile = s.camera_profile or "Default"
				if filter == "All Profiles" or profile == filter then
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
					local name = s.style_name or "Unknown"
					local label = string.format("%s • %s • %d (+%d recs) • %s", profile, name, count, recCount, badge)
					table.insert(items, { title = label, value = i })
				end
			end
			props.listItems = items
			if #items > 0 then
				props.selectedStyleIndex = items[1].value
			else
				props.selectedStyleIndex = 0
			end
		end

		props:addObserver("selectedProfileFilter", updateListItems)

		local function loadRecommendations()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/ProgressWait=Waiting for StyleAI backend to load...")

			LrTasks.startAsyncTask(function()
				if not Util.waitForServerDialog({ suppressProgressDialog = true, skipHealthCheck = true }) then
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

				-- Build profile filters
				local uniqueProfiles = {}
				local profileCounts = {}
				for i, style in ipairs(props.styles) do
					local profile = style.camera_profile or "Default"
					if not uniqueProfiles[profile] then
						uniqueProfiles[profile] = true
						profileCounts[profile] = 1
					else
						profileCounts[profile] = profileCounts[profile] + 1
					end
				end

				local sortedProfiles = {}
				for profile, count in pairs(profileCounts) do
					table.insert(sortedProfiles, { profile = profile, count = count })
				end
				table.sort(sortedProfiles, function(a, b)
					if a.count == b.count then
						return a.profile < b.profile
					end
					return a.count > b.count
				end)

				local profileList = { { title = LOC("$$$/StyleAI/StyleCatalog/AllProfiles=All Profiles"), value = "All Profiles" } }
				for _, item in ipairs(sortedProfiles) do
					table.insert(profileList, { 
						title = string.format("%s (%d)", item.profile, item.count), 
						value = item.profile 
					})
				end
				props.profileFilters = profileList

				updateListItems()

				props.isLoading = false
				props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/LoadedCount=Loaded ^1 style upgrade candidates.", tostring(#props.styles))
			end)
		end

		-- Action: Show Candidate Photos for selected style
		local function showSelectedPhotos()
			local rawIdx = props.selectedStyleIndex
			local idx = tonumber(rawIdx)
			if type(rawIdx) == "table" then
				if rawIdx.value then idx = tonumber(rawIdx.value)
				elseif rawIdx[1] ~= nil then idx = tonumber(rawIdx[1]) end
			end
			if not idx or idx < 1 or not props.styles or idx > #props.styles then return end
			local s = props.styles[idx]
			local recIds = s.recommended_photo_ids or {}
			if #recIds == 0 then return end

			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/SelectingProgress=Selecting photos in Lightroom Library...")

			LrTasks.startAsyncTask(function()
				local selectProgress = LrProgressScope({
					title = LOC("$$$/StyleAI/UpgradeAssistant/SelectingProgress=Selecting photos in Lightroom Library...")
				})
				local photos = SearchIndexAPI.findPhotosByPhotoIds(recIds, selectProgress)
				local wasCanceled = selectProgress:isCanceled()
				selectProgress:done()
				if wasCanceled then
				props.statusMessage = LOC("$$$/StyleAI/common/Canceled=Canceled.")
				props.isLoading = false
				return
			end

				if #photos > 0 then
					local catalog = LrApplication.activeCatalog()
					local sName = s.style_name or "Unknown Style"
					local sProf = s.camera_profile or ""
					local fullName = sName
					if sProf ~= "" and sProf ~= "Default" and not string.find(string.lower(sName), string.lower(sProf), 1, true) then
						fullName = string.format("%s (%s)", sName, sProf)
					end
					local coll = Util.addPhotosToUpgradeCandidatesCollection(photos, fullName)

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
					props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/PhotosSelectedStatus=Candidate photos added to collection and selected in Library.")
					LrDialogs.message(
						LOC("$$$/StyleAI/UpgradeAssistant/SelectedTitle=Photos Selected & Added to Collection"),
						string.format(LOC("$$$/StyleAI/UpgradeAssistant/SelectedMsg=Added %d recommended candidate photos to collection '%s' (under set 'StyleAI') and selected them in Library. You can now easily review them or train!"), #photos, fullName),
						"info"
					)
					LrDialogs.stopModalWithResult(ctx, "ok")
				else
					props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/PhotosNotFoundStatus=Candidate photos could not be found in active catalog.")
					LrDialogs.message(
						LOC("$$$/StyleAI/UpgradeAssistant/NoneFound=No Photos Found"),
						LOC("$$$/StyleAI/UpgradeAssistant/NoneFoundMsg=Could not locate the recommended photos in the active catalog. They may have been deleted or moved."),
						"warning"
					)
				end
				props.isLoading = false
			end)
		end

		-- Action: Find All & Create Collections for All Styles
		local function showAllPhotos()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/FindAllProgress=Creating upgrade collections for all styles...")

			LrTasks.startAsyncTask(function()
				local totalPhotosAdded = 0
				local stylesProcessed = 0
				local findAllProgress = LrProgressScope({
					title = LOC("$$$/StyleAI/UpgradeAssistant/FindAllProgress=Creating upgrade collections for all styles...")
				})
				local styleEntries = {}
				for _, s in ipairs(props.styles or {}) do
					if findAllProgress:isCanceled() then break end
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
					if findAllProgress:isCanceled() then break end
					if #(entry.photos or {}) > 0 then
						table.insert(stylesData, { styleName = entry.fullName, photos = entry.photos })
						totalPhotosAdded = totalPhotosAdded + #entry.photos
						stylesProcessed = stylesProcessed + 1
					end
				end
				
				if not findAllProgress:isCanceled() and #stylesData > 0 then
					Util.addMultipleUpgradePhotosToCollections(stylesData, nil, findAllProgress)
				end
				
				local wasCanceled = findAllProgress:isCanceled()
				findAllProgress:done()
				if wasCanceled then
					props.statusMessage = LOC("$$$/StyleAI/common/Canceled=Canceled.")
					props.isLoading = false
					return
				end
				if stylesProcessed > 0 then
					props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/FindAllSuccessStatus=Created upgrade collections for all candidate styles.")
					LrDialogs.message(
						LOC("$$$/StyleAI/UpgradeAssistant/FindAllSuccessTitle=Upgrade Collections Created"),
						string.format(LOC("$$$/StyleAI/UpgradeAssistant/FindAllSuccessMsg=Successfully created collections for %d styles (added %d total candidate photos) under set 'StyleAI'. You can now review them in Lightroom Library!"), stylesProcessed, totalPhotosAdded),
						"info"
					)
					LrDialogs.stopModalWithResult(ctx, "ok")
				else
					props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/FindAllNoneStatus=Could not locate candidate photos in active catalog.")
					LrDialogs.message(
						LOC("$$$/StyleAI/UpgradeAssistant/NoneFound=No Photos Found"),
						LOC("$$$/StyleAI/UpgradeAssistant/FindAllNoneMsg=Could not locate any recommended candidate photos in the active catalog."),
						"warning"
					)
				end
				props.isLoading = false
			end)
		end

		-- Build Dialog
		local function buildDialog()
			return f:column({
				bind_to_object = props,
				spacing = f:control_spacing(),

				-- Title
				f:row({
					f:static_text({
						title = LOC("$$$/StyleAI/UpgradeAssistant/Title=ML Style Upgrade Assistant"),
						font = "bold",
						size = "large",
					}),
				}),

				-- Status bar
				f:row({
					f:static_text({
						title = bind("statusMessage"),
						width_in_chars = 80,
					}),
				}),

				-- Toolbar
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/UpgradeAssistant/ShowPhotos=Show Candidate Photos"),
						action = showSelectedPhotos,
						width = share("toolbarButton"),
						enabled = bind("selectedActionEnabled"),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/UpgradeAssistant/ShowAllPhotos=Show All Candidate Photos"),
						action = showAllPhotos,
						width = share("toolbarButton"),
						enabled = bind("allActionEnabled"),
					}),
				}),

				-- Style list
				f:group_box({
					title = LOC("$$$/StyleAI/UpgradeAssistant/StyleList=Styles Needing Upgrades"),
					fill_horizontal = 1,
					fill_vertical = 1,
					f:column({
						spacing = f:control_spacing(),
						fill_horizontal = 1,
						fill_vertical = 1,
						f:row({
							f:spacer({ fill_horizontal = 1 }),
							f:column({
								spacing = f:control_spacing(),
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/FilterByProfile=Filter by Profile:") }),
								f:popup_menu({
									items = bind("profileFilters"),
									value = bind("selectedProfileFilter"),
									width = 600,
								}),
								f:simple_list({
									items = bind("listItems"),
									value = bind("selectedStyleIndex"),
									allows_multiple_selection = false,
									height_in_lines = 12,
									width = 600,
								}),
							}),
							f:spacer({ fill_horizontal = 1 }),
						}),
					}),
				}),

				-- Style detail panel
				f:group_box({
					title = LOC("$$$/StyleAI/UpgradeAssistant/StyleDetails=Upgrade Details"),
					fill_horizontal = 1,
					f:row({
						f:column({
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailName=Name:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailName"), width = 250 }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailGenre=Genre:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailGenre"), width = 250 }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailProfile=Profile:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailProfile"), width = 250 }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/UpgradeAssistant/DetailTierLabel=ML Tier:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailTier"), width = 250 }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/UpgradeAssistant/DetailNeededLabel=Target:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailNeeded"), width = 250, font = "<system/bold>" }),
							}),
						}),
						f:column({
							f:static_text({ title = LOC("$$$/StyleAI/UpgradeAssistant/DetailExplanationLabel=Recommendation Details:"), font = "<system/bold>" }),
							f:static_text({ title = bind("detailExplanation"), width_in_chars = 40, height_in_lines = 6, wrap = true }),
						}),
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
