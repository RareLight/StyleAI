-- TaskAutomatedTests.lua
-- A developer task to run automated diagnostics and logic assertions inside the Lightroom runtime.

local LrTasks = import("LrTasks")
local LrDialogs = import("LrDialogs")
local LrFunctionContext = import("LrFunctionContext")

require("JSON")
require("Util")
require("APISearchIndex")
local Pipeline = require("Pipeline")
local DevelopEditManager = require("DevelopEditManager")
local RenderingStateCapability = require("RenderingStateCapability")

---
-- Helper function to evaluate test conditions safely.
---
local function assertEqual(expected, actual, message)
	if expected ~= actual then
		error(
			string.format("ASSERTION FAILED: %s (Expected: %s, Got: %s)", message, tostring(expected), tostring(actual))
		)
	end
end

local function assertTrue(condition, message)
	if not condition then
		error(string.format("ASSERTION FAILED: %s", message))
	end
end

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("automatedTestsTask", function(ctx)
		local confirm = LrDialogs.confirm(
			LOC("$$$/StyleAI/TaskAutomatedTests/RunConfirmTitle=Run Automated Tests?"),
			LOC(
				"$$$/StyleAI/TaskAutomatedTests/RunConfirmMsg=This will run a series of integrity checks for JSON parsers, utilities, and backend connectivity. Do you want to proceed?"
			),
			LOC("$$$/StyleAI/TaskAutomatedTests/RunConfirmOk=Yes, Run Tests"),
			LOC("$$$/StyleAI/common/Cancel=Cancel")
		)

		if confirm == "cancel" then
			return
		end

		local testsPassed = 0
		local testsFailed = 0
		local errorMessages = {}

		local function runTest(testName, testFunc)
			log:info("Running test: " .. testName)
			local status, err = LrTasks.pcall(testFunc)
			if status then
				testsPassed = testsPassed + 1
			else
				testsFailed = testsFailed + 1
				table.insert(errorMessages, string.format("Test '%s' failed: %s", testName, tostring(err)))
				log:error(string.format("Test '%s' failed: %s", testName, tostring(err)))
			end
		end

		---------------------------------------------------------
		-- TEST CASES
		---------------------------------------------------------

		runTest("Util.string_split string operation", function()
			local res = Util.string_split("a,b,c", ",")
			assertEqual(3, #res, "Should return 3 elements")
			assertEqual("a", res[1], "First element should be 'a'")
		end)

		runTest("Util.trim string operation", function()
			assertEqual("hello", Util.trim("  hello  "), "Should trim whitespace")
			assertEqual("hello", Util.trim("hello\n"), "Should trim newlines")
		end)

		runTest("JSON array decoding", function()
			local decoded = JSON:decode('["a", "b", "c"]')
			assertTrue(decoded ~= nil, "Decoded object should not be nil")
			assertEqual(3, #decoded, "Array should have 3 elements")
			assertEqual("b", decoded[2], "Second element should be 'b'")
		end)

		runTest("JSON object encoding", function()
			local obj = { success = true, meta = { value = 1 } }
			local encoded = JSON:encode(obj)
			assertTrue(string.find(encoded, "success") ~= nil, "Encoded string should contain success")
			assertTrue(string.find(encoded, "true") ~= nil, "Encoded string should contain true")
		end)

		runTest("Backend Connectivity - APISearchIndex.pingServer", function()
			local isUp = SearchIndexAPI.pingServer()
			assertTrue(isUp, "The backend server should be online and reachable.")
		end)

		runTest("Backend Connectivity - APISearchIndex.getStats", function()
			-- Relies on the server being up
			local stats = SearchIndexAPI.getStats()
			assertTrue(stats ~= nil, "Should retrieve stats object")
			assertTrue(stats.photos ~= nil, "Stats should contain 'photos' property")
		end)

		runTest("Backend Connectivity - APISearchIndex.isClipReady", function()
			-- Validates the /clip/status endpoint (returns boolean)
			local ready = SearchIndexAPI.isClipReady()
			assertTrue(type(ready) == "boolean", "isClipReady should return a boolean")
		end)

		runTest("Backend Connectivity - APISearchIndex.pruneDatabase (Dry Run)", function()
			-- We do a dry run by sending a completely dummy photo ID. It shouldn't crash.
			local results, err = SearchIndexAPI.pruneDatabase("automated-test-catalog", {"dummy_id_123"})
			assertTrue(err == nil, "pruneDatabase should not return an error")
			assertTrue(type(results) == "table", "pruneDatabase should return a results table")
			assertTrue(results.deleted ~= nil, "pruneDatabase should return deleted count")
		end)

		---------------------------------------------------------
		-- PIPELINE TESTS
		---------------------------------------------------------

		runTest("Pipeline.runSequentialBatch - Success Path", function()
			local mockPhotos = { { id = 1 }, { id = 2 } }
			-- Mock photo object
			for _, p in ipairs(mockPhotos) do
				p.getFormattedMetadata = function(self, key) return "MockPhoto" .. self.id end
			end

			local processFn = function(photo, index, total, catalog)
				return true, "Success"
			end

			local summary = Pipeline.runSequentialBatch(mockPhotos, nil, {}, processFn)
			assertEqual(2, summary.successCount, "Should have 2 successes")
			assertEqual(0, summary.errorCount, "Should have 0 errors")
		end)

		runTest("Pipeline.runSequentialBatch - Error Capture", function()
			local mockPhotos = { { id = 1 }, { id = 2 } }
			for _, p in ipairs(mockPhotos) do
				p.getFormattedMetadata = function(self, key) return "MockPhoto" .. self.id end
			end

			local processFn = function(photo, index, total, catalog)
				if photo.id == 1 then
					return false, "Failed on photo 1"
				else
					return true, "Success"
				end
			end

			local summary = Pipeline.runSequentialBatch(mockPhotos, nil, {}, processFn)
			assertEqual(1, summary.successCount, "Should have 1 success")
			assertEqual(1, summary.errorCount, "Should have 1 error")
			assertTrue(string.find(summary.errors[1], "Failed on photo 1") ~= nil, "Error array should contain the failure message")
		end)

		runTest("Pipeline.runSequentialBatch - pcall Crash Protection", function()
			local mockPhotos = { { id = 1 } }
			for _, p in ipairs(mockPhotos) do
				p.getFormattedMetadata = function(self, key) return "MockPhoto" .. self.id end
			end

			local processFn = function(photo, index, total, catalog)
				error("Simulated crash in processing logic")
			end

			local summary = Pipeline.runSequentialBatch(mockPhotos, nil, {}, processFn)
			assertEqual(0, summary.successCount, "Should have 0 successes")
			assertEqual(1, summary.errorCount, "Should have 1 error")
			assertTrue(string.find(summary.errors[1], "Simulated crash") ~= nil, "Crash message should be caught and returned")
		end)

		runTest("Util.getGlobalPhotoIdForPhoto - Stable Deterministic ID", function()
			local mockPhoto = {
				getRawMetadata = function(self, key)
					local md = {
						dateTimeOriginal = "2025-01-01T12:00:00",
						cameraMake = "Canon",
						cameraModel = "EOS R5",
						shutterSpeed = 0.01,
						aperture = 2.8,
						isoSpeedRating = 100,
					}
					return md[key]
				end,
			}
			local id1 = Util.getGlobalPhotoIdForPhoto(mockPhoto)
			local id2 = Util.getGlobalPhotoIdForPhoto(mockPhoto)
			assertTrue(id1 ~= nil and id1 ~= "", "Global photo ID should not be empty")
			assertEqual(id1, id2, "Calling getGlobalPhotoIdForPhoto twice on same photo should return identical ID")
		end)

		runTest("DevelopEditManager.formatRecipeDetails - Safe Formatting", function()
			local response = {
				recipe = {
					Exposure = 0.5,
					Contrast = 10,
				},
				confidence = 0.85,
					source = "policy_v2",
			}
			local details = DevelopEditManager.formatRecipeDetails(response)
			assertTrue(details ~= nil and details ~= "", "Formatted recipe details should not be empty")
		end)

		runTest("RenderingStateCapability - Profile and HDR remain separate", function()
			local state = RenderingStateCapability.captureRenderingState({
				CameraProfile = "Camera Standard + HDR",
				CameraProfileRaw = "custom-profile-id",
				HDREditMode = 1,
			})
			assertEqual("Camera Standard + HDR", RenderingStateCapability.profileDisplayName(state), "Profile name must be preserved verbatim")
			assertTrue(RenderingStateCapability.isHdr(state), "HDR must be read from its own SDK field")
		end)

		runTest("RenderingStateCapability - Candidate settings preserve unrelated state", function()
			local baseline = { Exposure2012 = 0.25, CameraProfile = "Adobe Color", HDREditMode = 0 }
			local target = RenderingStateCapability.captureRenderingState({ CameraProfile = "Camera Neutral", HDREditMode = 1 })
			local candidate = RenderingStateCapability.buildCandidateSettings(baseline, target, { "CameraProfile" })
			assertEqual(0.25, candidate.Exposure2012, "Unrelated Develop settings must survive")
			assertEqual("Camera Neutral", candidate.CameraProfile, "Observed target representation must be copied")
			assertEqual(0, candidate.HDREditMode, "Independent profile tests must preserve HDR")
		end)

		runTest("RenderingStateCapability - Camera compatibility includes make and model", function()
			local first = RenderingStateCapability.cameraCompatibilityKey("Canon", "EOS R5")
			local second = RenderingStateCapability.cameraCompatibilityKey("Canon", "EOS R6")
			assertTrue(first ~= second, "Profiles from different camera models must not share a compatibility key")
			assertEqual(first, RenderingStateCapability.cameraCompatibilityKey("CANON", "EOS R5"), "Compatibility matching should be case-insensitive")
			assertEqual(nil, RenderingStateCapability.cameraCompatibilityKey(nil, nil), "Missing camera identity must never form a compatibility group")
		end)

		runTest("RenderingStateCapability - Substituted profiles fail exact verification", function()
			local requested = RenderingStateCapability.captureRenderingState({
				CameraProfile = "Custom Profile",
				HDREditMode = 0,
			})
			local substituted = RenderingStateCapability.captureRenderingState({
				CameraProfile = "Adobe Color",
				HDREditMode = 0,
			})
			assertTrue(
				not RenderingStateCapability.keysMatch(substituted, requested, { "CameraProfile" }),
				"A Lightroom profile substitution must trigger rollback"
			)
		end)

		---------------------------------------------------------
		-- REPORTING
		---------------------------------------------------------

		local summary =
			string.format("Automated Tests Completed.\n\nPassed: %d\nFailed: %d\n", testsPassed, testsFailed)

		if testsFailed > 0 then
			local combinedError = ""
			for i = 1, math.min(#errorMessages, 5) do
				combinedError = combinedError .. errorMessages[i] .. "\n"
			end
			if #errorMessages > 5 then
				combinedError = combinedError
					.. LOC("$$$/StyleAI/common/MoreErrors=... and ^1 more errors", #errorMessages - 5)
			end

			ErrorHandler.handleError(
				LOC("$$$/StyleAI/TaskAutomatedTests/FailedTitle=Some Tests Failed"),
				combinedError
			)
		else
			LrDialogs.message(LOC("$$$/StyleAI/TaskAutomatedTests/PassedTitle=All Tests Passed"), summary, "info")
		end
	end)
end)
