-- Release builds keep developer-only runtime controls disabled.  Development
-- packaging may replace this value without coupling it to user preferences.
return {
	developerBuild = false,
	-- These defaults keep developer-package manifest strings in the normal
	-- localization catalog even though Lightroom requires menu registrations to
	-- be emitted literally into the packaged Info.lua.
	developerMenuTitles = {
		LOC("$$$/StyleAI/Menu/DeveloperTests=Developer: Run Automated Tests..."),
		LOC("$$$/StyleAI/Menu/DeveloperBenchmark=Developer: Run Performance Benchmark..."),
		LOC("$$$/StyleAI/Menu/DeveloperRenderingSpike=Developer: Test Profile and HDR Capabilities..."),
		LOC("$$$/StyleAI/Menu/DeveloperReconcile=Developer: Reconcile Selected AI Edits..."),
	},
}
