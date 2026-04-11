OnboardingWizard = {}


function OnboardingWizard.show(manualTrigger)
    LrTasks.startAsyncTask(function()
        LrFunctionContext.callWithContext("OnboardingWizard", function(context)
            local propertyTable = LrBinding.makePropertyTable(context)
        
        -- Initial states with robust defaults
        propertyTable.currentPage = 1
        propertyTable.backendRunning = SearchIndexAPI.pingServer() or false
        propertyTable.clipReady = SearchIndexAPI.isClipReady() or false
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

        local function getPage(pageIndex)
            if pageIndex == 1 then
                -- Welcome Page
                return f:column {
                    spacing = f:label_spacing(),
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/WelcomeTitle=Welcome to LrGeniusAI!",
                        font = "<system/bold>",
                        size = "large",
                    },
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/WelcomeMessage=Thank you for choosing LrGeniusAI. This wizard will guide you through the initial setup to ensure everything is running smoothly.",
                        width_in_chars = 60,
                        wrap = true,
                    },
                }
            elseif pageIndex == 2 then
                -- Backend Page
                return f:column {
                    spacing = f:label_spacing(),
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/BackendTitle=Backend Server",
                        font = "<system/bold>",
                    },
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/BackendDesc=LrGeniusAI requires a local backend server to process your photos. We will attempt to start it now.",
                        width_in_chars = 60,
                        wrap = true,
                    },
                    f:row {
                        f:static_text {
                            title = LOC "$$$/LrGeniusAI/Onboarding/BackendStatus=Server Status:",
                        },
                        f:static_text {
                            title = bind {
                                key = 'backendRunning',
                                transform = function(v)
                                    if v == true then return LOC "$$$/LrGeniusAI/Onboarding/BackendRunning=Running" end
                                    if v == "starting" then return LOC "$$$/LrGeniusAI/Onboarding/BackendStarting=Starting..." end
                                    return LOC "$$$/LrGeniusAI/Onboarding/BackendError=Failed to start"
                                end
                            },
                            text_color = bind {
                                key = 'backendRunning',
                                transform = function(v)
                                    if v == true then return { 0, 0.8, 0 } end
                                    if v == "starting" then return { 0.8, 0.8, 0 } end
                                    return { 0.8, 0, 0 }
                                end
                            }
                        },
                    },
                    f:push_button {
                        title = LOC "$$$/LrGeniusAI/common/Start=Start",
                        action = startBackend,
                        enabled = bind {
                            key = 'backendRunning',
                            transform = function(v) return v ~= true and v ~= "starting" end
                        }
                    },
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/BackendHint=If the server fails to start, check if another application is using port 19819 or if your firewall is blocking it.",
                        size = "small",
                        width_in_chars = 60,
                        wrap = true,
                    },
                }
            elseif pageIndex == 3 then
                -- Providers Page
                return f:column {
                    spacing = f:label_spacing(),
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/ProvidersTitle=AI Providers",
                        font = "<system/bold>",
                    },
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/ProvidersDesc=Choose which AI models you want to use for metadata generation and edits.",
                        width_in_chars = 60,
                        wrap = true,
                    },
                    f:group_box {
                        title = LOC "$$$/LrGeniusAI/Onboarding/GeminiTitle=Google Gemini (Recommended)",
                        f:row {
                            f:static_text { title = LOC "$$$/LrGeniusAI/Onboarding/ApiKeyLabel=API Key:", width = share 'label' },
                            f:edit_field { value = bind 'geminiApiKey', width_in_chars = 40 },
                            f:push_button {
                                title = "?",
                                action = function() LrHttp.openUrlInBrowser("https://aistudio.google.com/app/apikey") end
                            }
                        }
                    },
                    f:group_box {
                        title = LOC "$$$/LrGeniusAI/Onboarding/ChatGPTTitle=OpenAI ChatGPT",
                        f:row {
                            f:static_text { title = LOC "$$$/LrGeniusAI/Onboarding/ApiKeyLabel=API Key:", width = share 'label' },
                            f:edit_field { value = bind 'chatgptApiKey', width_in_chars = 40 },
                            f:push_button {
                                title = "?",
                                action = function() LrHttp.openUrlInBrowser("https://platform.openai.com/api-keys") end
                            }
                        }
                    },
                    f:row {
                        f:push_button {
                            title = LOC "$$$/LrGeniusAI/Onboarding/LocalTitle=Local AI (Ollama / LM Studio)",
                            action = function() LrHttp.openUrlInBrowser("https://lrgenius.com/help/ollama-setup/") end
                        }
                    }
                }
            elseif pageIndex == 4 then
                -- Semantic Page
                return f:column {
                    spacing = f:label_spacing(),
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/SemanticTitle=Semantic Search",
                        font = "<system/bold>",
                    },
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/SemanticDesc=To enable advanced search by content, you need the OpenCLIP AI model. This is a ~4GB download.",
                        width_in_chars = 60,
                        wrap = true,
                    },
                    f:row {
                        f:checkbox {
                            title = LOC "$$$/LrGeniusAI/Onboarding/ClipAlreadyDownloaded=OpenCLIP model is already available.",
                            value = bind 'clipReady',
                            enabled = false,
                        },
                        f:push_button {
                            title = LOC "$$$/LrGeniusAI/Onboarding/DownloadClip=Download OpenCLIP Model",
                            action = function()
                                LrTasks.startAsyncTask(function()
                                    SearchIndexAPI.startClipDownload()
                                    propertyTable.clipReady = SearchIndexAPI.isClipReady()
                                end)
                            end,
                            enabled = bind {
                                key = 'clipReady',
                                transform = function(v) return not v end
                            }
                        }
                    }
                }
            elseif pageIndex == 5 then
                -- Finish Page
                return f:column {
                    spacing = f:label_spacing(),
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/FinishTitle=All Set!",
                        font = "<system/bold>",
                        size = "large",
                    },
                    f:static_text {
                        title = LOC "$$$/LrGeniusAI/Onboarding/FinishDesc=Configuration complete. LrGeniusAI is ready to help you manage your Lightroom catalog.",
                        width_in_chars = 60,
                        wrap = true,
                    },
                }
            end
        end

        local contents = f:column {
            spacing = f:label_spacing(),
            f:simple_list {
                f:column {
                    id = "page_container",
                    propertyTable.currentPage == 1 and getPage(1) or f:row{}
                }
            }
        }

        -- Update contents when currentPage changes
        propertyTable:addObserver('currentPage', function(props, key, value)
            -- This is a bit tricky in LR SDK as we can't easily swap views in a dialog
            -- We might need to use invisible rows or a more complex approach.
            -- For simplicity in this wizard, we will use a single view and update its children if possible,
            -- or recreate the dialog (not ideal).
            -- Actually, let's use a hidden/visible approach with all pages pre-rendered.
        end)

        -- Improved multi-page logic for LR View
        local pages = {}
        for i = 1, 5 do
            pages[i] = f:column {
                visible = bind {
                    key = 'currentPage',
                    transform = function(v) return v == i end
                },
                getPage(i)
            }
        end

        local dialogContents = f:column {
            spacing = f:label_spacing(),
            pages[1], pages[2], pages[3], pages[4], pages[5],
            f:separator { fill_horizontal = 1 },
            f:row {
                fill_horizontal = 1,
                f:push_button {
                    title = LOC "$$$/LrGeniusAI/Onboarding/Skip=Skip Setup",
                    action = function(d) d:done("skip") end,
                    visible = bind {
                        key = 'currentPage',
                        transform = function(v) return v < 5 end
                    }
                },
                f:spacer { fill_horizontal = 1 },
                f:push_button {
                    title = LOC "$$$/LrGeniusAI/Onboarding/Back=Back",
                    enabled = bind {
                        key = 'currentPage',
                        transform = function(v) return v > 1 end
                    },
                    action = function() propertyTable.currentPage = propertyTable.currentPage - 1 end,
                    visible = bind {
                        key = 'currentPage',
                        transform = function(v) return v < 5 end
                    }
                },
                f:push_button {
                    title = bind {
                        key = 'currentPage',
                        transform = function(v)
                            if v == 5 then return LOC "$$$/LrGeniusAI/Onboarding/Finish=Finish" end
                            return LOC "$$$/LrGeniusAI/Onboarding/Next=Next"
                        end
                    },
                    action = function(d)
                        if propertyTable.currentPage < 5 then
                            propertyTable.currentPage = propertyTable.currentPage + 1
                        else
                            d:done("ok")
                        end
                    end
                },
            }
        }

        local result = LrDialogs.presentModalDialog({
            title = LOC "$$$/LrGeniusAI/Onboarding/WizardTitle=LrGeniusAI Setup Wizard",
            contents = dialogContents,
            actionVerb = "OK", -- Overridden by my own buttons
            cancelVerb = "Cancel",
            resizable = false,
        })

        if result == "ok" or result == "skip" then
            prefs.onboardingCompleted = true
            -- Save settings
            prefs.geminiApiKey = propertyTable.geminiApiKey
            prefs.chatgptApiKey = propertyTable.chatgptApiKey
            log:info("Onboarding wizard completed with result: " .. tostring(result))
        end
    end)
    end)
end

return OnboardingWizard
