-- TaskAutomatedTests.lua
-- A developer task to run automated diagnostics and logic assertions inside the Lightroom runtime.

local LrTasks = import 'LrTasks'
local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrHttp = import 'LrHttp'
local LrPathUtils = import 'LrPathUtils'

require 'JSON'
require 'Util'
require 'APISearchIndex'

---
-- Helper function to evaluate test conditions safely.
---
local function assertEqual(expected, actual, message)
    if expected ~= actual then
        error(string.format("ASSERTION FAILED: %s (Expected: %s, Got: %s)", message, tostring(expected), tostring(actual)))
    end
end

local function assertTrue(condition, message)
    if not condition then
        error(string.format("ASSERTION FAILED: %s", message))
    end
end

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext('automatedTestsTask', function(ctx)
        
        local confirm = LrDialogs.confirm(
            "Run Automated Tests?",
            "This will run a series of integrity checks for JSON parsers, utilities, and backend connectivity. Do you want to proceed?",
            "Yes, Run Tests",
            "Cancel"
        )
        
        if confirm == "cancel" then
            return
        end

        local testsPassed = 0
        local testsFailed = 0
        local errorMessages = {}

        local function runTest(testName, testFunc)
            log:info("Running test: " .. testName)
            local status, err = pcall(testFunc)
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

        runTest("Util.split string operation", function()
            local res = Util.split("a,b,c", ",")
            assertEqual(3, #res, "Should return 3 elements")
            assertEqual("a", res[1], "First element should be 'a'")
        end)

        runTest("Util.trim string operation", function()
            assertEqual("hello", Util.trim("  hello  "), "Should trim whitespace")
            assertEqual("hello", Util.trim("hello\n"), "Should trim newlines")
        end)

        runTest("JSON array decoding", function()
            local decoded = JSON.decode('["a", "b", "c"]')
            assertTrue(decoded ~= nil, "Decoded object should not be nil")
            assertEqual(3, #decoded, "Array should have 3 elements")
            assertEqual("b", decoded[2], "Second element should be 'b'")
        end)

        runTest("JSON object encoding", function()
            local obj = { success = true, meta = { value = 1 } }
            local encoded = JSON.encode(obj)
            assertTrue(string.find(encoded, "success") ~= nil, "Encoded string should contain success")
            assertTrue(string.find(encoded, "true") ~= nil, "Encoded string should contain true")
        end)

        runTest("Backend Connectivity - APISearchIndex.pingServer", function()
            local isUp = SearchIndexAPI.pingServer()
            assertTrue(isUp, "The backend server should be online and reachable.")
        end)

        runTest("Backend Connectivity - APISearchIndex.getDatabaseStats", function()
            -- Relies on the server being up
            local stats = SearchIndexAPI.getDatabaseStats(nil)
            assertTrue(stats ~= nil, "Should retrieve stats object")
            assertTrue(stats.photos ~= nil, "Stats should contain 'photos' property")
        end)

        ---------------------------------------------------------
        -- REPORTING
        ---------------------------------------------------------

        local summary = string.format("Automated Tests Completed.\n\nPassed: %d\nFailed: %d\n", testsPassed, testsFailed)
        
        if testsFailed > 0 then
            local maxErrorsToShow = 5
            summary = summary .. "\nErrors:\n"
            for i = 1, math.min(#errorMessages, maxErrorsToShow) do
                summary = summary .. "- " .. errorMessages[i] .. "\n"
            end
            if #errorMessages > maxErrorsToShow then
                summary = summary .. string.format("\n...and %d more errors.", #errorMessages - maxErrorsToShow)
            end
            LrDialogs.message("Some Tests Failed", summary, "critical")
        else
            LrDialogs.message("All Tests Passed", summary, "info")
        end

    end)
end)
