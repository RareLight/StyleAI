PluginInfoDialogSections = {}

function PluginInfoDialogSections.startDialog(propertyTable)
	propertyTable.keepChecksRunning = true
	propertyTable.logging = prefs.logging
	propertyTable.geminiApiKey = prefs.geminiApiKey
	propertyTable.chatgptApiKey = prefs.chatgptApiKey

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

	propertyTable.periodicalUpdateCheck = prefs.periodicalUpdateCheck
	propertyTable.shutdownServerOnExit = prefs.shutdownServerOnExit
	propertyTable.dbStoragePath = prefs.dbStoragePath or ""
	propertyTable.backendServerUrl = prefs.backendServerUrl or Defaults.defaultBackendServerUrl
	propertyTable.ollamaBaseUrl = prefs.ollamaBaseUrl or Defaults.defaultOllamaBaseUrl
	propertyTable.lmstudioBaseUrl = prefs.lmstudioBaseUrl or Defaults.defaultLmStudioBaseUrl
	propertyTable.indexingParallelTasks = tostring(prefs.indexingParallelTasks or "3")
	propertyTable.indexingBatchSize = tostring(prefs.indexingBatchSize or "32")
	propertyTable.semanticClusteringThresholdInt = math.floor((tonumber(prefs.semanticClusteringThreshold) or 0.94) * 100)

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
				table.insert(issues, LOC("$$$/StyleAI/Health/BackendFailed=Backend server is not reachable."))
				color = { 0.8, 0, 0 }
			end
			if not health.gemini and not health.chatgpt and not health.ollama and not health.lmstudio then
				if status ~= "critical" then
					status = "warning"
					color = { 0.8, 0.8, 0 }
				end
				table.insert(
					issues,
					LOC("$$$/StyleAI/Health/ApiKeysMissing=No AI providers configured for AI generation.")
				)
			end

			propertyTable.healthStatus = status
			propertyTable.healthIssues = table.concat(issues, "; ")
			propertyTable.healthColor = color
		end)
	end

	updateHealth()
	LrTasks.startAsyncTask(function()
		while propertyTable.keepChecksRunning do
			LrTasks.sleep(10)
			updateHealth()
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

	propertyTable.models = {}

	propertyTable.promptTitleMenu = f:popup_menu({
		items = bind("promptTitles"),
		value = bind("prompt"),
	})

	return {

		{
			bind_to_object = propertyTable,

			title = LOC("$$$/StyleAI/PluginInfoDialogSections/header=StyleAI configuration"),

			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/Health/SummaryTitle=System Health"),

				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/Health/SummaryTitle=System Health"),
						font = "<system/bold>",
						alignment = "right",
					}),
					f:static_text({
						title = bind({
							key = "healthStatus",
							transform = function(v)
								if v == "healthy" then
									return LOC("$$$/StyleAI/Health/StatusHealthy=Everything looks good!")
								end
								if v == "warning" then
									return LOC(
										"$$$/StyleAI/Health/StatusWarning=Some features might not work correctly."
									)
								end
								return LOC(
									"$$$/StyleAI/Health/StatusCritical=Critical issues detected. Plugin cannot function."
								)
							end,
						}),
						text_color = bind("healthColor"),
					}),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/Health/RunWizard=Run Setup Wizard"),
						action = function()
							OnboardingWizard.show(true)
						end,
					}),
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
					title = LOC("$$$/StyleAI/PluginInfoDialogSections/Docs=Read documentation online"),
					action = function(button)
						LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki")
					end,
				}),
			}),
			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/PluginInfoDialogSections/ApiKeys=API keys"),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/GoogleApiKey=Google API key"),
						alignment = "right",
						width = share("apiKeyLabelWidth"),
					}),
					f:edit_field({
						value = bind("geminiApiKey"),
						fill_horizontal = 1,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/GetAPIkey=Get API key"),
						action = function(button)
							LrHttp.openUrlInBrowser("https://aistudio.google.com/app/apikey")
						end,
						width = share("apiKeyButtonWidth"),
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/ChatGPTApiKey=ChatGPT API key"),
						alignment = "right",
						width = share("apiKeyLabelWidth"),
					}),
					f:edit_field({
						value = bind("chatgptApiKey"),
						fill_horizontal = 1,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/GetAPIkey=Get API key"),
						action = function(button)
							LrHttp.openUrlInBrowser("https://platform.openai.com/api-keys")
						end,
						width = share("apiKeyButtonWidth"),
					}),
				}),

			}),
			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/PluginInfoDialogSections/BackendServer=Backend Server"),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC(
							"$$$/StyleAI/PluginInfoDialogSections/BackendServerUrl=Backend server URL (IP/hostname)"
						),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:edit_field({
						value = bind("backendServerUrl"),
						fill_horizontal = 1,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/DbStoragePath=Database storage folder"),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:edit_field({
						value = bind("dbStoragePath"),
						fill_horizontal = 1,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/Browse=Browse..."),
						action = function(button)
							local result = LrDialogs.runOpenPanel({
								title = LOC(
									"$$$/StyleAI/PluginInfo/SelectDbFolderTitle=Select Database Storage Folder"
								),
								prompt = LOC("$$$/StyleAI/PluginInfo/SelectFolder=Select"),
								canChooseFiles = false,
								canChooseDirectories = true,
								allowsMultipleSelection = false,
							})
							if result and result[1] then
								local newPath = result[1]
								local oldPath = (propertyTable.dbStoragePath or ""):gsub("^%s*(.-)%s*$", "%1")
								if oldPath ~= "" and oldPath ~= newPath then
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/DbPathChangedTitle=Database Path Changed"),
										LOC(
											"$$$/StyleAI/PluginInfo/DbPathChangedMessage=The AI search index will be created fresh at the new location. Your existing index data will remain at the old path and will not be moved. Re-run indexing after saving to rebuild the index."
										),
										"warning"
									)
								end
								propertyTable.dbStoragePath = newPath
							end
						end,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = "\n" .. LOC(
							"$$$/StyleAI/PluginInfo/DbStoragePathDesc=Leave empty to store next to the catalog (default)."
						),
						size = "small",
						fill_horizontal = 1,
						wrap = true,
						alignment = "center",
					}),
				}),
				f:row({
					f:push_button({
						title = LOC(
							"$$$/StyleAI/PluginInfo/GeneratePhotoIds=Generate hash-based photo IDs (catalog only)"
						),
						width = share("longBackendButtonWidth"),
						action = function(button)
							LrTasks.startAsyncTask(function()
								local ok, msg = SearchIndexAPI.generateGlobalPhotoIdsForCatalog()
								if ok then
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/PhotoIdGenTitle=Photo-ID Generation"),
										msg or LOC("$$$/StyleAI/common/GenerationCompleted=Generation completed.")
									)
								else
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/PhotoIdGenFailed=Photo-ID Generation failed"),
										msg or LOC("$$$/StyleAI/common/UnknownError=Unknown error"),
										"critical"
									)
								end
							end)
						end,
					}),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/ClaimPhotos=Claim photos for this catalog"),
						action = function(button)
							LrTasks.startAsyncTask(function()
								local progressScope = LrProgressScope({
									title = LOC(
										"$$$/StyleAI/SearchIndexAPI/claimingPhotos=Claiming photos for this catalog..."
									),
									functionContext = nil,
								})
								local ok, err, result = SearchIndexAPI.claimPhotosForCatalog(progressScope)
								progressScope:done()
								if ok then
									local msg = result
											and (LOC("$$$/StyleAI/PluginInfo/ClaimedPrefix=Claimed: ") .. tostring(
												result.claimed
											) .. (result.errors and result.errors > 0 and (LOC(
												"$$$/StyleAI/PluginInfo/ClaimedErrors=; errors: "
											) .. tostring(result.errors)) or ""))
										or LOC("$$$/StyleAI/common/Done=Done.")
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/ClaimPhotosTitle=Claim photos"),
										msg
									)
								else
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/ClaimPhotosFailed=Claim photos failed"),
										tostring(err or LOC("$$$/StyleAI/common/UnknownError=Unknown error")),
										"critical"
									)
								end
							end)
						end,
					}),
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
						title = LOC("$$$/StyleAI/PluginInfo/DownloadDbBackup=Download DB backup"),
						action = function(button)
							LrTasks.startAsyncTask(function()
								local result, path = SearchIndexAPI.downloadDatabaseBackup()
								if result then
									LrShell.revealInShell(path)
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/DbBackupDownloaded=Database backup downloaded."),
										path
									)
								else
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/DbBackupFailed=Database backup failed"),
										tostring(result or LOC("$$$/StyleAI/common/UnknownError=Unknown error")),
										"critical"
									)
								end
							end)
						end,
					}),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/CheckVersions=Check Plugin/Backend versions"),
						width = share("backendButtonWidth"),
						action = function(button)
							LrTasks.startAsyncTask(function()
								local result, err = SearchIndexAPI.checkVersionCompatibility()
								if result then
									local backendTag = tostring(
										result.backend_release_tag
											or (
												"v"
												.. tostring(
													result.backend_version
														or LOC("$$$/StyleAI/common/Unknown=unknown")
												)
											)
									)
									local pluginTag = tostring(
										result.plugin_release_tag
											or (
												"v"
												.. tostring(
													result.plugin_version
														or LOC("$$$/StyleAI/common/Unknown=unknown")
												)
											)
									)
									local buildInfo = LOC("$$$/StyleAI/PluginInfo/PluginBuild=Plugin build: ")
										.. tostring(result.plugin_build or "n/a")
										.. "\n"
										.. LOC("$$$/StyleAI/PluginInfo/BackendBuild=Backend build: ")
										.. tostring(result.backend_build or "n/a")

									if result.compatible then
										LrDialogs.message(
											LOC("$$$/StyleAI/PluginInfo/VersionCheckPassed=Version check passed"),
											LOC(
												"$$$/StyleAI/PluginInfo/VersionMatch=Plugin and backend versions match.\n"
											)
												.. LOC("$$$/StyleAI/PluginInfo/PluginPrefix=Plugin: ")
												.. pluginTag
												.. "\n"
												.. LOC("$$$/StyleAI/PluginInfo/BackendPrefix=Backend: ")
												.. backendTag
												.. "\n\n"
												.. buildInfo
										)
									else
										LrDialogs.message(
											LOC("$$$/StyleAI/PluginInfo/VersionMismatch=Version mismatch"),
											LOC(
												"$$$/StyleAI/PluginInfo/VersionDiffer=Plugin and backend versions differ.\n"
											)
												.. LOC("$$$/StyleAI/PluginInfo/PluginPrefix=Plugin: ")
												.. pluginTag
												.. "\n"
												.. LOC("$$$/StyleAI/PluginInfo/BackendPrefix=Backend: ")
												.. backendTag
												.. "\n"
												.. LOC("$$$/StyleAI/PluginInfo/ReasonPrefix=Reason: ")
												.. tostring(
													result.reason or LOC("$$$/StyleAI/common/Unknown=unknown")
												)
										.. "\n\n"
												.. buildInfo,
											"warning"
										)
									end
								else
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/VersionCheckFailed=Version check failed"),
										tostring(err or LOC("$$$/StyleAI/common/UnknownError=Unknown error")),
										"critical"
									)
								end
							end)
						end,
					}),
					f:push_button({
						title = LOC("Re-analyze Indexed Photos"),
						width = share("backendButtonWidth"),
						action = function(button)
							local prefs = import("LrPrefs").prefsForPlugin()
							prefs.indexScope = "indexed"
							prefs.regenerateMetadata = true
							-- Clear cache to allow re-running the task script
							package.loaded["TaskAnalyzeAndIndex"] = nil
							require("TaskAnalyzeAndIndex")
						end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/RestartBackend=Restart Backend"),
						width = share("backendButtonWidth"),
						action = function(button)
							LrTasks.startAsyncTask(function()
								local progressScope = LrProgressScope({
									title = LOC("$$$/StyleAI/PluginInfo/Restarting=Restarting..."),
									functionContext = nil,
								})
								local ok, err = SearchIndexAPI.restartBackend()
								progressScope:done()
								if ok then
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/RestartBackend=Restart Backend"),
										LOC("$$$/StyleAI/PluginInfo/RestartSuccess=Backend restarted successfully.")
									)
								else
									LrDialogs.message(
										LOC("$$$/StyleAI/PluginInfo/RestartBackend=Restart Backend"),
										LOC(
											"$$$/StyleAI/PluginInfo/RestartFailed=Failed to restart backend: ^1",
											tostring(err)
										),
										"critical"
									)
								end
							end)
						end,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:checkbox({
						value = bind("shutdownServerOnExit"),
						title = LOC("$$$/StyleAI/PluginInfo/ShutdownOnExit=Shut down backend server when Lightroom exits"),
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = "\n" .. LOC(
							"$$$/StyleAI/PluginInfo/ShutdownOnExitHint=Frees memory immediately. Increases startup time next session."
						),
						size = "small",
						font = "italic",
						fill_horizontal = 1,
						wrap = true,
						alignment = "center",
					}),
				}),
			}),
			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/PluginInfoDialogSections/ollamaSettings=Ollama Settings"),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/OllamaBaseUrl=Ollama Base URL"),
						width = share("setupLabelWidth"),
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:edit_field({
						value = bind("ollamaBaseUrl"),
						width_in_chars = 40,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/OllamaSetup=Setup Ollama"),
						action = function(button)
							LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki/Help-Ollama-Setup")
						end,
						width = share("setupButtonWidth"),
					}),
				}),
			}),
			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/PluginInfo/LmStudioSettings=LM Studio Settings"),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/LmStudioUrl=LM Studio Base URL (host:port)"),
						width = share("setupLabelWidth"),
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:edit_field({
						value = bind("lmstudioBaseUrl"),
						width_in_chars = 40,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/SetupLmStudio=Setup LM Studio"),
						action = function(button)
							LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki/Help-LM-Studio-Setup")
						end,
						width = share("setupButtonWidth"),
					}),
				}),
			}),
			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/PluginInfo/AdvancedSettings=Advanced Settings"),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/ParallelTasks=Parallel Indexing\nTasks"),
						width = share("advancedLabelWidth"),
						alignment = "right",
					}),
					f:popup_menu({
						value = bind("indexingParallelTasks"),
						items = {
							{ title = "1", value = "1" },
							{ title = "2", value = "2" },
							{ title = "3 (Default)", value = "3" },
							{ title = "4", value = "4" },
							{ title = "5", value = "5" },
							{ title = "6", value = "6" },
						},
						width = 200,
					}),
					f:spacer({ fill_horizontal = 1 }),
				}),
				f:row({
					fill_horizontal = 1,
					f:spacer({ width = share("advancedLabelWidth") }),
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/ParallelTasksHint=Higher values process photos faster but use more memory. 3 is recommended for Apple Silicon."),
						size = "small",
						fill_horizontal = 1,
						wrap = true,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/BatchSize=Parallel Indexing\nBatch Size"),
						width = share("advancedLabelWidth"),
						alignment = "right",
					}),
					f:popup_menu({
						value = bind("indexingBatchSize"),
						items = {
							{ title = "4", value = "4" },
							{ title = "8", value = "8" },
							{ title = "16", value = "16" },
							{ title = "32 (Recommended)", value = "32" },
							{ title = "64", value = "64" },
						},
						width = 200,
					}),
					f:spacer({ fill_horizontal = 1 }),
				}),
				f:row({
					fill_horizontal = 1,
					f:spacer({ width = share("advancedLabelWidth") }),
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/BatchSizeHint=Number of photos processed per request. Higher values saturate the GPU better but require more VRAM."),
						size = "small",
						fill_horizontal = 1,
						wrap = true,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/SemanticClustering=Clustering\nThreshold:"),
						width = share("advancedLabelWidth"),
						alignment = "right",
					}),
					f:column({
						f:row({
							f:slider({
								value = bind("semanticClusteringThresholdInt"),
								min = 80,
								max = 100,
								integral = true,
								immediate = true,
								width = 200,
							}),
							f:static_text({
								title = bind({
									key = "semanticClusteringThresholdInt",
									transform = function(v)
										return string.format("%.2f", (v or 94) / 100)
									end,
								}),
								width_in_chars = 5,
							}),
						}),
						f:push_button({
							title = LOC("$$$/StyleAI/common/Reset=Reset"),
							width = 60,
							action = function()
								propertyTable.semanticClusteringThresholdInt = 94
							end,
						}),
					}),
					f:spacer({ fill_horizontal = 1 }),
				}),
				f:row({
					fill_horizontal = 1,
					f:spacer({ width = share("advancedLabelWidth") }),
					f:static_text({
						title = LOC("$$$/StyleAI/PluginInfo/SemanticClusteringHint=Higher values require photos to be virtually identical to share generated metadata. Lower values (e.g. 0.80) clump more photos together to save indexing time."),
						size = "small",
						fill_horizontal = 1,
						wrap = true,
					}),
				}),
			}),
			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/UI/Prompts=Prompts"),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						alignment = "right",
						width = share("promptLabelWidth"),
						title = LOC("$$$/StyleAI/UI/PromptTitle=Title"),
					}),
					propertyTable.promptTitleMenu,
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/add=Add"),
						action = function(button)
							PromptConfigProvider.addPrompt(propertyTable)
						end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/delete=Delete"),
						action = function(button)
							PromptConfigProvider.deletePrompt(propertyTable)
						end,
					}),
				}),
				f:row({
					fill_horizontal = 1,
					f:static_text({
						alignment = "right",
						width = share("promptLabelWidth"),
						title = LOC("$$$/StyleAI/PromptConfig/PromptField=Prompt"),
					}),
					f:scrolled_view({
						horizontal_scroller = false,
						vertical_scroller = true,
						fill_horizontal = 1,
						height_in_lines = 15,
						width = 500,
						f:edit_field({
							value = bind("selectedPrompt"),
							fill_horizontal = 1,
							height_in_lines = 30,
							wraps = true,
							width = 480,
						}),
					}),
				}),
			}),
			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/PluginInfo/PreviewSettings=Preview Settings"),
				f:row({
					fill_horizontal = 1,
					f:checkbox({
						value = bind("usePreviewThumbnails"),
						title = LOC(
							"$$$/StyleAI/PluginInfo/UsePreviewThumbnails=Use Lightroom previews for faster indexing"
						),
					}),
				}),
			}),
			f:group_box({
				width = groupBoxWidth,
				title = LOC("$$$/StyleAI/Training/SectionTitle=My Style Profile"),
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
						title = LOC("$$$/StyleAI/Training/StyleDNA=Style DNA (Average):"),
						alignment = "right",
						width = share("labelWidth"),
					}),
					f:static_text({
						fill_horizontal = 1,
						title = bind({
							key = "styleStats",
							transform = function(s)
								if not s or not s.exposure then
									return "..."
								end
								local e = s.exposure
								local parts = {}
								if e.mean_luminance then
									table.insert(parts, string.format("Lum: %.0f%%", e.mean_luminance * 100))
								end
								if e.mean_contrast then
									table.insert(parts, string.format("Con: %.0f%%", e.mean_contrast * 100))
								end
								if e.mean_colorfulness then
									table.insert(parts, string.format("Color: %.0f%%", e.mean_colorfulness * 100))
								end
								return #parts > 0 and table.concat(parts, " | ") or "..."
							end,
						}),
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
						title = LOC("$$$/StyleAI/Training/ClearAll=Clear all training examples"),
						action = function(button)
							local confirm = LrDialogs.confirm(
								LOC("$$$/StyleAI/Training/ClearConfirmTitle=Clear Training Examples"),
								LOC(
									"$$$/StyleAI/Training/ClearConfirmMsg=This will permanently delete all saved training examples. The Style Engine will be reset to Cold Start. Continue?"
								),
								LOC("$$$/StyleAI/Training/ClearConfirmOk=Delete All"),
								LOC("$$$/StyleAI/Training/ClearConfirmCancel=Cancel")
							)
							if confirm == "ok" then
								LrTasks.startAsyncTask(function()
									local ok, err = SearchIndexAPI.clearAllTrainingExamples()
									if ok then
										propertyTable.refreshStyleStats()
										LrDialogs.message(
											LOC("$$$/StyleAI/Training/ClearedTitle=Training Data Cleared"),
											LOC(
												"$$$/StyleAI/Training/ClearedMsg=All training examples have been removed."
											),
											"info"
										)
									else
										ErrorHandler.handleError(
											LOC("$$$/StyleAI/Training/ClearFailedTitle=Clear Failed"),
											tostring(err or "Unknown error")
										)
									end
								end)
							end
						end,
					}),
				}),
			}),
		},
	}
end

function PluginInfoDialogSections.endDialog(propertyTable)
	prefs.geminiApiKey = propertyTable.geminiApiKey
	prefs.chatgptApiKey = propertyTable.chatgptApiKey
	prefs.indexingParallelTasks = tonumber(propertyTable.indexingParallelTasks) or 3
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
	prefs.dbStoragePath = (propertyTable.dbStoragePath and propertyTable.dbStoragePath:gsub("^%s*(.-)%s*$", "%1")) or ""

	if prefs.dbStoragePath ~= "" then
		LrTasks.startAsyncTask(function()
			local ok, errMsg = LrTasks.pcall(function()
				local _, err = SearchIndexAPI.initializeCatalog(prefs.dbStoragePath)
				if err then
					error(err)
				end
			end)
			if not ok then
				ErrorHandler.handleError(
					"DbPathInit",
					LOC(
						"$$$/StyleAI/PluginInfo/DbPathInitFailed=Failed to initialize database at the selected path: ^1\n\nCheck that the folder exists and is writable.",
						tostring(errMsg)
					)
				)
			end
		end)
	end

	if propertyTable.backendServerUrl and propertyTable.backendServerUrl:gsub("^%s*(.-)%s*$", "%1") ~= "" then
		prefs.backendServerUrl = propertyTable.backendServerUrl:gsub("^%s*(.-)%s*$", "%1")
	else
		prefs.backendServerUrl = Defaults.defaultBackendServerUrl
	end

	if propertyTable.ollamaBaseUrl and propertyTable.ollamaBaseUrl:gsub("^%s*(.-)%s*$", "%1") ~= "" then
		prefs.ollamaBaseUrl = propertyTable.ollamaBaseUrl:gsub("^%s*(.-)%s*$", "%1")
	else
		prefs.ollamaBaseUrl = Defaults.defaultOllamaBaseUrl
	end

	if propertyTable.lmstudioBaseUrl and propertyTable.lmstudioBaseUrl:gsub("^%s*(.-)%s*$", "%1") ~= "" then
		prefs.lmstudioBaseUrl = propertyTable.lmstudioBaseUrl:gsub("^%s*(.-)%s*$", "%1")
	else
		prefs.lmstudioBaseUrl = Defaults.defaultLmStudioBaseUrl
	end

	propertyTable.keepChecksRunning = false -- Stop background health polling
end
