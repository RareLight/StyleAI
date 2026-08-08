local TaskDiagnostics = require("TaskDiagnostics")
local UIFactory = require("UIFactory")
local BuildConfig = require("BuildConfig")

PluginInfoDialogSections = {}

function PluginInfoDialogSections.startDialog(propertyTable)
	propertyTable.keepChecksRunning = true
	propertyTable.logging = prefs.logging


	propertyTable.periodicalUpdateCheck = prefs.periodicalUpdateCheck == nil and true or prefs.periodicalUpdateCheck
	propertyTable.indexingPerformanceProfile = tonumber(prefs.indexingPerformanceProfile) or 2
	propertyTable.developerBuild = BuildConfig.developerBuild == true
	propertyTable.debugMode = prefs.debugMode == true
	propertyTable.captureLlmInputs = propertyTable.debugMode and prefs.captureLlmInputs == true
	propertyTable.captureLlmInputsPath = prefs.captureLlmInputsPath or ""
	propertyTable.captureInfoText = LOC("$$$/StyleAI/Debug/NoCaptures=No diagnostic captures saved.")
	propertyTable:addObserver("debugMode", function(properties, key, newValue)
		if newValue ~= true then
			properties.captureLlmInputs = false
		end
	end)

	local function refreshCaptureInfo()
		if propertyTable.debugMode ~= true then return end
		LrTasks.startAsyncTask(function()
			local info = SearchIndexAPI.getDiagnosticCaptureInfo(propertyTable.captureLlmInputsPath)
			if info then
				if propertyTable.captureLlmInputsPath == "" and info.path then
					propertyTable.captureLlmInputsPath = tostring(info.path)
				end
				local megabytes = (tonumber(info.bytes) or 0) / (1024 * 1024)
				propertyTable.captureInfoText = LOC(
					"$$$/StyleAI/Debug/CaptureSummary=^1 capture(s), ^2 MB — ^3",
					tostring(info.capture_count or 0),
					string.format("%.1f", megabytes),
					tostring(info.path or "")
				)
			end
		end)
	end
	propertyTable.refreshCaptureInfo = refreshCaptureInfo
	if propertyTable.debugMode then refreshCaptureInfo() end
	propertyTable.usePreviewThumbnails = prefs.usePreviewThumbnails ~= false
	propertyTable.pluginVersionText = string.format(
		"%d.%d.%d (%d)",
		Info.MAJOR or 0,
		Info.MINOR or 0,
		Info.REVISION or 0,
		Info.BUILD or 0
	)
	propertyTable.backendVersionText = LOC("$$$/StyleAI/common/Checking=Checking...")
	local catalogPath = LrApplication.activeCatalog():getPath()
	propertyTable.databasePath = LrPathUtils.child(LrPathUtils.parent(catalogPath), "styleai.db")
	LrTasks.startAsyncTask(function()
		local versionInfo = SearchIndexAPI.getBackendVersion()
		propertyTable.backendVersionText = versionInfo and (versionInfo.backend_version or versionInfo.version)
			or LOC("$$$/StyleAI/common/Unavailable=Unavailable")
	end)

	-- Training/Style Profile stats (loaded asynchronously).
	propertyTable.trainingCount = 0
	propertyTable.styleStats = nil
	propertyTable.styleReadiness = "cold_start"
	propertyTable.styleReadyText = LOC("$$$/StyleAI/Training/Status/ColdStart=Cold Start (0 examples)")
	propertyTable.styleReadyColor = { 0.7, 0.7, 0.7 }

	local function updateStats()
		LrTasks.startAsyncTask(function()
			local stats = SearchIndexAPI.getTrainingStats()
			if stats then
				propertyTable.styleStats = stats
				propertyTable.trainingCount = stats.count or 0

				local readiness = stats.readiness or "cold_start"
				propertyTable.styleReadiness = readiness

				if readiness == "active" then
					propertyTable.styleReadyText =
						LOC("$$$/StyleAI/Training/Status/Active=ACTIVE - High precision matching")
					propertyTable.styleReadyColor = { 0.2, 0.8, 0.2 }
				elseif readiness == "limited" then
					propertyTable.styleReadyText = LOC("$$$/StyleAI/Training/Status/Limited=LIMITED - Good matching")
					propertyTable.styleReadyColor = { 0.8, 0.8, 0.2 }
				elseif readiness == "warming_up" then
					propertyTable.styleReadyText = LOC(
						"$$$/StyleAI/Training/Status/WarmingUp=WARMING UP (^1/10 examples)",
						tostring(stats.count)
					)
					propertyTable.styleReadyColor = { 0.8, 0.4, 0.1 }
				else
					propertyTable.styleReadyText =
						LOC("$$$/StyleAI/Training/Status/ColdStart=COLD START (Add examples to begin)")
					propertyTable.styleReadyColor = { 0.7, 0.7, 0.7 }
				end
			end
		end)
	end

	updateStats()
	propertyTable.refreshStyleStats = updateStats

	-- System Health monitoring
	propertyTable.healthStatus = "healthy"
	propertyTable.healthIssues = ""
	propertyTable.healthColor = { 0, 0.8, 0 }
	propertyTable.backendStatusText = LOC("$$$/StyleAI/common/Checking=Checking...")
	propertyTable.visionStatusText = LOC("$$$/StyleAI/common/Checking=Checking...")
	propertyTable.metadataStatusText = LOC("$$$/StyleAI/common/Checking=Checking...")

	local function updateHealth()
		LrTasks.startAsyncTask(function()
			local health = SearchIndexAPI.getDetailedHealth()
			local status = "healthy"
			local issues = {}
			local color = { 0, 0.8, 0 }

			propertyTable.backendStatusText = health.backend
				and LOC("$$$/StyleAI/Health/ServiceReady=Ready — local background service is running.")
				or LOC("$$$/StyleAI/Health/ServiceUnavailable=Unavailable — local background service is not reachable.")
			propertyTable.visionStatusText = health.clip
				and LOC("$$$/StyleAI/Health/VisionReady=Ready — vision model is installed.")
				or LOC("$$$/StyleAI/Health/VisionUnavailable=Unavailable — vision model setup is required.")
			propertyTable.metadataStatusText = (health.ollama or health.lmstudio)
				and LOC("$$$/StyleAI/Health/MetadataReady=Ready — a local metadata model provider is available.")
				or LOC("$$$/StyleAI/Health/MetadataOptional=Optional — no local metadata model provider is configured.")

			if not health.backend then
				status = "critical"
				table.insert(issues, LOC("$$$/StyleAI/Health/BackendFailed=Local background service is not reachable."))
				color = { 0.8, 0, 0 }
			end
			if not health.clip then
				status = "critical"
				table.insert(issues, LOC("$$$/StyleAI/Health/ClipMissing=Vision model is not ready."))
				color = { 0.8, 0, 0 }
			end
			if not health.ollama and not health.lmstudio then
				if status ~= "critical" then
					status = "warning"
					color = { 0.1, 0.5, 0.8 } -- Blue instead of yellow to indicate it's optional
				end
				table.insert(
					issues,
					LOC("$$$/StyleAI/Health/LocalLlmOptional=Local LLM not configured (AI Auto-Tagging disabled, but Semantic Search and Predictive AI Editing work).")
				)
			end

			propertyTable.healthStatus = status
			propertyTable.healthIssues = table.concat(issues, "\n")
			propertyTable.healthColor = color
		end)
	end

	updateHealth()
	LrTasks.startAsyncTask(function()
		while propertyTable.keepChecksRunning do
			for _ = 1, 20 do
				if not propertyTable.keepChecksRunning then break end
				LrTasks.sleep(0.5)
			end
			if propertyTable.keepChecksRunning then
				updateHealth()
			end
		end
	end)

	-- Update Check initialization
	propertyTable.updateStatus = ""
	propertyTable.updateStatusColor = { 0.7, 0.7, 0.7 }
	propertyTable.updateButtonTitle = LOC("$$$/StyleAI/PluginInfoDialogSections/UpdateCheck=Check for updates")
	propertyTable.updateAvailable = false
	propertyTable.latestReleaseInfo = nil

	local function checkUpdates()
		LrTasks.startAsyncTask(function()
			local info = UpdateCheck.getLatestReleaseInfo()
			if info and info.is_newer then
				propertyTable.latestReleaseInfo = info
				propertyTable.updateAvailable = true
				propertyTable.updateStatus =
					LOC("$$$/StyleAI/PluginInfo/UpdateAvailable=Update Available: ^1", info.tag_name)
				propertyTable.updateStatusColor = { 0.1, 0.5, 0.8 }
				if info.is_code_only then
					propertyTable.updateButtonTitle = LOC("$$$/StyleAI/UpdateCheck/UpdateNow=Update Now")
				else
					propertyTable.updateButtonTitle = LOC("$$$/StyleAI/PluginInfo/DownloadUpdate=Download Update")
				end
			else
				propertyTable.updateStatus = LOC("$$$/StyleAI/PluginInfo/UpToDate=Plugin is up to date")
				propertyTable.updateStatusColor = { 0.5, 0.5, 0.5 }
				propertyTable.updateButtonTitle =
					LOC("$$$/StyleAI/PluginInfoDialogSections/UpdateCheck=Check for updates")
				propertyTable.updateAvailable = false
			end
		end)
	end

	checkUpdates()
	propertyTable.manualCheckUpdates = checkUpdates
end

function PluginInfoDialogSections.sectionsForBottomOfDialog(f, propertyTable)
	local bind = LrView.bind
	local share = LrView.share

	return {
		{
			bind_to_object = propertyTable,
			title = LOC("$$$/StyleAI/PluginInfo/UpdatesAndLogs=Updates & Log Files"),

			f:group_box({
				title = LOC("$$$/StyleAI/PluginInfo/UpdatesAndLogs=Updates & Log Files"),
				fill_horizontal = 1,

				f:row({
					f:static_text({
						title = bind("updateStatus"),
						text_color = bind("updateStatusColor"),
						fill_horizontal = 1,
						alignment = "center",
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/ShowLogfile=Show logfile"),
						action = function(button)
							LrShell.revealInShell(Util.getLogfilePath())
						end,
					}),
					f:push_button({
						title = LOC(
							"$$$/StyleAI/PluginInfoDialogSections/CopyLogToDesktop=Copy logfiles to Desktop"
						),
						action = function(button)
							LrTasks.startAsyncTask(function()
								Util.copyLogfilesToDesktop()
							end)
						end,
					}),
					f:push_button({
						title = bind("updateButtonTitle"),
						action = function(button)
							if propertyTable.updateAvailable then
								if propertyTable.latestReleaseInfo.is_code_only then
									local tu = require("TaskUpdate")
									tu.runUpdate(propertyTable.latestReleaseInfo)
								else
									LrHttp.openUrlInBrowser(
										propertyTable.latestReleaseInfo.release_url or UpdateCheck.latestReleaseUrl
									)
								end
							else
								propertyTable.manualCheckUpdates()
							end
						end,
					}),
				}),
				f:row({
					f:checkbox({
						value = bind("periodicalUpdateCheck"),
						title = LOC(
							"$$$/StyleAI/PluginInfoDialogSections/periodUpdateCheck=Periodically check for Updates"
						),
					}),
				}),
			}),
		},
		{
			title = LOC("$$$/StyleAI/PluginInfo/Credits=About"),
			f:group_box({
				fill_horizontal = 1,
				title = LOC("$$$/StyleAI/PluginInfo/Credits=About"),
				f:row({
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/AboutText=StyleAI is local-first software built with open-source libraries and models."),
						fill_horizontal = 1,
						wrap = true,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/ViewCredits=View Credits"),
						action = function()
							LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki/Credits")
						end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/ViewLicense=View License"),
						action = function()
						LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/blob/main/LICENSE")
					end,
					}),
				}),
				UIFactory.FormRow(f, {
					label = LOC("$$$/StyleAI/PluginInfo/PluginVersion=Plugin version:"),
					labelWidth = share("aboutLabelWidth"),
					f:static_text({ title = bind("pluginVersionText") }),
				}),
				UIFactory.FormRow(f, {
					label = LOC("$$$/StyleAI/PluginInfo/BackendVersion=Service version:"),
					labelWidth = share("aboutLabelWidth"),
					f:static_text({ title = bind("backendVersionText") }),
				}),
				UIFactory.Notice(f, {
					kind = "warning",
					visible = bind("developerBuild"),
					title = LOC("$$$/StyleAI/PluginInfo/DeveloperBuild=Developer build — developer-only tools are enabled."),
				}),
			}),
		},
	}
end

function PluginInfoDialogSections.sectionsForTopOfDialog(f, propertyTable)
	local bind = LrView.bind
	local share = LrView.share

	-- We remove the prompt title menu setup entirely as it was moved.

	return {
		{
			bind_to_object = propertyTable,
			title = LOC("$$$/StyleAI/PluginInfoDialogSections/header=StyleAI configuration"),

			-- 1. System Setup & Health
			f:group_box({
				fill_horizontal = 1,
				title = LOC("$$$/StyleAI/Health/SummaryTitle=System Setup & Health"),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/Health/SummaryTitle=System Health:"),
						font = "<system/bold>",
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:static_text({
						title = bind({
							key = "healthStatus",
							transform = function(v)
								if v == "healthy" then
									return LOC("$$$/StyleAI/Health/StatusHealthy=Ready — everything required is available.")
								end
								if v == "warning" then
									return LOC("$$$/StyleAI/Health/StatusWarning=Limited — an optional feature needs attention.")
								end
								return LOC("$$$/StyleAI/Health/StatusCritical=Unavailable — required setup needs attention.")
							end,
						}),
						text_color = bind("healthColor"),
					}),
				}),
				UIFactory.StatusRow(f, {
					label = LOC("$$$/StyleAI/Health/ServiceLabel=Background service:"),
					labelWidth = share("healthLabelWidth"),
					title = bind("backendStatusText"),
				}),
				UIFactory.StatusRow(f, {
					label = LOC("$$$/StyleAI/Health/VisionLabel=Vision model:"),
					labelWidth = share("healthLabelWidth"),
					title = bind("visionStatusText"),
				}),
				UIFactory.StatusRow(f, {
					label = LOC("$$$/StyleAI/Health/MetadataLabel=Metadata model:"),
					labelWidth = share("healthLabelWidth"),
					title = bind("metadataStatusText"),
				}),
				f:row({
					visible = bind({
						key = "healthIssues",
						transform = function(v)
							return v ~= ""
						end,
					}),
					f:spacer({ width = share("labelWidth") }),
					f:static_text({
						fill_horizontal = 1,
						title = bind("healthIssues"),
						text_color = bind("healthColor"),
						size = "small",
						wrap = true,
					}),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/Health/RunWizard=Run Setup Wizard"),
						action = function()
							OnboardingWizard.show(true)
						end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/Docs=Read documentation online"),
						action = function(button)
							LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki")
						end,
					}),
				}),
			}),

			-- 2. My Signature Styles (Editing Engine Prerequisites)
			f:group_box({
				fill_horizontal = 1,
				title = LOC("$$$/StyleAI/Training/SectionTitle=My Signature Styles"),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/Training/EngineStatus=Style Engine Status:"),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:static_text({
						title = bind("styleReadyText"),
						text_color = bind("styleReadyColor"),
						font = "<system/bold>",
						fill_horizontal = 1,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/Training/SavedExamples=Saved training examples:"),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:static_text({
						title = bind({
							key = "trainingCount",
							transform = function(v)
								return tostring(v or 0)
							end,
						}),
						fill_horizontal = 1,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/Training/TopDescriptors=Top Content Descriptors:"),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:static_text({
						fill_horizontal = 1,
						title = bind({
							key = "styleStats",
							transform = function(s)
								if not s or not s.descriptor_distribution then
									return "..."
								end
								local sorted = {}
								for k, v in pairs(s.descriptor_distribution) do
									table.insert(sorted, { name = k, count = v })
								end
								table.sort(sorted, function(a, b)
									return a.count > b.count
								end)
								local top = {}
								for i = 1, math.min(3, #sorted) do
									local name = sorted[i].name:gsub("_", " ")
									table.insert(top, name:sub(1, 1):upper() .. name:sub(2))
								end
								return #top > 0 and table.concat(top, ", ") or "None yet"
							end,
						}),
						font = "<system/italic>",
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/Training/LearnedCameras=Learned Cameras:"),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:static_text({
						fill_horizontal = 1,
						title = bind({
							key = "styleStats",
							transform = function(s)
								if not s or not s.camera_distribution then
									return "..."
								end
								local sorted = {}
								for k, v in pairs(s.camera_distribution) do
									table.insert(sorted, { name = k, count = v })
								end
								table.sort(sorted, function(a, b)
									return a.count > b.count
								end)
								local top = {}
								for i = 1, math.min(5, #sorted) do
									table.insert(top, string.format("%s (%d)", sorted[i].name, sorted[i].count))
								end
								return #top > 0 and table.concat(top, "\n") or "None yet"
							end,
						}),
						font = "<system/italic>",
						wrap = true,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/Training/TopSignatureStyles=Top Signature Styles:"),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:static_text({
						fill_horizontal = 1,
						title = bind({
							key = "styleStats",
							transform = function(s)
								if not s or not s.top_signature_styles then
									return "..."
								end
								local top = {}
								for i = 1, math.min(5, #s.top_signature_styles) do
									table.insert(top, string.format("%s (%d)", s.top_signature_styles[i].name, s.top_signature_styles[i].count))
								end
								return #top > 0 and table.concat(top, "\n") or "None yet"
							end,
						}),
						font = "<system/bold>",
						wrap = true,
					}),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/common/Refresh=Refresh"),
						action = function(button)
							propertyTable.refreshStyleStats()
						end,
					}),

				}),
			}),

			-- 4. Advanced Server Settings & Maintenance
			f:group_box({
				fill_horizontal = 1,
				title = LOC("$$$/StyleAI/PluginInfo/AdvancedSettings=Performance, Maintenance & Diagnostics"),
				-- dbStoragePath UI removed
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/ParallelTasks=Indexing speed"),
						width = share("labelWidth"),
						alignment = "right",
					}),
					f:column({
						spacing = 2,
						f:row({
							f:slider({
								value = bind("indexingPerformanceProfile"),
								min = 1,
								max = 4,
								integral = true,
								width = 200,
							}),
							f:static_text({
								title = bind({
									key = "indexingPerformanceProfile",
									transform = function(v)
										local val = tonumber(v) or 2
										if val == 1 then return LOC("$$$/StyleAI/PluginInfo/ThreadsLow=Stable")
										elseif val == 2 then return LOC("$$$/StyleAI/PluginInfo/ThreadsMed=Balanced")
										elseif val == 3 then return LOC("$$$/StyleAI/PluginInfo/ThreadsHigh=Fast")
										else return LOC("$$$/StyleAI/PluginInfo/ThreadsMax=Maximum") end
									end,
								}),
							}),
						}),
						f:static_text({
							title = LOC("$$$/StyleAI/PluginInfo/ParallelTasksHelp=Controls the maximum network connections to the background service. The plugin will automatically scale down based on the active LLM or GPU constraints."),
							text_color = LrColor(0.5, 0.5, 0.5),
							font = "<system/small>",
						}),
					}),
				}),
				UIFactory.FormRow(f, {
					label = LOC("$$$/StyleAI/PluginInfo/DatabaseLocation=Catalog-local data:"),
					labelWidth = share("labelWidth"),
					f:edit_field({
						value = bind("databasePath"),
						enabled = false,
						fill_horizontal = 1,
					}),
				}),
				UIFactory.HelpText(f, {
					title = LOC("$$$/StyleAI/PluginInfo/DatabaseLocationHelp=StyleAI keeps this catalog's search index, training examples, learned styles, and edit history beside the Lightroom catalog."),
				}),

				f:row({
					fill_horizontal = 1,
					f:checkbox({
						value = bind("usePreviewThumbnails"),
						title = LOC("$$$/StyleAI/PluginInfo/UsePreviewThumbnails=Use Lightroom previews for faster indexing"),
					}),
				}),
				f:separator({ fill_horizontal = 1 }),
				UIFactory.HelpText(f, {
					title = LOC("$$$/StyleAI/PluginInfo/BackupScopeNote=StyleAI backups protect AI indexes, training data, learned styles, and history. They do not back up the Lightroom catalog, photo files, or Develop edits."),
					text_color = LrColor(0.5, 0.5, 0.5),
				}),
				f:separator({ fill_horizontal = 1 }),
				UIFactory.DestructiveAction(f, {
					title = LOC("$$$/StyleAI/Training/WipeAll=Delete All Training Data"),
					explanation = LOC("$$$/StyleAI/Training/WipeMaintenanceHelp=Permanently delete saved training examples and learned styles. Search data, Lightroom photos, and Develop edits are not changed."),
					action = function()
						local confirm = LrDialogs.confirm(
							LOC("$$$/StyleAI/Training/WipeConfirmTitle=Delete All Training Data"),
							LOC("$$$/StyleAI/Training/WipeConfirmMsg=This permanently deletes all saved training examples and learned styles. Lightroom photos and Develop edits are not changed. Continue?"),
							LOC("$$$/StyleAI/Training/WipeConfirmOk=Delete Training Data"),
							LOC("$$$/StyleAI/Training/WipeConfirmCancel=Cancel")
						)
						if confirm ~= "ok" then return end
						LrTasks.startAsyncTask(function()
							local ok, err = SearchIndexAPI.clearAllTrainingData()
							if ok then
								propertyTable.refreshStyleStats()
								LrDialogs.message(
									LOC("$$$/StyleAI/Training/WipedTitle=Training Data Deleted"),
									LOC("$$$/StyleAI/Training/WipedMsg=Saved training examples and learned styles were permanently deleted."),
									"info"
								)
							else
								ErrorHandler.handleError(LOC("$$$/StyleAI/Training/WipeFailedTitle=Delete Failed"), tostring(err or "Unknown error"))
							end
						end)
					end,
				}),
				f:separator({ fill_horizontal = 1 }),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/ShowDbStats=Show DB stats"),
						action = function(button)
							LrTasks.startAsyncTask(function()
								local stats, err = SearchIndexAPI.getStats()
								if stats then
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/DbStatsTitle=Database statistics"),
										SearchIndexAPI.formatStats(stats),
										"info"
									)
								else
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/DbStatsFailed=Database statistics failed"),
										tostring(err or LOC("$$$/StyleAI/common/UnknownError=Unknown error")),
										"critical"
									)
								end
							end)
						end,
					}),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/RestoreDbBackup=Restore Backup..."),
						action = function(button)
							local confirm = LrDialogs.confirm(
								LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupTitle=Restore StyleAI Database"),
								LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupConfirm=This restores StyleAI's AI data only. It does not restore your Lightroom catalog, photo files, or Develop edits. StyleAI will validate the backup and create a pre-restore recovery snapshot before replacing its current database. Continue?"),
								LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupAction=Choose Backup"),
								LOC("$$$/StyleAI/common/Cancel=Cancel")
							)
							if confirm ~= "ok" then return end
							LrTasks.startAsyncTask(function()
								local ok, result = SearchIndexAPI.restoreDatabaseBackup()
								if ok then
									propertyTable.refreshStyleStats()
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupComplete=StyleAI database restored"),
										LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupCompleteMessage=The validated backup was restored successfully. Your Lightroom catalog and Develop edits were not changed."),
										"info"
									)
								elseif result ~= "canceled" then
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupFailed=Database restore failed"),
										tostring(result or LOC("$$$/StyleAI/common/UnknownError=Unknown error")),
										"critical"
									)
								end
							end)
						end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/DownloadDbBackup=Download Backup"),
						action = function(button)
							LrTasks.startAsyncTask(function()
								local result, path = SearchIndexAPI.downloadDatabaseBackup()
								if result then
									LrShell.revealInShell(path)
									LrDialogs.message(LOC("$$$/StyleAI/PluginInfo/DbBackupDownloaded=Database backup downloaded."), path)
								else
									LrDialogs.message(LOC("$$$/StyleAI/PluginInfo/DbBackupFailed=Database backup failed"), tostring(result or LOC("$$$/StyleAI/common/UnknownError=Unknown error")), "critical")
								end
							end)
						end,
					}),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PruneDatabase/MenuItem=Prune Database"),
						tooltip = LOC("$$$/StyleAI/PluginInfo/PruneDatabaseTooltip=Removes deleted or missing photos from the AI database to free up space."),
						action = function(button)
							LrTasks.startAsyncTask(function()
								local task = require("TaskPruneDatabase")
								task.process()
							end)
						end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/RestartBackend=Restart Service"),
						tooltip = LOC("$$$/StyleAI/PluginInfo/RestartBackendTooltip=Restarts the local background service process."),
						action = function(button)
							LrTasks.startAsyncTask(function()
								local progressScope = LrProgressScope({ title = LOC("$$$/StyleAI/PluginInfo/Restarting=Restarting..."), functionContext = nil })
								local ok, err = SearchIndexAPI.restartBackend()
								progressScope:done()
								if ok then
									LrDialogs.message(LOC("$$$/StyleAI/PluginInfo/RestartBackend=Restart Service"), LOC("$$$/StyleAI/PluginInfo/RestartSuccess=Service restarted successfully."))
								else
									LrDialogs.message(LOC("$$$/StyleAI/PluginInfo/RestartBackend=Restart Service"), LOC("$$$/StyleAI/PluginInfo/RestartFailed=Failed to restart service: ^1", tostring(err)), "critical")
								end
							end)
						end,
					}),
				}),
			}),
			f:group_box({
				title = LOC("$$$/StyleAI/Debug/Section=Debug Options"),
				fill_horizontal = 1,
				f:column({
					fill_horizontal = 1,
					spacing = f:control_spacing(),
					f:checkbox({
						value = bind("debugMode"),
						title = LOC("$$$/StyleAI/Debug/Enable=Enable Debug options"),
					}),
					f:column({
						visible = bind("debugMode"),
						fill_horizontal = 1,
						spacing = f:control_spacing(),
						UIFactory.Notice(f, {
							kind = "warning",
							title = LOC("$$$/StyleAI/Debug/PrivacyWarning=Debug capture is local, but saved files can contain photo pixels and metadata. Enable it only while troubleshooting."),
						}),
						f:checkbox({
							value = bind("captureLlmInputs"),
							title = LOC("$$$/StyleAI/Debug/CaptureInputs=Capture LLM image inputs for troubleshooting"),
						}),
						f:row({
							fill_horizontal = 1,
							f:static_text({
								title = LOC("$$$/StyleAI/Debug/Destination=Destination:"),
								width = share("debugLabelWidth"),
							}),
							f:edit_field({
								value = bind("captureLlmInputsPath"),
								enabled = false,
								fill_horizontal = 1,
							}),
							f:push_button({
								title = LOC("$$$/StyleAI/common/Choose=Choose..."),
								action = function()
									local selected = LrDialogs.runOpenPanel({
										title = LOC("$$$/StyleAI/Debug/ChooseDestination=Choose a diagnostic capture folder"),
										canChooseFiles = false,
										canChooseDirectories = true,
										allowsMultipleSelection = false,
									})
									if selected and selected[1] then
										propertyTable.captureLlmInputsPath = selected[1]
										propertyTable.refreshCaptureInfo()
									end
								end,
							}),
							f:push_button({
								title = LOC("$$$/StyleAI/common/Reveal=Reveal"),
								enabled = bind({ key = "captureLlmInputsPath", transform = function(v) return v and v ~= "" end }),
								action = function()
									if propertyTable.captureLlmInputsPath ~= "" then
										LrShell.revealInShell(propertyTable.captureLlmInputsPath)
									end
								end,
							}),
						}),
						UIFactory.HelpText(f, { title = bind("captureInfoText") }),
						f:row({
							f:push_button({
								title = LOC("$$$/StyleAI/Debug/ClearCaptures=Clear Captured Debug Data..."),
								action = function()
									local confirmed = LrDialogs.confirm(
										LOC("$$$/StyleAI/Debug/ClearTitle=Clear Captured Debug Data?"),
										LOC("$$$/StyleAI/Debug/ClearMessage=This deletes only StyleAI diagnostic capture files in the selected debug folder. Photos, catalogs, databases, and normal logs are not changed."),
										LOC("$$$/StyleAI/Debug/ClearAction=Clear Captures"),
										LOC("$$$/StyleAI/common/Cancel=Cancel")
									)
									if confirmed ~= "ok" then return end
									LrTasks.startAsyncTask(function()
										local result, err = SearchIndexAPI.clearDiagnosticCaptures(propertyTable.captureLlmInputsPath)
										if result then
											propertyTable.refreshCaptureInfo()
											LrDialogs.message(
												LOC("$$$/StyleAI/Debug/ClearedTitle=Debug Captures Cleared"),
												LOC("$$$/StyleAI/Debug/ClearedMessage=Deleted ^1 diagnostic file(s).", tostring(result.deleted_files or 0)),
												"info"
											)
										else
											ErrorHandler.handleError(LOC("$$$/StyleAI/Debug/ClearFailed=Could Not Clear Debug Captures"), tostring(err))
										end
									end)
								end,
							}),
						}),
					}),
				}),
			}),
		},
		{
			title = LOC("$$$/StyleAI/PluginInfo/Support=Support & Diagnostics"),
			synopsis = bind("healthStatus"),
			f:column({
				spacing = f:control_spacing(),
				fill_horizontal = 1,
				f:static_text({
					title = LOC("$$$/StyleAI/PluginInfo/DiagnosticsInfo=If you are experiencing issues with StyleAI, generate a diagnostic report. This report contains local health statuses and backend logs to help with troubleshooting."),
					fill_horizontal = 1,
					wrap = true,
				}),
				f:push_button({
					title = LOC("$$$/StyleAI/PluginInfo/GenerateDiagnosticReport=Generate Diagnostic Report"),
					font = "<system/bold>",
					action = function()
						TaskDiagnostics.generateReport()
					end,
				}),
			}),
		},
	}
end
function PluginInfoDialogSections.endDialog(propertyTable)
	prefs.indexingPerformanceProfile = tonumber(propertyTable.indexingPerformanceProfile) or 2
	prefs.usePreviewThumbnails = (propertyTable.usePreviewThumbnails ~= false)

	prefs.logging = propertyTable.logging
	if propertyTable.logging then
		log:enable("logfile")
	else
		log:disable()
	end

	prefs.periodicalUpdateCheck = propertyTable.periodicalUpdateCheck
	prefs.debugMode = propertyTable.debugMode == true
	prefs.captureLlmInputs = prefs.debugMode and propertyTable.captureLlmInputs == true
	prefs.captureLlmInputsPath = propertyTable.captureLlmInputsPath

	propertyTable.keepChecksRunning = false -- Stop background health polling
end

return PluginInfoDialogSections
