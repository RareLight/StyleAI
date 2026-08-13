-- TaskBenchmark.lua
-- A developer utility task to empirically test pipeline permutations for GPU saturation.

local TaskBenchmark = {}

local LrTasks = import("LrTasks")
local LrDialogs = import("LrDialogs")
local LrFunctionContext = import("LrFunctionContext")
local LrApplication = import("LrApplication")
local LrDate = import("LrDate")

require("Util")
local SearchIndexAPI = require("APISearchIndex")
local DeveloperOptions = require("DeveloperOptions")

function TaskBenchmark.run()
	LrTasks.startAsyncTask(function()
		LrFunctionContext.callWithContext("benchmarkTask", function(ctx)
		if not DeveloperOptions.requireEnabled() then return end
		local catalog = LrApplication.activeCatalog()
		local selectedPhotos = catalog:getTargetPhotos()
		
		local permutations = {
			{senders = 4, analyzers = 32, batch = 32},
			{senders = 4, analyzers = 32, batch = 64},
			{senders = 8, analyzers = 64, batch = 32},
			{senders = 8, analyzers = 64, batch = 64},
		}

		local minRequired = 256
		
		if not selectedPhotos or #selectedPhotos < minRequired then
			LrDialogs.message(
				"Benchmark Error",
				"Please select a representative batch of at least " .. minRequired .. " photos (ideally more) to run the benchmark.\n\nRunning it on the exact same set of photos ensures the backend gets fully saturated on later runs once Lightroom's thumbnail previews are cached.",
				"critical"
			)
			return
		end
		
		local confirm = LrDialogs.confirm(
			"Run Performance Benchmark?",
			"This will test " .. #permutations .. " permutations of worker threads and batch sizes on your selected " .. #selectedPhotos .. " photos. This will take a while and will force full processing.\n\nOpen StyleAI.log after completion to view results.",
			"Run Benchmark",
			"Cancel"
		)
		if confirm == "cancel" then return end
		
		if not Util.waitForServerDialog({ suppressProgressDialog = false }) then
			return
		end
		


		local results = {}
		local progressScope = LrDialogs.showModalProgressDialog({
			title = LOC("$$$/StyleAI/TaskBenchmark/Running=Running Performance Benchmark..."),
			functionContext = ctx
		})

		log:info("--- STARTING PERFORMANCE BENCHMARK ---")
		log:info("Testing " .. #selectedPhotos .. " photos across " .. #permutations .. " permutations.")

		if not progressScope:isCanceled() then
			log:info("Starting Run 0 (Warmup Pass)")
			progressScope:setCaption("Run 0: Warmup Pass (Generating Thumbnails...)")
			local warmupConfig = {senders = 4, analyzers = 32, batch = 32}
			local warmupOptions = {
				tasks = {"embeddings"},
				indexingMode = "embed",
				enableEmbeddings = true,
				enableMetadata = false,
				regenerate_metadata = true,
				cache_images = false,
				benchmarkConfig = warmupConfig,
				forceRecompute = true
			}
			SearchIndexAPI.analyzeAndIndexSelectedPhotos(selectedPhotos, progressScope, warmupOptions, false)
			log:info("Finished Run 0 (Warmup Pass)")
			
			if not progressScope:isCanceled() then
				LrTasks.sleep(5)
			end
		end

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
			
			local testPhotos = selectedPhotos

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
end

return TaskBenchmark
