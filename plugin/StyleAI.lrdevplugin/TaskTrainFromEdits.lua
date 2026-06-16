---
-- @module TaskTrainFromEdits
-- @description Core component of the Advanced Style Detection pipeline.
-- Allows the user to save their current Lightroom develop settings for selected
-- photos as AI style training examples. These are stored on the backend where 
-- SigLIP2 generates a dense visual embedding and an LLM extracts a semantic 
-- caption of the lighting and composition. 
--
-- This data is later injected as few-shot context during the "AI Edit Photos" 
-- flow, allowing the Style Engine to interpolate a highly personalized edit 
-- recipe rather than a generic auto-edit.
---

require("DevelopEditManager")

local function showTrainDialog(ctx)
	local f = LrView.osFactory()
	local bind = LrView.bind
	local props = LrBinding.makePropertyTable(ctx)

	props.label = prefs.trainingLabel or ""
	props.summary = prefs.trainingSummary or ""
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
		f:group_box({
			title = LOC("$$$/StyleAI/Training/StyleGroup=Edit Style"),
			fill_horizontal = 1,
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/Training/LabelLabel=Style label (optional):"),
					width = 180,
				}),
				f:edit_field({
					value = bind("label"),
					width_in_chars = 30,
					placeholder_string = LOC("$$$/StyleAI/Training/LabelPlaceholder=e.g. Wedding, Portrait, Street"),
				}),
			}),
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/Training/SummaryLabel=Description (optional):"),
					width = 180,
				}),
				f:edit_field({
					value = bind("summary"),
					width_in_chars = 30,
					height_in_lines = 2,
				}),
			}),
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/Training/KeywordsLabel=Keywords (optional):"),
					width = 180,
				}),
				f:edit_field({
					value = bind("userKeywords"),
					width_in_chars = 30,
					placeholder_string = LOC("$$$/StyleAI/Training/KeywordsPlaceholder=e.g. macro, nature, golden hour"),
				}),
			}),
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/Training/KeywordsHint=These keywords help group your style. Common: portrait, landscape, macro, street, architecture, wildlife"),
					size = "small",
					font = "italic",
				}),
			}),
		}),
		f:row({
			f:static_text({
				title = LOC(
					"$$$/StyleAI/Training/DialogHint=Hint: Only select photos that you have manually edited. The AI will learn your style from these examples."
				),
				font = "italic",
			}),
		}),
		f:row({
			f:checkbox({
				title = LOC("$$$/StyleAI/Training/ForceRetrain=Re-train already trained photos (overwrite existing data)"),
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
						props.label = ""
						props.summary = ""
						props.userKeywords = ""
						props.forceRetrain = false
					end
				end,
			}),
		}),
	})

	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/Training/DialogTitle=Save Edits as AI Training Examples"),
		contents = contents,
		actionVerb = LOC("$$$/StyleAI/Training/SaveButton=Save Examples"),
	})

	if result ~= "ok" then
		return nil
	end

	prefs.trainingLabel = props.label
	prefs.trainingSummary = props.summary
	prefs.trainingScope = props.scope
	prefs.trainingKeywords = props.userKeywords

	return {
		label = props.label,
		summary = props.summary,
		scope = props.scope,
		userKeywords = props.userKeywords,
		forceRetrain = props.forceRetrain,
	}
end

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("TrainFromEditsTask", function(ctx)
		LrDialogs.attachErrorDialogToFunctionContext(ctx)
		log:info("Save Training Examples task started")

		if not Util.waitForServerDialog() then
			log:warn("Train task aborted: backend server unavailable")
			return
		end

		local options = showTrainDialog(ctx)
		if not options then
			log:info("Train task cancelled by user")
			return
		end

		local photosToProcess = PhotoSelector.getPhotosInScope(options.scope)
		if not photosToProcess or #photosToProcess == 0 then
			LrDialogs.message(
				LOC("$$$/StyleAI/Training/NoPhotosTitle=No Photos"),
				LOC("$$$/StyleAI/Training/NoPhotosMsg=No photos found in the selected scope."),
				"info"
			)
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

		-- Fetch styles before training to detect tier upgrades later
		local _, preStyles = SearchIndexAPI.listStyles()
		local preStyleCounts = {}
		if preStyles then
			for _, s in ipairs(preStyles) do
				preStyleCounts[s.style_id] = tonumber(s.example_count) or 0
			end
		end

		-- Collect and send examples in chunks (reduces RAM usage by holding fewer base64 strings).
		local successCount = 0
		local errorCount = 0
		local errorMessages = {}
		local backendWarnings = {}

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
						progressScope:setCaption(LOC("$$$/StyleAI/Training/SendingBatch=Sending batch to StyleAI server..."))
						local ok, resp = SearchIndexAPI.addTrainingBatch(currentChunk, options.forceRetrain)

						if ok and resp and resp.results then
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
						else
							for _, chunkEx in ipairs(currentChunk) do
								errorCount = errorCount + 1
								table.insert(errorMessages, chunkEx.photo_id .. ": " .. tostring(resp or "API request failed"))
							end
						end
						log:info("Batch training chunk saved. successCount=" .. tostring(successCount))
						currentChunk = {}
					end
				elseif producerDone then
					-- Flush any remaining items in the current chunk
					if #currentChunk > 0 then
						progressScope:setCaption(LOC("$$$/StyleAI/Training/SendingBatch=Sending batch to StyleAI server..."))
						local ok, resp = SearchIndexAPI.addTrainingBatch(currentChunk, options.forceRetrain)
						if ok and resp and resp.results then
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
						else
							for _, chunkEx in ipairs(currentChunk) do
								errorCount = errorCount + 1
								table.insert(errorMessages, chunkEx.photo_id .. ": " .. tostring(resp or "API request failed"))
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

		-- Start the consumer in the background
		LrTasks.startAsyncTask(consumerWorker)

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
			local okGet, devOrErr = LrTasks.pcall(function()
				return photo:getDevelopSettings()
			end)
			if okGet and type(devOrErr) == "table" then
				developSettings = devOrErr
			else
				log:warn("Could not read develop settings for " .. fileName .. ": " .. tostring(devOrErr))
				developSettings = {}
			end

			-- Get a stable photo ID.
			local photoId, photoIdErr = SearchIndexAPI.getPhotoIdForPhoto(photo)
			if not photoId then
				log:error("Failed to resolve photo ID for " .. fileName .. ": " .. tostring(photoIdErr))
				errorCount = errorCount + 1
				table.insert(errorMessages, fileName .. ": " .. tostring(photoIdErr))
			else
				-- Collect EXIF metadata for richer style matching using standardized utility.
				local exifOptions = Util.getPhotoExif(photo)

				local actualProfile = nil
				if developSettings then
					if type(developSettings.Look) == "table" and developSettings.Look.Name then
						actualProfile = developSettings.Look.Name
					else
						actualProfile = developSettings.CameraProfile
					end
				end

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
					camera_profile = actualProfile,
					iso = exifOptions.iso,
					aperture = exifOptions.aperture,
					shutter_speed = exifOptions.shutter_speed,
					image_bytes = imageBytes,
				}
				if options.userKeywords and options.userKeywords ~= "" then
					example.user_keywords = options.userKeywords
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
		-- Add slight delay to ensure consumerWorker cleanly exits
		if not progressScope:isCanceled() then
			LrTasks.yield()
			LrTasks.sleep(0.2)
		end

		progressScope:done()

		-- Summary dialog.
		if errorCount > 0 or #backendWarnings > 0 then
			local uniqueErrors = {}
			local errorList = {}
			for _, msg in ipairs(errorMessages) do
				if not uniqueErrors[msg] then
					uniqueErrors[msg] = true
					table.insert(errorList, "- " .. msg)
					if #errorList >= 5 then
						break
					end
				end
			end

			local combinedReport =
				LOC("$$$/StyleAI/Training/Summary=Saved ^1 training example(s).", tostring(successCount))
			if errorCount > 0 then
				combinedReport = combinedReport
					.. "\n"
					.. LOC("$$$/StyleAI/common/Errors=Errors: ^1", tostring(errorCount))
			end

			if #errorList > 0 then
				combinedReport = combinedReport
					.. "\n\n"
					.. LOC("$$$/StyleAI/common/ErrorDetails=Error details:")
					.. "\n"
					.. table.concat(errorList, "\n")
				if #errorMessages > 5 then
					combinedReport = combinedReport
						.. "\n"
						.. LOC("$$$/StyleAI/common/MoreErrors=... and ^1 more errors", tostring(#errorMessages - 5))
				end
			end

			if #backendWarnings > 0 then
				combinedReport = combinedReport
					.. "\n\n"
					.. LOC("$$$/StyleAI/common/BackendWarnings=Backend Warnings:")
					.. "\n"
				for i = 1, math.min(5, #backendWarnings) do
					combinedReport = combinedReport .. "- " .. backendWarnings[i] .. "\n"
				end
				if #backendWarnings > 5 then
					combinedReport = combinedReport
						.. LOC(
							"$$$/StyleAI/common/MoreWarnings=... and ^1 more warnings",
							tostring(#backendWarnings - 5)
						)
				end
			end

			local recommendationMsg = ""
			local upgradeMsg = ""
			if successCount > 0 then
				local ok, styles = SearchIndexAPI.listStyles()
				if ok and styles and #styles > 0 then
					-- Detect tier upgrades
					local upgradedMLDirect = {}
					local upgradedMLPCA = {}
					for _, s in ipairs(styles) do
						local currentCount = tonumber(s.example_count) or 0
						local preCount = preStyleCounts[s.style_id] or 0
						local name = s.style_name or s.genre or "Unknown"
						
						if preCount < 50 and currentCount >= 50 then
							table.insert(upgradedMLDirect, name)
						elseif preCount < 20 and currentCount >= 20 and preCount < 50 and currentCount < 50 then
							table.insert(upgradedMLPCA, name)
						end
					end

					if #upgradedMLDirect > 0 then
						upgradeMsg = upgradeMsg .. "\n\n" .. LOC("$$$/StyleAI/Training/UpgradeMLDirect=🎉 '^1' reached 50 examples! Upgraded to ⭐️ ML Predictive (Best).", table.concat(upgradedMLDirect, ", "))
					end
					if #upgradedMLPCA > 0 then
						upgradeMsg = upgradeMsg .. "\n\n" .. LOC("$$$/StyleAI/Training/UpgradeMLPCA=🎉 '^1' reached 20 examples! Upgraded to 🌟 ML Predictive (Good).", table.concat(upgradedMLPCA, ", "))
					end

					-- Sort styles by example count for the weakest link recommendation
					table.sort(styles, function(a, b) return (tonumber(a.example_count) or 0) < (tonumber(b.example_count) or 0) end)
					local weakest = styles[1]
					local weakestCount = tonumber(weakest.example_count) or 0
					local name = weakest.style_name or weakest.genre or "one of your styles"
					if weakestCount < 5 then
						recommendationMsg = "\n\n" .. LOC("$$$/StyleAI/Training/RecommendMore=Tip: Your '^1' style only has ^2 examples (🔴 Undertrained). For the best AI edit results, try to provide at least 5-10 examples for this style.", name, tostring(weakestCount))
					elseif weakestCount < 10 then
						recommendationMsg = "\n\n" .. LOC("$$$/StyleAI/Training/RecommendGood=Tip: Your '^1' style has ^2 examples (🟡 Good). Adding a few more examples will make it even stronger.", name, tostring(weakestCount))
					elseif weakestCount < 20 then
						recommendationMsg = "\n\n" .. LOC("$$$/StyleAI/Training/RecommendStrong=Tip: Your styles look 🟢 Strong! The AI has a robust understanding of your editing preferences.")
					elseif weakestCount < 50 then
						recommendationMsg = "\n\n" .. LOC("$$$/StyleAI/Training/RecommendMLPCA=Tip: Your styles look 🌟 ML Predictive (Good)! The AI has trained a personalized local model for your edits.")
					else
						recommendationMsg = "\n\n" .. LOC("$$$/StyleAI/Training/RecommendMLBest=Tip: Your styles look ⭐️ ML Predictive (Best)! The AI has trained a highly robust predictive model for your edits.")
					end
				end
			end

			combinedReport = combinedReport .. upgradeMsg .. recommendationMsg


			ErrorHandler.handleError(
				LOC("$$$/StyleAI/Training/CompletionTitle=Training Examples Saved"),
				combinedReport
			)
		else
			LrDialogs.message(
				LOC("$$$/StyleAI/Training/SuccessTitle=Training Examples Saved"),
				LOC(
					"$$$/StyleAI/Training/SuccessSummary=Successfully saved ^1 training example(s).\nAI Edit Photos will use your style when editing visually similar photos.",
					tostring(successCount)
				),
				"info"
			)
		end
	end)
end)
