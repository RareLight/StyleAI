local LrPrefs = import("LrPrefs")
local Defaults = require("Defaults")

SettingsManager = {}

local prefs = LrPrefs.prefsForPlugin()

--- Initializes all preferences with defaults if they don't exist
function SettingsManager.initializeDefaults()
    local defaultMap = {
        ai = "",
        generateLanguage = Defaults.defaultGenerateLanguage,
        useClip = true,
        showPhotoContextDialog = true,
        submitKeywords = true,
        temperature = Defaults.defaultTemperature,
        generateKeywords = true,
        generateCaption = true,
        generateAltText = true,
        enableValidation = true,
        bilingualKeywords = Defaults.defaultBilingualKeywords,
        keywordSecondaryLanguage = Defaults.defaultKeywordSecondaryLanguage,
        keywordAliases = Defaults.defaultKeywordAliases,
        replaceSS = false,
        useKeywordHierarchy = true,
        useTopLevelKeyword = true,
        prompts = { Default = Defaults.defaultSystemInstruction },
        prompt = Defaults.defaultPromptName,
        periodicalUpdateCheck = false,
        captureLlmInputs = false,
        submitFolderName = false,
        useGlobalPhotoId = true,
        topLevelKeyword = Defaults.defaultTopLevelKeyword,
        knownTopLevelKeywords = Defaults.defaultTopLevelKeywords,
    }

    for key, defaultValue in pairs(defaultMap) do
        if prefs[key] == nil then
            prefs[key] = defaultValue
        end
    end

    -- Diagnostic image capture used to be controlled directly by
    -- `auditLlmInputs`. Never carry an enabled legacy value forward; capture
    -- remains an explicit, independent opt-in.
    if prefs.debugSettingsMigrated ~= true then
        if prefs.auditLlmInputsPath and not prefs.captureLlmInputsPath then
            prefs.captureLlmInputsPath = prefs.auditLlmInputsPath
        end
        prefs.captureLlmInputs = false
        prefs.auditLlmInputs = false
        prefs.debugSettingsMigrated = true
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

end

--- Get a preference
function SettingsManager.get(key)
    return prefs[key]
end

--- Set a preference with optional validation
function SettingsManager.set(key, value)
    -- Add validation rules for specific keys
    if key == "temperature" then
        if type(value) ~= "number" then return false end
        value = math.max(0.0, math.min(2.0, value))
    end

    prefs[key] = value
    return true
end

return SettingsManager
