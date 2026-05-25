Info = {}

Info.MAJOR = 1
Info.MINOR = 0
Info.REVISION = 0
Info.BUILD = 20260524
Info.VERSION = { major = Info.MAJOR, minor = Info.MINOR, revision = Info.REVISION, build = Info.BUILD }

return {

	LrSdkVersion = 14.0,
	LrSdkMinimumVersion = 14.0,
	LrToolkitIdentifier = "StyleAI",
	LrPluginName = "StyleAI",
	LrInitPlugin = "Init.lua",
	LrPluginInfoProvider = "PluginInfo.lua",
	LrPluginInfoURL = "https://github.com/StyleAI",

	VERSION = Info.VERSION,

	LrMetadataProvider = "MetadataProvider.lua",
	LrMetadataTagsetFactory = "MetadataTagset.lua",

	LrLibraryMenuItems = {
		{
			title = LOC("$$$/StyleAI/Info/AiEditPhotosTitle=AI Edit Photos..."),
			file = "TaskAiEditPhotos.lua",
		},
		{
			title = LOC("$$$/StyleAI/Training/MenuItem=Save Edits as AI Training Examples..."),
			file = "TaskTrainFromEdits.lua",
		},
		{
			title = LOC("$$$/StyleAI/StyleCatalog/MenuItem=Style Catalog..."),
			file = "TaskStyleCatalog.lua",
		},
	},

	LrExportMenuItems = {
		{
			title = LOC("$$$/StyleAI/Info/AiEditPhotosTitle=AI Edit Photos..."),
			file = "TaskAiEditPhotos.lua",
		},
		{
			title = LOC("$$$/StyleAI/Training/MenuItem=Save Edits as AI Training Examples..."),
			file = "TaskTrainFromEdits.lua",
		},
		{
			title = LOC("$$$/StyleAI/StyleCatalog/MenuItem=Style Catalog..."),
			file = "TaskStyleCatalog.lua",
		},
	},

	LrHelpMenuItems = {
		{
			title = "Developer: Run Automated Tests...",
			file = "TaskAutomatedTests.lua",
		},
	},

	LrShutdownApp = "ShutdownApp.lua",
}
