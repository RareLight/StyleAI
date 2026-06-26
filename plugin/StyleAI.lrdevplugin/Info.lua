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
			title = LOC("$$$/StyleAI/AnalyzeAndIndex/MenuAutoTagger=Step 1: Index Photos"),
			file = "TaskAnalyzeAndIndex.lua",
		},
		{
			title = LOC("$$$/StyleAI/Training/MenuLearnMyStyles=Step 2: Learn My Styles"),
			file = "TaskTrainFromEdits.lua",
		},
		{
			title = LOC("$$$/StyleAI/Info/MenuApplyMyStyleEdits=Step 3: Apply Style Edits"),
			file = "TaskAiEditPredictive.lua",
		},
		{
			title = LOC("$$$/StyleAI/StyleCatalog/MenuMyStylesIndex=Utility: Styles Index"),
			file = "TaskStyleCatalog.lua",
		},
		{
			title = LOC("$$$/StyleAI/Info/MenuApplyLLMEdits=Utility: LLM Edit by Prompt"),
			file = "TaskAiEditCreative.lua",
		},
		{
			title = LOC("$$$/StyleAI/Developer/RunPerformanceBenchmark=Developer: Run Performance Benchmark..."),
			file = "TaskBenchmark.lua",
		},
	},

	LrExportMenuItems = {
		{
			title = LOC("$$$/StyleAI/AnalyzeAndIndex/MenuAutoTagger=Step 1: Index Photos"),
			file = "TaskAnalyzeAndIndex.lua",
		},
		{
			title = LOC("$$$/StyleAI/Training/MenuLearnMyStyles=Step 2: Learn My Styles"),
			file = "TaskTrainFromEdits.lua",
		},
		{
			title = LOC("$$$/StyleAI/Info/MenuApplyMyStyleEdits=Step 3: Apply Style Edits"),
			file = "TaskAiEditPredictive.lua",
		},
		{
			title = LOC("$$$/StyleAI/StyleCatalog/MenuMyStylesIndex=Utility: Styles Index"),
			file = "TaskStyleCatalog.lua",
		},
		{
			title = LOC("$$$/StyleAI/Info/MenuApplyLLMEdits=Utility: LLM Edit by Prompt"),
			file = "TaskAiEditCreative.lua",
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
