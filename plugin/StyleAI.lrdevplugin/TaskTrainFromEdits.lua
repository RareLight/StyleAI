---
-- @module TaskTrainFromEdits
-- @description Core component of the Advanced Style Detection pipeline.
-- Allows the user to save their current Lightroom develop settings for selected
-- photos as editing-policy training examples. SigLIP2 source embeddings and
-- neutral-preview metrics condition absolute Lightroom target prediction;
-- user-authored keywords remain explanatory, open-vocabulary evidence.
---

require("DevelopEditManager")
local WorkCoordinator = require("WorkCoordinator")

local function showTrainDialog(ctx)
	local f = LrView.osFactory()
	local bind = LrView.bind
	local props = LrBinding.makePropertyTable(ctx)

	props.scope = prefs.trainingScope or "selected"
	props.forceRetrain = false

	local contents = f:column({
		bind_to_object = props,
		spacing = f:control_spacing(),
		f:group_box({
			title = LOC("$$$/StyleAI/AnalyzeAndIndex/Scope=Scope"),
			fill_horizontal = 1,
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/AnalyzeAndIndex/Scope=Scope"),
					width = 150,
				}),
				f:popup_menu({
					value = bind("scope"),
					width = 300,
					items = {
						{ title = LOC("$$$/StyleAI/common/ScopeSelected=Selected photos only"), value = "selected" },
						{ title = LOC("$$$/StyleAI/common/ScopeView=Current view"), value = "view" },
						{ title = LOC("$$$/StyleAI/common/ScopeAll=Entire Catalog"), value = "all" },
					},
				}),
			}),
		}),
		f:row({
			f:static_text({
				title = LOC(
					"$$$/StyleAI/Training/DialogHint=StyleAI learns from your edited RAW and DNG photos. Indexed photos can be reused immediately; other photos are analyzed while learning."
				),
				font = "italic",
				wrap = true,
				width_in_chars = 60,
			}),
		}),
		f:spacer({ height = 5 }),
		f:row({
			f:checkbox({
				title = LOC("$$$/StyleAI/Training/ForceRetrain=Re-analyze photos that have already been learned (overwrites existing data)"),
				value = bind("forceRetrain"),
			}),
		}),
		f:row({
			f:push_button({
				title = LOC("$$$/StyleAI/common/ResetAllDefaults=Reset to Defaults"),
				action = function()
					local confirm = LrDialogs.confirm(
						LOC("$$$/StyleAI/common/ResetAllDefaultsConfirmTitle=Reset Settings"),
						LOC("$$$/StyleAI/common/ResetAllDefaultsConfirmMessage=Are you sure you want to reset all options in this dialog to their default values?")
					)
					if confirm == "ok" then
						props.scope = "selected"
						props.forceRetrain = false
					end
				end,
			}),
		}),
	})

	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/Training/DialogTitle=Learn My Editing Style"),
		contents = contents,
		actionVerb = LOC("$$$/StyleAI/Training/SaveButton=Learn My Style"),
	})

	if result ~= "ok" then
		return nil
	end

	prefs.trainingScope = props.scope

	return {
		scope = props.scope,
		forceRetrain = props.forceRetrain,
	}
end

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("TrainFromEditsTask", function(ctx)
		LrDialogs.attachErrorDialogToFunctionContext(ctx)
		log:info("Save Training Examples task started")

		-- Preserve the user's target-photo selection before modal UI changes
		-- Lightroom's live selection/focus state.
		local selectedPhotosSnapshot = PhotoSelector.snapshotSelectedPhotos()

		local options = showTrainDialog(ctx)
		if not options then
			log:info("Train task cancelled by user")
			return
		end

		local photosToProcess = PhotoSelector.getPhotosInScope(options.scope, nil, nil, selectedPhotosSnapshot)
		if not photosToProcess or #photosToProcess == 0 then
			LrDialogs.message(
				LOC("$$$/StyleAI/Training/NoPhotosTitle=No Photos"),
				LOC("$$$/StyleAI/Training/NoPhotosMsg=No photos found in the selected scope."),
				"info"
			)
			return
		end

		-- Now that the user has committed, ensure the backend is running.
		if not Util.waitForServerDialog({ suppressProgressDialog = false }) then
			return
		end

		-- Filter photos: only RAW or DNG formats.
		local photos = {}
		for _, photo in ipairs(photosToProcess) do
			local fmt = photo:getRawMetadata("fileFormat")

			-- Only include RAW or DNG.
			if fmt == "RAW" or fmt == "DNG" then
				table.insert(photos, photo)
			end
		end

		if #photos == 0 then
			LrDialogs.message(
				LOC("$$$/StyleAI/Training/NoValidPhotosTitle=No Valid Training Photos"),
				LOC(
					"$$$/StyleAI/Training/NoValidPhotosMsg=None of the photos in the selected scope match the training criteria (must be RAW or DNG format). JPEGs, TIFFs, and other formats are excluded."
				),
				"info"
			)
			return
		end

		local progressScope = LrProgressScope({
			title = LOC("$$$/StyleAI/Training/Progress=Saving training examples..."),
			functionContext = ctx,
		})
		progressScope:setPortionComplete(0, #photos)

		local photoIdsByPhoto = {}
		local operationItemIds = {}
		for index, photo in ipairs(photos) do
			progressScope:setCaption(
				LOC("$$$/StyleAI/Training/PreparingOperation=Preparing training operation...")
			)
			progressScope:setPortionComplete(index - 1, #photos)
			local photoId = SearchIndexAPI.getPhotoIdForPhoto(photo)
			if photoId then
				photoIdsByPhoto[photo] = photoId
				 table.insert(operationItemIds, photoId)
			end
		end
		if #operationItemIds == 0 then
			progressScope:done()
			ErrorHandler.handleError(
				"Could not start training operation",
				"No stable Lightroom photo IDs could be resolved."
			)
			return
		end
		local operationOk, operation = SearchIndexAPI.startOperation(
			"training",
			operationItemIds,
			{ scope = tostring(options.scope), photo_count = #operationItemIds },
			nil,
			8,
			false
		)
		if not operationOk then
			progressScope:done()
			ErrorHandler.handleError("Could not start training operation", operation)
			return
		end
		local operationId = operation.job_id
		local trainingLease, trainingLeaseError = WorkCoordinator.acquire(
			"backend_training_workflow",
			progressScope
		)
		if not trainingLease then
			SearchIndexAPI.cancelOperation(operationId)
			SearchIndexAPI.completeOperation(operationId)
			progressScope:done()
			if trainingLeaseError ~= "canceled" then
				ErrorHandler.handleError("Could not schedule training", trainingLeaseError)
			end
			return
		end
		ctx:addCleanupHandler(function()
			WorkCoordinator.release(trainingLease)
		end)

		-- Collect and send examples in chunks (reduces RAM usage by holding fewer base64 strings).
		local successCount = 0
		local errorCount = 0
		local errorMessages = {}
		local backendWarnings = {}
		local function markChunkFailed(chunk, message)
			local updates = {}
			for _, chunkEx in ipairs(chunk) do
				table.insert(updates, {
					item_id = chunkEx.photo_id,
					state = "failed",
					error = tostring(message or "Training request failed"),
				})
			end
			SearchIndexAPI.updateOperationItems(operationId, updates)
		end

		local trainingQueue = {}
		local producerDone = false
		local consumerDone = false
		local chunkSize = 10

		local function consumerWorker()
			local currentChunk = {}
			while not progressScope:isCanceled() do
				if #trainingQueue > 0 then
					local ex = table.remove(trainingQueue, 1)
					table.insert(currentChunk, ex)

					if #currentChunk >= chunkSize then
						local retryChunk = true
						while retryChunk and not progressScope:isCanceled() do
							retryChunk = false
							progressScope:setCaption(LOC("$$$/StyleAI/Training/SendingBatch=Sending batch to StyleAI server..."))
							local ok, resp = SearchIndexAPI.addTrainingBatch(currentChunk, options.forceRetrain, false, operationId)
	
							if ok and resp and resp.results then
								local hitTimeout = false
								for _, result in ipairs(resp.results) do
									if result.status ~= "ok" and result.error and string.find(result.error, "EXIFTOOL_") then
										hitTimeout = true
									end
								end
								
								if hitTimeout then
									local LrDialogs = require("LrDialogs")
									local userAction = LrDialogs.promptForActionWithDoNotShow({
										message = LOC("$$$/StyleAI/Training/ExiftoolTimeout=Waiting for file access. Your NAS may be spinning up, or macOS might be prompting for file permissions in the background. Do you want to keep waiting or cancel?"),
										actionPrefKey = "exiftoolTimeoutAction",
										verbBtns = {
											{ label = LOC("$$$/StyleAI/Training/Proceed=Keep Waiting"), verb = "proceed" },
											{ label = LOC("$$$/StyleAI/Training/Cancel=Cancel Training"), verb = "cancel" }
										}
									})
									if userAction == "proceed" then
										retryChunk = true
									else
										progressScope:cancel()
										break
									end
								end
								
								if not retryChunk then
									for _, result in ipairs(resp.results) do
										if result.status == "ok" then
											successCount = successCount + 1
											if result.warning then
												if string.find(result.warning, "Already trained") then
													log:info("Suppressed warning: " .. result.warning)
												else
													table.insert(backendWarnings, result.photo_id .. ": " .. result.warning)
												end
											end
										else
											errorCount = errorCount + 1
											table.insert(errorMessages, result.photo_id .. ": " .. (result.error or "Unknown error"))
										end
									end
								end
							else
								markChunkFailed(currentChunk, resp)
								for _, chunkEx in ipairs(currentChunk) do
									errorCount = errorCount + 1
									table.insert(errorMessages, chunkEx.photo_id .. ": " .. tostring(resp or "API request failed"))
								end
							end
						end
						log:info("Batch training chunk saved. successCount=" .. tostring(successCount))
						currentChunk = {}
					end
				elseif producerDone then
					-- Flush any remaining items in the current chunk
					if #currentChunk > 0 then
						local retryChunk = true
						while retryChunk and not progressScope:isCanceled() do
							retryChunk = false
							progressScope:setCaption(LOC("$$$/StyleAI/Training/SendingBatch=Sending batch to StyleAI server..."))
							local ok, resp = SearchIndexAPI.addTrainingBatch(currentChunk, options.forceRetrain, false, operationId)
							if ok and resp and resp.results then
								local hitTimeout = false
								for _, result in ipairs(resp.results) do
									if result.status ~= "ok" and result.error and string.find(result.error, "EXIFTOOL_") then
										hitTimeout = true
									end
								end
								
								if hitTimeout then
									local LrDialogs = require("LrDialogs")
									local userAction = LrDialogs.promptForActionWithDoNotShow({
										message = LOC("$$$/StyleAI/Training/ExiftoolTimeout=Waiting for file access. Your NAS may be spinning up, or macOS might be prompting for file permissions in the background. Do you want to keep waiting or cancel?"),
										actionPrefKey = "exiftoolTimeoutAction",
										verbBtns = {
											{ label = LOC("$$$/StyleAI/Training/Proceed=Keep Waiting"), verb = "proceed" },
											{ label = LOC("$$$/StyleAI/Training/Cancel=Cancel Training"), verb = "cancel" }
										}
									})
									if userAction == "proceed" then
										retryChunk = true
									else
										progressScope:cancel()
										break
									end
								end
								
								if not retryChunk then
									for _, result in ipairs(resp.results) do
										if result.status == "ok" then
											successCount = successCount + 1
											if result.warning then
												table.insert(backendWarnings, result.photo_id .. ": " .. result.warning)
											end
										else
											errorCount = errorCount + 1
											table.insert(errorMessages, result.photo_id .. ": " .. (result.error or "Unknown error"))
										end
									end
								end
							else
								markChunkFailed(currentChunk, resp)
								for _, chunkEx in ipairs(currentChunk) do
									errorCount = errorCount + 1
									table.insert(errorMessages, chunkEx.photo_id .. ": " .. tostring(resp or "API request failed"))
								end
							end
						end
						log:info("Final training chunk saved. successCount=" .. tostring(successCount))
					end
					consumerDone = true
					break
				else
					-- Wait for producer to add more items
					LrTasks.yield()
					LrTasks.sleep(0.1)
				end
			end
			consumerDone = true
		end

		-- Start the consumer in the background. Always publish terminal worker
		-- state so an unexpected Lua/API error cannot strand the parent dialog.
		LrTasks.startAsyncTask(function()
			local workerOk, workerError = LrTasks.pcall(consumerWorker)
			if not workerOk then
				log:error("Training consumer failed: " .. tostring(workerError))
				errorCount = errorCount + 1
				table.insert(errorMessages, "Training worker failed: " .. tostring(workerError))
				SearchIndexAPI.cancelOperation(operationId)
			end
			consumerDone = true
		end)

		local batchRawMetaMap = {}
		if catalog and catalog.batchGetRawMetadata then
			LrTasks.pcall(function()
				batchRawMetaMap = catalog:batchGetRawMetadata(photos, { "path", "rating", "pickStatus" }) or {}
			end)
		end
		local function getPhotoRawMeta(photo, key)
			if batchRawMetaMap[photo] and batchRawMetaMap[photo][key] ~= nil then
				return batchRawMetaMap[photo][key]
			end
			return photo:getRawMetadata(key)
		end

		for index, photo in ipairs(photos) do
			if progressScope:isCanceled() then
				break
			end

			-- Backpressure: Pause Lightroom extraction if the queue gets too large
			while #trainingQueue >= 20 and not progressScope:isCanceled() do
				LrTasks.yield()
				LrTasks.sleep(0.1)
			end

			local fileName = photo:getFormattedMetadata("fileName") or "Photo"
			progressScope:setCaption(
				string.format(
					LOC("$$$/StyleAI/Training/ProgressCaption=Preparing %s (%d of %d)"),
					fileName,
					index,
					#photos
				)
			)
			progressScope:setPortionComplete(index - 1, #photos)

			-- Read current develop settings.
			local developSettings
			local exifOptions
			local okGet, devOrErr = LrTasks.pcall(function()
				exifOptions = Util.getPhotoExif(photo)
				return photo:getDevelopSettings()
			end)
			if not exifOptions then
				exifOptions = Util.getPhotoExif(photo)
			end
			if okGet and type(devOrErr) == "table" then
				developSettings = devOrErr
			else
				log:warn("Could not read develop settings for " .. fileName .. ": " .. tostring(devOrErr))
				developSettings = {}
			end

			-- Get a stable photo ID.
			local photoId = photoIdsByPhoto[photo]
			local photoIdErr = photoId and nil or "Stable photo ID could not be resolved"
			if not photoId then
				log:error("Failed to resolve photo ID for " .. fileName .. ": " .. tostring(photoIdErr))
				errorCount = errorCount + 1
				table.insert(errorMessages, fileName .. ": " .. tostring(photoIdErr))
			else
				-- Get JPEG preview for the backend to compute exposure metrics.
				local jpegData, jpegErr = SearchIndexAPI.getJpegThumbnailForPhoto(photo, 1024, 1024)
				local imageBytes = nil
				if jpegData then
					imageBytes = LrStringUtils.encodeBase64(jpegData)
				else
					log:warn("Could not get thumbnail for " .. fileName .. ": " .. tostring(jpegErr))
				end

				local example = {
					photo_id = photoId,
					develop_settings = developSettings or {},
					label = options.label,
					summary = options.summary,
					focal_length = exifOptions.focal_length,
					capture_time = exifOptions.capture_time,
					camera_make = exifOptions.camera_make,
					camera_model = exifOptions.camera_model,
					camera_profile = exifOptions.camera_profile,
					iso = exifOptions.iso,
					aperture = exifOptions.aperture,
					shutter_speed = exifOptions.shutter_speed,
					lens = exifOptions.lens,
					image_bytes = imageBytes,
					filepath = getPhotoRawMeta(photo, "path"),
					rating = tonumber(getPhotoRawMeta(photo, "rating")) or 0,
					pick_status = tonumber(getPhotoRawMeta(photo, "pickStatus")) or 0,
				}
				local keywordText = photo:getFormattedMetadata("keywordTagsForExport")
				if type(keywordText) == "string" and keywordText ~= "" then
					example.user_keywords = Util.string_split(keywordText, ",")
				elseif type(keywordText) == "table" then
					example.user_keywords = keywordText
				end

				table.insert(trainingQueue, example)
			end

			progressScope:setPortionComplete(index, #photos)
		end

		producerDone = true

		-- Wait for consumer to finish sending remaining chunks
		while not consumerDone and not progressScope:isCanceled() do
			LrTasks.yield()
			LrTasks.sleep(0.1)
		end
		if progressScope:isCanceled() then
			SearchIndexAPI.cancelOperation(operationId)
		end
		SearchIndexAPI.completeOperation(operationId)
		if not progressScope:isCanceled() and successCount > 0 then
			progressScope:setCaption(
				LOC("$$$/StyleAI/Training/BuildingPolicies=Building editing policies from all saved examples...")
			)
			local rebuilt, rebuildResult = SearchIndexAPI.discoverStyles()
			if not rebuilt then
				table.insert(
					backendWarnings,
					LOC(
						"$$$/StyleAI/Training/PolicyBuildFailed=Training examples were saved, but editing-policy generation failed: ^1",
						tostring(rebuildResult or "Unknown error")
					)
				)
			end
		end
		WorkCoordinator.release(trainingLease)

		progressScope:done()

		-- Summary dialog.
		-- 1. Deduplicate errors and warnings to prevent massive string overflow
		local uniqueErrors = {}
		local errorList = {}
		local errorListCount = 0
		for _, msg in ipairs(errorMessages) do
			if not uniqueErrors[msg] then
				uniqueErrors[msg] = 1
				if errorListCount < 3 then
					table.insert(errorList, "- " .. msg)
					errorListCount = errorListCount + 1
				end
			else
				uniqueErrors[msg] = uniqueErrors[msg] + 1
			end
		end

		local uniqueWarnings = {}
		local warningList = {}
		local warningListCount = 0
		for _, msg in ipairs(backendWarnings) do
			if not uniqueWarnings[msg] then
				uniqueWarnings[msg] = 1
				if warningListCount < 2 then
					table.insert(warningList, "- " .. msg)
					warningListCount = warningListCount + 1
				end
			else
				uniqueWarnings[msg] = uniqueWarnings[msg] + 1
			end
		end

		-- 2. Build upgrade and recommendation messages if we had any successes
		local recommendationMsg = ""
		if successCount > 0 then
			local ok, styles = SearchIndexAPI.listStyles()
			if ok and styles and #styles > 0 then
				recommendationMsg = "\n\n" .. LOC(
					"$$$/StyleAI/Training/PolicyComplete=Editing-policy training completed. Open Style Upgrade Assistant to review high-confidence examples that improve policy coverage."
				)
			end
		end

		-- 3. Construct the final report
		local combinedReport = LOC("$$$/StyleAI/Training/Summary=Saved ^1 training example(s).", tostring(successCount))
		
		if errorCount > 0 then
			combinedReport = combinedReport .. "\n" .. LOC("$$$/StyleAI/common/Errors=Errors: ^1", tostring(errorCount))
			combinedReport = combinedReport .. "\n" .. table.concat(errorList, "\n")
			if errorCount > 3 then
				combinedReport = combinedReport .. "\n" .. LOC("$$$/StyleAI/common/MoreErrors=... and ^1 more errors", tostring(errorCount - 3))
			end
		end

		if #backendWarnings > 0 then
			combinedReport = combinedReport .. "\n\n" .. LOC("$$$/StyleAI/common/BackendWarnings=Warnings: ^1", tostring(#backendWarnings))
			combinedReport = combinedReport .. "\n" .. table.concat(warningList, "\n")
			if #backendWarnings > 2 then
				combinedReport = combinedReport .. "\n" .. LOC("$$$/StyleAI/common/MoreWarnings=... and ^1 more warnings", tostring(#backendWarnings - 2))
			end
		end

		combinedReport = combinedReport .. recommendationMsg

		-- 4. Present the appropriate dialog
		if errorCount > 0 then
			-- Actual failures warrant the ErrorHandler UI
			ErrorHandler.handleError(
				LOC("$$$/StyleAI/Training/CompletionTitle=Training Finished (with errors)"),
				combinedReport
			)
		else
			-- Success (even if there are warnings, use the standard LrDialogs.message)
			LrDialogs.message(
				LOC("$$$/StyleAI/Training/SuccessTitle=Training Examples Saved"),
				combinedReport,
				#backendWarnings > 0 and "warning" or "info"
			)
		end
	end)
end)
