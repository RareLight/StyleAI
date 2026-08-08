local LrPrefs = import("LrPrefs")
local Defaults = require("Defaults")

SettingsManager = {}

local prefs = LrPrefs.prefsForPlugin()

--- Initializes all preferences with defaults if they don't exist
function SettingsManager.initializeDefaults()
    local defaultMap = {
        ai = "",
        generateLanguage = Defaults.defaultGenerateLanguage,
        exportSize = Defaults.defaultExportSize,
        exportQuality = Defaults.defaultExportQuality,
        useClip = true,
        usePreviewThumbnails = true,
        showPhotoContextDialog = true,
        submitKeywords = true,
        temperature = Defaults.defaultTemperature,
        maxTokens = Defaults.defaultMaxTokens,
        generateKeywords = true,
        generateCaption = true,
        generateAltText = true,
        enableValidation = true,
        showCosts = true,
        bilingualKeywords = Defaults.defaultBilingualKeywords,
        keywordSecondaryLanguage = Defaults.defaultKeywordSecondaryLanguage,
        keywordAliases = Defaults.defaultKeywordAliases,
        replaceSS = false,
        useKeywordHierarchy = true,
        useTopLevelKeyword = true,
        prompts = { Default = Defaults.defaultSystemInstruction },
        prompt = Defaults.defaultPromptName,
        editPrompts = { Default = Defaults.defaultEditSystemInstruction },
        editPrompt = Defaults.defaultEditPromptName,
        periodicalUpdateCheck = false,
        submitFolderName = false,
        useGlobalPhotoId = true,
        useLightroomKeywords = false,
        topLevelKeyword = Defaults.defaultTopLevelKeyword,
        knownTopLevelKeywords = Defaults.defaultTopLevelKeywords,
        searchInSemanticSiglip = true,
        searchInMetadata = true,
        searchInMetadataKeywords = true,
        searchInMetadataCaption = true,
        searchInMetadataTitle = true,
        searchInMetadataAltText = true,
        semanticClusteringThreshold = 75, -- Validated bounding
    }

    for key, defaultValue in pairs(defaultMap) do
        if prefs[key] == nil then
            prefs[key] = defaultValue
        end
    end
    -- Force 'Default' prompts to update to the latest shipped version
    -- ONLY if the user hasn't customized them (i.e., they exactly match a known legacy default)
    if type(prefs.prompts) == "table" and prefs.prompts["Default"] then
        local current = prefs.prompts["Default"]
        if current ~= Defaults.defaultSystemInstruction then
            for _, legacy in ipairs(Defaults.legacySystemInstructions or {}) do
                if current == legacy then
                    prefs.prompts["Default"] = Defaults.defaultSystemInstruction
                    break
                end
            end
        end
    end

    if type(prefs.editPrompts) == "table" and prefs.editPrompts["Default"] then
        local current = prefs.editPrompts["Default"]
        if current ~= Defaults.defaultEditSystemInstruction then
            for _, legacy in ipairs(Defaults.legacyEditSystemInstructions or {}) do
                if current == legacy then
                    prefs.editPrompts["Default"] = Defaults.defaultEditSystemInstruction
                    break
                end
            end
        end
    end
    
end

--- Get a preference
function SettingsManager.get(key)
    return prefs[key]
end

--- Set a preference with optional validation
function SettingsManager.set(key, value)
    -- Add validation rules for specific keys
    if key == "semanticClusteringThreshold" then
        if type(value) ~= "number" then return false end
        value = math.max(50, math.min(100, value))
    end
    if key == "temperature" then
        if type(value) ~= "number" then return false end
        value = math.max(0.0, math.min(2.0, value))
    end

    prefs[key] = value
    return true
end

return SettingsManager
