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

		-- Collect all examples first (no network calls yet).
		local examples = {}
		local collectErrors = {}

		for index, photo in ipairs(photos) do
			if progressScope:isCanceled() then
				break
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
				table.insert(collectErrors, fileName .. ": " .. tostring(photoIdErr))
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

				table.insert(examples, example)
			end

			progressScope:setPortionComplete(index, #photos)
		end

		-- Send all examples in a single batch request.
		local successCount = 0
		local errorCount = #collectErrors
		local errorMessages = {}
		for _, msg in ipairs(collectErrors) do
			table.insert(errorMessages, msg)
		end
		local backendWarnings = {}

		if #examples > 0 then
			local chunkSize = 50
			for i = 1, #examples, chunkSize do
				local chunk = {}
				local endIndex = math.min(i + chunkSize - 1, #examples)
				for j = i, endIndex do
					table.insert(chunk, examples[j])
				end
				
				progressScope:setCaption(
					LOC("$$$/StyleAI/Training/SendingBatch=Sending batch ^1 to ^2 of ^3 to StyleAI server...", tostring(i), tostring(endIndex), tostring(#examples))
				)

				local ok, resp = SearchIndexAPI.addTrainingBatch(chunk, options.forceRetrain)

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
					for _, ex in ipairs(chunk) do
						errorCount = errorCount + 1
						table.insert(errorMessages, ex.photo_id .. ": " .. tostring(resp or "API request failed"))
					end
				end
				
				log:info("Batch training chunk saved. successCount=" .. tostring(successCount))
			end
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
