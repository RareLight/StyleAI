Info = {}

Info.MAJOR = 0
Info.MINOR = 8
Info.REVISION = 1
Info.BUILD = 20260604
Info.VERSION = { major = Info.MAJOR, minor = Info.MINOR, revision = Info.REVISION, build = Info.BUILD }

return {

	LrSdkVersion = 14.0,
	LrSdkMinimumVersion = 7.0,
	LrToolkitIdentifier = "StyleAI",
	LrPluginName = "StyleAI",
	LrInitPlugin = "Init.lua",
	LrPluginInfoProvider = "PluginInfo.lua",
	LrPluginInfoURL = "https://github.com/RareLight/StyleAI",

	VERSION = Info.VERSION,

	LrMetadataProvider = "MetadataProvider.lua",
	LrMetadataTagsetFactory = "MetadataTagset.lua",

	-- Lightroom treats a present-but-empty menu array as one malformed menu
	-- description and reports its missing title as nil. Keep release menu
	-- declarations literal, and omit menu keys that have no entries.
	LrLibraryMenuItems = {
		{
			title = LOC("$$$/StyleAI/Menu/PreparePhotos=Prepare Photos..."),
			file = "TaskAnalyzeAndIndex.lua",
		},
		{
			title = LOC("$$$/StyleAI/Menu/LearnFromEdits=Learn From My Edits..."),
			file = "TaskTrainFromEdits.lua",
		},
		{
			title = LOC("$$$/StyleAI/Menu/ApplyMyStyle=Apply My Style..."),
			file = "TaskAiEditPredictive.lua",
		},
		{
			title = LOC("$$$/StyleAI/Menu/RateEdits=Rate Selected AI Edits..."),
			file = "TaskReviewAIEditOutcome.lua",
		},
		{
			title = LOC("$$$/StyleAI/Menu/StylesTraining=Styles & Training..."),
			file = "TaskStyleCatalog.lua",
		},
		{
			title = LOC("$$$/StyleAI/Menu/FindExamples=Find More Training Examples..."),
			file = "TaskDiscoverUpgradeCandidates.lua",
		},
	},

	LrShutdownApp = "ShutdownApp.lua",
}
