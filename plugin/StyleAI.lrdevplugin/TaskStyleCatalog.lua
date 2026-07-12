-- TaskStyleCatalog.lua
-- Browse and manage the AI-discovered style catalog.
-- Allows viewing, deleting, and re-discovering styles.

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("StyleCatalogTask", function(ctx)
		LrDialogs.attachErrorDialogToFunctionContext(ctx)
		log:info("Style Catalog task started")

		if not Util.waitForServerDialog() then
			log:warn("Style Catalog task aborted: backend server unavailable")
			return
		end

		local f = LrView.osFactory()
		local bind = LrView.bind
		local share = LrView.share
		local props = LrBinding.makePropertyTable(ctx)

		-- State
		props.styles = {}
		props.listItems = {}
		props.selectedStyleIndex = 0
		props.selectedProfileFilter = "All Profiles"
		props.profileFilters = { { title = "All Profiles", value = "All Profiles" } }
		props.isLoading = false

		props.detailName = ""
		props.detailGenre = ""
		props.detailProfile = ""
		props.detailCount = ""
		props.detailStrengthText = ""
		props.detailStrengthColor = LrColor(0.7, 0.7, 0.7)
		props.detailDesc = ""

		local function updateDetailView()
			local rawIdx = props.selectedStyleIndex
			local idx = tonumber(rawIdx)
			
			if type(rawIdx) == "table" then
				if rawIdx.value then
					idx = tonumber(rawIdx.value)
				elseif rawIdx[1] ~= nil then
					-- Lightroom sometimes returns an array of the 'value' fields of selected items
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
				props.detailName = "Select a style to view details."
				props.detailGenre = ""
				props.detailProfile = ""
				props.detailCount = ""
				props.detailStrengthText = ""
				props.detailDesc = ""
				return
			end
			
			local s = props.styles[idx]
			props.detailName = s.style_name or ""
			props.detailGenre = s.genre or ""
			props.detailProfile = s.camera_profile or ""
			
			local count = tonumber(s.example_count) or 0
			props.detailCount = tostring(count)
			if count >= 50 then
				props.detailStrengthText = LOC("$$$/StyleAI/StyleCatalog/StrengthML=🌟 ML Predictive (Best)")
			elseif count >= 15 then
				props.detailStrengthText = LOC("$$$/StyleAI/StyleCatalog/StrengthMLPCA=⭐️ ML Predictive (Good)")
			elseif count >= 3 then
				props.detailStrengthText = LOC("$$$/StyleAI/StyleCatalog/StrengthGood=🟡 Basic")
			else
				props.detailStrengthText = LOC("$$$/StyleAI/StyleCatalog/StrengthWeak=🔴 Undertrained")
			end
			
			props.detailDesc = s.description or ""
		end

		props:addObserver("selectedStyleIndex", updateDetailView)
		props:addObserver("styles", updateDetailView)

		local function updateListItems()
			local items = {}
			local filter = props.selectedProfileFilter or "All Profiles"
			for i, style in ipairs(props.styles or {}) do
				local profile = style.camera_profile or "default"
				if filter == "All Profiles" or profile == filter then
					local name = style.style_name or style.style_id or "Unknown"
					local count = style.example_count or 0
					local cleanName = name
					
					local strength = "🔴 Undertrained"
					if count >= 50 then
						strength = "🌟 ML Predictive (Best)"
					elseif count >= 15 then
						strength = "⭐️ ML Predictive (Good)"
					elseif count >= 3 then
						strength = "🟡 Basic"
					end
					
					local label = string.format("%s • %s • %d • %s", profile, cleanName, count, strength)
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

		-- Load styles from backend
		local function loadStyles()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Loading=Loading styles...")

			LrTasks.startAsyncTask(function()
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

					local profileList = { { title = "All Profiles", value = "All Profiles" } }
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

		-- Discover styles from all training examples
		local function discoverStyles()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Discovering=Discovering styles from training examples...")

			LrTasks.startAsyncTask(function()
				local success, result = SearchIndexAPI.discoverStyles(nil)
				if success then
					local count = result.styles_created or 0
					props.statusMessage = LOC(
						"$$$/StyleAI/StyleCatalog/DiscoveredCount=Discovered ^1 style(s).",
						tostring(count)
					)
					-- Reload the list
					loadStyles()
				else
					props.statusMessage = LOC(
						"$$$/StyleAI/StyleCatalog/DiscoverError=Discovery failed: ^1",
						tostring(result)
					)
					props.isLoading = false
				end
			end)
		end

		-- Delete the selected style
		local function deleteSelectedStyle()
			local idx = props.selectedStyleIndex
			if not idx or idx < 1 or idx > #props.styles then
				return
			end

			local style = props.styles[idx]
			local styleName = style.style_name or style.style_id or "this style"

			local confirm = LrDialogs.confirm(
				LOC("$$$/StyleAI/StyleCatalog/DeleteTitle=Delete Style"),
				LOC(
					"$$$/StyleAI/StyleCatalog/DeleteConfirm=Are you sure you want to delete '^1'? Training examples will not be affected.",
					styleName
				),
				LOC("$$$/StyleAI/common/Delete=Delete"),
				LOC("$$$/StyleAI/common/Cancel=Cancel")
			)

			if confirm ~= "ok" then
				return
			end

			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Deleting=Deleting style...")

			LrTasks.startAsyncTask(function()
				local success, err = SearchIndexAPI.resetStyle(style.style_id)
				if success then
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Deleted=Style deleted.")
					loadStyles()
				else
					props.statusMessage = LOC(
						"$$$/StyleAI/StyleCatalog/DeleteError=Delete failed: ^1",
						tostring(err)
					)
					props.isLoading = false
				end
			end)
		end

		-- Rename the selected style
		local function renameSelectedStyle()
			local idx = props.selectedStyleIndex
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

		-- Reset all styles
		local function resetAllStyles()
			local confirm = LrDialogs.confirm(
				LOC("$$$/StyleAI/StyleCatalog/ResetAllTitle=Reset All Styles"),
				LOC("$$$/StyleAI/StyleCatalog/ResetAllConfirm=This will delete ALL discovered styles. Training examples will be preserved. Are you sure?"),
				LOC("$$$/StyleAI/common/ResetAll=Reset All"),
				LOC("$$$/StyleAI/common/Cancel=Cancel")
			)

			if confirm ~= "ok" then
				return
			end

			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/ResettingAll=Resetting all styles...")

			LrTasks.startAsyncTask(function()
				local success, err = SearchIndexAPI.resetAllStyles()
				if success then
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/ResetAllDone=All styles cleared.")
					loadStyles()
				else
					props.statusMessage = LOC(
						"$$$/StyleAI/StyleCatalog/ResetAllError=Reset failed: ^1",
						tostring(err)
					)
				end
				props.isLoading = false
			end)
		end

		-- Show Photos for a specific style
		local function showPhotos()
			local rawIdx = props.selectedStyleIndex
			local idx = tonumber(rawIdx)
			if type(rawIdx) == "table" then
				if rawIdx.value then idx = tonumber(rawIdx.value)
				elseif rawIdx[1] ~= nil then idx = tonumber(rawIdx[1]) end
			end
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
				
				for _, style in ipairs(styles) do
					if style.example_photo_ids and #style.example_photo_ids > 0 then
						local photos = SearchIndexAPI.findPhotosByPhotoIds(style.example_photo_ids, nil)
						if #photos > 0 then
							Util.addPhotosToTrainedStylesCollection(photos, style.camera_profile, style.style_name)
							totalPhotos = totalPhotos + #photos
							totalStyles = totalStyles + 1
						end
					end
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
						title = LOC("$$$/StyleAI/StyleCatalog/Title=Signature Styles Index"),
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
						title = LOC("$$$/StyleAI/StyleCatalog/Discover=Discover"),
						action = discoverStyles,
						width = share("toolbarButton"),
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
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
						title = LOC("$$$/StyleAI/StyleCatalog/Delete=Delete"),
						action = deleteSelectedStyle,
						width = share("toolbarButton"),
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/StyleCatalog/ResetAll=Reset All"),
						action = resetAllStyles,
						width = share("toolbarButton"),
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
				}),

				-- Style list
				f:group_box({
					title = LOC("$$$/StyleAI/StyleCatalog/StyleList=Discovered Styles"),
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
					title = LOC("$$$/StyleAI/StyleCatalog/StyleDetails=Style Details"),
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
								f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailExamples=Examples:"), width = share("detailLabel"), alignment = "right", font = "<system/bold>" }),
								f:static_text({ title = bind("detailCount") }),
								f:spacer({ width = 10 }),
								f:static_text({ title = bind("detailStrengthText"), font = "<system/bold>" }),
							}),
						}),
						f:column({
							f:static_text({ title = LOC("$$$/StyleAI/StyleCatalog/DetailDescription=Description:"), font = "<system/bold>" }),
							f:static_text({ title = bind("detailDesc"), width_in_chars = 40, height_in_lines = 6, wrap = true }),
						}),
					}),
				}),
			})
		end

		-- Load styles initially
		loadStyles()

		-- Show the dialog
		LrDialogs.presentModalDialog({
			title = LOC("$$$/StyleAI/StyleCatalog/DialogTitle=Signature Styles Index"),
			contents = buildDialog(),
			actionVerb = LOC("$$$/StyleAI/common/Close=Close"),
		})

		log:info("Style Catalog task finished")
	end)
end)
