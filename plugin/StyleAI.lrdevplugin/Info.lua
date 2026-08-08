Info = {}

Info.MAJOR = 0
Info.MINOR = 8
Info.REVISION = 1
Info.BUILD = 20260604
Info.VERSION = { major = Info.MAJOR, minor = Info.MINOR, revision = Info.REVISION, build = Info.BUILD }

local libraryMenuItems = {
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
}

-- Lightroom registers manifest menu items statically. Developer utilities are
-- therefore omitted from release manifests instead of being tied to the
-- runtime Debug preference.
local helpMenuItems = {}
-- Keep the release manifest self-contained: Info.lua is evaluated before the
-- plugin module search path is guaranteed to be initialized. Development
-- packaging flips this constant together with BuildConfig.developerBuild.
local developerBuild = false
if developerBuild then
	table.insert(helpMenuItems, {
		title = LOC("$$$/StyleAI/RenderingSpike/Menu=Developer: Test Profile and HDR SDK Support..."),
		file = "TaskRenderingStateCapabilitySpike.lua",
	})
	table.insert(helpMenuItems, {
		title = LOC("$$$/StyleAI/ReconcileEdits/Menu=Developer: Reconcile Selected AI Edit State..."),
		file = "TaskReconcileAIEditState.lua",
	})
	table.insert(helpMenuItems, {
		title = LOC("$$$/StyleAI/Developer/RunAutomatedTests=Developer: Run Automated Tests..."),
		file = "TaskAutomatedTests.lua",
	})
	table.insert(helpMenuItems, {
		title = LOC("$$$/StyleAI/Developer/RunPerformanceBenchmark=Developer: Run Performance Benchmark..."),
		file = "TaskBenchmark.lua",
	})
end

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

	LrLibraryMenuItems = libraryMenuItems,
	LrHelpMenuItems = helpMenuItems,

	LrShutdownApp = "ShutdownApp.lua",
}
