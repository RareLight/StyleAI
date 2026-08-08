-- TaskStyleCatalog.lua
-- Browse and manage the AI-discovered style catalog.
-- Allows viewing, deleting, and re-discovering styles.

local StyleUI = require("StyleUI")
local StyleDiscovery = require("StyleDiscovery")

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("StyleCatalogTask", function(ctx)
		LrDialogs.attachErrorDialogToFunctionContext(ctx)
		log:info("Style Catalog task started")

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

		props.detailName = ""
		props.detailGenre = ""
		props.detailProfile = ""
		props.detailPartition = ""
		props.detailCount = ""
		props.detailStrengthText = ""
		props.detailDesc = ""

		local function updateDetailView()
			local idx = StyleUI.resolveSelectedIndex(props.selectedStyleIndex, props.listItems)
			
			if not idx or idx < 1 or not props.styles or idx > #props.styles then
				props.detailName = LOC("$$$/StyleAI/StyleCatalog/SelectPrompt=Select a style to view details.")
				props.detailGenre = ""
				props.detailProfile = ""
				props.detailPartition = ""
				props.detailCount = ""
				props.detailStrengthText = ""
				props.detailDesc = ""
				return
			end
			
			local s = props.styles[idx]
			props.detailName = s.style_name or ""
			local cueNames = {}
			for _, cue in ipairs(s.policy_descriptors or {}) do
				if cue.descriptor and #cueNames < 4 then
					table.insert(cueNames, cue.descriptor)
				end
			end
			props.detailGenre = #cueNames > 0 and table.concat(cueNames, ", ") or
				LOC("$$$/StyleAI/StyleCatalog/NoCues=No explanatory cues yet")
			props.detailProfile = s.camera_profile or ""
			props.detailPartition = s.hard_partition_key or s.camera_profile or ""
			
			local count = tonumber(s.example_count) or 0
			props.detailCount = tostring(count)
			props.detailStrengthText = s.local_correction_enabled and
				LOC("$$$/StyleAI/StyleCatalog/ValidatedLocalPolicy=Global + validated local refinement") or
				LOC("$$$/StyleAI/StyleCatalog/GlobalPolicy=Global conditional policy")
			
			props.detailDesc = s.description or
				LOC("$$$/StyleAI/StyleCatalog/PolicyDescription=Source-conditioned absolute Lightroom targets with ambiguity-aware matching.")
		end

		props:addObserver("selectedStyleIndex", updateDetailView)
		props:addObserver("styles", updateDetailView)

		local function updateListItems()
			local items = {}
			local filter = props.selectedProfileFilter or "All Profiles"
			for i, style in ipairs(props.styles or {}) do
				local profile = style.camera_profile or "default"
				if filter == "All Profiles" or profile == filter then
					local name = style.style_name or style.style_id or LOC("$$$/StyleAI/common/Unknown=Unknown")
					local count = style.example_count or 0
					local cleanName = name
					
					local strength = LOC("$$$/StyleAI/StyleCatalog/PolicyShort=Editing Policy")
					
					local label = string.format("%s • %s • %d • %s", profile, cleanName, count, strength)
					table.insert(items, { title = label, value = i })
				end
			end
			local previousSelection = props.selectedStyleIndex
			props.listItems = items
			props.selectedStyleIndex = StyleUI.keepSelection(items, previousSelection)
		end

		props:addObserver("selectedProfileFilter", updateListItems)

		-- Load styles from backend
		local function loadStyles()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/LoadingWait=Waiting for StyleAI backend to load...")

			LrTasks.startAsyncTask(function()
				if not Util.waitForServerDialog({ suppressProgressDialog = true, skipHealthCheck = true }) then
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/ErrorNoServer=Backend server unavailable.")
					props.isLoading = false
					return
				end

				props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Loading=Loading styles...")

				local success, result = SearchIndexAPI.listStyles()
				if success then
					props.styles = result or {}
					table.sort(props.styles, function(a, b)
						return (a.example_count or 0) > (b.example_count or 0)
					end)
					
					-- Build profile filters
					local uniqueProfiles = {}
					local profileCounts = {}
					for i, style in ipairs(props.styles) do
						local profile = style.camera_profile or "default"
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

					props.statusMessage = LOC(
						"$$$/StyleAI/StyleCatalog/LoadedCount=^1 style(s) discovered.",
						tostring(#props.styles)
					)
				else
					props.statusMessage = LOC(
						"$$$/StyleAI/StyleCatalog/LoadError=Error loading styles: ^1",
						tostring(result)
					)
					props.styles = {}
					props.listItems = {}
				end

				props.isLoading = false
			end)
		end



		-- Rename the selected style
		local function renameSelectedStyle()
			local idx = StyleUI.resolveSelectedIndex(props.selectedStyleIndex, props.listItems)
			if not idx or idx < 1 or idx > #props.styles then
				return
			end

			local style = props.styles[idx]
			local currentName = style.style_name or ""
			
			local newName = LrDialogs.runTextInputDialog(
				LOC("$$$/StyleAI/StyleCatalog/RenameTitle=Rename Style"),
				LOC("$$$/StyleAI/StyleCatalog/RenamePrompt=Enter a new name for this style:"),
				currentName
			)

			if not newName or newName == "" or newName == currentName then
				return
			end

			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Renaming=Renaming style...")

			LrTasks.startAsyncTask(function()
				local success, err = SearchIndexAPI.renameStyle(style.style_id, newName)
				if success then
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Renamed=Style renamed.")
					loadStyles()
				else
					props.statusMessage = LOC(
						"$$$/StyleAI/StyleCatalog/RenameError=Rename failed: ^1",
						tostring(err)
					)
					props.isLoading = false
				end
			end)
		end

		-- Reset and Discover styles
		local function resetAndDiscoverStyles()
			local confirm = LrDialogs.confirm(
				LOC("$$$/StyleAI/StyleCatalog/ResetAndDiscoverTitle=Rebuild Styles"),
				LOC("$$$/StyleAI/StyleCatalog/ResetAndDiscoverConfirm=This will build a replacement style generation from your saved training examples. Your current styles remain available unless the rebuild succeeds. Continue?"),
				LOC("$$$/StyleAI/StyleCatalog/ResetAndDiscoverAction=Rebuild Styles"),
				LOC("$$$/StyleAI/common/Cancel=Cancel")
			)

			if confirm ~= "ok" then
				return
			end

			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Discovering=Discovering styles from training examples...")

			LrTasks.startAsyncTask(function()
				local dSuccess, result = SearchIndexAPI.discoverStyles(nil)
				if dSuccess then
					local completed, discoveryResult = StyleDiscovery.waitForCompletion(function(discovery)
						if discovery.phase == "fitting_partitions" then
							props.statusMessage = LOC(
								"$$$/StyleAI/StyleCatalog/DiscoveringProgress=Discovering policies: ^1 of ^2 compatible camera/profile partitions...",
								tostring(discovery.completed_partitions or 0),
								tostring(discovery.eligible_partitions or 0)
							)
						elseif discovery.phase == "activating" then
							props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Activating=Validating and activating the replacement style generation...")
						end
					end)
					if completed then
						loadStyles()
					else
						props.statusMessage = LOC(
							"$$$/StyleAI/StyleCatalog/DiscoverError=Discovery failed: ^1",
							tostring(discoveryResult)
						)
						props.isLoading = false
					end
				else
					props.statusMessage = LOC(
						"$$$/StyleAI/StyleCatalog/DiscoverError=Discovery failed: ^1",
						tostring(result)
					)
					props.isLoading = false
				end
			end)
		end

		-- Show Photos for a specific style
		local function showPhotos()
			local idx = StyleUI.resolveSelectedIndex(props.selectedStyleIndex, props.listItems)
			if not idx or idx < 1 or not props.styles or idx > #props.styles then return end
			local s = props.styles[idx]

			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/LoadingPhotos=Loading trained photos...")

			LrTasks.startAsyncTask(function()
				local success, details = SearchIndexAPI.getStyleDetails(s.style_id)
				if not success then
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/LoadingError=Failed to load style details.")
					props.isLoading = false
					return
				end

				if not details.example_photo_ids or #details.example_photo_ids == 0 then
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/NoPhotos=No trained photos found.")
					props.isLoading = false
					return
				end

				local selectProgress = LrProgressScope({ title = LOC("$$$/StyleAI/StyleCatalog/SelectingProgress=Selecting photos in Lightroom Library...") })
				local photos = SearchIndexAPI.findPhotosByPhotoIds(details.example_photo_ids, selectProgress)
				selectProgress:done()

				if #photos > 0 then
					local coll = Util.addPhotosToTrainedStylesCollection(photos, details.camera_profile, details.style_name)
					LrTasks.pcall(function() LrApplicationView.switchToModule("library") end)
					LrTasks.yield()
					LrTasks.sleep(0.2)
					if coll then
						LrTasks.pcall(function()
							local catalog = LrApplication.activeCatalog()
							catalog:setActiveSources({ coll })
							LrTasks.sleep(0.15)
							catalog:setSelectedPhotos(photos[1], photos)
						end)
					end
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/PhotosShown=Photos added to collection and selected.")
					LrDialogs.stopModalWithResult(ctx, "ok")
				else
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/PhotosNotFound=Photos could not be found in catalog.")
				end
				props.isLoading = false
			end)
		end

		-- Show All Trained Photos
		local function showAllPhotos()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/LoadingAllPhotos=Loading all trained photos...")

			LrTasks.startAsyncTask(function()
				local success, styles = SearchIndexAPI.getAllStylesWithExamples()
				if not success then
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/LoadingAllError=Failed to load styles.")
					props.isLoading = false
					return
				end

				local findAllProgress = LrProgressScope({ title = LOC("$$$/StyleAI/StyleCatalog/FindAllProgress=Creating collections for all trained styles...") })
				local totalPhotos = 0
				local totalStyles = 0
				local stylesData = {}
				
				local allRequiredPhotoIds = {}
				local uniqueIds = {}
				for _, style in ipairs(styles) do
					local exList = style.examples or style.example_photo_ids
					if exList then
						for _, pidInfo in ipairs(exList) do
							local pidStr = type(pidInfo) == "table" and pidInfo.globalPhotoId or pidInfo
							if pidStr and not uniqueIds[pidStr] then
								uniqueIds[pidStr] = true
								table.insert(allRequiredPhotoIds, pidInfo)
							end
						end
					end
				end

				local photoMap = SearchIndexAPI.findPhotosByPhotoIdsMap(allRequiredPhotoIds, findAllProgress)
				
				for _, style in ipairs(styles) do
					local exList = style.examples or style.example_photo_ids
					if exList and #exList > 0 then
						local photos = {}
						for _, pidInfo in ipairs(exList) do
							local pidStr = type(pidInfo) == "table" and pidInfo.globalPhotoId or pidInfo
							if pidStr and photoMap[pidStr] then
								table.insert(photos, photoMap[pidStr])
							end
						end

						if #photos > 0 then
							table.insert(stylesData, {
								photos = photos,
								profileName = style.camera_profile,
								styleName = style.style_name
							})
							totalPhotos = totalPhotos + #photos
							totalStyles = totalStyles + 1
						end
					end
				end
				
				if #stylesData > 0 then
					Util.addMultipleStylePhotosToCollections(stylesData, nil, findAllProgress)
				end
				findAllProgress:done()

				props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/AllPhotosShown=Created collections for all trained photos.")
				LrDialogs.message(
					LOC("$$$/StyleAI/StyleCatalog/FindAllComplete=Collections Created"),
					string.format(LOC("$$$/StyleAI/StyleCatalog/FindAllCompleteMsg=Created collections for %d styles and added %d total photos under 'StyleAI -> Trained Styles'."), totalStyles, totalPhotos),
					"info"
				)
				props.isLoading = false
			end)
		end

		-- Build the dialog contents
		local function buildDialog()
			return f:column({
				bind_to_object = props,
				spacing = f:control_spacing(),

				-- Title
				f:row({
					f:static_text({
						title = LOC("$$$/StyleAI/StyleCatalog/Title=Styles & Training"),
						font = "bold",
						size = "large",
					}),
				}),

				-- Status bar
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = bind("statusMessage"),
						fill_horizontal = 1,
						wrap = true,
					}),
				}),

				-- Toolbar
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/StyleCatalog/ShowPhotos=Show Photos"),
						action = showPhotos,
						width = share("toolbarButton"),
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/StyleCatalog/ShowAllPhotos=Show All Photos"),
						action = showAllPhotos,
						width = share("toolbarButton"),
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/StyleCatalog/Rename=Rename"),
						action = renameSelectedStyle,
						width = share("toolbarButton"),
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/Menu/FindExamples=Find More Training Examples..."),
						action = function()
							LrDialogs.stopModalWithResult(ctx, "other")
						end,
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
				}),

				StyleUI.filteredListGroup(f, {
					title = LOC("$$$/StyleAI/StyleCatalog/StyleList=Learned Styles"),
					filterLabel = LOC("$$$/StyleAI/StyleCatalog/FilterByProfile=Filter by Profile:"),
					filterItems = bind("profileFilters"),
					filterValue = bind("selectedProfileFilter"),
					listItems = bind("listItems"),
					selectedValue = bind("selectedStyleIndex"),
				}),

				-- Style detail panel
				f:group_box({
					title = LOC("$$$/StyleAI/StyleCatalog/StyleDetails=Style Details"),
					fill_horizontal = 1,
					f:column({
						fill_horizontal = 1,
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailName=Name:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailName"), fill_horizontal = 1, wrap = true }),
							}),
							f:row({
									f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailGenre=Evidence cues:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailGenre"), fill_horizontal = 1, wrap = true }),
							}),

							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailProfile=Profile:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailProfile"), fill_horizontal = 1, wrap = true }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailPartition=Rendering partition:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailPartition"), fill_horizontal = 1, wrap = true }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailExamples=Examples:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailCount") }),
								f:spacer({ width = 10 }),
								f:static_text({ title = bind("detailStrengthText"), font = "<system/bold>" }),
							}),
							f:row({
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailDescription=Description:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailDesc"), fill_horizontal = 1, wrap = true }),
							}),
					}),
				}),

				f:group_box({
					title = LOC("$$$/StyleAI/StyleCatalog/Maintenance=Style Maintenance"),
					fill_horizontal = 1,
					f:row({
						fill_horizontal = 1,
						f:static_text({
							title = LOC("$$$/StyleAI/StyleCatalog/RebuildHelp=Rebuild learned styles from your saved training examples after changing or refreshing training data."),
							fill_horizontal = 1,
							wrap = true,
						}),
						f:push_button({
							title = LOC("$$$/StyleAI/StyleCatalog/ResetAndDiscoverAction=Rebuild Styles"),
							action = resetAndDiscoverStyles,
							enabled = bind({
								key = "isLoading",
								transform = function(v) return not v end,
							}),
						}),
					}),
				}),
			})
		end

		-- Load styles initially
		loadStyles()

		-- Show the dialog
		local result = LrDialogs.presentModalDialog({
			title = LOC("$$$/StyleAI/StyleCatalog/DialogTitle=Styles & Training"),
			contents = buildDialog(),
			actionVerb = LOC("$$$/StyleAI/common/Close=Close"),
			resizable = true,
		})
		if result == "other" then
			dofile(_PLUGIN.path .. "/TaskDiscoverUpgradeCandidates.lua")
		end

		log:info("Style Catalog task finished")
	end)
end)
