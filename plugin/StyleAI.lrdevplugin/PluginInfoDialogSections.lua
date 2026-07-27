local SettingsManager = require("SettingsManager")
local TaskDiagnostics = require("TaskDiagnostics")

PluginInfoDialogSections = {}

function PluginInfoDialogSections.startDialog(propertyTable)
	propertyTable.keepChecksRunning = true
	propertyTable.logging = prefs.logging


	propertyTable.exportSize = prefs.exportSize
	propertyTable.exportQuality = prefs.exportQuality
	propertyTable.usePreviewThumbnails = (prefs.usePreviewThumbnails ~= false)

	propertyTable.promptTitles = {}
	for title in pairs(prefs.prompts) do
		table.insert(propertyTable.promptTitles, { title = title, value = title })
	end

	propertyTable.prompt = prefs.prompt
	propertyTable.prompts = prefs.prompts

	propertyTable.selectedPrompt = prefs.prompts[prefs.prompt]

	propertyTable:addObserver("prompt", function(properties, key, newValue)
		properties.selectedPrompt = properties.prompts[newValue]
	end)

	propertyTable:addObserver("selectedPrompt", function(properties, key, newValue)
		properties.prompts[properties.prompt] = newValue
	end)

	propertyTable.periodicalUpdateCheck = prefs.periodicalUpdateCheck == nil and true or prefs.periodicalUpdateCheck
	propertyTable.shutdownServerOnExit = prefs.shutdownServerOnExit == nil and true or prefs.shutdownServerOnExit
	propertyTable.indexingPerformanceProfile = tonumber(prefs.indexingPerformanceProfile) or 2
	propertyTable.indexingBatchSize = tostring(prefs.indexingBatchSize or "32")
	propertyTable.semanticClusteringThresholdInt = math.floor((tonumber(prefs.semanticClusteringThreshold) or 0.94) * 100)
	propertyTable.forceFreshPreviews = prefs.forceFreshPreviews or false
	propertyTable.auditLlmInputs = prefs.auditLlmInputs or false
	propertyTable.auditLlmInputsPath = prefs.auditLlmInputsPath or ""
	propertyTable.backupRotationDays = prefs.backupRotationDays or "0"
	propertyTable.usePreviewThumbnails = prefs.usePreviewThumbnails == nil and true or prefs.usePreviewThumbnails

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

	local function updateHealth()
		LrTasks.startAsyncTask(function()
			local health = SearchIndexAPI.getDetailedHealth()
			local status = "healthy"
			local issues = {}
			local color = { 0, 0.8, 0 }

			if not health.backend then
				status = "critical"
				table.insert(issues, LOC("$$$/StyleAI/Health/BackendFailed=Local background service is not reachable."))
				color = { 0.8, 0, 0 }
			else
				table.insert(issues, LOC("$$$/StyleAI/Health/BackendOk=Local ML Engine: Running (Editing fully functional)."))
			end
			if not health.ollama and not health.lmstudio then
				if status ~= "critical" then
					status = "warning"
					color = { 0.1, 0.5, 0.8 } -- Blue instead of yellow to indicate it's optional
				end
				table.insert(
					issues,
					LOC("$$$/StyleAI/Health/ApiKeysMissing=LLM not configured (AI Auto-Tagging disabled, but Semantic Search and Predictive AI Editing work).")
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
			title = LOC("$$$/StyleAI/PluginInfo/Logging=Logging"),

			f:group_box({
				title = LOC("$$$/StyleAI/PluginInfo/Logging=Logging"),
				width = 600,

				f:row({
					f:static_text({
						title = bind("updateStatus"),
						text_color = bind("updateStatusColor"),
						width = share("bottomButtons"),
						alignment = "center",
					}),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/ShowLogfile=Show logfile"),
						action = function(button)
							LrShell.revealInShell(Util.getLogfilePath())
						end,
						width = share("bottomButtons"),
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
						width = share("bottomButtons"),
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
						width = share("bottomButtons"),
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
			title = LOC("$$$/StyleAI/PluginInfo/Credits=CREDITS"),
			f:group_box({
				width = 600,
				title = LOC("$$$/StyleAI/PluginInfo/Credits=CREDITS"),
				f:row({
					f:static_text({
						title = Defaults.copyrightString,
						width_in_chars = 140,
						height_in_lines = 20,
					}),
				}),
			}),
		},
	}
end

function PluginInfoDialogSections.sectionsForTopOfDialog(f, propertyTable)
	local bind = LrView.bind
	local share = LrView.share

	local groupBoxWidth = 600

	-- We remove the prompt title menu setup entirely as it was moved.

	return {
		{
			bind_to_object = propertyTable,
			title = LOC("$$$/StyleAI/PluginInfoDialogSections/header=StyleAI configuration"),

			-- 1. System Setup & Health
			f:group_box({
				width = groupBoxWidth,
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
									return LOC("$$$/StyleAI/Health/StatusHealthy=✅ Everything looks good!")
								end
								if v == "warning" then
									return LOC("$$$/StyleAI/Health/StatusWarning=⚠️ Some features might not work correctly.")
								end
								return LOC("$$$/StyleAI/Health/StatusCritical=🚨 Critical issues detected. Plugin cannot function.")
							end,
						}),
						text_color = bind("healthColor"),
					}),
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
				width = groupBoxWidth,
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
						title = LOC("$$$/StyleAI/Training/TopScenes=Top Scene Types:"),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:static_text({
						fill_horizontal = 1,
						title = bind({
							key = "styleStats",
							transform = function(s)
								if not s or not s.scene_distribution then
									return "..."
								end
								local sorted = {}
								for k, v in pairs(s.scene_distribution) do
									table.insert(sorted, { name = k, count = v })
								end
								table.sort(sorted, function(a, b)
									return a.count > b.count
								end)
								local top = {}
								for i = 1, math.min(3, #sorted) do
									local name = sorted[i].name:gsub("^scene_", ""):gsub("_", " ")
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
						height_in_lines = 5,
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
						height_in_lines = 5,
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

					f:push_button({
						title = LOC("$$$/StyleAI/Training/WipeAll=🛑 Wipe All Training"),
						action = function(button)
							local confirm = LrDialogs.confirm(
								LOC("$$$/StyleAI/Training/WipeConfirmTitle=Wipe All Training Data"),
								LOC("$$$/StyleAI/Training/WipeConfirmMsg=This is a destructive process. It will completely delete all discovered signature styles AND all underlying vector embeddings for your training examples. This cannot be undone. Continue?"),
								LOC("$$$/StyleAI/Training/WipeConfirmOk=Wipe Everything"),
								LOC("$$$/StyleAI/Training/WipeConfirmCancel=Cancel")
							)
							if confirm == "ok" then
								LrTasks.startAsyncTask(function()
									local ok, err = SearchIndexAPI.clearAllTrainingData()
									if ok then
										propertyTable.refreshStyleStats()
										LrDialogs.message(LOC("$$$/StyleAI/Training/WipedTitle=Training Wiped"), LOC("$$$/StyleAI/Training/WipedMsg=All training examples and signature styles have been permanently deleted."), "info")
									else
										ErrorHandler.handleError(LOC("$$$/StyleAI/Training/WipeFailedTitle=Wipe Failed"), tostring(err or "Unknown error"))
									end
								end)
							end
						end,
					}),
				}),
			}),

			-- 4. Advanced Server Settings & Maintenance
			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/PluginInfo/AdvancedSettings=Advanced Service Settings & Maintenance"),
				-- dbStoragePath UI removed
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/BackupRotationDays=Days before DB backups rotate (0 = off)"),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:edit_field({
						value = bind("backupRotationDays"),
						fill_horizontal = 1,
						width_in_chars = 4,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/ParallelTasks=Backend Parallel Tasks"),
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

				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/SemanticClustering=Clustering Threshold:"),
						width = share("labelWidth"),
						alignment = "right",
					}),
					f:column({
						f:row({
							f:slider({
								value = bind("semanticClusteringThresholdInt"),
								tooltip = LOC("$$$/StyleAI/PluginInfo/SemanticClusteringTooltip=Adjusts how strictly AI groups similar photos. Higher values mean photos must be more similar to group together."),
								min = 80,
								max = 100,
								integral = true,
								immediate = true,
								width = 200,
							}),
							f:static_text({
								title = bind({
									key = "semanticClusteringThresholdInt",
									transform = function(v) return string.format("%.2f", (v or 94) / 100) end,
								}),
								width_in_chars = 5,
							}),
						}),
						f:push_button({
							title = LOC("$$$/StyleAI/common/Reset=Reset"),
							width = 60,
							action = function() propertyTable.semanticClusteringThresholdInt = 94 end,
						}),
					}),
					f:spacer({ fill_horizontal = 1 }),
				}),
				f:row({
					fill_horizontal = 1,
					f:checkbox({
						value = bind("usePreviewThumbnails"),
						title = LOC("$$$/StyleAI/PluginInfo/UsePreviewThumbnails=Use Lightroom previews for faster indexing"),
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:checkbox({
						value = bind("shutdownServerOnExit"),
						title = LOC("$$$/StyleAI/PluginInfo/ShutdownOnExit=Shut down background service when Lightroom exits"),
					}),
				}),
				f:separator({ fill_horizontal = 1 }),
				f:row({
					fill_horizontal = 1,
					f:checkbox({
						value = bind("forceFreshPreviews"),
						title = LOC("$$$/StyleAI/PluginInfo/ForceFreshPreviews=Force generate fresh LLM previews (Bypass cache)"),
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:checkbox({
						value = bind("auditLlmInputs"),
						title = LOC("$$$/StyleAI/PluginInfo/AuditLlmInputs=Audit LLM inputs (Save copies of images)"),
					}),
					f:edit_field({
						value = bind("auditLlmInputsPath"),
						enabled = bind("auditLlmInputs"),
						width_in_chars = 30,
						tooltip = LOC("$$$/StyleAI/PluginInfo/AuditDirTooltip=Directory to save audited images"),
					}),
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
		},
		{
			title = LOC("$$$/StyleAI/PluginInfo/Support=Support & Diagnostics"),
			synopsis = bind("healthStatus"),
			f:column({
				spacing = f:control_spacing(),
				fill_horizontal = 1,
				f:static_text({
					title = LOC("$$$/StyleAI/PluginInfo/DiagnosticsInfo=If you are experiencing issues with StyleAI, generate a diagnostic report. This report contains local health statuses and backend logs to help with troubleshooting."),
					width_in_chars = 60,
					height_in_lines = 3,
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
	prefs.indexingBatchSize = tonumber(propertyTable.indexingBatchSize) or 32

	prefs.exportSize = propertyTable.exportSize
	prefs.exportQuality = propertyTable.exportQuality
	prefs.usePreviewThumbnails = (propertyTable.usePreviewThumbnails ~= false)
	prefs.semanticClusteringThreshold = tonumber(propertyTable.semanticClusteringThresholdInt) / 100

	prefs.prompt = propertyTable.prompt
	prefs.prompts = propertyTable.prompts

	prefs.logging = propertyTable.logging
	if propertyTable.logging then
		log:enable("logfile")
	else
		log:disable()
	end

	prefs.periodicalUpdateCheck = propertyTable.periodicalUpdateCheck
	prefs.shutdownServerOnExit = (propertyTable.shutdownServerOnExit ~= false)
	prefs.backupRotationDays = propertyTable.backupRotationDays

	prefs.forceFreshPreviews = propertyTable.forceFreshPreviews
	prefs.auditLlmInputs = propertyTable.auditLlmInputs
	prefs.auditLlmInputsPath = propertyTable.auditLlmInputsPath

	propertyTable.keepChecksRunning = false -- Stop background health polling
end

return PluginInfoDialogSections
