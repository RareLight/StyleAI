Info = {}

Info.MAJOR = 9
Info.MINOR = 9
Info.REVISION = 9
Info.BUILD = 99991212
Info.VERSION = { major = Info.MAJOR, minor = Info.MINOR, revision = Info.REVISION, build = Info.BUILD }

return {

	LrSdkVersion = 14.0,
	LrSdkMinimumVersion = 14.0,
	LrToolkitIdentifier = "LrGeniusAI",
	LrPluginName = "LrGeniusAI",
	LrInitPlugin = "Init.lua",
	LrPluginInfoProvider = "PluginInfo.lua",
	LrPluginInfoURL = "https://github.com/LrGenius",

	VERSION = Info.VERSION,

	LrMetadataProvider = "MetadataProvider.lua",
	LrMetadataTagsetFactory = "MetadataTagset.lua",

	LrLibraryMenuItems = {
		{
			title = LOC("$$$/LrGeniusAI/Info/AiEditPhotosTitle=AI Edit Photos..."),
			file = "TaskAiEditPhotos.lua",
		},
		{
			title = LOC("$$$/LrGeniusAI/Training/MenuItem=Save Edits as AI Training Examples..."),
			file = "TaskTrainFromEdits.lua",
		},
		{
			title = LOC("$$$/LrGeniusAI/StyleCatalog/MenuItem=Style Catalog..."),
			file = "TaskStyleCatalog.lua",
		},
		{
			title = LOC("$$$/LrGeniusAI/Menu/AnalyzeAndIndex=Index Photos for Style Matching..."),
			file = "TaskAnalyzeAndIndex.lua",
		},
	},

	LrExportMenuItems = {
		{
			title = LOC("$$$/LrGeniusAI/Info/AiEditPhotosTitle=AI Edit Photos..."),
			file = "TaskAiEditPhotos.lua",
		},
		{
			title = LOC("$$$/LrGeniusAI/Training/MenuItem=Save Edits as AI Training Examples..."),
			file = "TaskTrainFromEdits.lua",
		},
		{
			title = LOC("$$$/LrGeniusAI/StyleCatalog/MenuItem=Style Catalog..."),
			file = "TaskStyleCatalog.lua",
		},
		{
			title = LOC("$$$/LrGeniusAI/Menu/AnalyzeAndIndex=Index Photos for Style Matching..."),
			file = "TaskAnalyzeAndIndex.lua",
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
