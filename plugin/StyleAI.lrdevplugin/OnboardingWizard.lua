OnboardingWizard = {}

function OnboardingWizard.show(manualTrigger)
	LrTasks.startAsyncTask(function()
		LrFunctionContext.callWithContext("OnboardingWizard", function(context)
			local propertyTable = LrBinding.makePropertyTable(context)

			-- Initial states with robust defaults
			propertyTable.backendRunning = SearchIndexAPI.pingServer() or false
			propertyTable.clipReady = SearchIndexAPI.isClipReady() or false
			propertyTable.clipDownloading = false
			propertyTable.setupReady = propertyTable.backendRunning == true and propertyTable.clipReady == true

			local function updateSetupState()
				propertyTable.setupReady = propertyTable.backendRunning == true and propertyTable.clipReady == true
			end
			propertyTable:addObserver("backendRunning", updateSetupState)
			propertyTable:addObserver("clipReady", updateSetupState)


			if propertyTable.backendRunning == true and prefs.indexingPerformanceProfile == nil then
				local vInfo = SearchIndexAPI.getBackendVersion()
				if vInfo and vInfo.recommended_parallel_tasks then
					local rec = tonumber(vInfo.recommended_parallel_tasks) or 4
					local profile = math.ceil(rec / 2)
					prefs.indexingPerformanceProfile = math.min(math.max(profile, 1), 4)
					log:info("Dynamically profiled indexingPerformanceProfile to " .. tostring(prefs.indexingPerformanceProfile))
				end
			end

			local f = LrView.osFactory()
			local bind = LrView.bind
			local share = LrView.share

			local function updateBackendStatus()
				propertyTable.backendRunning = SearchIndexAPI.pingServer()
			end

			local function startBackend()
				propertyTable.backendRunning = "starting"
				LrTasks.startAsyncTask(function()
					SearchIndexAPI.startServer({ readyTimeoutSeconds = 30 })
					updateBackendStatus()
					if propertyTable.backendRunning == true and prefs.indexingPerformanceProfile == nil then
						local vInfo = SearchIndexAPI.getBackendVersion()
						if vInfo and vInfo.recommended_parallel_tasks then
							local rec = tonumber(vInfo.recommended_parallel_tasks) or 4
							local profile = math.ceil(rec / 2)
							prefs.indexingPerformanceProfile = math.min(math.max(profile, 1), 4)
							log:info("Dynamically profiled indexingPerformanceProfile to " .. tostring(prefs.indexingPerformanceProfile))
						end
					end
				end)
			end

			local dialogContents = f:column({
				bind_to_object = propertyTable,
				spacing = f:control_spacing(),
				width = 650,

				f:group_box({
					title = LOC("$$$/StyleAI/Onboarding/Step1Title=Start the StyleAI Service (Required)"),
					fill_horizontal = 1,
					f:static_text({
						title = LOC(
							"$$$/StyleAI/Onboarding/Step1Desc=StyleAI runs a lightweight, local Python server in the background to handle heavy mathematical tasks without sending your photos to the cloud. This local server is the foundation of the AI Editing system."
						),
						width_in_chars = 60,
						wrap = true,
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
									return LOC("$$$/StyleAI/Onboarding/BackendError=Failed to start")
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
					f:static_text({
						title = LOC(
							"$$$/StyleAI/Onboarding/BackendHint=If the server fails to start, check if another application is using port 19819 or if your firewall is blocking it."
						),
						size = "small",
						width_in_chars = 60,
						wrap = true,
					}),
				}),

				f:group_box({
					title = LOC("$$$/StyleAI/Onboarding/Step2Title=Install the Vision Model (Required)"),
					fill_horizontal = 1,
					f:static_text({
						title = LOC(
							"$$$/StyleAI/Onboarding/Step2Desc=To predict your unique editing style, StyleAI requires the SigLIP2 vision model. This local model analyzes the lighting, subject, and composition of your photos entirely offline. (~4GB download)"
						),
						width_in_chars = 60,
						wrap = true,
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
										return LOC("$$$/StyleAI/Onboarding/DownloadingClip=Downloading (Check progress bar)...")
									end
									return LOC("$$$/StyleAI/Onboarding/DownloadClip=Download SigLIP2 Model")
								end,
							}),
							action = function()
								propertyTable.clipDownloading = true
								LrTasks.startAsyncTask(function()
									SearchIndexAPI.startClipDownload()
									propertyTable.clipReady = SearchIndexAPI.isClipReady()
									propertyTable.clipDownloading = false
								end)
							end,
							enabled = bind({
								keys = { "clipReady", "clipDownloading" },
								transform = function(v)
									return not propertyTable.clipReady and not propertyTable.clipDownloading
								end,
							}),
						}),
					}),
				}),

				f:group_box({
					title = LOC("$$$/StyleAI/Onboarding/Step3Title=Optional: Add Local AI Metadata"),
					fill_horizontal = 1,
					f:static_text({
						title = LOC(
							"$$$/StyleAI/Onboarding/Step3Desc=StyleAI uses local-first LLMs (Ollama or LM Studio) to generate keywords, titles, and descriptions during indexing without sending images to the cloud."
						),
						width_in_chars = 60,
						wrap = true,
					}),
					f:spacer({ height = 10 }),
						f:row({
							fill_horizontal = 1,
							f:push_button({
								title = LOC("$$$/StyleAI/PluginInfoDialogSections/OllamaSetup=Setup Ollama"),
								tooltip = LOC("$$$/StyleAI/PluginInfo/OllamaTooltip=Opens the setup guide for Ollama integration."),
							action = function(button)
								LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki/Help-Ollama-Setup")
							end,
							}),
							f:push_button({
								title = LOC("$$$/StyleAI/PluginInfo/SetupLmStudio=Setup LM Studio"),
							tooltip = LOC("$$$/StyleAI/PluginInfo/LmStudioTooltip=Opens the setup guide for LM Studio integration."),
							action = function(button)
								LrHttp.openUrlInBrowser("https://github.com/RareLight/StyleAI/wiki/Help-LM-Studio-Setup")
							end,
							}),
						}),
				}),

				f:group_box({
					title = LOC("$$$/StyleAI/Onboarding/FinishTitle=Next Step"),
					fill_horizontal = 1,
					f:static_text({
						title = bind({
							key = "setupReady",
							transform = function(ready)
								if ready then
									return LOC("$$$/StyleAI/Onboarding/SetupReady=StyleAI is ready. Index photos next to prepare visual analysis for search, learning, and editing.")
								end
								return LOC("$$$/StyleAI/Onboarding/SetupIncomplete=Finish the required items above to enable style learning and editing. You can finish setup later.")
							end,
						}),
						width_in_chars = 60,
						wrap = true,
					}),
				}),
			})

			local result = LrDialogs.presentModalDialog({
				title = LOC("$$$/StyleAI/Onboarding/WizardTitle=StyleAI Setup"),
				contents = dialogContents,
				actionVerb = LOC("$$$/StyleAI/Onboarding/Done=Done"),
				cancelVerb = LOC("$$$/StyleAI/common/Cancel=Cancel"),
				otherVerb = LOC("$$$/StyleAI/Onboarding/Skip=Finish Later"),
				resizable = false,
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
