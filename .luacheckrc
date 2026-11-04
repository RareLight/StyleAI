std = "lua51"

-- Lightroom globals and project-specific globals
globals = {
    "import",
    "LOC",
    "MAC_ENV",
    "WIN_ENV",
    "_PLUGIN",
    "prefs",
    "log",
    "recipe",
    "JSON",
    "Util",
    "ErrorHandler",
    "Info",
    "UpdateCheck",
    "Defaults",
    "KeywordConfigProvider",
    "MetadataManager",
    "OnboardingWizard",
    "PhotoContextData",
    "PhotoSelector",
    "PluginInfoDialogSections",
    "PromptConfigProvider",
    "SearchIndexAPI",
    "SkipPhotoContextDialog",
    "DevelopEditManager",
    "TaskUpdate",
    "Pipeline",
    "SettingsManager",
    "UIFactory"
}

-- Read-only Lightroom SDK namespaces
read_globals = {
    "LrApplication",
    "LrApplicationView",
    "LrBinding",
    "LrColor",
    "LrDate",
    "LrDevelopController",
    "LrDialogs",
    "LrExportSession",
    "LrFileUtils",
    "LrFunctionContext",
    "LrHttp",
    "LrMD5",
    "LrPathUtils",
    "LrProgressScope",
    "LrShell",
    "LrStringUtils",
    "LrTasks",
    "LrSystemInfo",
    "LrView"
}

-- You can selectively ignore certain standard checks here if needed
ignore = {
    "212", -- Unused argument (e.g. in UI callbacks where you don't need 'view')
    "631", -- Line is too long
}

-- Existing warnings are scoped to named symbols while this legacy code is cleaned up.
files["plugin/StyleAI.lrdevplugin/APISearchIndex.lua"] = { ignore = { "211/buildUrlWithParams", "211/ensureDbMigrationsDone", "211/msg", "211/response", "211/startedAt", "211/waitForCatalogDbMigrationsDone", "221/progressScope", "231/matched", "231/maxWorkers", "231/userMessage", "311/jpegData", "311/modelDisplay", "311/requestRef", "421/msg", "421/serverDir", "431/success", "542" } }
files["plugin/StyleAI.lrdevplugin/AiEditAction.lua"] = { ignore = { "311/activeProducers", "412/collections" } }
files["plugin/StyleAI.lrdevplugin/DevelopEditManager.lua"] = { ignore = { "311/originalRenderingSettings" } }
files["plugin/StyleAI.lrdevplugin/MetadataBenchmark.lua"] = { ignore = { "213/model", "431/operation", "431/preflightError" } }
files["plugin/StyleAI.lrdevplugin/MetadataBenchmarkReport.lua"] = { ignore = { "411/result" } }
files["plugin/StyleAI.lrdevplugin/MetadataManager.lua"] = { ignore = { "311/topKeyword" } }
files["plugin/StyleAI.lrdevplugin/PhotoSelector.lua"] = { ignore = { "211/err" } }
files["plugin/StyleAI.lrdevplugin/Pipeline.lua"] = { ignore = { "311/resultOrErr", "311/success" } }
files["plugin/StyleAI.lrdevplugin/TaskBenchmark.lua"] = { ignore = { "211/combinedError", "211/combinedWarnings", "211/processedPhotos", "211/status" } }
files["plugin/StyleAI.lrdevplugin/TaskStyleCatalog.lua"] = { ignore = { "213/i" } }
files["plugin/StyleAI.lrdevplugin/Util.lua"] = { ignore = { "213/i" } }
