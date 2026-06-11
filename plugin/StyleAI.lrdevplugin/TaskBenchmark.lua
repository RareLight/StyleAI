-- TaskBenchmark.lua
-- A developer utility task to empirically test pipeline permutations for GPU saturation.

local LrTasks = import("LrTasks")
local LrDialogs = import("LrDialogs")
local LrFunctionContext = import("LrFunctionContext")
local LrApplication = import("LrApplication")
local LrDate = import("LrDate")

require("Util")
local SearchIndexAPI = require("APISearchIndex")

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("benchmarkTask", function(ctx)
		local catalog = LrApplication.activeCatalog()
		local selectedPhotos = catalog:getTargetPhotos()
		
		if not selectedPhotos or #selectedPhotos < 160 then
			LrDialogs.message(
				"Benchmark Error",
				"Please select a representative batch of at least 160 photos (ideally 500+) to run the benchmark.\n\nThis ensures each test permutation runs on a completely distinct set of photos, preventing Lightroom's internal preview caches from skewing the results.",
				"critical"
			)
			return
		end

		local confirm = LrDialogs.confirm(
			"Run Performance Benchmark?",
			"This will test 10 permutations of worker threads and batch sizes on your selected " .. #selectedPhotos .. " photos. This will take a while and will force full processing.\n\nOpen StyleAI.log after completion to view results.",
			"Run Benchmark",
			"Cancel"
		)
		if confirm == "cancel" then return end

		local permutations = {
			{senders = 2, analyzers = 8, batch = 8},
			{senders = 2, analyzers = 8, batch = 16},
			{senders = 2, analyzers = 16, batch = 16},
			{senders = 4, analyzers = 16, batch = 16},
			{senders = 4, analyzers = 16, batch = 32},
			{senders = 4, analyzers = 32, batch = 32},
			{senders = 4, analyzers = 16, batch = 64},
			{senders = 4, analyzers = 32, batch = 64},
		}
		
		local photosPerTest = math.floor(#selectedPhotos / #permutations)

		local results = {}
		local progressScope = LrDialogs.showModalProgressDialog({
			title = "Running Performance Benchmark...",
			functionContext = ctx
		})

		log:info("--- STARTING PERFORMANCE BENCHMARK ---")
		log:info("Testing " .. #selectedPhotos .. " photos across " .. #permutations .. " permutations.")

		for i, config in ipairs(permutations) do
			if progressScope:isCanceled() then
				log:info("Benchmark canceled by user.")
				break
			end
			
			local titleText = string.format("Test %d of %d (Senders: %d, Analyzers: %d, Batch: %d)", i, #permutations, config.senders, config.analyzers, config.batch)
			progressScope:setCaption(titleText)
			
			local options = {
				tasks = {"embeddings"},
				indexingMode = "embed",
				enableEmbeddings = true,
				enableMetadata = false,
				regenerate_metadata = true,
				cache_images = false,
				benchmarkConfig = config,
				forceRecompute = true
			}
			
			local testPhotos = {}
			local startIndex = ((i - 1) * photosPerTest) + 1
			for p = startIndex, startIndex + photosPerTest - 1 do
				table.insert(testPhotos, selectedPhotos[p])
			end

			local startTime = LrDate.currentTime()
			-- Run the batch pipeline natively
			local status, processed, failed, processedPhotos, combinedError, combinedWarnings = 
				SearchIndexAPI.analyzeAndIndexSelectedPhotos(testPhotos, progressScope, options, false)
				
			local duration = LrDate.currentTime() - startTime
			local avg = 0
			if processed and processed > 0 then avg = duration / processed end

			table.insert(results, {
				senders = config.senders,
				analyzers = config.analyzers,
				batch = config.batch,
				duration = duration,
				processed = processed or 0,
				failed = failed or 0,
				avg = avg
			})

			log:info(string.format("BENCHMARK RESULT: Senders=%d, Analyzers=%d, Batch=%d | Duration: %.2f sec (%.2f s/photo) | Failed: %d", 
					 config.senders, config.analyzers, config.batch, duration, avg, failed or 0))

			-- Wait 5 seconds to let the system cool down and the DB flush between runs
			if i < #permutations and not progressScope:isCanceled() then
				progressScope:setCaption(string.format("Cooling down (5s) before Test %d...", i + 1))
				LrTasks.sleep(5)
			end
		end

		progressScope:done()

		-- Output summary to log
		log:info("--- BENCHMARK RESULTS SUMMARY ---")
		table.sort(results, function(a, b) return a.duration < b.duration end)
		
		local summary = "Benchmark Complete.\nSee StyleAI.log for full details.\n\nTop Results:\n"
		for i, r in ipairs(results) do
			local line = string.format("#%d: S:%d A:%d B:%d -> %.1f sec (%.2f s/photo)", i, r.senders, r.analyzers, r.batch, r.duration, r.avg)
			log:info(line)
			if i <= 5 then
				summary = summary .. line .. "\n"
			end
		end
		log:info("--- END BENCHMARK ---")

		LrDialogs.message("Benchmark Complete", summary, "info")
	end)
end)
