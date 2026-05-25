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
		local props = LrBinding.makePropertyTable(ctx)

		-- State
		props.styles = {}
		props.selectedStyleIndex = 0
		props.isLoading = false
		props.statusMessage = ""
		props.detailStyle = nil

		-- Load styles from backend
		local function loadStyles()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Loading=Loading styles...")

			local success, result = SearchIndexAPI.listStyles()
			if success then
				props.styles = result or {}
				props.statusMessage = LOC(
					"$$$/StyleAI/StyleCatalog/LoadedCount=^1 style(s) discovered.",
					tostring(#props.styles)
				)
				if #props.styles > 0 then
					props.selectedStyleIndex = 1
				end
			else
				props.statusMessage = LOC(
					"$$$/StyleAI/StyleCatalog/LoadError=Error loading styles: ^1",
					tostring(result)
				)
				props.styles = {}
			end

			props.isLoading = false
		end

		-- Discover styles from all training examples
		local function discoverStyles()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Discovering=Discovering styles from training examples...")

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

			local success, err = SearchIndexAPI.resetAllStyles()
			if success then
				props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/ResetAllDone=All styles cleared.")
				props.styles = {}
				props.selectedStyleIndex = 0
			else
				props.statusMessage = LOC(
					"$$$/StyleAI/StyleCatalog/ResetAllError=Reset failed: ^1",
					tostring(err)
				)
			end
			props.isLoading = false
		end

		-- Export styles to file
		local function exportStyles()
			props.isLoading = true
			props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/Exporting=Exporting styles...")

			local success, data = SearchIndexAPI.exportStyles()
			if not success then
				props.statusMessage = LOC(
					"$$$/StyleAI/StyleCatalog/ExportError=Export failed: ^1",
					tostring(data)
				)
				props.isLoading = false
				return
			end

			local catalog = LrApplication.activeCatalog()
			local catalogPath = catalog:getPath()
			local catalogDir = LrPathUtils.parent(catalogPath)
			local defaultPath = LrPathUtils.child(catalogDir, "StyleAI-Styles.json")

			local path = LrDialogs.runSavePanel({
				title = LOC("$$$/StyleAI/StyleCatalog/ExportDialogTitle=Export Style Catalog"),
				prompt = LOC("$$$/StyleAI/StyleCatalog/ExportDialogPrompt=Save"),
				requiredFileType = "json",
				initialDirectory = catalogDir,
			})

			if path then
				-- Ensure .json extension
				if not path:match("%.json$") then
					path = path .. ".json"
				end

				local file = io.open(path, "w")
				if file then
					file:write(JSON:encode(data))
					file:close()
					props.statusMessage = LOC(
						"$$$/StyleAI/StyleCatalog/ExportedTo=Exported to ^1",
						path
					)
				else
					props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/ExportFileError=Could not write file.")
				end
			else
				props.statusMessage = LOC("$$$/StyleAI/StyleCatalog/ExportCancelled=Export cancelled.")
			end

			props.isLoading = false
		end

		-- Build the dialog contents
		local function buildDialog()
			return f:column({
				bind_to_object = props,
				spacing = f:control_spacing(),
				width = 750,

				-- Title
				f:row({
					f:static_text({
						title = LOC("$$$/StyleAI/StyleCatalog/Title=AI Style Catalog"),
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
						title = LOC("$$$/StyleAI/StyleCatalog/Refresh=Refresh"),
						action = loadStyles,
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/StyleCatalog/Discover=Discover"),
						action = discoverStyles,
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/StyleCatalog/Export=Export"),
						action = exportStyles,
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/StyleCatalog/Delete=Delete"),
						action = deleteSelectedStyle,
						enabled = bind({
							key = "isLoading",
							transform = function(v) return not v end,
						}),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/StyleCatalog/ResetAll=Reset All"),
						action = resetAllStyles,
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
					f:simple_list({
						items = bind({
							key = "styles",
							transform = function(styles)
								local items = {}
								for i, style in ipairs(styles) do
									local name = style.style_name or style.style_id or "Unknown"
									local count = style.example_count or 0
									local profile = style.camera_profile or "default"
									local label = name .. "  [" .. count .. " examples, " .. profile .. "]"
									table.insert(items, { title = label, value = i })
								end
								return items
							end,
						}),
						value = bind("selectedStyleIndex"),
						allows_multiple_selection = false,
						height_in_lines = 12,
					}),
				}),

				-- Style detail panel
				f:group_box({
					title = LOC("$$$/StyleAI/StyleCatalog/StyleDetails=Style Details"),
					fill_horizontal = 1,
					f:column({
						spacing = f:control_spacing(),
						f:row({
							f:static_text({
								title = bind({
									key = "styles",
									transform = function(styles)
										local idx = props.selectedStyleIndex
										if not idx or idx < 1 or idx > #styles then
											return "Select a style to view details."
										end
										local s = styles[idx]
										local lines = {}
										if s.style_name then
											table.insert(lines, "Name: " .. s.style_name)
										end
										if s.genre then
											table.insert(lines, "Genre: " .. s.genre)
										end
										if s.camera_model then
											table.insert(lines, "Camera: " .. s.camera_model)
										end
										if s.camera_profile then
											table.insert(lines, "Profile: " .. s.camera_profile)
										end
										if s.example_count then
											table.insert(lines, "Examples: " .. tostring(s.example_count))
										end
										if s.confidence_threshold then
											table.insert(lines, "Min Confidence: " .. tostring(s.confidence_threshold))
										end
										if s.description and s.description ~= "" then
											table.insert(lines, "")
											table.insert(lines, s.description)
										end
										return table.concat(lines, "\n")
									end,
								}),
								width_in_chars = 80,
								wrap = true,
								height_in_lines = 8,
							}),
						}),
					}),
				}),
			})
		end

		-- Load styles initially
		loadStyles()

		-- Show the dialog
		LrDialogs.presentModalDialog({
			title = LOC("$$$/StyleAI/StyleCatalog/DialogTitle=AI Style Catalog"),
			contents = buildDialog(),
			actionVerb = LOC("$$$/StyleAI/common/Close=Close"),
		})

		log:info("Style Catalog task finished")
	end)
end)
