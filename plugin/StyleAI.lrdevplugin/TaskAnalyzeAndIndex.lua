-- TaskAnalyzeAndIndex.lua
-- Unified task for analyzing photos with AI metadata and indexing them.
-- Combines the old TaskAnalyzeImage and TaskManageIndex into one streamlined workflow.

local WorkCoordinator = require("WorkCoordinator")
local Defaults = require("Defaults")
local UIFactory = require("UIFactory")
local SearchIndexAPI = require("APISearchIndex")
local Util = require("Util")

---
-- Shows the main configuration dialog for analyze and index task.
-- @param ctx The LrFunctionContext for the dialog.
-- @return table with configuration options or nil if canceled.
--
local function showAnalyzeAndIndexDialog(ctx)
    local f = LrView.osFactory()
    local bind = LrView.bind
    local share = LrView.share

    local props = LrBinding.makePropertyTable(ctx)

    -- Scope settings
    props.scope = prefs.indexScope or "selected"
    if props.scope == "view" then props.scope = "selected" end
    props.indexingMode = prefs.indexingMode or "both"
	local selectedPhotos = LrApplication.activeCatalog():getTargetPhotos() or {}
	props.selectedCount = #selectedPhotos


    -- Check if CLIP model is ready on server non-blockingly
    props.clipReady = true
    LrTasks.startAsyncTask(function()
        local ready = SearchIndexAPI.isClipReady()
        if props and props.clipReady ~= nil then
            props.clipReady = ready
        end
    end)

    -- Tasks to perform (automatically aligned with selected indexingMode)
    if props.indexingMode == "meta" then
        props.enableEmbeddings = false
        props.enableMetadata = true
    elseif props.indexingMode == "embed" then
        props.enableEmbeddings = props.clipReady
        props.enableMetadata = false
    else -- "both"
        props.enableEmbeddings = props.clipReady
        props.enableMetadata = true
    end
    props.regenerateMetadata = false

    -- Automatically sync task checkboxes whenever indexingMode changes
    props:addObserver('indexingMode', function(properties, key, newValue)
        if newValue == "both" then
            properties.enableEmbeddings = properties.clipReady
            properties.enableMetadata = true
        elseif newValue == "meta" then
            properties.enableEmbeddings = false
            properties.enableMetadata = true
        elseif newValue == "embed" then
            properties.enableEmbeddings = properties.clipReady
            properties.enableMetadata = false
        end
    end)

    props:addObserver('clipReady', function(properties, key, newValue)
        if properties.indexingMode == "embed" or properties.indexingMode == "both" then
            properties.enableEmbeddings = newValue
        end
    end)

    local syncingTasks = false
    local function syncModeFromTasks(properties)
        if syncingTasks then return end
        syncingTasks = true
        if properties.enableEmbeddings and properties.enableMetadata then
            properties.indexingMode = "both"
        elseif properties.enableEmbeddings then
            properties.indexingMode = "embed"
        elseif properties.enableMetadata then
            properties.indexingMode = "meta"
        end
        syncingTasks = false
    end
    props:addObserver('enableEmbeddings', syncModeFromTasks)
    props:addObserver('enableMetadata', syncModeFromTasks)

    -- Force Re-index automatically switches write mode to overwrite (appendMetadata = false)
    props:addObserver('regenerateMetadata', function(properties, key, newValue)
        if newValue == true then
            properties.appendMetadata = false
        else
            properties.appendMetadata = prefs.appendMetadata ~= false
        end
    end)
	props:addObserver('scope', function(properties, key, newValue)
		if newValue == 'missing' and properties.regenerateMetadata then
			properties.regenerateMetadata = false
		end
	end)

    -- Metadata generation options
    props.temperature = prefs.temperature or 0.1
    props.promptTitles = {}
    for title, _ in pairs(prefs.prompts) do
        table.insert(props.promptTitles, { title = title, value = title })
    end

    props.prompt = prefs.prompt
	props.prompts = {}
	for name, prompt in pairs(prefs.prompts or {}) do
		props.prompts[name] = prompt
	end

    props.selectedPrompt = prefs.prompts[props.prompt]

    props:addObserver('prompt', function(properties, key, newValue)
        properties.selectedPrompt = properties.prompts[newValue]
    end)

    props:addObserver('selectedPrompt', function(properties, key, newValue)
        if newValue ~= nil and properties.prompt then
            properties.prompts[properties.prompt] = newValue
        end
    end)

    props.generateKeywords = prefs.generateKeywords ~= false
    props.generateCaption = prefs.generateCaption ~= false
    props.generateTitle = prefs.generateTitle ~= false
    props.generateAltText = prefs.generateAltText or false
    props.useKeywordHierarchy = prefs.useKeywordHierarchy or false
    props.useCatalogKeywordStructure = prefs.useCatalogKeywordStructure or false
    props.useTopLevelKeyword = true
    props.topLevelKeyword = prefs.topLevelKeyword or "StyleAI"
    props.bilingualKeywords = prefs.bilingualKeywords or false
    props.keywordSecondaryLanguage = prefs.keywordSecondaryLanguage or Defaults.defaultKeywordSecondaryLanguage

    -- AI Model selection (unified across providers)
    local activeProvider = prefs.activeMetadataProvider or Defaults.defaultMetadataProvider
    props.modelKey = prefs[activeProvider .. "ModelKey"] or prefs.modelKey -- format: "provider::model"
    props.language = prefs.generateLanguage or "English"
    props.temperature = prefs.temperature or 0.1
    props.replaceSS = prefs.replaceSS or false

    -- Build model list from server (local providers first)
    local modelItems = {}

    for _, choice in ipairs(SearchIndexAPI.getModelChoices()) do
        table.insert(modelItems, { title = choice.title, value = choice.key })
    end

    table.sort(modelItems, function(a, b) return a.title < b.title end)
    if (not modelItems or #modelItems == 0) then
        -- Fallback option if no models detected from backend
        table.insert(modelItems, { title = LOC("$$$/StyleAI/TaskAiEditPhotos/NoModels=No AI models available"), value = "none" })
    end
    local validModelKeys = {}
    for _, item in ipairs(modelItems) do validModelKeys[item.value] = true end
    if not props.modelKey or props.modelKey == '' or not validModelKeys[props.modelKey] then
        props.modelKey = modelItems[1].value
    end

    -- Context options
    props.submitGPS = prefs.submitGPS or false
    props.submitKeywords = prefs.submitKeywords or false
    props.submitFolderName = prefs.submitFolderName or false
    props.showPhotoContextDialog = prefs.showPhotoContextDialog or false

    -- Catalog data writing options
    props.saveDataToCatalog = prefs.saveDataToCatalog ~= false -- default true
    props.appendMetadata = prefs.appendMetadata
    if props.appendMetadata == nil then
        props.appendMetadata = true
    end
    -- Validation
    props.enableValidation = prefs.enableValidation or false

    -- LLM status properties
    local function updateLlmStatusText(properties)
        local key = properties.modelKey
        if not key or key == "" then
            properties.llmStatusText = LOC("$$$/StyleAI/AnalyzeAndIndex/LlmStatusNone=LLM: No model selected")
            properties.llmStatusColor = LrColor(0.8, 0, 0)
            return
        end

        local sep = string.find(key, "::", 1, true)
        local provider = key
        if sep then
            provider = string.sub(key, 1, sep - 1)
        end
        
        local health = properties.healthData or {}
        local providers = health.llm_providers or {}
        local status = providers[provider]

        if status == "available" or status == "registered" then
			properties.llmStatusText = LOC("$$$/StyleAI/Prepare/ProviderReady=^1: Ready", string.upper(provider))
            properties.llmStatusColor = LrColor(0, 0.8, 0)
        elseif status == "failed" then
            local errMsg = health.llm_errors and health.llm_errors[provider] or "unknown error"
			properties.llmStatusText = LOC("$$$/StyleAI/Prepare/ProviderFailed=^1: Failed (^2)", string.upper(provider), tostring(errMsg))
            properties.llmStatusColor = LrColor(0.8, 0, 0)
        elseif status == "not_configured" then
			properties.llmStatusText = LOC("$$$/StyleAI/Prepare/ProviderNotConfigured=^1: Not configured", string.upper(provider))
            properties.llmStatusColor = LrColor(0.8, 0.5, 0)
        else
			properties.llmStatusText = LOC("$$$/StyleAI/Prepare/ProviderUnknown=^1: Status unknown", string.upper(provider))
            properties.llmStatusColor = LrColor(0.5, 0.5, 0.5)
        end
    end

    local healthData = SearchIndexAPI.getHealth()
    props.healthData = healthData or {}

    props:addObserver('modelKey', function(properties, key, newValue)
        updateLlmStatusText(properties)
    end)
    updateLlmStatusText(props)



	-- The former mode-popup/overlapping-view implementation was removed after
	-- payload parity verification; the outcome-based controls below are now
	-- the single source of truth for this dialog.
	local metadataSettingKeys = {
		"generateKeywords",
		"generateTitle",
		"generateCaption",
		"generateAltText",
		"useKeywordHierarchy",
		"useCatalogKeywordStructure",
		"bilingualKeywords",
		"keywordSecondaryLanguage",
		"saveDataToCatalog",
		"enableValidation",
		"appendMetadata",
		"modelKey",
		"language",
		"temperature",
		"submitGPS",
		"submitKeywords",
		"submitFolderName",
		"showPhotoContextDialog",
		"prompt",
		"selectedPrompt",
	}

	local function showMetadataSettingsDialog()
		local snapshot = {
			prompts = Util.deepcopy(props.prompts or {}),
			promptTitles = Util.deepcopy(props.promptTitles or {}),
		}
		for _, key in ipairs(metadataSettingKeys) do
			snapshot[key] = props[key]
		end
		local promptTitleMenu = f:popup_menu {
			items = bind 'promptTitles',
			value = bind 'prompt',
			width = 320,
		}
		props.promptTitleMenu = promptTitleMenu

		local metadataContents = UIFactory.DialogColumn(f, {
			bind_to_object = props,
			spacing = f:control_spacing(),
			width = 700,
			UIFactory.HelpText(f, {
				title = LOC "$$$/StyleAI/Prepare/MetadataSettingsHelp=Choose the generated fields, local model, instructions, and optional context.",
			}),
			f:tab_view {
				fill_horizontal = 1,
				f:tab_view_item {
					title = LOC "$$$/StyleAI/Prepare/TabOutput=Metadata Output",
					identifier = 'prepare_output',
					f:column {
						spacing = f:control_spacing(),
						UIFactory.SettingsGroup(f, {
							title = LOC "$$$/StyleAI/AnalyzeAndIndex/MetadataOptions=Generated Fields",
							f:row {
								f:checkbox { value = bind 'generateKeywords', title = LOC "$$$/StyleAI/PluginInfoDialogSections/keywords=Keywords" },
								f:checkbox { value = bind 'generateTitle', title = LOC "$$$/StyleAI/PluginInfoDialogSections/title=Title" },
								f:checkbox { value = bind 'generateCaption', title = LOC "$$$/StyleAI/PluginInfoDialogSections/caption=Caption" },
								f:checkbox { value = bind 'generateAltText', title = LOC "$$$/StyleAI/PluginInfoDialogSections/alttext=Alt Text" },
							},
						}),
						UIFactory.SettingsGroup(f, {
							title = LOC "$$$/StyleAI/Prepare/KeywordOrganization=Keyword Organization",
							f:checkbox {
								value = bind 'useKeywordHierarchy',
								enabled = bind 'generateKeywords',
								title = LOC "$$$/StyleAI/UI/EnableHierarchy=Organize generated keywords in a hierarchy",
							},
							f:row {
								visible = bind 'useKeywordHierarchy',
								f:checkbox {
									value = bind 'useCatalogKeywordStructure',
									enabled = bind 'generateKeywords',
									title = LOC "$$$/StyleAI/UI/UseCatalogKeywordStructure=Use the existing catalog structure",
								},
								f:push_button {
									enabled = bind 'generateKeywords',
									title = LOC "$$$/StyleAI/PluginInfoDialogSections/editKeywordHierarchy=Edit categories...",
									action = function() KeywordConfigProvider.showKeywordCategoryDialog() end,
								},
							},
							f:row {
								f:checkbox {
									value = bind 'bilingualKeywords',
									enabled = bind 'generateKeywords',
									title = LOC "$$$/StyleAI/UI/BilingualKeywords=Add bilingual keyword synonyms",
								},
								f:combo_box {
									value = bind 'keywordSecondaryLanguage',
									items = Defaults.generateLanguages,
									enabled = bind 'bilingualKeywords',
								},
							},
						}),
						UIFactory.SettingsGroup(f, {
							title = LOC "$$$/StyleAI/Prepare/CatalogWriting=Lightroom Catalog",
							f:checkbox {
								value = bind 'saveDataToCatalog',
								title = LOC "$$$/StyleAI/AnalyzeAndIndex/SaveDataToCatalog=Write generated metadata to the Lightroom catalog",
							},
							f:checkbox {
								enabled = bind 'saveDataToCatalog',
								value = bind 'enableValidation',
								title = LOC "$$$/StyleAI/PluginInfoDialogSections/validation=Review each photo before saving",
							},
							f:column {
								enabled = bind 'saveDataToCatalog',
								f:radio_button {
									value = bind 'appendMetadata',
									checked_value = true,
									title = LOC "$$$/StyleAI/Prepare/Append=Append generated metadata to existing values",
								},
								f:radio_button {
									value = bind 'appendMetadata',
									checked_value = false,
									title = LOC "$$$/StyleAI/Prepare/Replace=Replace existing values for generated fields",
								},
							},
							UIFactory.HelpText(f, {
								title = LOC "$$$/StyleAI/Prepare/ReplaceFieldsHelp=Replace changes only the generated fields selected above; other Lightroom metadata is preserved.",
							}),
						}),
					},
				},
				f:tab_view_item {
					title = LOC "$$$/StyleAI/Prepare/TabInstructions=Model & Instructions",
					identifier = 'prepare_instructions',
					f:column {
						spacing = f:control_spacing(),
						UIFactory.SettingsGroup(f, {
							title = LOC "$$$/StyleAI/AnalyzeAndIndex/LlmTasks=Local Metadata Model",
							UIFactory.FormRow(f, {
								label = LOC "$$$/StyleAI/PluginInfoDialogSections/aiModel=Model:",
								labelWidth = share 'prepareMetadataLabel',
								f:popup_menu { value = bind 'modelKey', items = modelItems, width = 540 },
							}),
							UIFactory.FormRow(f, {
								label = LOC "$$$/StyleAI/PluginInfoDialogSections/generateLanguage=Language:",
								labelWidth = share 'prepareMetadataLabel',
								f:combo_box { value = bind 'language', items = Defaults.generateLanguages },
							}),
							UIFactory.FormRow(f, {
								label = LOC "$$$/StyleAI/AnalyzeAndIndex/Temperature=Creativity:",
								labelWidth = share 'prepareMetadataLabel',
								f:slider { value = bind 'temperature', min = 0.0, max = 0.5, fill_horizontal = 1 },
								f:static_text { title = bind 'temperature' },
							}),
						}),
						UIFactory.SettingsGroup(f, {
							title = LOC "$$$/StyleAI/UI/PromptTitle=Prompt Template",
							f:row {
								fill_horizontal = 1,
								promptTitleMenu,
								f:push_button { title = LOC "$$$/StyleAI/PluginInfoDialogSections/add=Add...", action = function() PromptConfigProvider.addPrompt(props) end },
								f:push_button { title = LOC "$$$/StyleAI/PromptConfig/Rename=Rename", action = function() PromptConfigProvider.renamePrompt(props) end },
								f:push_button { title = LOC "$$$/StyleAI/PluginInfoDialogSections/delete=Delete", action = function() PromptConfigProvider.deletePrompt(props) end },
								f:push_button { title = LOC "$$$/StyleAI/PromptConfig/RestoreAction=Restore Default", action = function() PromptConfigProvider.restoreDefaultPrompt(props, Defaults.defaultSystemInstruction) end },
							},
							f:scrolled_view {
								fill_horizontal = 1,
								height = 150,
								horizontal_scroller = false,
								vertical_scroller = true,
								f:edit_field {
									value = bind 'selectedPrompt',
									fill_horizontal = 1,
									height_in_lines = 10,
									wraps = true,
									allow_newlines = true,
								},
							},
						}),
					},
				},
				f:tab_view_item {
					title = LOC "$$$/StyleAI/Prepare/TabContext=Context",
					identifier = 'prepare_context',
					UIFactory.SettingsGroup(f, {
						title = LOC "$$$/StyleAI/AnalyzeAndIndex/ContextOptions=Optional Context Sent to the Local Model",
						f:checkbox { value = bind 'submitGPS', title = LOC "$$$/StyleAI/MetadataProvider/GPS=GPS coordinates" },
						f:checkbox { value = bind 'submitKeywords', title = LOC "$$$/StyleAI/PluginInfoDialogSections/submitKeywords=Existing Lightroom keywords" },
						f:checkbox { value = bind 'submitFolderName', title = LOC "$$$/StyleAI/PluginInfoDialogSections/folderNames=Parent folder names" },
						f:checkbox { value = bind 'showPhotoContextDialog', title = LOC "$$$/StyleAI/Prepare/AskContext=Ask for optional instructions for each photo" },
						UIFactory.HelpText(f, {
							title = LOC "$$$/StyleAI/Prepare/ContextLocal=All selected context remains on this computer and is sent only to the selected local model.",
						}),
					}),
				},
			},
		})

		local result = LrDialogs.presentModalDialog {
			title = LOC "$$$/StyleAI/Prepare/MetadataSettingsTitle=Local Metadata Settings",
			contents = metadataContents,
			actionVerb = LOC "$$$/StyleAI/common/Done=Done",
			cancelVerb = LOC "$$$/StyleAI/common/Cancel=Cancel",
			resizable = true,
		}

		if result ~= 'ok' then
			props.prompts = Util.deepcopy(snapshot.prompts)
			props.promptTitles = Util.deepcopy(snapshot.promptTitles)
			for _, key in ipairs(metadataSettingKeys) do
				props[key] = snapshot[key]
			end
		end
	end

	local contents = UIFactory.DialogColumn(f, {
        bind_to_object = props,
        spacing = f:control_spacing(),
		width = 620,

		UIFactory.Notice(f, {
			kind = "info",
			title = LOC "$$$/StyleAI/Prepare/Intro=Prepare photos for learned editing and optional local metadata.",
		}),

        UIFactory.SettingsGroup(f, {
            title = LOC "$$$/StyleAI/Prepare/Tasks=What should StyleAI do?",
            f:row {
                fill_horizontal = 1,
                f:checkbox {
                    value = bind 'enableEmbeddings',
                    title = LOC "$$$/StyleAI/Prepare/Analyze=Analyze photos for StyleAI",
                    enabled = bind 'clipReady',
                },
                f:static_text {
                    title = bind {
                        key = 'clipReady',
                        transform = function(ready)
                            return ready
                                and LOC "$$$/StyleAI/Prepare/VisionReady=Vision model ready"
                                or LOC "$$$/StyleAI/Prepare/VisionMissing=Vision model needs setup"
                        end,
                    },
					width = 210,
					wrap = true,
                },
            },
			UIFactory.HelpText(f, {
				title = LOC "$$$/StyleAI/Prepare/AnalyzeHelp=Creates visual analysis for learned editing and training recommendations.",
            }),
            f:row {
                fill_horizontal = 1,
                f:checkbox {
                    value = bind 'enableMetadata',
                    title = LOC "$$$/StyleAI/Prepare/Metadata=Generate keywords and descriptions",
                },
                f:static_text {
                    title = bind 'llmStatusText',
                    text_color = bind 'llmStatusColor',
					width = 210,
					wrap = true,
                },
            },
			UIFactory.HelpText(f, {
				title = LOC "$$$/StyleAI/Prepare/MetadataHelp=Uses your selected local vision-language model to generate Lightroom metadata.",
			}),
			f:row {
				f:push_button {
					title = LOC "$$$/StyleAI/Prepare/MetadataSettingsAction=Metadata Settings...",
					enabled = bind 'enableMetadata',
					action = showMetadataSettingsDialog,
				},
			},
        }),

        UIFactory.SettingsGroup(f, {
            title = LOC "$$$/StyleAI/Prepare/Photos=Photos and existing data",
            UIFactory.FormRow(f, {
                label = LOC "$$$/StyleAI/common/Scope=Scope:",
                labelWidth = share 'prepareLabelWidth',
                f:popup_menu {
                    value = bind 'scope',
					width = 360,
                    items = {
                        { title = LOC "$$$/StyleAI/common/ScopeSelected=Selected photos only", value = 'selected' },
                        { title = LOC "$$$/StyleAI/AnalyzeAndIndex/ScopeAll=All photos in catalog", value = 'all' },
                        { title = LOC "$$$/StyleAI/AnalyzeAndIndex/ScopeMissing=New or unprocessed photos", value = 'missing' },
                        { title = LOC "$$$/StyleAI/AnalyzeAndIndex/ScopeIndexed=Previously indexed photos", value = 'indexed' },
                    },
                },
            }),
            UIFactory.FormRow(f, {
                label = LOC "$$$/StyleAI/Prepare/Existing=Existing StyleAI data:",
                labelWidth = share 'prepareLabelWidth',
                f:column {
                    f:radio_button {
                        value = bind 'regenerateMetadata',
                        checked_value = false,
                        title = LOC "$$$/StyleAI/Prepare/SkipExisting=Keep existing data and process only what is needed",
                    },
                    f:radio_button {
                        value = bind 'regenerateMetadata',
                        checked_value = true,
						enabled = bind {
							key = 'scope',
							transform = function(scope) return scope ~= 'missing' end,
						},
                        title = LOC "$$$/StyleAI/Prepare/ReplaceExisting=Replace selected StyleAI-generated data",
                    },
					UIFactory.HelpText(f, {
						visible = bind { key = 'scope', transform = function(scope) return scope == 'missing' end },
						title = LOC "$$$/StyleAI/Prepare/MissingKeepsExisting=New or unprocessed scope always keeps existing StyleAI data; choose another scope to replace data.",
					}),
                },
            }),
			UIFactory.HelpText(f, {
				title = LOC "$$$/StyleAI/Prepare/ExistingDataHelp=Keep is recommended. Replace affects only StyleAI data selected for this run.",
			}),
		}),

        UIFactory.Summary(f, {
            title = LOC "$$$/StyleAI/UI/Summary=Run Summary",
            text = bind {
				keys = { 'enableEmbeddings', 'enableMetadata', 'scope', 'regenerateMetadata', 'selectedCount' },
                transform = function()
                    local tasks = {}
                    if props.enableEmbeddings then table.insert(tasks, LOC "$$$/StyleAI/Prepare/SummaryAnalyze=visual analysis") end
                    if props.enableMetadata then table.insert(tasks, LOC "$$$/StyleAI/Prepare/SummaryMetadata=local metadata") end
                    local taskText = #tasks > 0 and table.concat(tasks, " + ") or LOC "$$$/StyleAI/Prepare/SummaryNone=no task selected"
                    local existingText = props.regenerateMetadata
                        and LOC "$$$/StyleAI/Prepare/SummaryReplace=replace selected existing StyleAI data"
                        or LOC "$$$/StyleAI/Prepare/SummaryKeep=keep existing data where available"
					local scopeText = props.scope == 'selected'
						and LOC("$$$/StyleAI/Prepare/SummarySelected=^1 selected photo(s)", tostring(props.selectedCount or 0))
						or LOC "$$$/StyleAI/Prepare/SummaryChosenScope=the chosen photo scope"
					return scopeText .. " — " .. taskText .. " — " .. existingText
                end,
            },
        }),
    })

    local result = LrDialogs.presentModalDialog {
        title = LOC "$$$/StyleAI/Prepare/WindowTitle=Prepare Photos",
        contents = contents,
        actionVerb = LOC "$$$/StyleAI/Prepare/Action=Prepare Photos",
        cancelVerb = LOC "$$$/StyleAI/common/Cancel=Cancel",
        resizable = true,
    }

    if result == 'ok' then
		if not props.enableEmbeddings and not props.enableMetadata then
			LrDialogs.message(
				LOC "$$$/StyleAI/Prepare/NoTaskTitle=Nothing Selected",
				LOC "$$$/StyleAI/Prepare/NoTaskMessage=Select Analyze photos for StyleAI, Generate keywords and descriptions, or both.",
				"warning"
			)
			return nil
		end
		if props.enableMetadata and (not props.modelKey or props.modelKey == "" or props.modelKey == "none") then
			LrDialogs.message(
				LOC "$$$/StyleAI/Prepare/NoModelTitle=Local Metadata Model Required",
				LOC "$$$/StyleAI/Prepare/NoModelMessage=Choose an available local Ollama or LM Studio model, or turn off metadata generation.",
				"warning"
			)
			return nil
		end
		if props.enableMetadata and not (props.generateKeywords or props.generateCaption or props.generateTitle or props.generateAltText) then
			LrDialogs.message(
				LOC "$$$/StyleAI/Prepare/NoFieldsTitle=No Metadata Fields Selected",
				LOC "$$$/StyleAI/Prepare/NoFieldsMessage=Select at least one generated metadata field, or turn off metadata generation.",
				"warning"
			)
			return nil
		end
        -- Save preferences
        prefs.indexingMode = props.indexingMode
        prefs.indexScope = props.scope
        prefs.enableEmbeddings = props.enableEmbeddings
        prefs.enableMetadata = props.enableMetadata
        prefs.appendMetadata = props.appendMetadata
        prefs.generateKeywords = props.generateKeywords
        prefs.generateCaption = props.generateCaption
        prefs.generateTitle = props.generateTitle
        prefs.generateAltText = props.generateAltText
        prefs.replaceSS = props.replaceSS
        prefs.modelKey = props.modelKey
        if props.modelKey then
            local sep = string.find(props.modelKey, "::", 1, true)
            if sep then
                local prov = string.sub(props.modelKey, 1, sep - 1)
                prefs.ai = prov
                prefs[prov .. "ModelKey"] = props.modelKey
            end
        end
        prefs.generateLanguage = props.language
        prefs.temperature = props.temperature
        prefs.submitGPS = props.submitGPS
        prefs.submitKeywords = props.submitKeywords
        prefs.submitFolderName = props.submitFolderName
        prefs.showPhotoContextDialog = props.showPhotoContextDialog
        prefs.enableValidation = props.enableValidation
        prefs.saveDataToCatalog = props.saveDataToCatalog
        prefs.indexingPerformanceProfile = prefs.indexingPerformanceProfile or 2
        prefs.prompt = props.prompt
        prefs.prompts = props.prompts
        prefs.useKeywordHierarchy = props.useKeywordHierarchy
        prefs.useCatalogKeywordStructure = props.useCatalogKeywordStructure
        prefs.useTopLevelKeyword = props.useTopLevelKeyword
        prefs.topLevelKeyword = props.topLevelKeyword
        prefs.bilingualKeywords = props.bilingualKeywords
        prefs.keywordSecondaryLanguage = props.keywordSecondaryLanguage

        -- Keep track of used top-level keywords
        if not prefs.knownTopLevelKeywords then prefs.knownTopLevelKeywords = {} end
        if props.useTopLevelKeyword and not Util.table_contains(prefs.knownTopLevelKeywords, props.topLevelKeyword) then
            table.insert(prefs.knownTopLevelKeywords, props.topLevelKeyword)
        end

        return props
    end

    return nil
end


local function showPhotoContextDialog(photo)
    local f = LrView.osFactory()
    local bind = LrView.bind

    local props = {}
    props.skipFromHere = SkipPhotoContextDialog
    local photoContextFromCatalog = photo:getPropertyForPlugin(_PLUGIN, 'photoContext')
    if photoContextFromCatalog ~= nil then
        PhotoContextData = photoContextFromCatalog
    end
    props.photoContextData = PhotoContextData
    props.skipFromHere = false

    local dialogView = UIFactory.DialogColumn(f, {
        bind_to_object = props,
		spacing = f:control_spacing(),
		width = 620,
        f:row {
            f:static_text {
                title = photo:getFormattedMetadata('fileName'),
            },
        },
        f:row {
            alignment = "center",
            f:catalog_photo {
                photo = photo,
				width = 240, -- Bounded preview; avoids unbounded modal growth.
            },
        },
        f:row {
            f:static_text {
                title = LOC "$$$/StyleAI/AnalyzeImageTask/PhotoContextDialogData=Photo Context",
            },
        },
        f:row {
			fill_horizontal = 1,
            f:edit_field {
                value = bind 'photoContextData',
				fill_horizontal = 1,
                height_in_lines = 10,
				wraps = true,
				allow_newlines = true,
            },
        },
        f:checkbox {
            value = bind 'skipFromHere',
			title = LOC "$$$/StyleAI/AnalyzeImageTask/SkipPreflightFromHere=Use this context for all remaining photos.",
        },
    })

    local result = LrDialogs.presentModalDialog({
        title = LOC "$$$/StyleAI/AnalyzeImageTask/PhotoContextDialogData=Photo Context",
        contents = dialogView,
		resizable = true,
    })

    SkipPhotoContextDialog = props.skipFromHere

    return result, props.photoContextData, props.skipFromHere
end

-- Apply a keyword name mapping to a keyword structure (flat strings, hierarchical dict,
-- or alias-object arrays).  mapping: { lowercase_name = "CanonicalName" }
local function applyKeywordNameMapping(keywords, mapping)
	if type(keywords) ~= "table" or not next(mapping) then
		return keywords
	end
	if keywords[1] ~= nil then
		local result = {}
		for _, item in ipairs(keywords) do
			if type(item) == "string" then
				table.insert(result, mapping[item:lower()] or item)
			elseif type(item) == "table" and type(item.name) == "string" then
				local canonical = mapping[item.name:lower()]
				if canonical then
					local copy = {}
					for k, v in pairs(item) do
						copy[k] = v
					end
					copy.name = canonical
					table.insert(result, copy)
				else
					table.insert(result, item)
				end
			else
				table.insert(result, item)
			end
		end
		return result
	else
		-- hierarchical dict: keys are keyword/category names
		local result = {}
		for key, value in pairs(keywords) do
			if type(key) == "string" then
				local canonical = mapping[key:lower()]
				result[canonical or key] = applyKeywordNameMapping(value, mapping)
			else
				result[key] = value
			end
		end
		return result
	end
end

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("AnalyzeAndIndexTask", function(context)
		-- Lightroom's target-photo set is live UI state. Capture it before the
		-- modal dialog or backend checks can move focus to a single photo.
		local selectedPhotosSnapshot = PhotoSelector.snapshotSelectedPhotos()

		-- The dialog performs model and provider checks while it is built. Wait
		-- for an idle-shutdown replacement backend before opening it so a normal
		-- startup connection refusal cannot be misreported as missing setup.
		if not Util.waitForServerDialog({ suppressProgressDialog = false }) then
			return
		end

		-- Show dialog
		local props = showAnalyzeAndIndexDialog(context)
		if not props then
			return
		end

		-- Validate that at least one task is selected
		if
			not props.enableEmbeddings
			and not props.enableMetadata
		then
			LrDialogs.showError(
				LOC("$$$/StyleAI/AnalyzeAndIndex/NoTasksSelected=Please select at least one task to perform.")
			)
			return
		end

		-- Warn when "New or unprocessed photos" scope is combined with "Regenerate all":
		-- the backend will treat every photo as needing processing, so delta filtering has no effect.
		if props.scope == "missing" and props.regenerateMetadata then
			local confirm = LrDialogs.confirm(
				LOC("$$$/StyleAI/AnalyzeAndIndex/RegenerateWithDeltaTitle=Scope conflict"),
				LOC(
					'$$$/StyleAI/AnalyzeAndIndex/RegenerateWithDeltaMessage=You selected "New or unprocessed photos" but "Regenerate all" is also enabled. All photos will be processed — the delta filter has no effect. Continue?'
				),
				LOC("$$$/StyleAI/common/Continue=Continue"),
				LOC("$$$/StyleAI/common/Cancel=Cancel")
			)
			if confirm ~= "ok" then
				return
			end
		end

		-- Build tasks array
		local tasks = {}
		if props.enableEmbeddings and (props.indexingMode == "embed" or props.indexingMode == "both") then
			table.insert(tasks, "embeddings")
		end
		if props.enableMetadata and (props.indexingMode == "meta" or props.indexingMode == "both") then
			table.insert(tasks, "metadata")
		end


		-- Parse provider and model from unified modelKey (format: provider::model)
		local providerFromKey, modelFromKey = nil, nil
		if props.modelKey then
			local sep = string.find(props.modelKey, "::", 1, true)
			if sep then
				providerFromKey = string.sub(props.modelKey, 1, sep - 1)
				modelFromKey = string.sub(props.modelKey, sep + 2)
				if modelFromKey == "" then
					modelFromKey = nil
				end
			else
				providerFromKey = props.modelKey -- fallback
			end
		end

		-- Build options for the API
		local options = {
			tasks = tasks,
			provider = providerFromKey,
			model = modelFromKey,
			language = props.language,
			temperature = props.temperature,
			generate_keywords = props.generateKeywords,
			generate_caption = props.generateCaption,
			generate_title = props.generateTitle,
			generate_alt_text = props.generateAltText,
			submit_gps = props.submitGPS,
			submit_keywords = props.submitKeywords,
			submit_folder_names = props.submitFolderName,
			submit_user_context = props.showPhotoContextDialog,
			enableMetadata = props.enableMetadata and (props.indexingMode == "meta" or props.indexingMode == "both"),
			replace_ss = props.replaceSS,
			regenerate_metadata = props.regenerateMetadata,
			prompt = props.selectedPrompt,
			bilingual_keywords = props.bilingualKeywords,
			keyword_secondary_language = props.keywordSecondaryLanguage,
		}


		if prefs.useKeywordHierarchy then
			if prefs.useCatalogKeywordStructure then
				options.keyword_categories = MetadataManager.getCatalogKeywordHierarchy()
			else
				options.keyword_categories = KeywordConfigProvider.getKeywordCategories()
			end
		end

		-- Create progress scope
		local progressScope = LrProgressScope({
			title = LOC("$$$/StyleAI/AnalyzeAndIndex/ProgressTitle=Processing photos..."),
			functionContext = context,
		})

		-- Get photos to process
		-- For scope 'missing', pass task options so backend checks which photos need the selected tasks
		local taskOptionsForScope = (props.scope == "missing")
				and {
					enableEmbeddings = props.enableEmbeddings,
					enableMetadata = props.enableMetadata,
					regenerateMetadata = props.regenerateMetadata,
				}
			or nil

		-- Use the main progress scope for "missing" lookup so the bar resets for import/analysis (nested child scopes complete the parent segment).
		local lookupScope = (props.scope == "missing") and progressScope or nil
		local photosToProcess, errorStatus =
			PhotoSelector.getPhotosInScope(props.scope, taskOptionsForScope, lookupScope, selectedPhotosSnapshot)
		log:info(
			"Resolved "
				.. tostring(photosToProcess and #photosToProcess or 0)
				.. " photo(s) for indexing scope "
				.. tostring(props.scope)
		)

		if photosToProcess == nil or type(photosToProcess) ~= "table" or #photosToProcess == 0 then
			progressScope:done()
			if errorStatus == "Invalid view" then
				LrDialogs.message(
					LOC("$$$/StyleAI/AnalyzeAndIndex/NoPhotosTitle=No photos found"),
					LOC("$$$/StyleAI/AnalyzeAndIndex/NoPhotosMessage=Please select a folder or collection to process."),
					"info"
				)
			else
				log:trace(
					"No photos found to process in scope: " .. props.scope .. " errorStatus: " .. (errorStatus or "nil")
				)
				LrDialogs.message(
					LOC("$$$/StyleAI/common/NoPhotosTitle=No Photos Found"),
					LOC("$$$/StyleAI/common/NoPhotosInScope=No photos found in the selected scope.")
				)
			end
			return
		end

		-- Per-photo progress for import and analysis (denominator = photos to process, not 1)
		progressScope:setCaption(
			LOC("$$$/StyleAI/AnalyzeAndIndex/ProgressCount=^1 photos to process", tostring(#photosToProcess))
		)
		progressScope:setPortionComplete(0, #photosToProcess)

		-- If photo context dialog is enabled, show it for each photo
		if props.showPhotoContextDialog and props.enableMetadata then
			-- Show photo context dialog to gather additional context
			local skipFromHere = false
			local contextData = ""
			for _, photo in ipairs(photosToProcess) do
				local result
				if not skipFromHere then
					result, contextData, skipFromHere = showPhotoContextDialog(photo)
					if result == "cancel" then
						log:trace(
							"User canceled photo context dialog for photo: "
								.. (photo:getFormattedMetadata("fileName") or "unknown")
						)
						progressScope:done()
						return
					end
				end
				LrApplication.activeCatalog():withPrivateWriteAccessDo(function()
					photo:setPropertyForPlugin(_PLUGIN, "photoContext", contextData)
				end)
			end
		end

		-- Indexing and local metadata generation are machine-saturating workflows.
		-- Queue a second Lightroom request instead of multiplying GPU/LLM contexts.
		progressScope:setCaption(
			LOC("$$$/StyleAI/AnalyzeAndIndex/WaitingForWorkflow=Waiting for an earlier indexing or tagging operation...")
		)
		local workflowLease, workflowLeaseError = WorkCoordinator.acquire(
			"backend_index_workflow",
			progressScope
		)
		if not workflowLease then
			progressScope:done()
			if workflowLeaseError ~= "canceled" then
				ErrorHandler.handleError("Could not schedule indexing", workflowLeaseError)
			end
			return
		end
		context:addCleanupHandler(function()
			WorkCoordinator.release(workflowLease)
		end)

		log:trace("Starting AnalyzeAndIndexTask with " .. #photosToProcess .. " photos")

		-- When validation is disabled, apply metadata inline as each photo's analysis returns
		-- so keywords/title/caption land on photos progressively instead of all at the end.
		-- Validation-on keeps the two-phase flow because modal dialogs must serialize on the main task.
		local usedInlineApply = false
		if
			props.enableMetadata
			and props.saveDataToCatalog
			and not props.enableValidation
			and not props.keywordAliases
		then
			usedInlineApply = true
			options.onBatchAnalyzed = function(batch, scope)
				local writeLease, leaseError = WorkCoordinator.acquire("catalog_write", scope)
				if not writeLease then
					error("Catalog write canceled: " .. tostring(leaseError))
				end
				local writeOk, writeError = LrTasks.pcall(function()
					LrApplication.activeCatalog():withWriteAccessDo("Apply AI Metadata Batch", function()
						for _, item in ipairs(batch) do
							local photo = item.photo
							local photoId = item.photo_id
							local response = SearchIndexAPI.getPhotoData(photoId)
							if response and response.metadata then
								MetadataManager.applyMetadata(photo, response, nil, {
									applyKeywords = props.generateKeywords,
									applyTitle = props.generateTitle,
									applyCaption = props.generateCaption,
									applyAltText = props.generateAltText,
									useTopLevelKeyword = props.useTopLevelKeyword,
									topLevelKeyword = props.topLevelKeyword,
									appendMetadata = props.appendMetadata,
									useExistingTransaction = true,
								})
							else
								error("Generated metadata was unavailable for " .. tostring(photoId))
							end
						end
					end, Defaults.catalogWriteAccessOptions)
				end)
				WorkCoordinator.release(writeLease)
				if not writeOk then error(writeError) end
			end
		end
		options.deferCatalogHandoff = props.enableMetadata and props.saveDataToCatalog and not usedInlineApply

		local status, processed, failed, processedPhotos, combinedError, combinedWarnings, operationId, alreadyComplete
		status, processed, failed, processedPhotos, combinedError, combinedWarnings, operationId, alreadyComplete =
			SearchIndexAPI.analyzeAndIndexSelectedPhotos(photosToProcess, progressScope, options, false)

		-- De-clutter: cluster the generated keywords and build a name-mapping so that
		-- near-duplicates (e.g. "Automobile" → "Car") are unified before being written
		-- to the catalog.  Existing catalog keywords are preferred as canonical.
		-- No LLM validation here — CLIP threshold alone keeps latency reasonable.
		local keywordMapping = {}
		local mergedPairs = {} -- {from="Automobile", to="Car"} for dialog display


		if
			status ~= "allfailed"
			and status ~= "canceled"
			and not progressScope:isCanceled()
			and props.enableMetadata
			and props.saveDataToCatalog
			and not usedInlineApply
			and operationId ~= nil
		then
			log:trace("Saving metadata for processed photos...")
			local savedCount = 0
			local skippedCount = 0
			local skipFromHere = false
			local handoffCanceled = false

			local function finishHandoffItem(photoId, state, itemError)
				local updateOk, updateError = SearchIndexAPI.updateOperationItems(operationId, {
					{ item_id = photoId, state = state, error = itemError },
				})
				if not updateOk then
					error("Could not persist Lightroom metadata handoff: " .. tostring(updateError))
				end
			end

			local handoffOk, handoffError = LrTasks.pcall(function()
				for _, photo in ipairs(processedPhotos) do
					LrTasks.yield()
					LrTasks.sleep(0.01)
					local photoId, photoIdErr = SearchIndexAPI.getPhotoIdForPhoto(photo)
					if not photoId then
						error("Could not identify photo during metadata handoff: " .. tostring(photoIdErr))
					end
					local response = SearchIndexAPI.getPhotoData(photoId)

					-- Pre-compute deduped keywords; the validation dialog shows both
					-- side-by-side. Non-validation paths apply the mapping automatically.
					local dedupedKeywords = nil
					if next(keywordMapping) and response and response.metadata and response.metadata.keywords then
						dedupedKeywords = applyKeywordNameMapping(response.metadata.keywords, keywordMapping)
					end

					log:trace("Got generated data for photo: " .. (photo:getFormattedMetadata("fileName") or "unknown"))
					log:trace("Response: " .. (Util.dumpTable(response) or "nil"))

					if not response or not response.metadata then
						local message = "Generated metadata unavailable during Lightroom handoff"
						finishHandoffItem(photoId, "failed", message)
						failed = failed + 1
						skippedCount = skippedCount + 1
					elseif props.enableValidation then
						local result, validatedData

						if not skipFromHere then
							-- Show validation dialog
							result, validatedData = MetadataManager.showValidationDialog(context, photo, response, {
								applyKeywords = props.generateKeywords,
								applyTitle = props.generateTitle,
								applyCaption = props.generateCaption,
								applyAltText = props.generateAltText,
								appendMetadata = props.appendMetadata,
							}, dedupedKeywords, mergedPairs)

							if validatedData ~= nil and validatedData.skipFromHere then
								log:trace("Skipping validation from here for subsequent photos.")
								skipFromHere = true
							end

							if result == "ok" and validatedData then
								-- Apply validated metadata
								MetadataManager.applyMetadata(photo, response, validatedData, {
									applyKeywords = props.generateKeywords,
									applyTitle = props.generateTitle,
									applyCaption = props.generateCaption,
									applyAltText = props.generateAltText,
									useTopLevelKeyword = props.useTopLevelKeyword,
									topLevelKeyword = props.topLevelKeyword,
									appendMetadata = props.appendMetadata,
								})

								-- Overwrite with validated data
								log:trace(
									"Reimported validated metadata for photo: "
										.. (photo:getFormattedMetadata("fileName") or "unknown")
								)

								savedCount = savedCount + 1
								finishHandoffItem(photoId, "succeeded")
							elseif result == "other" then
								skippedCount = skippedCount + 1
								-- Clear only metadata so the photo stays in the index and can be regenerated later
								SearchIndexAPI.removePhotoMetadata(photoId)
								Util.addPhotoToRejectedDescriptionsCollection(photo, Defaults.catalogWriteAccessOptions)
								finishHandoffItem(photoId, "canceled", "Metadata review rejected")
							elseif result == "cancel" then
								handoffCanceled = true
								break
							else
								finishHandoffItem(photoId, "failed", "Metadata review returned no usable result")
								failed = failed + 1
								skippedCount = skippedCount + 1
							end
						else
							-- Validation has been skipped from here on; apply metadata without showing dialog
							if dedupedKeywords then
								response.metadata.keywords = dedupedKeywords
							end
							MetadataManager.applyMetadata(photo, response, nil, {
								applyKeywords = props.generateKeywords,
								applyTitle = props.generateTitle,
								applyCaption = props.generateCaption,
								applyAltText = props.generateAltText,
								useTopLevelKeyword = props.useTopLevelKeyword,
								topLevelKeyword = props.topLevelKeyword,
								appendMetadata = props.appendMetadata,
							})

							log:trace(
								"Applied metadata without validation for photo (skipFromHere active): "
									.. (photo:getFormattedMetadata("fileName") or "unknown")
							)

							savedCount = savedCount + 1
							finishHandoffItem(photoId, "succeeded")
						end
					else
						-- Directly save generated metadata without validation
						if dedupedKeywords then
							response.metadata.keywords = dedupedKeywords
						end
						MetadataManager.applyMetadata(photo, response, nil, {
							applyKeywords = props.generateKeywords,
							applyTitle = props.generateTitle,
							applyCaption = props.generateCaption,
							applyAltText = props.generateAltText,
							useTopLevelKeyword = props.useTopLevelKeyword,
							topLevelKeyword = props.topLevelKeyword,
							appendMetadata = props.appendMetadata,
						})
						savedCount = savedCount + 1
						finishHandoffItem(photoId, "succeeded")
					end
				end
			end)
			if handoffCanceled then
				SearchIndexAPI.cancelOperation(operationId)
				status = "canceled"
			elseif not handoffOk then
				failed = failed + 1
				status = "somefailed"
				combinedError = tostring(handoffError)
				log:error("Deferred Lightroom metadata handoff failed: " .. tostring(handoffError))
			else
				local completeOk, completeError = SearchIndexAPI.completeOperation(operationId)
				if not completeOk then
					failed = failed + 1
					status = "somefailed"
					combinedError = "Could not finalize metadata operation: " .. tostring(completeError)
					log:error(combinedError)
				end
			end
		end

		progressScope:done()

		-- Show completion message based on status
		if status == "canceled" then
			LrDialogs.message(
				LOC("$$$/StyleAI/common/TaskCanceled/Title=Task Canceled"),
				LOC("$$$/StyleAI/common/TaskCanceled/Message=The task was canceled by the user.")
			)
		elseif status == "allfailed" then
			if combinedError then
				ErrorHandler.handleError(
					LOC("$$$/StyleAI/AnalyzeAndIndex/AllFailedMessage=All ^1 photos failed to process.", processed),
					combinedError
				)
			else
				LrDialogs.message(
					LOC("$$$/StyleAI/common/TaskFailed/Title=Task Failed"),
					LOC("$$$/StyleAI/AnalyzeAndIndex/AllFailedMessage=All ^1 photos failed to process.", processed)
				)
			end
		elseif status == "somefailed" then
			local successCount = processed - failed
			if combinedError then
				ErrorHandler.handleError(
					LOC(
						"$$$/StyleAI/AnalyzeAndIndex/SomeFailedMessage=^1 of ^2 photos processed successfully. ^3 failed.",
						successCount,
						processed,
						failed
					),
					combinedError
				)
			else
				LrDialogs.message(
					LOC("$$$/StyleAI/common/TaskCompleted/Title=Task Completed with Errors"),
					LOC(
						"$$$/StyleAI/AnalyzeAndIndex/SomeFailedMessage=^1 of ^2 photos processed successfully. ^3 failed.",
						successCount,
						processed,
						failed
					)
				)
			end
		else -- success
			local msg
			if alreadyComplete then
				msg = LOC(
					"$$$/StyleAI/AnalyzeAndIndex/AlreadyComplete=All ^1 unique photo(s) are already complete.",
					processed
				)
			else
				msg = LOC("$$$/StyleAI/AnalyzeAndIndex/SuccessMessage=Successfully processed ^1 photos.", processed)
			end
			if combinedWarnings then
				msg = msg .. "\n\nWarnings:\n" .. combinedWarnings
				LrDialogs.message(LOC("$$$/StyleAI/common/TaskCompleted/Title=Task Completed with Warnings"), msg)
			else
				LrDialogs.message(LOC("$$$/StyleAI/common/TaskCompleted/Title=Task Completed"), msg)
			end
		end

		log:trace(
			"AnalyzeAndIndexTask completed: Status=" .. status .. ", Processed=" .. processed .. ", Failed=" .. failed
		)
	end)
end)
