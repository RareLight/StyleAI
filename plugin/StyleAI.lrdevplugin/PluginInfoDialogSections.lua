local TaskDiagnostics = require("TaskDiagnostics")
local UIFactory = require("UIFactory")
local BuildConfig = require("BuildConfig")

PluginInfoDialogSections = {}

local function abbreviatePath(path, maxCharacters)
	path = tostring(path or "")
	maxCharacters = tonumber(maxCharacters) or 84
	if #path <= maxCharacters then return path end
	return "…" .. string.sub(path, -(maxCharacters - 1))
end

local function processingLoadMode(profile)
	profile = tonumber(profile)
	if profile and profile <= 1 then return "lower" end
	if profile and profile >= 3 then return "faster" end
	return "automatic"
end

function PluginInfoDialogSections.startDialog(propertyTable)
	propertyTable.keepChecksRunning = true
	propertyTable.developerBuild = BuildConfig.developerBuild == true
	propertyTable.periodicalUpdateCheck = prefs.periodicalUpdateCheck == true
	propertyTable.processingLoadMode = processingLoadMode(prefs.indexingPerformanceProfile)

	propertyTable.debugMode = prefs.debugMode == true
	propertyTable.captureLlmInputs = propertyTable.debugMode and prefs.captureLlmInputs == true
	propertyTable.captureLlmInputsPath = prefs.captureLlmInputsPath or ""
	propertyTable.captureInfoText = LOC("$$$/StyleAI/Debug/NoCaptures=No diagnostic captures saved.")
	propertyTable:addObserver("debugMode", function(properties, _, newValue)
		if newValue ~= true then
			properties.captureLlmInputs = false
		end
	end)

	local function refreshCaptureInfo()
		if propertyTable.debugMode ~= true then return end
		LrTasks.startAsyncTask(function()
			local info = SearchIndexAPI.getDiagnosticCaptureInfo(propertyTable.captureLlmInputsPath)
			if type(info) ~= "table" then return end
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
		end)
	end
	propertyTable.refreshCaptureInfo = refreshCaptureInfo
	if propertyTable.debugMode then refreshCaptureInfo() end

	propertyTable.pluginVersionText = string.format(
		"%d.%d.%d (%d)",
		Info.MAJOR or 0,
		Info.MINOR or 0,
		Info.REVISION or 0,
		Info.BUILD or 0
	)
	propertyTable.backendVersionText = LOC("$$$/StyleAI/common/Checking=Checking...")
	LrTasks.startAsyncTask(function()
		local versionInfo = SearchIndexAPI.getBackendVersion()
		propertyTable.backendVersionText = type(versionInfo) == "table"
			and tostring(versionInfo.backend_version or versionInfo.version or LOC("$$$/StyleAI/common/Unavailable=Unavailable"))
			or LOC("$$$/StyleAI/common/Unavailable=Unavailable")
	end)

	local catalog = LrApplication.activeCatalog()
	local catalogPath = catalog and catalog:getPath() or ""
	propertyTable.databasePath = catalogPath ~= ""
		and LrPathUtils.child(LrPathUtils.parent(catalogPath), "styleai.db")
		or ""
	propertyTable.databaseDisplayPath = abbreviatePath(propertyTable.databasePath)
	propertyTable.dataRecoveryBusy = false
	propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/DataRecoveryReady=Ready.")
	propertyTable.runDataRecovery = function(runningStatus, errorTitle, work, onSuccess)
		if propertyTable.dataRecoveryBusy then return end
		-- Claim the UI before scheduling so rapid repeat clicks cannot submit twice.
		propertyTable.dataRecoveryBusy = true
		propertyTable.dataRecoveryStatus = runningStatus
		LrTasks.startAsyncTask(function()
			local callOk, actionOk, result, detail = LrTasks.pcall(work)
			local errorDetail = nil
			if not callOk then
				propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/DataRecoveryFailed=The action failed. Review the error details and try again.")
				errorDetail = actionOk
			elseif actionOk == nil and result == "canceled" then
				propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/DataRecoveryReady=Ready.")
			elseif actionOk ~= true then
				propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/DataRecoveryFailed=The action failed. Review the error details and try again.")
				errorDetail = result
			else
				local completionOk, completionError = LrTasks.pcall(function()
					onSuccess(result, detail)
					-- Keep the durable inline summary, and add conspicuous feedback
					-- without a modal window that can hide behind Plug-In Manager.
					LrDialogs.showBezel(propertyTable.dataRecoveryStatus, 4)
				end)
				if not completionOk then
					propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/DataRecoveryFailed=The action failed. Review the error details and try again.")
					errorDetail = completionError
				end
			end
			propertyTable.dataRecoveryBusy = false
			if errorDetail ~= nil then ErrorHandler.handleError(errorTitle, tostring(errorDetail)) end
		end)
	end

	propertyTable.styleSummaryText = LOC("$$$/StyleAI/PluginInfo/StyleSummaryLoading=Loading style summary...")
	local function updateStyleSummary()
		LrTasks.startAsyncTask(function()
			local stats = SearchIndexAPI.getTrainingStats()
			local stylesOk, styles = SearchIndexAPI.listStyles()
			local exampleCount = type(stats) == "table" and tonumber(stats.count) or 0
			local styleCount = stylesOk and type(styles) == "table" and #styles or 0
			propertyTable.styleSummaryText = LOC(
				"$$$/StyleAI/PluginInfo/StyleSummary=^1 saved example(s) · ^2 active style(s)",
				tostring(exampleCount or 0),
				tostring(styleCount)
			)
		end)
	end
	propertyTable.refreshStyleSummary = updateStyleSummary
	updateStyleSummary()

	propertyTable.healthStatus = "checking"
	propertyTable.healthIssues = ""
	propertyTable.healthColor = { 0.5, 0.5, 0.5 }
	propertyTable.backendAvailable = false
	propertyTable.backendStatusText = LOC("$$$/StyleAI/common/Checking=Checking...")
	propertyTable.visionStatusText = LOC("$$$/StyleAI/common/Checking=Checking...")
	propertyTable.metadataStatusText = LOC("$$$/StyleAI/common/Checking=Checking...")
	propertyTable.serviceRepairing = false
	propertyTable.serviceRepairStatus = ""

	local function updateHealth()
		LrTasks.startAsyncTask(function()
			local health = SearchIndexAPI.getDetailedHealth() or {}
			local status = "healthy"
			local issues = {}
			local color = { 0, 0.65, 0 }

			propertyTable.backendAvailable = health.backend == true
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
				color = { 0.8, 0, 0 }
				table.insert(issues, LOC("$$$/StyleAI/Health/BackendFailed=Local background service is not reachable."))
			end
			if not health.clip then
				status = "critical"
				color = { 0.8, 0, 0 }
				table.insert(issues, LOC("$$$/StyleAI/Health/ClipMissing=Vision model is not ready."))
			end
			if not health.ollama and not health.lmstudio then
				if status ~= "critical" then
					status = "warning"
					color = { 0.1, 0.5, 0.8 }
				end
				table.insert(issues, LOC("$$$/StyleAI/Health/LocalLlmOptional=Local metadata generation is optional and currently unavailable."))
			end

			propertyTable.healthStatus = status
			propertyTable.healthIssues = table.concat(issues, "\n")
			propertyTable.healthColor = color
		end)
	end
	propertyTable.refreshHealth = updateHealth
	updateHealth()

	propertyTable.repairService = function()
		if propertyTable.serviceRepairing then return end
		propertyTable.serviceRepairing = true
		propertyTable.serviceRepairStatus = LOC("$$$/StyleAI/PluginInfo/RepairingService=Repairing the background service...")
		LrTasks.startAsyncTask(function()
			local progressScope = LrProgressScope({
				title = LOC("$$$/StyleAI/PluginInfo/RepairingService=Repairing the background service..."),
				functionContext = nil,
			})
			local ok, err = SearchIndexAPI.repairBackend()
			progressScope:done()
			propertyTable.serviceRepairing = false
			if ok then
				propertyTable.serviceRepairStatus = LOC("$$$/StyleAI/PluginInfo/RepairServiceSuccess=Background service is ready.")
				propertyTable.refreshStyleSummary()
			else
				propertyTable.serviceRepairStatus = LOC(
					"$$$/StyleAI/PluginInfo/RepairServiceFailed=Could not repair the background service: ^1",
					tostring(err or LOC("$$$/StyleAI/common/UnknownError=Unknown error"))
				)
			end
			propertyTable.refreshHealth()
		end)
	end

	LrTasks.startAsyncTask(function()
		while propertyTable.keepChecksRunning do
			for _ = 1, 20 do
				if not propertyTable.keepChecksRunning then break end
				LrTasks.sleep(0.5)
			end
			if propertyTable.keepChecksRunning then updateHealth() end
		end
	end)

	propertyTable.updateStatus = propertyTable.periodicalUpdateCheck
		and LOC("$$$/StyleAI/common/Checking=Checking...")
		or LOC("$$$/StyleAI/PluginInfo/UpdatesManual=Updates are checked manually.")
	propertyTable.updateStatusColor = { 0.5, 0.5, 0.5 }
	propertyTable.updateButtonTitle = LOC("$$$/StyleAI/PluginInfoDialogSections/UpdateCheck=Check for updates")
	propertyTable.updateAvailable = false
	propertyTable.latestReleaseInfo = nil

	local function checkUpdates()
		LrTasks.startAsyncTask(function()
			propertyTable.updateStatus = LOC("$$$/StyleAI/common/Checking=Checking...")
			local info = UpdateCheck.getLatestReleaseInfo()
			if info and info.is_newer then
				propertyTable.latestReleaseInfo = info
				propertyTable.updateAvailable = true
				propertyTable.updateStatus = LOC("$$$/StyleAI/PluginInfo/UpdateAvailable=Update Available: ^1", info.tag_name)
				propertyTable.updateStatusColor = { 0.1, 0.5, 0.8 }
				propertyTable.updateButtonTitle = info.is_code_only
					and LOC("$$$/StyleAI/UpdateCheck/UpdateNow=Update Now")
					or LOC("$$$/StyleAI/PluginInfo/DownloadUpdate=Download Update")
			else
				propertyTable.latestReleaseInfo = nil
				propertyTable.updateAvailable = false
				propertyTable.updateStatus = LOC("$$$/StyleAI/PluginInfo/UpToDate=Plugin is up to date")
				propertyTable.updateStatusColor = { 0.5, 0.5, 0.5 }
				propertyTable.updateButtonTitle = LOC("$$$/StyleAI/PluginInfoDialogSections/UpdateCheck=Check for updates")
			end
		end)
	end
	propertyTable.manualCheckUpdates = checkUpdates
	propertyTable:addObserver("periodicalUpdateCheck", function(_, _, newValue)
		if newValue == true then checkUpdates() end
	end)
	if propertyTable.periodicalUpdateCheck then checkUpdates() end
end

function PluginInfoDialogSections.sectionsForTopOfDialog(f, propertyTable)
	local bind = LrView.bind
	local share = LrView.share

	return {
		{
			bind_to_object = propertyTable,
			title = LOC("$$$/StyleAI/PluginInfo/StatusAndSetup=Status & Setup"),
			synopsis = bind("healthStatus"),
			f:column({
				fill_horizontal = 1,
				spacing = f:control_spacing(),
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
				UIFactory.Notice(f, {
					kind = "warning",
					visible = bind({ key = "healthIssues", transform = function(value) return value ~= "" end }),
					title = bind("healthIssues"),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/Health/ConfigureModels=Configure Local Models..."),
						action = function() OnboardingWizard.show(true) end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/RepairService=Repair Background Service"),
						visible = bind({ key = "backendAvailable", transform = function(value) return value ~= true end }),
						enabled = bind({ key = "serviceRepairing", transform = function(value) return value ~= true end }),
						action = propertyTable.repairService,
					}),
				}),
				UIFactory.HelpText(f, { title = bind("serviceRepairStatus") }),
			}),
		},
		{
			bind_to_object = propertyTable,
			title = LOC("$$$/StyleAI/PluginInfo/Styles=Styles"),
			synopsis = bind("styleSummaryText"),
			f:column({
				fill_horizontal = 1,
				spacing = f:control_spacing(),
				UIFactory.HelpText(f, { title = bind("styleSummaryText") }),
				UIFactory.HelpText(f, {
					title = LOC("$$$/StyleAI/PluginInfo/StylesHelp=Manage learned styles, inspect evidence, rebuild policies, and remove training data in Styles & Training."),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/Menu/StylesTraining=Styles & Training..."),
						action = function()
						LrTasks.startAsyncTask(function()
							local ok, err = LrTasks.pcall(function()
								dofile(_PLUGIN.path .. "/TaskStyleCatalog.lua")
							end)
							if not ok then ErrorHandler.handleError(LOC("$$$/StyleAI/PluginInfo/OpenStylesFailed=Could not open Styles & Training"), tostring(err)) end
						end)
					end,
					}),
				}),
			}),
		},
		{
			bind_to_object = propertyTable,
			title = LOC("$$$/StyleAI/PluginInfo/DataRecovery=Data & Recovery"),
			synopsis = LOC("$$$/StyleAI/PluginInfo/AdvancedSynopsis=Advanced"),
			f:column({
				fill_horizontal = 1,
				spacing = f:control_spacing(),
				UIFactory.FormRow(f, {
					label = LOC("$$$/StyleAI/PluginInfo/DatabaseLocation=Catalog-local data:"),
					labelWidth = share("dataLabelWidth"),
					f:static_text({
						title = bind("databaseDisplayPath"),
						tooltip = bind("databasePath"),
						fill_horizontal = 1,
						wrap = true,
					}),
				}),
				UIFactory.HelpText(f, {
					title = LOC("$$$/StyleAI/PluginInfo/DatabaseLocationHelp=StyleAI keeps this catalog's search index, training examples, learned styles, and edit history beside the Lightroom catalog."),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/RevealDataFolder=Reveal Data Folder"),
						enabled = bind({ key = "databasePath", transform = function(value) return value ~= "" end }),
						action = function()
						if propertyTable.databasePath ~= "" then LrShell.revealInShell(propertyTable.databasePath) end
					end,
					}),
				}),
				f:separator({ fill_horizontal = 1 }),
				UIFactory.HelpText(f, {
					title = LOC("$$$/StyleAI/PluginInfo/BackupScopeNote=StyleAI backups protect AI indexes, training data, learned styles, and history. They do not back up the Lightroom catalog, photo files, or Develop edits."),
				}),
				UIFactory.StatusRow(f, {
					label = LOC("$$$/StyleAI/PluginInfo/DataRecoveryStatus=Status:"),
					labelWidth = share("dataLabelWidth"),
					title = bind("dataRecoveryStatus"),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/ExportDbBackup=Export Backup..."),
						enabled = bind({ key = "dataRecoveryBusy", transform = function(value) return value ~= true end }),
						action = function()
							if propertyTable.dataRecoveryBusy then return end
							propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/DataRecoveryReady=Ready.")
							local outputPath, chooseError = SearchIndexAPI.chooseDatabaseBackupDestination()
							if not outputPath then
								if chooseError ~= "canceled" then ErrorHandler.handleError(LOC("$$$/StyleAI/PluginInfo/DbBackupFailed=Database backup failed"), tostring(chooseError)) end
								return
							end
							propertyTable.runDataRecovery(
								LOC("$$$/StyleAI/PluginInfo/BackupRunning=Creating and validating the backup..."),
								LOC("$$$/StyleAI/PluginInfo/DbBackupFailed=Database backup failed"),
								function() return SearchIndexAPI.createDatabaseBackup(outputPath) end,
								function(path)
									propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/BackupComplete=Backup exported successfully.")
									LrShell.revealInShell(path)
								end
							)
						end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/RestoreDbBackup=Restore Backup..."),
						enabled = bind({ key = "dataRecoveryBusy", transform = function(value) return value ~= true end }),
						action = function()
							if propertyTable.dataRecoveryBusy then return end
							propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/DataRecoveryReady=Ready.")
							local confirm = LrDialogs.confirm(
								LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupTitle=Restore StyleAI Database"),
								LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupConfirm=This restores StyleAI's AI data only. It does not restore your Lightroom catalog, photo files, or Develop edits. StyleAI will validate the backup and create a pre-restore recovery snapshot before replacing its current database. Continue?"),
								LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupAction=Choose Backup"),
								LOC("$$$/StyleAI/common/Cancel=Cancel")
							)
							if confirm ~= "ok" then return end
							local archivePath, chooseError = SearchIndexAPI.chooseDatabaseBackupArchive()
							if not archivePath then
								if chooseError ~= "canceled" then ErrorHandler.handleError(LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupFailed=Database restore failed"), tostring(chooseError)) end
								return
							end
							propertyTable.runDataRecovery(
								LOC("$$$/StyleAI/PluginInfo/RestoreRunning=Validating and restoring the backup..."),
								LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupFailed=Database restore failed"),
								function() return SearchIndexAPI.restoreDatabaseBackupFromPath(archivePath) end,
								function()
									propertyTable.refreshStyleSummary()
									propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/RestoreDbBackupCompleteMessage=The validated backup was restored successfully. Your Lightroom catalog and Develop edits were not changed.")
								end
							)
						end,
					}),
				}),
				f:separator({ fill_horizontal = 1 }),
				UIFactory.HelpText(f, {
					title = LOC("$$$/StyleAI/PluginInfo/CleanupHelp=Remove StyleAI records for photos that are no longer in this Lightroom catalog. A backup is created before cleanup."),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/CleanupRemovedPhotos=Clean Up Removed Photos..."),
						enabled = bind({ key = "dataRecoveryBusy", transform = function(value) return value ~= true end }),
						action = function()
							if propertyTable.dataRecoveryBusy then return end
							propertyTable.dataRecoveryStatus = LOC("$$$/StyleAI/PluginInfo/DataRecoveryReady=Ready.")
							local task = require("TaskPruneDatabase")
							if not task.confirm() then return end
							propertyTable.runDataRecovery(
								LOC("$$$/StyleAI/PluginInfo/CleanupRunning=Checking the catalog and cleaning removed-photo records..."),
								LOC("$$$/StyleAI/PruneDatabase/FailedTitle=Database Cleanup Failed"),
								function() return task.process() end,
								function(results)
									results = type(results) == "table" and results or {}
									propertyTable.dataRecoveryStatus = LOC(
										"$$$/StyleAI/PluginInfo/CleanupComplete=Cleanup complete — checked ^1; removed ^2; disassociated ^3. A backup was created.",
										tostring(results.checked or 0),
										tostring(results.deleted or 0),
										tostring(results.disassociated or 0)
									)
								end
							)
						end,
					}),
				}),
			}),
		},
		{
			bind_to_object = propertyTable,
			title = LOC("$$$/StyleAI/PluginInfo/SupportDebug=Support & Debug"),
			synopsis = LOC("$$$/StyleAI/PluginInfo/SupportSynopsis=Logs, diagnostics, and optional debug capture"),
			f:column({
				fill_horizontal = 1,
				spacing = f:control_spacing(),
				UIFactory.HelpText(f, {
					title = LOC("$$$/StyleAI/PluginInfo/SupportHelp=Generate a support report when troubleshooting. It contains system details and available StyleAI logs, but never your Lightroom catalog or original photos."),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/GenerateSupportReport=Generate Support Report..."),
						action = function() TaskDiagnostics.generateReport() end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/OpenLogsFolder=Open Logs Folder"),
						action = function() LrShell.revealInShell(Util.getLogfilePath()) end,
					}),
				}),
				f:separator({ fill_horizontal = 1 }),
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
					UIFactory.FormRow(f, {
						label = LOC("$$$/StyleAI/PluginInfo/ProcessingLoad=Processing load:"),
						labelWidth = share("debugLabelWidth"),
						f:popup_menu({
							value = bind("processingLoadMode"),
							fill_horizontal = 1,
							items = {
								{ title = LOC("$$$/StyleAI/PluginInfo/LoadAutomatic=Automatic (recommended)"), value = "automatic" },
								{ title = LOC("$$$/StyleAI/PluginInfo/LoadLower=Lower Resource Use"), value = "lower" },
								{ title = LOC("$$$/StyleAI/PluginInfo/LoadFaster=Faster"), value = "faster" },
							},
						}),
					}),
					UIFactory.HelpText(f, {
						title = LOC("$$$/StyleAI/PluginInfo/ProcessingLoadHelp=Automatic respects detected hardware limits and runtime memory pressure. Change this only while troubleshooting performance."),
					}),
					f:checkbox({
						value = bind("captureLlmInputs"),
						title = LOC("$$$/StyleAI/Debug/CaptureInputs=Capture LLM image inputs for troubleshooting"),
					}),
					UIFactory.FormRow(f, {
						label = LOC("$$$/StyleAI/Debug/Destination=Destination:"),
						labelWidth = share("debugLabelWidth"),
						f:edit_field({
							value = bind("captureLlmInputsPath"),
							enabled = false,
							fill_horizontal = 1,
						}),
					}),
					f:row({
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
							enabled = bind({ key = "captureLlmInputsPath", transform = function(value) return value ~= "" end }),
							action = function()
								if propertyTable.captureLlmInputsPath ~= "" then LrShell.revealInShell(propertyTable.captureLlmInputsPath) end
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
		},
	}
end

function PluginInfoDialogSections.sectionsForBottomOfDialog(f, propertyTable)
	local bind = LrView.bind
	local share = LrView.share

	return {
		{
			bind_to_object = propertyTable,
			title = LOC("$$$/StyleAI/PluginInfo/Updates=Updates"),
			f:column({
				fill_horizontal = 1,
				spacing = f:control_spacing(),
				f:static_text({
					title = bind("updateStatus"),
					text_color = bind("updateStatusColor"),
					fill_horizontal = 1,
					wrap = true,
				}),
				f:row({
					f:push_button({
						title = bind("updateButtonTitle"),
						action = function()
						local info = propertyTable.latestReleaseInfo
						if propertyTable.updateAvailable and type(info) == "table" then
							if info.is_code_only then
								local taskUpdate = require("TaskUpdate")
								taskUpdate.runUpdate(info)
							else
								LrHttp.openUrlInBrowser(info.release_url or UpdateCheck.latestReleaseUrl)
							end
						else
							propertyTable.manualCheckUpdates()
						end
					end,
					}),
				}),
				f:checkbox({
					value = bind("periodicalUpdateCheck"),
					title = LOC("$$$/StyleAI/PluginInfo/AutomaticUpdates=Automatically check for updates"),
				}),
			}),
		},
		{
			bind_to_object = propertyTable,
			title = LOC("$$$/StyleAI/PluginInfo/Credits=About"),
			f:column({
				fill_horizontal = 1,
				spacing = f:control_spacing(),
				UIFactory.HelpText(f, {
					title = LOC("$$$/StyleAI/PluginInfo/AboutText=StyleAI is local-first software built with open-source libraries and models."),
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
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfoDialogSections/Docs=Read documentation online"),
						action = function() LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki") end,
					}),
				}),
				f:row({
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/ViewCredits=View Credits"),
						action = function() LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki/Credits") end,
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/PluginInfo/ViewLicense=View License"),
						action = function() LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/blob/main/LICENSE") end,
					}),
				}),
			}),
		},
	}
end

function PluginInfoDialogSections.endDialog(propertyTable)
	if propertyTable.processingLoadMode == "lower" then
		prefs.indexingPerformanceProfile = 1
	elseif propertyTable.processingLoadMode == "faster" then
		prefs.indexingPerformanceProfile = 3
	else
		prefs.indexingPerformanceProfile = nil
	end

	prefs.periodicalUpdateCheck = propertyTable.periodicalUpdateCheck == true
	prefs.debugMode = propertyTable.debugMode == true
	prefs.captureLlmInputs = prefs.debugMode and propertyTable.captureLlmInputs == true
	prefs.captureLlmInputsPath = propertyTable.captureLlmInputsPath
	propertyTable.keepChecksRunning = false
end

return PluginInfoDialogSections
