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

	LrLibraryMenuItems = {
		{
			title = LOC("$$$/StyleAI/Training/MenuLearnMyStyles=Learn My Styles"),
			file = "TaskTrainFromEdits.lua",
		},
		{
			title = LOC("$$$/StyleAI/Info/MenuApplyMyStyleEdits=Apply My Style Edits"),
			file = "TaskAiEditPredictive.lua",
		},
		{
			title = LOC("$$$/StyleAI/StyleCatalog/MenuMyStylesIndex=My Styles Index"),
			file = "TaskStyleCatalog.lua",
		},
		{
			title = LOC("$$$/StyleAI/Info/MenuApplyLLMEdits=Apply LLM Edits"),
			file = "TaskAiEditCreative.lua",
		},
		{
			title = LOC("$$$/StyleAI/AnalyzeAndIndex/MenuAutoTagger=Auto-Tagger (optional)"),
			file = "TaskAnalyzeAndIndex.lua",
		},
		{
			title = LOC("$$$/StyleAI/Developer/RunPerformanceBenchmark=Developer: Run Performance Benchmark..."),
			file = "TaskBenchmark.lua",
		},
	},

	LrExportMenuItems = {
		{
			title = LOC("$$$/StyleAI/Training/MenuLearnMyStyles=Learn My Styles"),
			file = "TaskTrainFromEdits.lua",
		},
		{
			title = LOC("$$$/StyleAI/Info/MenuApplyMyStyleEdits=Apply My Style Edits"),
			file = "TaskAiEditPredictive.lua",
		},
		{
			title = LOC("$$$/StyleAI/StyleCatalog/MenuMyStylesIndex=My Styles Index"),
			file = "TaskStyleCatalog.lua",
		},
		{
			title = LOC("$$$/StyleAI/Info/MenuApplyLLMEdits=Apply LLM Edits"),
			file = "TaskAiEditCreative.lua",
		},
		{
			title = LOC("$$$/StyleAI/AnalyzeAndIndex/MenuAutoTagger=Auto-Tagger (optional)"),
			file = "TaskAnalyzeAndIndex.lua",
		},
	},

	LrHelpMenuItems = {
		{
			title = LOC("$$$/StyleAI/Developer/RunAutomatedTests=Developer: Run Automated Tests..."),
			file = "TaskAutomatedTests.lua",
		},
		{
			title = LOC("$$$/StyleAI/Developer/RunPerformanceBenchmark=Developer: Run Performance Benchmark..."),
			file = "TaskBenchmark.lua",
		},
	},

	LrShutdownApp = "ShutdownApp.lua",
}
