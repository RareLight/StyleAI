-- TaskAnalyzeAndIndex.lua
-- Unified task for analyzing photos with AI metadata and indexing them.
-- Combines the old TaskAnalyzeImage and TaskManageIndex into one streamlined workflow.


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
    props.indexingMode = prefs.indexingMode or "both"


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

    -- Force Re-index automatically switches write mode to overwrite (appendMetadata = false)
    props:addObserver('regenerateMetadata', function(properties, key, newValue)
        if newValue == true then
            properties.appendMetadata = false
        else
            properties.appendMetadata = prefs.appendMetadata ~= false
        end
    end)

    -- Metadata generation options
    props.temperature = prefs.temperature or 0.1
    props.promptTitles = {}
    for title, _ in pairs(prefs.prompts) do
        table.insert(props.promptTitles, { title = title, value = title })
    end

    props.prompt = prefs.prompt
    props.prompts = prefs.prompts

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
    props.useTopLevelKeyword = prefs.useTopLevelKeyword or false
    props.topLevelKeyword = prefs.topLevelKeyword or "StyleAI"
    props.bilingualKeywords = prefs.bilingualKeywords or false
    props.keywordSecondaryLanguage = prefs.keywordSecondaryLanguage or Defaults.defaultKeywordSecondaryLanguage

    -- AI Model selection (unified across providers)
    props.modelKey = prefs.modelKey -- format: "provider::model"
    props.language = prefs.generateLanguage or "English"
    props.temperature = prefs.temperature or 0.1
    props.replaceSS = prefs.replaceSS or false

    -- Build model list from server (local providers first)
    local modelItems = {}

    local modelsResp = SearchIndexAPI.getModels()
    if modelsResp and modelsResp.models then
        for provider, list in pairs(modelsResp.models) do
            for _, model in ipairs(list) do
                local title = provider .. ": " .. model
                local value = provider .. "::" .. model
                table.insert(modelItems, { title = title, value = value })
            end
        end
    end

    table.sort(modelItems, function(a, b) return a.title < b.title end)
    if (not modelItems or #modelItems == 0) then
        -- Fallback option if no models detected from backend
        table.insert(modelItems, { title = LOC("$$$/StyleAI/TaskAiEditPhotos/NoModels=No AI models available"), value = "none" })
    end
    if not props.modelKey or props.modelKey == '' then
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
            properties.llmStatusText = string.upper(provider) .. ": Ready"
            properties.llmStatusColor = LrColor(0, 0.8, 0)
        elseif status == "failed" then
            local errMsg = health.llm_errors and health.llm_errors[provider] or "unknown error"
            properties.llmStatusText = string.upper(provider) .. ": Failed (" .. tostring(errMsg) .. ")"
            properties.llmStatusColor = LrColor(0.8, 0, 0)
        elseif status == "not_configured" then
            properties.llmStatusText = string.upper(provider) .. ": Not Configured"
            properties.llmStatusColor = LrColor(0.8, 0.5, 0)
        else
            properties.llmStatusText = string.upper(provider) .. ": Unknown"
            properties.llmStatusColor = LrColor(0.5, 0.5, 0.5)
        end
    end

    local Defaults = require("Defaults")
    local UIFactory = require("UIFactory")
    local SearchIndexAPI = require("APISearchIndex")
    local healthData = SearchIndexAPI.getHealth()
    props.healthData = healthData or {}

    props:addObserver('modelKey', function(properties, key, newValue)
        updateLlmStatusText(properties)
    end)
    updateLlmStatusText(props)



    props.promptTitleMenu = f:popup_menu {
        items = bind 'promptTitles',
        value = bind 'prompt',
    }

    local contents = f:column {
        bind_to_object = props,
        spacing = f:control_spacing(),
        fill_horizontal = 1,

        f:static_text {
            title = LOC "$$$/StyleAI/AnalyzeAndIndex/Disclaimer=Note: This tool extracts fundamental AI vision metadata (SigLIP2) and is REQUIRED for Semantic Search, Auto-Tagging, and the ML Style Upgrade Assistant.",
            wrap = true,
            width_in_chars = 75,
            height_in_lines = 2,
            text_color = LrColor(0.1, 0.4, 0.9),
        },

        UIFactory.SettingsGroup(f, {
            title = LOC "$$$/StyleAI/AnalyzeAndIndex/ModeLabel=Workflow Mode",
            fill_horizontal = 1,
            f:row {
                f:static_text { title = LOC "$$$/StyleAI/AnalyzeAndIndex/IndexingMode=Indexing Mode:", width = share 'labelWidth' },
                f:popup_menu {
                    value = bind 'indexingMode',
                    tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/IndexingModeTooltip=Selects which AI features to run. Embedding enables semantic search; Metadata generates text tags.",
                    items = {
                        { title = LOC "$$$/StyleAI/UI/ModeEmbedOnly=AI Search Embedding Only", value = "embed" },
                        { title = LOC "$$$/StyleAI/UI/ModeMetaOnly=AI Auto-Tagging/Metadata Only", value = "meta" },
                        { title = LOC "$$$/StyleAI/UI/ModeBoth=Complete Package (Both)", value = "both" },
                    },
                    width = 300,
                },
            },
            f:row {
                f:static_text { title = LOC "$$$/StyleAI/AnalyzeAndIndex/Scope=Scope:", width = share 'labelWidth' },
                f:popup_menu {
                    value = bind 'scope',
                    tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/ScopeTooltip=Choose which photos to process in this run.",
                    width = 300,
                    items = {
                        { title = LOC "$$$/StyleAI/common/ScopeSelected=Selected photos only",              value = 'selected' },
                        { title = LOC "$$$/StyleAI/common/ScopeView=Current view",                          value = 'view' },
                        { title = LOC "$$$/StyleAI/AnalyzeAndIndex/ScopeAll=All photos in catalog",         value = 'all' },
                        { title = LOC "$$$/StyleAI/AnalyzeAndIndex/ScopeMissing=New or unprocessed photos", value = 'missing' },
                        { title = LOC "Photos already indexed in database", value = 'indexed' },
                    },
                },
            },
            f:row {
                f:static_text { title = LOC "$$$/StyleAI/AnalyzeAndIndex/ProcessingMode=Processing Mode:", width = share 'labelWidth' },
                f:column {
                    f:radio_button { value = bind 'regenerateMetadata', title = LOC "$$$/StyleAI/AnalyzeAndIndex/SkipExisting=Resume (Skip photos with existing data)", checked_value = false },
                    f:radio_button { value = bind 'regenerateMetadata', title = LOC "$$$/StyleAI/AnalyzeAndIndex/RegenerateMetadata=Force Re-index (Overwrite existing AI data)", checked_value = true },
                    f:static_text {
                        title = LOC "$$$/StyleAI/AnalyzeAndIndex/ForceReindexWarning=⚠️ Caution: Force Re-index will overwrite all existing tags, titles, captions, and alt text for processed photos.",
                        visible = bind 'regenerateMetadata',
                        text_color = LrColor(0.85, 0.4, 0),
                        tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/ForceReindexWarningTooltip=Forces re-analysis and completely overwrites existing metadata for selected fields.",
                    },
                }
            },
        }),

        f:view {
            place = 'overlapping',
            fill_horizontal = 1,

            -- BRANCH A: Embed Only View (No Tabs)
            f:view {

                fill_horizontal = 1,
            f:column {
                visible = bind {
                    key = "indexingMode",
                    transform = function(v) return v == "embed" end,
                },
                fill_horizontal = 1,
                
                UIFactory.SettingsGroup(f, {
                    title = LOC "$$$/StyleAI/AnalyzeAndIndex/EmbeddingTasks=Search Indexing (SigLIP2)",
                    fill_horizontal = 1,
                    visible = bind {
                        key = "indexingMode",
                        transform = function(v) return v == "embed" end,
                    },
                    f:row {
                        visible = bind {
                            key = "indexingMode",
                            transform = function(v) return v == "embed" end,
                        },

                        f:static_text {
                            visible = bind {
                                key = "indexingMode",
                                transform = function(v) return v == "embed" end,
                            },
                            title = bind {
                                key = "clipReady",
                                transform = function(v)
                                    if v then return LOC("$$$/StyleAI/AnalyzeAndIndex/SigLIPReady=SigLIP2: Ready (Model cached)")
                                    else return LOC("$$$/StyleAI/AnalyzeAndIndex/SigLIPNotReady=SigLIP2: Not Ready (Model missing)") end
                                end,
                            },
                            text_color = bind {
                                key = "clipReady",
                                transform = function(v)
                                    if v then return LrColor(0, 0.8, 0)
                                    else return LrColor(0.8, 0, 0) end
                                end
                            },
                        },
                    },
                }),
            },
            },

            -- BRANCH B: Metadata or Both View (With Tabs)
            f:view {

                fill_horizontal = 1,
            f:tab_view {
                visible = bind {
                    key = "indexingMode",
                    transform = function(v) return v == "meta" or v == "both" end,
                },
                fill_horizontal = 1,

                --------------------------------------------------------
                -- 1. GENERAL SETTINGS
                --------------------------------------------------------
                f:tab_view_item {
                    title = LOC "$$$/StyleAI/UI/TabGeneral=General Settings",
                    identifier = 'general',

                    UIFactory.SettingsGroup(f, {
                        title = LOC "$$$/StyleAI/AnalyzeAndIndex/EmbeddingTasks=Search Indexing (SigLIP2)",
                        fill_horizontal = 1,
                        visible = bind {
                            key = "indexingMode",
                            transform = function(v) return v == "both" end,
                        },
                    f:row {
                        visible = bind {
                            key = "indexingMode",
                            transform = function(v) return v == "both" end,
                        },
                        f:checkbox {
                            visible = bind {
                                key = "indexingMode",
                                transform = function(v) return v == "both" end,
                            },
                            value = bind 'enableEmbeddings',
                            title = LOC "$$$/StyleAI/AnalyzeAndIndex/EnableEmbeddings=Create search embeddings",
                            tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/EnableEmbeddingsTooltip=Analyzes image content visually to enable natural language semantic search.",
                            enabled = props.clipReady,
                        },
                        f:static_text {
                            visible = bind {
                                key = "indexingMode",
                                transform = function(v) return v == "both" end,
                            },
                            title = bind {
                                key = "clipReady",
                                transform = function(v)
                                    if v then return LOC("$$$/StyleAI/AnalyzeAndIndex/SigLIPReady=SigLIP2: Ready (Model cached)")
                                    else return LOC("$$$/StyleAI/AnalyzeAndIndex/SigLIPNotReady=SigLIP2: Not Ready (Model missing)") end
                                end,
                            },
                            text_color = bind {
                                key = "clipReady",
                                transform = function(v)
                                    if v then return LrColor(0, 0.8, 0)
                                    else return LrColor(0.8, 0, 0) end
                                end
                            },
                        },
                    },
                }),

                UIFactory.SettingsGroup(f, {
                    title = LOC "$$$/StyleAI/AnalyzeAndIndex/LlmTasks=AI Auto-Tagging",
                    fill_horizontal = 1,
                    visible = bind {
                        key = "indexingMode",
                        transform = function(v) return v == "meta" or v == "both" end,
                    },
                    f:row {
                        f:checkbox {
                            value = bind 'enableMetadata',
                            title = LOC "$$$/StyleAI/AnalyzeAndIndex/EnableMetadata=Generate AI metadata (Keywords, Title, Caption)",
                            tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/EnableMetadataTooltip=Uses a Vision LLM to automatically describe and tag your photos.",
                        },
                        f:static_text {
                            title = bind 'llmStatusText',
                            text_color = bind 'llmStatusColor',
                        },
                    },
                    f:row {
                        f:static_text { title = LOC "$$$/StyleAI/PluginInfoDialogSections/aiModel=AI Model:", width = share 'labelWidth' },
                        f:column {
                            f:popup_menu { value = bind 'modelKey', items = modelItems, width = 300 },

                        },
                    },
                    f:row {
                        f:static_text { title = LOC "$$$/StyleAI/AnalyzeAndIndex/Temperature=Temperature:", width = share 'labelWidth' },
                        f:slider { value = bind 'temperature', min = 0.0, max = 0.5, width = 200, tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/TemperatureTooltip=Lower values produce safer, more literal descriptions. Higher values are more creative." },
                        f:static_text { title = bind 'temperature', width = 40 },
                    },
                    f:row {
                        f:static_text { title = LOC "$$$/StyleAI/PluginInfoDialogSections/generateLanguage=Language:", width = share 'labelWidth' },
                        f:combo_box { value = bind 'language', items = Defaults.generateLanguages },
                    },
                }),

            },

            --------------------------------------------------------
            -- 2. KEYWORDS & METADATA
            --------------------------------------------------------
            f:tab_view_item {
                title = LOC "$$$/StyleAI/UI/TabKeywords=Keywords & Metadata",
                identifier = 'metadata',

                f:group_box {
                    title = LOC "$$$/StyleAI/AnalyzeAndIndex/MetadataOptions=Generated Fields",
                    fill_horizontal = 1,
                    f:row {
                        f:checkbox { value = bind 'generateKeywords', title = LOC "$$$/StyleAI/PluginInfoDialogSections/keywords=Keywords" },
                        f:spacer { width = 10 },
                        f:checkbox { value = bind 'generateTitle', title = LOC "$$$/StyleAI/PluginInfoDialogSections/title=Title" },
                        f:spacer { width = 10 },
                        f:checkbox { value = bind 'generateCaption', title = LOC "$$$/StyleAI/PluginInfoDialogSections/caption=Caption" },
                        f:spacer { width = 10 },
                        f:checkbox { value = bind 'generateAltText', title = LOC "$$$/StyleAI/PluginInfoDialogSections/alttext=Alt Text" },
                    },
                },

                f:group_box {
                    title = LOC "$$$/StyleAI/AnalyzeAndIndex/HierarchyOptions=Hierarchy & Language",
                    fill_horizontal = 1,
                    f:row {
                        f:static_text { title = LOC "$$$/StyleAI/PluginInfoDialogSections/useKeywordHierarchy=Keyword Hierarchy:", width = share 'labelWidth' },
                        f:checkbox { value = bind 'useKeywordHierarchy', title = LOC "$$$/StyleAI/UI/EnableHierarchy=Enable" },
                        f:push_button {
                            enabled = bind 'useKeywordHierarchy',
                            title = LOC "$$$/StyleAI/PluginInfoDialogSections/editKeywordHierarchy=Edit categories",
                            action = function() KeywordConfigProvider.showKeywordCategoryDialog() end,
                        },
                    },
                    f:row {
                        f:spacer { width = share 'labelWidth' },
                        f:checkbox { value = bind 'useCatalogKeywordStructure', title = LOC "$$$/StyleAI/UI/UseCatalogKeywordStructure=Use existing catalog structure" }
                    },
                    f:row {
                        f:static_text { title = LOC "$$$/StyleAI/PluginInfoDialogSections/useTopLevelKeyword=Top-level Keyword:", width = share 'labelWidth' },
                        f:checkbox { value = bind 'useTopLevelKeyword' },
                        f:edit_field { value = bind 'topLevelKeyword', width_in_chars = 20, enabled = bind 'useTopLevelKeyword' },
                    },
                    f:row {
                        f:static_text { title = LOC "$$$/StyleAI/UI/BilingualKeywords=Bilingual Synonyms:", width = share 'labelWidth' },
                        f:checkbox { value = bind 'bilingualKeywords', enabled = bind 'generateKeywords' },
                        f:combo_box { value = bind 'keywordSecondaryLanguage', items = Defaults.generateLanguages, enabled = bind 'bilingualKeywords', width = 160 },
                    }
                },
            },

            --------------------------------------------------------
            -- 3. PROMPT & CONTEXT
            --------------------------------------------------------
            f:tab_view_item {
                title = LOC "$$$/StyleAI/UI/TabContext=Prompt & Context",
                identifier = 'context',

                f:group_box {
                    title = LOC "$$$/StyleAI/UI/PromptTitle=Prompt Template",
                    fill_horizontal = 1,
                    f:row {
                        f:static_text { title = LOC "$$$/StyleAI/PluginInfoDialogSections/editPrompts=Template:", width = share 'labelWidth' },
                        props.promptTitleMenu,
                        f:push_button { title = LOC "$$$/StyleAI/PluginInfoDialogSections/add=Add", action = function() PromptConfigProvider.addPrompt(props) end },
                        f:push_button { title = LOC "$$$/StyleAI/PluginInfoDialogSections/delete=Delete", action = function() PromptConfigProvider.deletePrompt(props) end },
                    },
                    f:row {
                        f:static_text { title = LOC "$$$/StyleAI/PromptConfig/PromptField=Custom Prompt:", width = share 'labelWidth' },
                        f:scrolled_view {
                            height_in_lines = 8, fill_horizontal = 1, horizontal_scroller = false, vertical_scroller = true,
                            f:edit_field { value = bind 'selectedPrompt', width = 430, height_in_lines = 20, wraps = true, allow_newlines = true },
                        },
                    },
                },
                f:group_box {
                    title = LOC "$$$/StyleAI/AnalyzeAndIndex/ContextOptions=AI Context",
                    fill_horizontal = 1,
                    f:row {
                        f:checkbox { value = bind 'submitGPS', title = LOC "$$$/StyleAI/MetadataProvider/GPS=GPS Coordinates", tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/ContextGPSTooltip=Sends GPS data to the AI to help identify locations and landmarks." },
                        f:checkbox { value = bind 'submitKeywords', title = LOC "$$$/StyleAI/PluginInfoDialogSections/submitKeywords=Existing Keywords", tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/ContextKeywordsTooltip=Sends your existing Lightroom keywords to the AI to guide its focus." },
                        f:checkbox { value = bind 'submitFolderName', title = LOC "$$$/StyleAI/PluginInfoDialogSections/folderNames=Folder Names", tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/ContextFolderTooltip=Sends the parent folder name to the AI to provide context for the event or subject." },
                    },
                    f:row {
                        f:checkbox { value = bind 'showPhotoContextDialog', title = LOC "$$$/StyleAI/PluginInfoDialogSections/showPhotoContextDialog=Ask for context before each batch" },
                    },
                },
            },

            --------------------------------------------------------
            -- 4. ADVANCED / MAINTENANCE
            --------------------------------------------------------
            f:tab_view_item {
                title = LOC "$$$/StyleAI/UI/TabAdvanced=Advanced / Maintenance",
                identifier = 'advanced',

                f:group_box {
                    title = LOC "$$$/StyleAI/AnalyzeAndIndex/CatalogIntegration=Catalog Integration",
                    fill_horizontal = 1,
                    f:row {
                        f:checkbox { value = bind 'saveDataToCatalog', title = LOC "$$$/StyleAI/AnalyzeAndIndex/SaveDataToCatalog=Write generated data to Lightroom catalog", tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/SaveDataToCatalogTooltip=If unchecked, metadata is only stored in the backend AI database." },
                        f:checkbox { enabled = bind 'saveDataToCatalog', value = bind 'enableValidation', title = LOC "$$$/StyleAI/PluginInfoDialogSections/validation=Review/Edit each photo before saving", tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/ValidationTooltip=Opens a confirmation dialog for each photo before writing to the catalog." },
                    },
                },
                f:group_box {
                    title = LOC "$$$/StyleAI/AnalyzeAndIndex/MetadataHandling=Metadata Handling",
                    fill_horizontal = 1,
                    f:row {
                        f:static_text { title = LOC "$$$/StyleAI/AnalyzeAndIndex/WriteMode=Write:", width = share 'ctxLabelWidth' },
                        f:column {
                            f:checkbox { value = bind 'appendMetadata', title = LOC "$$$/StyleAI/AnalyzeAndIndex/AppendMetadata=Append to existing values instead of replacing", tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/AppendMetadataTooltip=Adds AI keywords and text without erasing your existing metadata." },
                            f:static_text {
                                title = LOC "$$$/StyleAI/AnalyzeAndIndex/ForceReindexWriteNotice=⚠️ Force Re-index active: Write mode is set to overwrite existing values.",
                                visible = bind 'regenerateMetadata',
                                text_color = LrColor(0.85, 0.4, 0),
                            },
                        },
                    },
                },
            },
        },
        },
        },

        f:row {
            f:push_button {
                title = LOC("$$$/StyleAI/common/ResetAllDefaults=Reset to Defaults"),
                action = function()
                    local confirm = LrDialogs.confirm(
                        LOC("$$$/StyleAI/common/ResetAllDefaultsConfirmTitle=Reset Settings"),
                        LOC("$$$/StyleAI/common/ResetAllDefaultsConfirmMessage=Are you sure you want to reset all options in this dialog to their default values?")
                    )
                    if confirm == "ok" then
                        props.indexingMode = "both"
                        props.scope = "selected"
                        props.enableMetadata = true
                        props.enableEmbeddings = props.clipReady
                        props.regenerateMetadata = false
                        props.temperature = 0.1
                        props.prompt = "Default"
                        props.selectedPrompt = Defaults.defaultSystemInstruction
                        props.generateKeywords = true
                        props.generateCaption = true
                        props.generateTitle = true
                        props.generateAltText = false
                        props.useKeywordHierarchy = false
                        props.useCatalogKeywordStructure = false
                        props.useTopLevelKeyword = false
                        props.topLevelKeyword = "StyleAI"
                        props.bilingualKeywords = false
                        props.keywordSecondaryLanguage = Defaults.defaultKeywordSecondaryLanguage
                        props.modelKey = (modelItems and modelItems[1]) and modelItems[1].value or "none"
                        props.language = "English"
                        props.replaceSS = false
                        props.submitGPS = false
                        props.submitKeywords = false
                        props.submitFolderName = false
                        props.showPhotoContextDialog = false
                        props.saveDataToCatalog = true
                        props.appendMetadata = true
                        props.enableValidation = false
                    end
                end,
            },
        },
    }
    local result = LrDialogs.presentModalDialog {
        title = LOC "$$$/StyleAI/AnalyzeAndIndex/WindowTitle=Analyze and Index Photos",
        contents = contents,
        actionVerb = LOC "$$$/StyleAI/common/Start=Start",
        cancelVerb = LOC "$$$/StyleAI/common/Cancel=Cancel",
        resizable = true,
    }

    if result == 'ok' then
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
        prefs.indexingBatchSize = prefs.indexingBatchSize or 32
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

    local dialogView = f:column {
        bind_to_object = props,
        f:row {
            f:static_text {
                title = photo:getFormattedMetadata('fileName'),
            },
        },
        f:row {
            f:spacer {
                height = 10,
            },
        },
        f:row {
            alignment = "center",
            f:catalog_photo {
                photo = photo,
                width = 300,
            },
        },
        f:row {
            f:spacer {
                height = 10,
            },
        },
        f:row {
            f:static_text {
                title = LOC "$$$/StyleAI/AnalyzeImageTask/PhotoContextDialogData=Photo Context",
            },
        },
        f:row {
            f:spacer {
                height = 10,
            },
        },
        f:row {
            f:edit_field {
                value = bind 'photoContextData',
                width_in_chars = 40,
                height_in_lines = 10,
            },
        },
        f:row {
            f:spacer {
                height = 10,
            },
        },
        f:checkbox {
            value = bind 'skipFromHere',
            title = LOC "$$$/StyleAI/AnalyzeImageTask/SkipPreflightFromHere=Use for all following pictures.",
        },
    }

    local result = LrDialogs.presentModalDialog({
        title = LOC "$$$/StyleAI/AnalyzeImageTask/PhotoContextDialogData=Photo Context",
        contents = dialogView,
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

		-- Now that the user has committed to processing, ensure the backend is running.
		if not Util.waitForServerDialog({ suppressProgressDialog = false }) then
			return
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

		local SearchIndexAPI = require("APISearchIndex")

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
			PhotoSelector.getPhotosInScope(props.scope, taskOptionsForScope, lookupScope)

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

		local LrPathUtils = import("LrPathUtils")
		-- Per-photo progress for import and analysis (denominator = photos to process, not 1)
		progressScope:setCaption(
			LOC("$$$/StyleAI/AnalyzeAndIndex/ProgressCount=^1 photos to process", tostring(#photosToProcess))
		)
		progressScope:setPortionComplete(0, #photosToProcess)

		if #photosToProcess >= 50 then
			log:info("Triggering database autosave before processing " .. tostring(#photosToProcess) .. " photos")
			progressScope:setCaption(LOC("$$$/StyleAI/AnalyzeAndIndex/CreatingSafetyBackup=Creating safety backup..."))
			SearchIndexAPI.triggerBackup(prefs.backupRotationDays)
		end

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
			options.onPhotoAnalyzed = function(photo, photoId, scope)
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
					})
				end
			end
		end

		local status, processed, failed, processedPhotos, combinedError, combinedWarnings
		status, processed, failed, processedPhotos, combinedError, combinedWarnings =
			SearchIndexAPI.analyzeAndIndexSelectedPhotos(photosToProcess, progressScope, options, false)

		-- De-clutter: cluster the generated keywords and build a name-mapping so that
		-- near-duplicates (e.g. "Automobile" → "Car") are unified before being written
		-- to the catalog.  Existing catalog keywords are preferred as canonical.
		-- No LLM validation here — CLIP threshold alone keeps latency reasonable.
		local keywordMapping = {}
		local mergedPairs = {} -- {from="Automobile", to="Car"} for dialog display


		if status ~= "allfailed" and props.enableMetadata and props.saveDataToCatalog and not usedInlineApply then
			log:trace("Saving metadata for processed photos...")
			local savedCount = 0
			local skippedCount = 0

			local skipFromHere = false

			for _, photo in ipairs(processedPhotos) do
				LrTasks.yield()
				LrTasks.sleep(0.001)
				-- Process responses if validation is enabled or just save metadata
				local photoId, photoIdErr = SearchIndexAPI.getPhotoIdForPhoto(photo)
				if photoId then
					local response = SearchIndexAPI.getPhotoData(photoId)

					-- Pre-compute deduped keywords; the validation dialog shows both
					-- side-by-side. Non-validation paths apply the mapping automatically.
					local dedupedKeywords = nil
					if next(keywordMapping) and response and response.metadata and response.metadata.keywords then
						dedupedKeywords = applyKeywordNameMapping(response.metadata.keywords, keywordMapping)
					end

					log:trace("Got generated data for photo: " .. (photo:getFormattedMetadata("fileName") or "unknown"))
					log:trace("Response: " .. (Util.dumpTable(response) or "nil"))

					if props.enableValidation and props.enableMetadata and response and response.metadata then
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
							elseif result == "other" then
								skippedCount = skippedCount + 1
								-- Clear only metadata so the photo stays in the index and can be regenerated later
								SearchIndexAPI.removePhotoMetadata(photoId)
								Util.addPhotoToRejectedDescriptionsCollection(photo, Defaults.catalogWriteAccessOptions)
							elseif result == "cancel" then
								break
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
						end
					elseif props.enableMetadata and response and response.metadata then
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
					end
				else
					log:error("Skipping photo data retrieval due to missing photo_id: " .. tostring(photoIdErr))
					skippedCount = skippedCount + 1
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
			local msg =
				LOC("$$$/StyleAI/AnalyzeAndIndex/SuccessMessage=Successfully processed ^1 photos.", processed)
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
