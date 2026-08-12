Info = {}

Info.MAJOR = 0
Info.MINOR = 8
Info.REVISION = 2
Info.BUILD = 20260808
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

	-- LrExportMenuItems is Lightroom's cross-module File > Plug-in Extras
	-- registration point. LrLibraryMenuItems disappears outside the Library
	-- module. Keep this declaration literal and omit menu keys with no entries.
	LrExportMenuItems = {
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
}
