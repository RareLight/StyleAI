local LrPrefs = import("LrPrefs")
local LrPasswords = import("LrPasswords")
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
        ollamaBaseUrl = Defaults.defaultOllamaBaseUrl,
        lmstudioBaseUrl = Defaults.defaultLmStudioBaseUrl,
        backendServerUrl = Defaults.defaultBackendServerUrl,
        periodicalUpdateCheck = false,
        submitFolderName = false,
        useGlobalPhotoId = true,
        useLightroomKeywords = false,
        topLevelKeyword = Defaults.defaultTopLevelKeyword,
        knownTopLevelKeywords = Defaults.defaultTopLevelKeywords,
        shutdownServerOnExit = true,
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
    
    -- Special handling for empty backend url
    if prefs.backendServerUrl == "" then
        prefs.backendServerUrl = Defaults.defaultBackendServerUrl
    end

    -- Priority 8 Security: Migrate old plaintext API keys to OS Keychain
    if prefs.geminiApiKey ~= nil and prefs.geminiApiKey ~= "" then
        LrPasswords.store("StyleAI", "geminiApiKey", prefs.geminiApiKey)
        prefs.geminiApiKey = nil
    end
    if prefs.chatgptApiKey ~= nil and prefs.chatgptApiKey ~= "" then
        LrPasswords.store("StyleAI", "chatgptApiKey", prefs.chatgptApiKey)
        prefs.chatgptApiKey = nil
    end
end

--- Get a preference
function SettingsManager.get(key)
    if key == "geminiApiKey" or key == "chatgptApiKey" then
        return LrPasswords.retrieve("StyleAI", key) or ""
    end
    return prefs[key]
end

--- Set a preference with optional validation
function SettingsManager.set(key, value)
    if key == "geminiApiKey" or key == "chatgptApiKey" then
        if value and value ~= "" then
            LrPasswords.store("StyleAI", key, value)
        else
            -- If cleared, LR doesn't have a direct delete, so store empty string
            LrPasswords.store("StyleAI", key, "")
        end
        return true
    end

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
