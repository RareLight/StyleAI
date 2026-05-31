OnboardingWizard = {}

function OnboardingWizard.show(manualTrigger)
	LrTasks.startAsyncTask(function()
		LrFunctionContext.callWithContext("OnboardingWizard", function(context)
			local propertyTable = LrBinding.makePropertyTable(context)

			-- Initial states with robust defaults
			propertyTable.backendRunning = SearchIndexAPI.pingServer() or false
			propertyTable.clipReady = SearchIndexAPI.isClipReady() or false
			propertyTable.clipDownloading = false
			propertyTable.geminiApiKey = prefs.geminiApiKey or ""
			propertyTable.chatgptApiKey = prefs.chatgptApiKey or ""

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
				end)
			end

			local dialogContents = f:column({
				bind_to_object = propertyTable,
				spacing = f:control_spacing(),
				width = 650,

				f:tab_view({
					fill_horizontal = 1,

					-- BACKEND TAB
					f:tab_view_item({
						title = LOC("$$$/StyleAI/Onboarding/BackendTitle=Backend Server"),
						identifier = "backend",

						f:group_box({
							title = LOC("$$$/StyleAI/Onboarding/WelcomeTitle=Welcome to StyleAI!"),
							fill_horizontal = 1,
							f:static_text({
								title = LOC(
									"$$$/StyleAI/Onboarding/WelcomeMessage=Thank you for choosing StyleAI. This wizard will guide you through the initial setup to ensure everything is running smoothly."
								),
								width_in_chars = 60,
								wrap = true,
							}),
						}),

						f:group_box({
							title = LOC("$$$/StyleAI/Onboarding/BackendTitle=Backend Server"),
							fill_horizontal = 1,
							f:static_text({
								title = LOC(
									"$$$/StyleAI/Onboarding/BackendDesc=StyleAI requires a local backend server to process your photos. We will attempt to start it now."
								),
								width_in_chars = 60,
								wrap = true,
							}),
							f:spacer({ height = 5 }),
							f:row({
								f:static_text({
									title = LOC("$$$/StyleAI/Onboarding/BackendStatus=Server Status:"),
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
									title = LOC("$$$/StyleAI/common/Start=Start"),
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
					}),

					-- PROVIDERS TAB
					f:tab_view_item({
						title = LOC("$$$/StyleAI/Onboarding/ProvidersTitle=AI Providers"),
						identifier = "providers",

						f:group_box({
							title = LOC("$$$/StyleAI/Onboarding/ProvidersTitle=AI Providers"),
							fill_horizontal = 1,
							f:static_text({
								title = LOC(
									"$$$/StyleAI/Onboarding/ProvidersDesc=Choose which AI models you want to use for metadata generation and edits."
								),
								width_in_chars = 60,
								wrap = true,
							}),
						}),

						f:group_box({
							title = LOC("$$$/StyleAI/Onboarding/GeminiTitle=Google Gemini (Recommended)"),
							fill_horizontal = 1,
							f:row({
								f:static_text({
									title = LOC("$$$/StyleAI/Onboarding/ApiKeyLabel=API Key:"),
									width = share("label"),
								}),
								f:edit_field({ value = bind("geminiApiKey"), width_in_chars = 40 }),
								f:push_button({
									title = "?",
									action = function()
										LrHttp.openUrlInBrowser("https://aistudio.google.com/app/apikey")
									end,
								}),
							}),
						}),
						f:group_box({
							title = LOC("$$$/StyleAI/Onboarding/ChatGPTTitle=OpenAI ChatGPT"),
							fill_horizontal = 1,
							f:row({
								f:static_text({
									title = LOC("$$$/StyleAI/Onboarding/ApiKeyLabel=API Key:"),
									width = share("label"),
								}),
								f:edit_field({ value = bind("chatgptApiKey"), width_in_chars = 40 }),
								f:push_button({
									title = "?",
									action = function()
										LrHttp.openUrlInBrowser("https://platform.openai.com/api-keys")
									end,
								}),
							}),
						}),
						f:group_box({
							title = LOC("$$$/StyleAI/Onboarding/LocalTitle=Local AI (Ollama / LM Studio)"),
							fill_horizontal = 1,
							f:row({
								f:push_button({
									title = LOC("$$$/StyleAI/Onboarding/LocalTitle=Local AI (Ollama / LM Studio)"),
									action = function()
										LrHttp.openUrlInBrowser("https://styleai.com/help/ollama-setup/")
									end,
								}),
							}),
						}),
					}),

					-- SEMANTIC SEARCH TAB
					f:tab_view_item({
						title = LOC("$$$/StyleAI/Onboarding/SemanticTitle=Semantic Search"),
						identifier = "semantic",

						f:group_box({
							title = LOC("$$$/StyleAI/Onboarding/SemanticTitle=Semantic Search"),
							fill_horizontal = 1,
							f:static_text({
								title = LOC(
									"$$$/StyleAI/Onboarding/SemanticDesc=To enable advanced search by content, you need the SigLIP2 AI model. This is a ~4GB download."
								),
								width_in_chars = 60,
								wrap = true,
							}),
							f:spacer({ height = 5 }),
							f:row({
								f:checkbox({
									title = LOC(
										"$$$/StyleAI/Onboarding/ClipAlreadyDownloaded=SigLIP2 model is already available."
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
							title = LOC("$$$/StyleAI/Onboarding/FinishTitle=All Set!"),
							fill_horizontal = 1,
							f:static_text({
								title = LOC(
									"$$$/StyleAI/Onboarding/FinishDesc=Configuration complete. StyleAI is ready to help you manage your Lightroom catalog."
								),
								width_in_chars = 60,
								wrap = true,
							}),
						}),
					}),
				}),
			})

			local result = LrDialogs.presentModalDialog({
				title = LOC("$$$/StyleAI/Onboarding/WizardTitle=StyleAI Setup"),
				contents = dialogContents,
				actionVerb = LOC("$$$/StyleAI/common/OK=OK"),
				cancelVerb = LOC("$$$/StyleAI/common/Cancel=Cancel"),
				otherVerb = LOC("$$$/StyleAI/Onboarding/Skip=Skip Setup"),
				resizable = false,
			})

			if result == "ok" or result == "other" then
				prefs.onboardingCompleted = true
				if result == "ok" then
					-- Save settings
					prefs.geminiApiKey = propertyTable.geminiApiKey
					prefs.chatgptApiKey = propertyTable.chatgptApiKey
					log:info("Onboarding wizard completed with OK.")
				else
					log:info("Onboarding wizard skipped.")
				end
			end
		end)
	end)
end

return OnboardingWizard
