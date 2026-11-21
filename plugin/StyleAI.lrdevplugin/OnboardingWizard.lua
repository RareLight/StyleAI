OnboardingWizard = {}

local UIFactory = require("UIFactory")

function OnboardingWizard.show()
	LrTasks.startAsyncTask(function()
		LrFunctionContext.callWithContext("OnboardingWizard", function(context)
			local propertyTable = LrBinding.makePropertyTable(context)

			propertyTable.backendRunning = false
			propertyTable.backendProblem = ""
			propertyTable.clipReady = false
			propertyTable.clipDownloading = false
			propertyTable.setupReady = false

			local function updateSetupState()
				propertyTable.setupReady = propertyTable.backendRunning == true and propertyTable.clipReady == true
			end
			propertyTable:addObserver("backendRunning", updateSetupState)
			propertyTable:addObserver("clipReady", updateSetupState)

			local f = LrView.osFactory()
			local bind = LrView.bind
			local share = LrView.share

			local function startBackend()
				propertyTable.backendRunning = "starting"
				propertyTable.backendProblem = ""
				LrTasks.startAsyncTask(function()
					local started, problem = SearchIndexAPI.startServer({ readyTimeoutSeconds = 120 })
					if started then
						propertyTable.clipReady = SearchIndexAPI.isClipReady()
						if prefs.indexingPerformanceProfile == nil then
							local version = SearchIndexAPI.getBackendVersion()
							if version and version.recommended_parallel_tasks then
								local recommended = tonumber(version.recommended_parallel_tasks) or 4
								prefs.indexingPerformanceProfile = math.min(math.max(math.ceil(recommended / 2), 1), 4)
							end
						end
					end
					propertyTable.backendProblem = problem or ""
					propertyTable.backendRunning = started and true or "failed"
				end)
			end
			startBackend()

			local dialogContents = UIFactory.DialogColumn(f, {
				bind_to_object = propertyTable,
				spacing = f:control_spacing(),
				width = 660,

				f:group_box({
					title = LOC("$$$/StyleAI/Onboarding/Step1Title=Start the StyleAI Service (Required)"),
					fill_horizontal = 1,
					UIFactory.HelpText(f, {
						title = LOC(
							"$$$/StyleAI/Onboarding/Step1Desc=StyleAI runs a lightweight, local Python server in the background to handle heavy mathematical tasks without sending your photos to the cloud. This local server is the foundation of the AI Editing system."
						),
					}),
					f:spacer({ height = 5 }),
					f:row({
						f:static_text({
							title = LOC("$$$/StyleAI/Onboarding/BackendStatus=Local Server Status:"),
							width = share("label"),
						}),
						f:static_text({
							title = bind({
								key = "backendRunning",
								transform = function(v)
									if v == true then
										return LOC("$$$/StyleAI/Onboarding/BackendRunning=Running")
									end
									if v == "starting" then
										return LOC("$$$/StyleAI/Onboarding/BackendStarting=Starting...")
									end
									if v == "failed" then
										return LOC("$$$/StyleAI/Onboarding/BackendError=Failed to start")
									end
									return LOC("$$$/StyleAI/Onboarding/BackendStopped=Not running")
								end,
							}),
							text_color = bind({
								key = "backendRunning",
								transform = function(v)
									if v == true then
										return LrColor(0, 0.8, 0)
									end
									if v == "starting" then
										return LrColor(0.8, 0.8, 0)
									end
									return LrColor(0.8, 0, 0)
								end,
							}),
						}),
						f:push_button({
							title = LOC("$$$/StyleAI/common/Start=Start Local Server"),
							action = startBackend,
							enabled = bind({
								key = "backendRunning",
								transform = function(v)
									return v ~= true and v ~= "starting"
								end,
							}),
						}),
					}),
					f:spacer({ height = 5 }),
					UIFactory.HelpText(f, {
						title = bind("backendProblem"),
						visible = bind({
							key = "backendRunning",
							transform = function(value)
								return value == "failed"
							end,
						}),
					}),
				}),

				f:group_box({
					title = LOC("$$$/StyleAI/Onboarding/Step2Title=Install the Vision Model (Required)"),
					fill_horizontal = 1,
					UIFactory.HelpText(f, {
						title = LOC(
							"$$$/StyleAI/Onboarding/Step2Desc=SigLIP2 analyzes photos offline. Its full download is about 9.1 GB. Allow at least 12 GB free on the drive that stores your model cache."
						),
					}),
					f:spacer({ height = 5 }),
					f:row({
						f:checkbox({
							title = LOC(
								"$$$/StyleAI/Onboarding/ClipAlreadyDownloaded=SigLIP2 model is downloaded and ready."
							),
							value = bind("clipReady"),
							enabled = false,
						}),
					}),
					f:row({
						f:push_button({
							title = bind({
								key = "clipDownloading",
								transform = function(v)
									if v then
										return LOC(
											"$$$/StyleAI/Onboarding/DownloadingClip=Downloading (Check progress bar)..."
										)
									end
									return LOC("$$$/StyleAI/Onboarding/DownloadClip=Download SigLIP2 Model")
								end,
							}),
							action = function()
								propertyTable.clipDownloading = true
								LrTasks.startAsyncTask(function()
									local ok, result, problem = LrTasks.pcall(SearchIndexAPI.startClipDownload)
									propertyTable.clipReady = SearchIndexAPI.isClipReady()
									propertyTable.clipDownloading = false
									if not ok or problem then
										ErrorHandler.handleError(
											LOC("$$$/StyleAI/ClipDownload/ErrorTitle=Error downloading CLIP model"),
											problem or tostring(result)
										)
									end
								end)
							end,
							enabled = bind({
								keys = { "backendRunning", "clipReady", "clipDownloading" },
								transform = function()
									return propertyTable.backendRunning == true
										and not propertyTable.clipReady
										and not propertyTable.clipDownloading
								end,
							}),
						}),
					}),
				}),

				f:group_box({
					title = LOC("$$$/StyleAI/Onboarding/Step3Title=Optional: Add Local AI Metadata"),
					fill_horizontal = 1,
					UIFactory.HelpText(f, {
						title = LOC(
							"$$$/StyleAI/Onboarding/Step3Desc=For generated metadata, choose Ollama, LM Studio, or AX Engine under Local metadata API in Plug-in Manager. For AX Engine, set the AX model folder there."
						),
					}),
					f:spacer({ height = 10 }),
					f:row({
						fill_horizontal = 1,
						f:push_button({
							title = LOC("$$$/StyleAI/PluginInfoDialogSections/OllamaSetup=Setup Ollama"),
							tooltip = LOC(
								"$$$/StyleAI/PluginInfo/OllamaTooltip=Opens the setup guide for Ollama integration."
							),
							action = function(button)
								LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki/Help-Ollama-Setup")
							end,
						}),
						f:push_button({
							title = LOC("$$$/StyleAI/PluginInfo/SetupLmStudio=Setup LM Studio"),
							tooltip = LOC(
								"$$$/StyleAI/PluginInfo/LmStudioTooltip=Opens the setup guide for LM Studio integration."
							),
							action = function(button)
								LrHttp.openUrlInBrowser(
									"https://github.com/RareLight/StyleAI/wiki/Help-LM-Studio-Setup"
								)
							end,
						}),
					}),
				}),

				f:group_box({
					title = LOC("$$$/StyleAI/Onboarding/FinishTitle=Next Step"),
					fill_horizontal = 1,
					UIFactory.HelpText(f, {
						title = bind({
							key = "setupReady",
							transform = function(ready)
								if ready then
									return LOC(
										"$$$/StyleAI/Onboarding/SetupReady=The background service is connected to this catalog and SigLIP2 is ready."
									)
								end
								return LOC(
									"$$$/StyleAI/Onboarding/SetupIncomplete=Finish the required items above to enable style learning and editing. You can finish setup later."
								)
							end,
						}),
					}),
				}),
			})

			local result = LrDialogs.presentModalDialog({
				title = LOC("$$$/StyleAI/Onboarding/WizardTitle=StyleAI Setup"),
				contents = dialogContents,
				actionVerb = LOC("$$$/StyleAI/Onboarding/Done=Done"),
				cancelVerb = LOC("$$$/StyleAI/common/Cancel=Cancel"),
				otherVerb = LOC("$$$/StyleAI/Onboarding/Skip=Finish Later"),
				resizable = true,
			})

			if result == "ok" or result == "other" then
				prefs.onboardingCompleted = result == "ok" and propertyTable.setupReady == true
				prefs.onboardingDismissed = not prefs.onboardingCompleted
				if prefs.onboardingCompleted then
					log:info("Onboarding wizard completed with OK.")
				else
					log:info("Onboarding wizard dismissed before required setup was complete.")
				end
			end
		end)
	end)
end

return OnboardingWizard
