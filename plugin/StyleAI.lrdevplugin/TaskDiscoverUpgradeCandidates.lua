-- TaskDiscoverUpgradeCandidates.lua
-- Find policy-specific training candidates for learned styles.
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
local StyleUI = require("StyleUI")
local UIFactory = require("UIFactory")

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
		props.detailDescriptorLabel = LOC("$$$/StyleAI/StyleCatalog/DetailGenre=Genre:")
		props.detailGenre = ""
		props.detailProfile = ""
		props.detailTier = ""
		props.detailNeeded = ""
		props.detailCoverage = ""
		props.showPolicyDetails = false
		props.detailExplanation = ""
		props.detailButtonTitle = ""
		props.detailButtonEnabled = false
		props.detailRecommendedIds = {}
		props.detailReviewId = ""
		props.detailPolicyId = ""
		props.findAllEnabled = false
		props.selectedActionEnabled = false
		props.allActionEnabled = false
		props.feedbackActionEnabled = false

		local function refreshActionState()
			props.selectedActionEnabled = props.detailButtonEnabled and not props.isLoading
			props.allActionEnabled = props.findAllEnabled and not props.isLoading
			props.feedbackActionEnabled = props.detailReviewId ~= "" and not props.isLoading
		end

		local function updateDetailView()
			local idx = StyleUI.resolveSelectedIndex(props.selectedStyleIndex, props.listItems)
			if not idx or idx < 1 or not props.styles or idx > #props.styles then
				props.detailName = LOC("$$$/StyleAI/UpgradeAssistant/SelectPrompt=Select a style to view details.")
				props.detailProfile = ""
				props.detailTier = ""
				props.detailNeeded = ""
				props.detailCoverage = ""
				props.showPolicyDetails = false
				props.detailExplanation = ""
				props.detailButtonTitle = LOC("$$$/StyleAI/UpgradeAssistant/BtnNoRecs=No Candidate Photos Found")
				props.detailButtonEnabled = false
				props.detailRecommendedIds = {}
				props.detailReviewId = ""
				props.detailPolicyId = ""
				return
			end

			local s = props.styles[idx]
			local sName = s.style_name or s.policy_name or LOC("$$$/StyleAI/common/UnknownStyle=Unknown Style")
			
			props.detailName = sName
			props.detailProfile = s.camera_profile or LOC("$$$/StyleAI/common/Default=Default")
			props.showPolicyDetails = true
			props.detailDescriptorLabel = LOC("$$$/StyleAI/UpgradeAssistant/PolicyCuesLabel=Policy cues:")
			local cueNames = {}
			for _, cue in ipairs(s.policy_descriptors or {}) do
				local cueName = type(cue) == "table" and cue.descriptor or cue
				if cueName and cueName ~= "" then
					table.insert(cueNames, tostring(cueName))
					if #cueNames >= 4 then break end
				end
			end
			props.detailGenre = #cueNames > 0 and table.concat(cueNames, ", ")
				or LOC("$$$/StyleAI/UpgradeAssistant/PolicyCuesPending=Learned visual/editing policy")
			
			local current = tonumber(s.current_count) or 0
			local tierName = s.local_correction_enabled and
				LOC("$$$/StyleAI/UpgradeAssistant/ValidatedLocalPolicy=Global + validated local refinement") or
				LOC("$$$/StyleAI/UpgradeAssistant/GlobalPolicy=Global conditional policy")
			props.detailTier = string.format("%s (%d examples)", tierName, current)

			props.detailRecommendedIds = s.recommended_photo_ids or {}
			props.detailReviewId = s.review_id or ""
			props.detailPolicyId = s.policy_id or ""
			local recCount = #props.detailRecommendedIds

			local admitted = tonumber(s.admitted_candidate_count) or recCount
			local ambiguous = tonumber(s.ambiguous_candidate_count) or 0
			local rejected = tonumber(s.rejected_candidate_count) or 0
			props.detailCoverage = s.coverage_summary
				or string.format(LOC("$$$/StyleAI/UpgradeAssistant/CoverageSummaryFmt=%d coverage-focused recommendations"), recCount)
			props.detailNeeded = s.target_summary
				or string.format(LOC("$$$/StyleAI/UpgradeAssistant/MoreExamplesFmt=Additional examples requested: %d"), tonumber(s.needed_count) or recCount)
			if recCount > 0 then
				props.detailExplanation = string.format(
					LOC("$$$/StyleAI/UpgradeAssistant/PolicyRecsFoundFmt=%d high-confidence candidates were selected after policy membership, ambiguity, burst, and quality checks. %d candidates were admissible; %d were ambiguous and %d were rejected."),
					recCount, admitted, ambiguous, rejected
				)
				props.detailButtonTitle = string.format(LOC("$$$/StyleAI/UpgradeAssistant/BtnSelectFmt=Select %d Recommended Photos in Library"), recCount)
				props.detailButtonEnabled = true
			else
				props.detailExplanation = string.format(
					LOC("$$$/StyleAI/UpgradeAssistant/PolicyNoRecsFmt=No high-confidence candidates are currently available. %d were ambiguous and %d failed policy or quality safeguards."),
					ambiguous, rejected
				)
				props.detailButtonTitle = LOC("$$$/StyleAI/UpgradeAssistant/BtnNoRecs=No Candidate Photos Found")
				props.detailButtonEnabled = false
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
		props:addObserver("detailReviewId", refreshActionState)

		local function updateListItems()
			local items = {}
			local filter = props.selectedProfileFilter or "All Profiles"
			for i, s in ipairs(props.styles or {}) do
				local profile = s.camera_profile or "Default"
				if filter == "All Profiles" or profile == filter then
					local count = tonumber(s.current_count) or 0
					local badge = s.local_correction_enabled and
						LOC("$$$/StyleAI/UpgradeAssistant/ValidatedLocalBadge=Validated local refinement") or
						LOC("$$$/StyleAI/UpgradeAssistant/GlobalPolicyBadge=Global conditional policy")
					local recCount = #(s.recommended_photo_ids or {})
					local name = s.style_name or s.policy_name or LOC("$$$/StyleAI/common/Unknown=Unknown")
					local label = string.format("%s • %s • %d (+%d recs) • %s", profile, name, count, recCount, badge)
					table.insert(items, { title = label, value = i })
				end
			end
			local previousSelection = props.selectedStyleIndex
			props.listItems = items
			props.selectedStyleIndex = StyleUI.keepSelection(items, previousSelection)
		end

		props:addObserver("selectedProfileFilter", updateListItems)

		local function loadRecommendations()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/ProgressWait=Waiting for StyleAI backend to load...")

			local progressScope = LrProgressScope({
				title = LOC("$$$/StyleAI/UpgradeAssistant/Progress=Discovering style upgrade candidates..."),
			})

			if not Util.waitForServerDialog({ suppressProgressDialog = true, skipHealthCheck = true }) then
				progressScope:done()
				props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/ErrorNoServer=Background service unavailable.")
				props.isLoading = false
				return
			end

			props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/Progress=Discovering style upgrade candidates...")
			local success, results = SearchIndexAPI.getUpgradeRecommendations(100)
			progressScope:done()

			if not success then
				props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/Error=Failed to get recommendations: ^1", tostring(results))
				props.isLoading = false
				return
			end

			local styles = type(results) == "table" and results.styles or nil
			if type(styles) ~= "table" or #styles == 0 then
				props.styles = {}
				props.listItems = {}
				props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/NoStylesMsg=No styles currently need additional training examples. Styles with sufficient coverage are omitted; if you have not learned a style yet, start with Learn From My Edits.")
				props.isLoading = false
				return
			end

			props.styles = styles

			-- Build profile filters only for the populated workspace.
			local uniqueProfiles = {}
			local profileCounts = {}
			for _, style in ipairs(props.styles) do
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
				if a.count == b.count then return a.profile < b.profile end
				return a.count > b.count
			end)

			local profileList = {
				{ title = LOC("$$$/StyleAI/StyleCatalog/AllProfiles=All Profiles"), value = "All Profiles" },
			}
			for _, item in ipairs(sortedProfiles) do
				table.insert(profileList, {
					title = string.format("%s (%d)", item.profile, item.count),
					value = item.profile,
				})
			end
			props.profileFilters = profileList
			updateListItems()

			props.isLoading = false
			props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/LoadedCount=^1 style(s) could benefit from more examples.", tostring(#props.styles))
		end

		-- Action: Show Candidate Photos for selected style
		local function showSelectedPhotos()
			local idx = StyleUI.resolveSelectedIndex(props.selectedStyleIndex, props.listItems)
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
					local sName = s.style_name or s.policy_name or "Unknown Style"
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
				local findAllProgress = nil
				local wasCanceled = false
				local ok, err = LrTasks.pcall(function()
					findAllProgress = LrProgressScope({
						title = LOC("$$$/StyleAI/UpgradeAssistant/FindAllProgress=Creating upgrade collections for all styles...")
					})
					local styleEntries = {}
					for _, s in ipairs(props.styles or {}) do
						if findAllProgress:isCanceled() then break end
						local recIds = s.recommended_photo_ids or {}
						if #recIds > 0 then
							local sName = s.style_name or s.policy_name or "Unknown Style"
							local sProf = s.camera_profile or ""
							local fullName = sName
							if sProf ~= "" and sProf ~= "Default" and not string.find(string.lower(sName), string.lower(sProf), 1, true) then
								fullName = string.format("%s (%s)", sName, sProf)
							end
							table.insert(styleEntries, {
								styleKey = s.policy_id or s.style_id or fullName,
								fullName = fullName,
								photoIds = recIds,
							})
						end
					end
					local batchedResults = SearchIndexAPI.findPhotosBatchedByStyleMap(styleEntries, findAllProgress)
					local stylesData = {}
					for _, entry in ipairs(batchedResults) do
						if findAllProgress:isCanceled() then break end
						if #(entry.photos or {}) > 0 then
							table.insert(stylesData, {
								styleKey = entry.styleKey,
								styleName = entry.fullName,
								photos = entry.photos,
							})
							totalPhotosAdded = totalPhotosAdded + #entry.photos
							stylesProcessed = stylesProcessed + 1
						end
					end

					if not findAllProgress:isCanceled() and #stylesData > 0 then
						Util.addMultipleUpgradePhotosToCollections(stylesData, nil, findAllProgress)
					end
					wasCanceled = findAllProgress:isCanceled()
				end)

				-- Always close the progress scope and unlock the dialog, including API and catalog-write failures.
				if findAllProgress then
					local progressOk, progressErr = LrTasks.pcall(function()
						findAllProgress:done()
					end)
					if not progressOk and ok then
						ok = false
						err = progressErr
					end
				end
				props.isLoading = false

				if not ok then
					log:error("Show All Candidate Photos failed: " .. tostring(err))
					props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/FindAllErrorStatus=Could not create all upgrade collections.")
					LrDialogs.message(
						LOC("$$$/StyleAI/UpgradeAssistant/FindAllErrorTitle=Unable to Create Upgrade Collections"),
						LOC("$$$/StyleAI/UpgradeAssistant/FindAllErrorMsg=StyleAI stopped creating upgrade collections. Any completed collection changes were preserved; see the plugin log for details."),
						"critical"
					)
					return
				end

				if wasCanceled then
					props.statusMessage = LOC("$$$/StyleAI/common/Canceled=Canceled.")
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
			end)
		end

		local function submitSelectedFeedback(policyMatch, useful)
			local reviewId = props.detailReviewId or ""
			local policyId = props.detailPolicyId or ""
			if reviewId == "" or policyId == "" then return end

			local candidateIds = {}
			for _, item in ipairs(props.detailRecommendedIds or {}) do
				local photoId = type(item) == "table" and (item.globalPhotoId or item.photo_id) or item
				if photoId and photoId ~= "" then
					candidateIds[tostring(photoId)] = true
				end
			end
			local labels = {}
			local catalog = LrApplication.activeCatalog()
			for _, photo in ipairs(catalog:getTargetPhotos() or {}) do
				local photoId = Util.getGlobalPhotoIdForPhoto(photo, { skipCacheWrite = true })
				if photoId and candidateIds[tostring(photoId)] then
					table.insert(labels, {
						photo_id = tostring(photoId),
						policy_match = policyMatch,
						useful = useful,
					})
				end
			end
			if #labels == 0 then
				LrDialogs.message(
					LOC("$$$/StyleAI/UpgradeAssistant/FeedbackNoneTitle=No Reviewed Candidates Selected"),
					LOC("$$$/StyleAI/UpgradeAssistant/FeedbackNoneMsg=Select one or more photos from the selected policy candidate collection in Library, then reopen the assistant and record the appropriate review label."),
					"warning"
				)
				return
			end

			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/FeedbackSaving=Saving local recommendation feedback...")
			LrTasks.startAsyncTask(function()
				local success, result = SearchIndexAPI.submitUpgradeFeedback(reviewId, policyId, labels)
				props.isLoading = false
				if not success then
					props.statusMessage = LOC("$$$/StyleAI/UpgradeAssistant/FeedbackError=Could not save feedback: ^1", tostring(result))
					return
				end
				props.statusMessage = LOC(
					"$$$/StyleAI/UpgradeAssistant/FeedbackSaved=Saved local feedback for ^1 candidate photos.",
					tostring((result and result.updated) or #labels)
				)
			end)
		end

		-- Resolve the state before constructing the window. Lightroom does not
		-- reliably shrink a resizable dialog after a large list/detail workspace
		-- becomes hidden, so an empty result gets a genuinely compact view.
		loadRecommendations()
		local hasStyles = #props.styles > 0

		-- Build Dialog
		local function buildDialog()
			if not hasStyles then
				return f:column({
					bind_to_object = props,
					spacing = f:control_spacing(),
					fill_horizontal = 1,
					UIFactory.Notice(f, {
						kind = "info",
						title = bind("statusMessage"),
						width = 600,
					}),
				})
			end

			return f:column({
				bind_to_object = props,
				spacing = f:control_spacing(),
				fill_horizontal = 1,

				-- Status bar
				UIFactory.HelpText(f, {
					title = bind("statusMessage"),
				}),

				StyleUI.filteredListGroup(f, {
					title = LOC("$$$/StyleAI/UpgradeAssistant/StyleList=Styles Needing Upgrades"),
					filterLabel = LOC("$$$/StyleAI/StyleCatalog/FilterByProfile=Filter by Profile:"),
					filterItems = bind("profileFilters"),
					filterValue = bind("selectedProfileFilter"),
					listItems = bind("listItems"),
					selectedValue = bind("selectedStyleIndex"),
					heightInLines = 7,
					fillVertical = false,
				}),

				-- Style detail panel
				f:group_box({
					title = LOC("$$$/StyleAI/UpgradeAssistant/StyleDetails=Upgrade Details"),
					fill_horizontal = 1,
					f:column({
						fill_horizontal = 1,
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailName=Name:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailName"), fill_horizontal = 1, wrap = true }),
							}),
							f:row({
								f:static_text({ title = bind("detailDescriptorLabel"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailGenre"), fill_horizontal = 1, wrap = true }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailProfile=Profile:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailProfile"), fill_horizontal = 1, wrap = true }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/UpgradeAssistant/DetailTierLabel=Policy model:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailTier"), fill_horizontal = 1, wrap = true }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/UpgradeAssistant/DetailNeededLabel=Target:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailNeeded"), fill_horizontal = 1, wrap = true, font = "<system/bold>" }),
							}),
							f:row({
								visible = bind("showPolicyDetails"),
								f:static_text({ title = LOC("$$$/StyleAI/UpgradeAssistant/CoverageLabel=Coverage:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailCoverage"), fill_horizontal = 1, wrap = true }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/UpgradeAssistant/DetailExplanationLabel=Recommendation Details:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailExplanation"), fill_horizontal = 1, wrap = true }),
							}),
						}),
					}),

				f:group_box({
					title = LOC("$$$/StyleAI/UpgradeAssistant/Actions=Candidate Review"),
					fill_horizontal = 1,
					f:column({
						fill_horizontal = 1,
						spacing = f:control_spacing(),
						UIFactory.HelpText(f, {
							title = LOC("$$$/StyleAI/UpgradeAssistant/ShowGuide=Open candidate photos in Library before deciding whether they improve this style."),
						}),
						f:row({
							f:push_button({
								title = LOC("$$$/StyleAI/UpgradeAssistant/ShowPhotos=Show Candidate Photos"),
								action = showSelectedPhotos,
								enabled = bind("selectedActionEnabled"),
							}),
							f:push_button({
								title = LOC("$$$/StyleAI/UpgradeAssistant/ShowAllPhotos=Show All Candidate Photos"),
								action = showAllPhotos,
								enabled = bind("allActionEnabled"),
							}),
						}),
						f:separator({ fill_horizontal = 1 }),
						UIFactory.HelpText(f, {
							title = LOC("$$$/StyleAI/UpgradeAssistant/FeedbackGuide=After reviewing a candidate collection in Library, select photos and label them here:"),
						}),
						f:row({
							f:push_button({
								title = LOC("$$$/StyleAI/UpgradeAssistant/FeedbackHelpful=Helpful Example"),
								action = function() submitSelectedFeedback(true, true) end,
								enabled = bind("feedbackActionEnabled"),
							}),
							f:push_button({
								title = LOC("$$$/StyleAI/UpgradeAssistant/FeedbackRedundant=Fits, But Redundant"),
								action = function() submitSelectedFeedback(true, false) end,
								enabled = bind("feedbackActionEnabled"),
							}),
							f:push_button({
								title = LOC("$$$/StyleAI/UpgradeAssistant/FeedbackWrong=Not This Policy"),
								action = function() submitSelectedFeedback(false, false) end,
								enabled = bind("feedbackActionEnabled"),
							}),
						}),
					}),
				}),
			})
		end

		LrDialogs.presentModalDialog({
			title = LOC("$$$/StyleAI/UpgradeAssistant/DialogTitle=Find More Training Examples"),
			contents = buildDialog(),
			actionVerb = LOC("$$$/StyleAI/common/Close=Close"),
			resizable = hasStyles,
		})

		log:info("Discover Style Upgrade Candidates task finished")
	end)
end)
