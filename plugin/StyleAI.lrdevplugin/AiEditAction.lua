---
-- @module TaskAiEditPhotos
-- @description Executes source-conditioned learned editing policies for selected photos.
---

require("DevelopEditManager")
local WorkCoordinator = require("WorkCoordinator")
local UIFactory = require("UIFactory")

local AiEditAction = {}

local PENDING_APPLICATION_EVENT_KEY = "pendingAiEditApplicationEvent"

local function readPendingApplicationEvent(photo)
	local raw = photo:getPropertyForPlugin(_PLUGIN, PENDING_APPLICATION_EVENT_KEY)
	if type(raw) ~= "string" or raw == "" then return nil end
	local ok, decoded = LrTasks.pcall(function()
		return JSON:decode(raw)
	end)
	if not ok or type(decoded) ~= "table" or type(decoded.event) ~= "table" then
		return false, "Stored AI edit application receipt is invalid"
	end
	return decoded
end

local function writePendingApplicationEvent(photo, payload, progressScope)
	local lease, leaseError = WorkCoordinator.acquire("catalog_write", progressScope)
	if not lease then return false, leaseError end
	local ok, result = LrTasks.pcall(function()
		local encoded = JSON:encode(payload)
		LrApplication.activeCatalog():withPrivateWriteAccessDo(function()
			photo:setPropertyForPlugin(_PLUGIN, PENDING_APPLICATION_EVENT_KEY, encoded)
		end, Defaults.catalogWriteAccessOptions)
	end)
	WorkCoordinator.release(lease)
	if not ok then return false, result end
	return true
end

local function clearPendingApplicationEvent(photo, progressScope)
	local lease, leaseError = WorkCoordinator.acquire("catalog_write", progressScope)
	if not lease then return false, leaseError end
	local ok, result = LrTasks.pcall(function()
		LrApplication.activeCatalog():withPrivateWriteAccessDo(function()
			photo:setPropertyForPlugin(_PLUGIN, PENDING_APPLICATION_EVENT_KEY, "")
		end, Defaults.catalogWriteAccessOptions)
	end)
	WorkCoordinator.release(lease)
	if not ok then return false, result end
	return true
end

local function copyOptions(source)
	local copied = {}
	for key, value in pairs(source or {}) do
		copied[key] = value
	end
	return copied
end

local function getAiEditOptions(ctx, selectedPhotosSnapshot)
	log:trace("getAiEditOptions: start")
	local f = LrView.osFactory()
	local bind = LrView.bind
	local share = LrView.share
	local props = LrBinding.makePropertyTable(ctx)

	props.scope = prefs.aiEditScope or "selected"
	props.selectedCount = #(selectedPhotosSnapshot or {})
	local function getValidStyleStrength(val)
		if type(val) ~= "number" then return Defaults.defaultEditStyleStrength or 0.75 end
		for _, item in ipairs(Defaults.editStyleStrengths or {}) do
			if math.abs(item.value - val) < 0.01 then
				return item.value
			end
		end
		return Defaults.defaultEditStyleStrength or 0.75
	end
	props.styleStrength = getValidStyleStrength(prefs.aiEditStyleStrength)
	props.createVirtualCopies = prefs.aiEditCreateVirtualCopies ~= false
	props.reviewBeforeApply = prefs.aiEditReviewBeforeApply ~= false
	props.profileMode = prefs.aiEditProfileMode or "suggest"
	props.hdrMode = prefs.aiEditHdrMode or "suggest"
	props.applyMasks = prefs.aiEditApplyMasks ~= false
	props.allowAutoCrop = prefs.aiEditAllowAutoCrop == true
	props.allowAutoRotate = prefs.aiEditAllowAutoRotate == true

	local function createPredictiveContent()
		return f:column({
			bind_to_object = props,
			spacing = f:control_spacing(),
			UIFactory.SettingsGroup(f, {
				title = LOC("$$$/StyleAI/common/Scope=Photos"),
				UIFactory.FormRow(f, {
					label = LOC("$$$/StyleAI/common/ApplyTo=Apply to:"),
					labelWidth = share("applyLabelWidth"),
					f:popup_menu({
						value = bind("scope"),
						fill_horizontal = 1,
						items = {
							{ title = LOC("$$$/StyleAI/common/ScopeSelected=Selected photos only"), value = "selected" },
							{ title = LOC("$$$/StyleAI/common/ScopeView=Current view"), value = "view" },
							{ title = LOC("$$$/StyleAI/common/ScopeAll=All photos in catalog"), value = "all" },
						},
					}),
				}),
				UIFactory.HelpText(f, {
					title = bind({
						key = "scope",
						transform = function(scope)
							if scope == "selected" then
								return LOC("$$$/StyleAI/TaskAiEditPhotos/SelectedCount=^1 selected photo(s) will be considered.", tostring(props.selectedCount))
							end
							return LOC("$$$/StyleAI/TaskAiEditPhotos/ScopeCountPending=StyleAI will resolve the chosen scope after you continue.")
						end,
					}),
				}),
			}),
			f:group_box({
				title = LOC("$$$/StyleAI/TaskAiEditPhotos/Workflow=Style and safety"),
				fill_horizontal = 1,
				f:row({
					f:static_text({
						title = LOC("$$$/StyleAI/TaskAiEditPhotos/TrainedStyleStrength=Style strength:"),
						width = share("labelWidth"),
					}),
					f:popup_menu({
						value = bind("styleStrength"),
						items = Defaults.editStyleStrengths,
						width = 200,
					}),
				}),
				UIFactory.HelpText(f, {
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/RenderingHelp=Suggest reports a compatible recommendation for review. Auto applies it only when the learned policy passes its evidence and compatibility gates."),
				}),
				f:row({
					f:static_text({
						title = LOC("$$$/StyleAI/TaskAiEditPhotos/ProfileSelection=Camera profile:"),
						width = share("labelWidth"),
					}),
					f:popup_menu({
						value = bind("profileMode"),
						items = {
							{ title = LOC("$$$/StyleAI/RenderingMode/Off=Off"), value = "off" },
							{ title = LOC("$$$/StyleAI/RenderingMode/Suggest=Suggest"), value = "suggest" },
							{ title = LOC("$$$/StyleAI/RenderingMode/Auto=Auto"), value = "auto" },
						},
						width = 200,
					}),
				}),
				f:row({
					f:static_text({
						title = LOC("$$$/StyleAI/TaskAiEditPhotos/HdrSelection=HDR editing mode:"),
						width = share("labelWidth"),
					}),
					f:popup_menu({
						value = bind("hdrMode"),
						items = {
							{ title = LOC("$$$/StyleAI/RenderingMode/Off=Off"), value = "off" },
							{ title = LOC("$$$/StyleAI/RenderingMode/Suggest=Suggest"), value = "suggest" },
							{ title = LOC("$$$/StyleAI/RenderingMode/Auto=Auto"), value = "auto" },
						},
						width = 200,
					}),
				}),
				f:row({
					f:checkbox({
						title = LOC("$$$/StyleAI/TaskAiEditPhotos/AllowAutoCrop=Allow AI to crop"),
						value = bind("allowAutoCrop"),
					}),
					f:checkbox({
						title = LOC("$$$/StyleAI/TaskAiEditPhotos/AllowAutoRotate=Allow AI to straighten/rotate"),
						value = bind("allowAutoRotate"),
					}),
				}),
			}),
			UIFactory.SettingsGroup(f, {
				title = LOC("$$$/StyleAI/TaskAiEditPhotos/Safety=Application Safety"),
				f:checkbox({
					value = bind("createVirtualCopies"),
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/CreateVirtualCopies=Create virtual copies before applying edits"),
				}),
				f:checkbox({
					value = bind("reviewBeforeApply"),
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/ReviewProposed=Review each proposed edit before applying it"),
				}),
				f:checkbox({
					value = bind("applyMasks"),
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/ApplyMasks=Apply learned masks when the recipe contains supported masks"),
				}),
				UIFactory.Summary(f, {
					title = LOC("$$$/StyleAI/UI/Summary=Safety Summary"),
					text = bind({
						keys = { "createVirtualCopies", "reviewBeforeApply", "applyMasks", "allowAutoCrop", "allowAutoRotate" },
						transform = function()
							local target = props.createVirtualCopies
								and LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryCopies=apply to new virtual copies")
								or LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryOriginals=apply to the selected photos")
						local review = props.reviewBeforeApply
							and LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryReview=review each edit")
							or LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryAutomatic=apply without per-photo review")
						local masks = props.applyMasks
							and LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryMasks=apply supported recipe masks")
							or LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryNoMasks=do not apply masks")
						local crop = props.allowAutoCrop
							and LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryCrop=crop allowed")
							or LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryNoCrop=no crop")
						local rotation = props.allowAutoRotate
							and LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryRotate=straighten/rotation allowed")
							or LOC("$$$/StyleAI/TaskAiEditPhotos/SummaryNoRotate=no straighten/rotation")
						return target .. " — " .. review .. " — " .. masks .. " — " .. crop .. " — " .. rotation
						end,
					}),
				}),
			}),
		})
	end

	local contents = f:column({
		bind_to_object = props,
		spacing = f:control_spacing(),
		createPredictiveContent(),
	})

	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/TaskAiEditPhotos/DialogTitleML=Apply My Style"),
		contents = contents,
		actionVerb = LOC("$$$/StyleAI/TaskAiEditPhotos/GenerateEdits=Apply My Style"),
		resizable = true,
	})
	log:trace("getAiEditOptions: dialog result=" .. tostring(result))

	if result ~= "ok" then
		return nil
	end

	prefs.aiEditScope = props.scope
	prefs.aiEditStyleStrength = props.styleStrength
	prefs.aiEditCreateVirtualCopies = props.createVirtualCopies
	prefs.aiEditReviewBeforeApply = props.reviewBeforeApply
	prefs.aiEditProfileMode = props.profileMode
	prefs.aiEditHdrMode = props.hdrMode
	prefs.aiEditApplyMasks = props.applyMasks
	prefs.aiEditAllowAutoCrop = props.allowAutoCrop
	prefs.aiEditAllowAutoRotate = props.allowAutoRotate

	local options = {
		scope = props.scope,
		style_strength = props.styleStrength,
		applyMasks = props.applyMasks,
		reviewBeforeApply = props.reviewBeforeApply,
		profile_mode = props.profileMode,
		hdr_mode = props.hdrMode,
		createVirtualCopies = props.createVirtualCopies,
		allow_auto_crop = props.allowAutoCrop,
		allow_auto_rotate = props.allowAutoRotate,
	}

	return options
end

local function enrichPhotoOptions(photo, baseOptions)
	log:trace("enrichPhotoOptions: start for " .. tostring(photo and photo:getFormattedMetadata("fileName") or "nil"))
	local photoOptions = copyOptions(baseOptions)
	local datetime = photo:getRawMetadata("dateTime")
	if datetime ~= nil and type(datetime) == "number" then
		photoOptions.date_time = LrDate.timeToW3CDate(datetime)
		photoOptions.capture_time = datetime -- Unix timestamp for style engine
	end

	-- Add EXIF fields for style engine matching using standardized utility.
	local exif = Util.getPhotoExif(photo)
	for k, v in pairs(exif) do
		photoOptions[k] = v
	end
	photoOptions.raw_filepath = photo:getRawMetadata("path")
	return photoOptions
end

function AiEditAction.run()
	LrTasks.startAsyncTask(function()
		LrFunctionContext.callWithContext("AiEditPhotosTask", function(ctx)
		LrDialogs.attachErrorDialogToFunctionContext(ctx)
		log:info("AI Edit task started")
		-- Preserve the user's target-photo selection before modal UI and
		-- backend readiness checks can change Lightroom's live target set.
		local selectedPhotosSnapshot = PhotoSelector.snapshotSelectedPhotos()

		local options = getAiEditOptions(ctx, selectedPhotosSnapshot)
		if not options then
			log:info("AI Edit task canceled by user in options dialog")
			return
		end

		-- Now that user confirmed options, verify backend and training stats
		if not Util.waitForServerDialog({ requireClip = true }) then
			log:warn("AI Edit task aborted: backend server unavailable")
			return
		end

		local stats = SearchIndexAPI.getTrainingStats()
		if not stats or (stats.count or 0) < 5 then
			LrDialogs.showError(
				LOC("$$$/StyleAI/TaskAiEditPhotos/ColdStartTitle=Cold Start"),
				LOC("$$$/StyleAI/TaskAiEditPhotos/ColdStartMsg=StyleAI needs at least 5 examples to learn your baseline editing style. Run 'Learn From My Edits' first.")
			)
			log:warn("AI Edit task aborted: Cold Start (<5 examples)")
			return
		end

		log:trace(
			"AI Edit options selected: scope="
				.. tostring(options.scope)
				.. " review="
				.. tostring(options.reviewBeforeApply)
				.. " styleStrength="
				.. tostring(options.style_strength)
				.. " masks="
				.. tostring(options.applyMasks)
				.. " crop="
				.. tostring(options.allow_auto_crop)
				.. " rotate="
				.. tostring(options.allow_auto_rotate)
		)

		local photos = PhotoSelector.getPhotosInScope(options.scope, nil, nil, selectedPhotosSnapshot)
		if not photos or #photos == 0 then
			LrDialogs.message(
				LOC("$$$/StyleAI/common/NoPhotosTitle=No Photos"),
				LOC("$$$/StyleAI/common/NoPhotosInScope=No photos found in the selected scope."),
				"info"
			)
			log:warn("AI Edit task found no photos in scope: " .. tostring(options.scope))
			return
		end

		local progressTitle = LOC("$$$/StyleAI/TaskAiEditPhotos/ProgressTitleML=Applying ML Edits...")
		local completionTitle = LOC("$$$/StyleAI/TaskAiEditPhotos/CompletionTitleML=ML Edit Completed")
		local successTitle = LOC("$$$/StyleAI/TaskAiEditPhotos/SuccessTitleML=ML Lightroom Edit")

		local progressScope = LrProgressScope({
			title = progressTitle,
			functionContext = ctx,
		})

		progressScope:setCaption(progressTitle)
		progressScope:setPortionComplete(0, #photos)

		local successCount = 0
		local skippedCount = 0
		local errorCount = 0
		local errorMessages = {}
		local backendWarnings = {}
		local runLog = {}

		-- Queue state
		local results = {}
		local producerDone = false
		local stopRequested = false
		local photoIdsByIndex = {}
		local photoIdErrorsByIndex = {}
		local operationItemIds = {}
		for index, photo in ipairs(photos) do
			local photoId, photoIdErr = SearchIndexAPI.getPhotoIdForPhoto(photo)
			photoIdsByIndex[index] = photoId
			photoIdErrorsByIndex[index] = photoIdErr
			if photoId then table.insert(operationItemIds, photoId) end
		end
		local operationOk, operation = SearchIndexAPI.startOperation(
			"edit",
			operationItemIds,
			{ scope = tostring(options.scope), photo_count = #operationItemIds },
			nil,
			9,
			false
		)
		if not operationOk then
			progressScope:done()
			ErrorHandler.handleError("Could not start AI edit operation", operation)
			return
		end
		local operationId = operation.job_id
		local function finishOperationItem(photoId, state, operationError)
			if not photoId then return false, "Missing operation photo ID" end
			local updated, updateError = SearchIndexAPI.updateOperationItems(operationId, {
				{ item_id = photoId, state = state, error = operationError },
			})
			if not updated then
				log:warn("Could not finalize edit operation item " .. tostring(photoId) .. ": " .. tostring(updateError))
				return false, updateError
			end
			return true
		end

		local function flushApplicationReceipt(photo, payload)
			local eventOk, eventResponse = SearchIndexAPI.submitStyleEditApplicationEvents({ payload.event })
			if not eventOk then
				return false, "Could not persist AI edit application history: " .. tostring(eventResponse)
			end

			local itemOk, itemError = SearchIndexAPI.updateOperationItems(payload.operation_job_id, {
				{
					item_id = payload.operation_item_id,
					state = payload.terminal_state,
					error = payload.terminal_error,
				},
			})
			if not itemOk then
				-- A receipt can survive beyond the operation's retention window, or
				-- Lightroom may have crashed after both backend writes but before the
				-- catalog outbox was cleared. Keep the receipt unless we can prove that
				-- the desired terminal state is already durable.
				local statusOk, job = SearchIndexAPI.getOperation(payload.operation_job_id, true)
				local alreadyTerminal = false
				if statusOk and type(job.items) == "table" then
					for _, item in ipairs(job.items) do
						if tostring(item.item_id) == tostring(payload.operation_item_id)
							and item.state == payload.terminal_state
						then
							alreadyTerminal = true
							break
						end
					end
				end
				if not alreadyTerminal then
					return false, "Application history was stored, but its operation receipt was not: " .. tostring(itemError)
				end
			end

			local clearOk, clearError = clearPendingApplicationEvent(photo, progressScope)
			if not clearOk then
				return false, "Application receipt was stored, but its Lightroom outbox could not be cleared: " .. tostring(clearError)
			end
			return true
		end

		local function recordApplicationEvent(
			photo,
			photoId,
			response,
			status,
			terminalState,
			currentSettings,
			applyOptions,
			warnings,
			errorMessage
		)
			local inferenceId = type(response) == "table" and response.edit_inference_id or nil
			if not inferenceId or inferenceId == "" then
				return false, "Backend edit response omitted its inference ID"
			end
			local payload = {
				operation_job_id = operationId,
				operation_item_id = photoId,
				terminal_state = terminalState,
				terminal_error = errorMessage,
				event = {
					edit_inference_id = inferenceId,
					idempotency_key = "application:" .. tostring(inferenceId),
					status = status,
					current_settings = currentSettings,
					global_applied = applyOptions and applyOptions.applyGlobal == true or false,
					masks_applied = applyOptions and applyOptions.applyMasks == true or false,
					warnings = warnings or {},
					error = errorMessage or "",
				},
			}
			local pendingOk, pendingError = writePendingApplicationEvent(photo, payload, progressScope)
			if not pendingOk then
				return false, "Could not create durable Lightroom application receipt: " .. tostring(pendingError)
			end
			return flushApplicationReceipt(photo, payload)
		end

		local consumerIndex = 1
		local nextIndexToProcess = 1
		local activeProducers = 0
		local profile = tonumber(prefs.indexingPerformanceProfile) or 2
		local maxWorkers = profile * 2
		local compatibilityOk, _, hardwareInfo = SearchIndexAPI.ensureVersionCompatibility()
		if compatibilityOk and hardwareInfo then
			maxWorkers = math.min(
				maxWorkers,
				math.max(1, tonumber(hardwareInfo.recommended_parallel_tasks) or maxWorkers)
			)
		end
		
		local backendRequestLane = "backend_edit_request"
		WorkCoordinator.configureLane(backendRequestLane, maxWorkers)

		local function producerWorker()
			while not progressScope:isCanceled() and not stopRequested do
				local index = nextIndexToProcess
				if index > #photos then break end

				-- Throttle to avoid unbounded memory/disk usage (max workers ahead of consumer)
				if index > consumerIndex + (maxWorkers * 2) then
					LrTasks.yield()
					LrTasks.sleep(0.1)
				else
					nextIndexToProcess = nextIndexToProcess + 1

					local photo = photos[index]
					local fileName = photo:getFormattedMetadata("fileName") or "Photo"
					local resultObj = { fileName = fileName, continueProcessing = true }

					local photoId = photoIdsByIndex[index]
					local photoIdErr = photoIdErrorsByIndex[index]
					local pendingReceipt, pendingReceiptError = readPendingApplicationEvent(photo)
					local pendingReady = true
					if pendingReceipt == false then
						pendingReady = false
						pendingReceiptError = pendingReceiptError or "Stored application receipt is invalid"
					elseif pendingReceipt then
						pendingReady, pendingReceiptError = flushApplicationReceipt(photo, pendingReceipt)
					end
					if not pendingReady then
						local message = "A previous AI edit receipt must be synchronized before another edit: "
							.. tostring(pendingReceiptError)
						log:error(message)
						resultObj.errorMsg = fileName .. ": " .. message
						resultObj.continueProcessing = false
					elseif not photoId then
						log:error("Failed to resolve photo ID for " .. fileName .. ": " .. tostring(photoIdErr))
						resultObj.errorMsg = fileName .. ": " .. tostring(photoIdErr)
						resultObj.continueProcessing = false
					else
						local photoOptions = nil
						local okSettings, currentSettings = LrTasks.pcall(function()
							photoOptions = enrichPhotoOptions(photo, options)
							return photo:getDevelopSettings()
						end)
						if not photoOptions then
							photoOptions = enrichPhotoOptions(photo, options)
						end
						photoOptions.job_id = operationId
						if okSettings and currentSettings then
							photoOptions.current_settings = currentSettings
						end
						local base_path = SearchIndexAPI.exportPhotoForIndexing(photo)
						if not base_path then
							log:error("Failed to export photo for AI edit generation: " .. fileName)
							resultObj.errorMsg = fileName .. ": export failed"
							resultObj.continueProcessing = false
							SearchIndexAPI.updateOperationItems(operationId, {
								{ item_id = photoId, state = "failed", error = "Lightroom preview export failed" },
							})
						else
							local requestLease, requestLeaseError = WorkCoordinator.acquire(backendRequestLane, progressScope)
							local ok, apiOk, apiResponse
							if requestLease then
								ok, apiOk, apiResponse = LrTasks.pcall(function()
									return SearchIndexAPI.styleEdit(photoId, base_path, photoOptions)
								end)
							else
								ok, apiOk, apiResponse = false, requestLeaseError, nil
							end
							WorkCoordinator.release(requestLease)

							SearchIndexAPI.cleanupExportedPhoto(base_path)

							if not ok then
								log:error("AI edit generation threw for " .. fileName .. ": " .. tostring(apiOk))
								resultObj.errorMsg = fileName .. ": exception thrown: " .. tostring(apiOk)
								resultObj.continueProcessing = false
								SearchIndexAPI.updateOperationItems(operationId, {
									{ item_id = photoId, state = "failed", error = tostring(apiOk) },
								})
							else
								if apiOk and type(apiResponse) == "table" and apiResponse.status == "error" and (apiResponse.error == "profile_mismatch" or apiResponse.error == "low_confidence") then
									local title = apiResponse.error == "profile_mismatch" 
										and LOC("$$$/StyleAI/TaskAiEditPhotos/ProfileMismatchTitle=Camera Profile Mismatch")
										or LOC("$$$/StyleAI/TaskAiEditPhotos/LowConfidenceTitle=Low Match Confidence")
									
									local message = apiResponse.error == "profile_mismatch"
										and LOC("$$$/StyleAI/TaskAiEditPhotos/ProfileMismatchMessage=No training examples exist for the camera profile used by ^1. Please train a style model for this profile first.", fileName)
										or LOC("$$$/StyleAI/TaskAiEditPhotos/LowConfidenceMessage=StyleAI could not find a confident match for ^1 based on your training examples.", fileName)

									LrDialogs.message(title, message, "info")
									apiResponse = { status = "error", error = apiResponse.error }
								end

								resultObj.response = apiResponse
								if apiResponse and apiResponse.warning then
									resultObj.warning = fileName .. ": " .. tostring(apiResponse.warning)
								end

								if not apiOk or not apiResponse or type(apiResponse) ~= "table" or apiResponse.status ~= "success" then
									local errMsg = "Unknown error"
									if not apiOk then errMsg = tostring(apiResponse)
									elseif type(apiResponse) == "string" then errMsg = apiResponse
									elseif apiResponse and apiResponse.error then errMsg = apiResponse.error end
									resultObj.errorMsg = fileName .. ": " .. errMsg
									resultObj.continueProcessing = false
								end
							end
						end
					end

					results[index] = resultObj
				end
			end
		end

		activeProducers = maxWorkers
		for i = 1, maxWorkers do
			LrTasks.startAsyncTask(function()
				local workerOk, workerError = LrTasks.pcall(function()
					LrFunctionContext.callWithContext("ProducerTask_" .. tostring(i), function(producerCtx)
						producerWorker()
					end)
				end)
				activeProducers = activeProducers - 1
				if not workerOk then
					stopRequested = true
					local message = "Edit producer failed: " .. tostring(workerError)
					log:error(message)
					table.insert(errorMessages, message)
					SearchIndexAPI.cancelOperation(operationId)
				end
				if activeProducers <= 0 then producerDone = true end
			end)
		end

		for index, photo in ipairs(photos) do
			if progressScope:isCanceled() then break end

			consumerIndex = index
			local fileName = photo:getFormattedMetadata("fileName") or "Photo"
			progressScope:setCaption("Processing " .. fileName .. " (" .. tostring(index) .. " of " .. tostring(#photos) .. ")")
			progressScope:setPortionComplete(index - 1, #photos)

			-- Wait for producer to finish this photo
			while results[index] == nil and not producerDone and not progressScope:isCanceled() do
				LrTasks.sleep(0.1)
			end

			if progressScope:isCanceled() then break end

			local res = results[index]
			if not res then break end

			if res.warning then
				table.insert(backendWarnings, res.warning)
			end

			if not res.continueProcessing then
				if res.errorMsg then
					table.insert(errorMessages, res.errorMsg)
					table.insert(runLog, string.format("- %s: ERROR: %s", fileName, res.errorMsg))
				else
					table.insert(runLog, string.format("- %s: ERROR: Unknown error", fileName))
				end
				errorCount = errorCount + 1
				finishOperationItem(photoIdsByIndex[index], "failed", res.errorMsg or "Edit generation failed")
			else
				local response = res.response
				local persistLease, persistLeaseError = WorkCoordinator.acquire("catalog_write", progressScope)
				local okPersist, persistErr
				if persistLease then
					okPersist, persistErr = LrTasks.pcall(function()
						DevelopEditManager.persistEditRecipe(photo, response, nil, "generated")
					end)
				else
					okPersist, persistErr = false, persistLeaseError
				end
				WorkCoordinator.release(persistLease)
				if not okPersist then
					log:error("Persist generated recipe threw for " .. fileName .. ": " .. tostring(persistErr))
					table.insert(errorMessages, fileName .. ": could not persist recipe: " .. tostring(persistErr))
					errorCount = errorCount + 1
					finishOperationItem(photoIdsByIndex[index], "failed", "Could not persist generated recipe")
				else
					local applyOptions = { applyGlobal = true, applyMasks = options.applyMasks }

					if options.reviewBeforeApply then
						local result, validated = DevelopEditManager.showValidationDialog(ctx, photo, response, options)
						if result == "cancel" then
							skippedCount = skippedCount + 1
							res.continueProcessing = false
							local receiptOk, receiptError = recordApplicationEvent(
								photo,
								photoIdsByIndex[index],
								response,
								"not_applied",
								"canceled",
								nil,
								applyOptions,
								nil,
								"review_canceled"
							)
							if not receiptOk then
								local message = "Could not finalize canceled edit history: " .. tostring(receiptError)
								log:error(message)
								table.insert(backendWarnings, message)
							end
						elseif validated then
							applyOptions = validated
						end
					end

					if res.continueProcessing and not applyOptions.applyGlobal and not applyOptions.applyMasks then
						skippedCount = skippedCount + 1
						res.continueProcessing = false
						local receiptOk, receiptError = recordApplicationEvent(
							photo,
							photoIdsByIndex[index],
							response,
							"not_applied",
							"canceled",
							nil,
							applyOptions,
							nil,
							"all_edit_sections_disabled"
						)
						if not receiptOk then
							local message = "Could not finalize skipped edit history: " .. tostring(receiptError)
							log:error(message)
							table.insert(backendWarnings, message)
						end
					end

					if res.continueProcessing then
						local applyLease, applyLeaseError = WorkCoordinator.acquire("catalog_write", progressScope)
						local applyOk, applied, warnings = LrTasks.pcall(function()
							if not applyLease then
								error("Catalog write canceled: " .. tostring(applyLeaseError))
							end
							return DevelopEditManager.applyRecipe(photo, response, applyOptions)
						end)
						WorkCoordinator.release(applyLease)
						if not applyOk then
							local applyError = applied
							applied = false
							warnings = { tostring(applyError) }
							log:error("AI edit application threw for " .. fileName .. ": " .. tostring(applyError))
						end
						if applied then
							local readOk, readback = LrTasks.pcall(function()
								return photo:getDevelopSettings()
							end)
							local receiptOk, receiptError
							if readOk and type(readback) == "table" then
								receiptOk, receiptError = recordApplicationEvent(
									photo,
									photoIdsByIndex[index],
									response,
									"apply_confirmed",
									"succeeded",
									readback,
									applyOptions,
									warnings,
									nil
								)
							else
								receiptOk, receiptError = recordApplicationEvent(
									photo,
									photoIdsByIndex[index],
									response,
									"apply_unconfirmed",
									"succeeded",
									nil,
									applyOptions,
									warnings,
									tostring(readback)
								)
							end
							if not receiptOk then
								local message = "Edit was applied, but its durable receipt is pending: " .. tostring(receiptError)
								log:error(message)
								table.insert(backendWarnings, message)
							end
							successCount = successCount + 1
							local styleInfo = "Editing Policy"
							if response.engine and response.engine ~= "none" then
								local conf = response.confidence and math.floor(response.confidence * 100) or 0
								local styleName = (response.matched_filenames and response.matched_filenames[1]) or "Unknown Style"
								local examples = response.matched_examples or 0

								local strength = options.style_strength or "normal"
								styleInfo = string.format("Editing Policy: %s (%d examples, %d%% conf, %s strength)", styleName, examples, conf, strength)
							end
							table.insert(runLog, string.format("- %s: %s", fileName, styleInfo))
						else
							local receiptOk, receiptError = recordApplicationEvent(
								photo,
								photoIdsByIndex[index],
								response,
								"apply_failed",
								"failed",
								nil,
								applyOptions,
								warnings,
								"Lightroom applyDevelopSettings failed"
							)
							if not receiptOk then
								local message = "Could not finalize failed edit history: " .. tostring(receiptError)
								log:error(message)
								table.insert(backendWarnings, message)
							end
							errorCount = errorCount + 1
							table.insert(errorMessages, fileName .. ": failed to apply recipe")
						end
						if warnings and #warnings > 0 then
							log:warn("AI edit warnings for " .. fileName .. ": " .. table.concat(warnings, " | "))
						end
					end
				end
			end
		end

		if progressScope:isCanceled() then
			stopRequested = true
			SearchIndexAPI.cancelOperation(operationId)
		end
		while activeProducers > 0 do
			LrTasks.yield()
			LrTasks.sleep(0.05)
		end
		if stopRequested then
			-- Catch items that completed backend inference after the first cancel
			-- request but before their Lightroom handoff could run.
			SearchIndexAPI.cancelOperation(operationId)
		end
		SearchIndexAPI.completeOperation(operationId)

		progressScope:done()

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
				LOC("$$$/StyleAI/TaskAiEditPhotos/Summary=Applied edits to ^1 photo(s).", tostring(successCount))
			if skippedCount > 0 then
				combinedReport = combinedReport
					.. "\n"
					.. LOC("$$$/StyleAI/common/Skipped=Skipped: ^1", tostring(skippedCount))
			end
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

			if errorCount > 0 then
				ErrorHandler.handleError(
					completionTitle,
					combinedReport
				)
			else
				LrDialogs.message(
					completionTitle,
					combinedReport,
					"warning"
				)
			end
		else
			if #runLog > 0 then
				local f = LrView.osFactory()
				local dialogContent = f:column({
					spacing = f:control_spacing(),
					f:static_text({
						title = LOC(
							"$$$/StyleAI/TaskAiEditPhotos/SuccessSummary=Applied edits to ^1 photo(s).\nSkipped: ^2",
							tostring(successCount),
							tostring(skippedCount)
						),
						font = "<system/bold>",
					}),
					f:static_text({
						title = LOC("$$$/StyleAI/AiEdit/ExportLogHint=You can export a detailed log of the ML styles and confidence metrics applied to these photos."),
						size = "small"
					})
				})
				
				local res = LrDialogs.presentModalDialog({
					title = successTitle,
					contents = dialogContent,
					actionVerb = LOC("$$$/StyleAI/common/OK=OK"),
					cancelVerb = "Export Log",
				})
				
				if res == "cancel" then
					local exportDir = LrDialogs.runOpenPanel({
						title = LOC("$$$/StyleAI/AiEdit/ChooseLogFolder=Choose Export Folder for ML Edit Log"),
						canChooseFiles = false,
						canChooseDirectories = true,
						canCreateDirectories = true,
						allowsMultipleSelection = false,
					})
					if exportDir and exportDir[1] then
						local LrPathUtils = import("LrPathUtils")
						local timestamp = os.date("%Y%m%d_%H%M%S")
						local fileName = "StyleAI_Edit_Log_" .. timestamp .. ".txt"
						local savePath = LrPathUtils.child(exportDir[1], fileName)
						
						local file = io.open(savePath, "w")
						if file then
							file:write("StyleAI ML Edit Log\n")
							file:write("===================\n\n")
							for _, line in ipairs(runLog) do
								file:write(line .. "\n")
							end
							file:close()
						end
					end
				end
			else
				LrDialogs.message(
					successTitle,
					LOC(
						"$$$/StyleAI/TaskAiEditPhotos/SuccessSummary=Applied edits to ^1 photo(s).\nSkipped: ^2",
						tostring(successCount),
						tostring(skippedCount)
					),
					"info"
				)
			end
		end
		log:info(
			"AI Edit task completed. success="
				.. tostring(successCount)
				.. " skipped="
				.. tostring(skippedCount)
				.. " errors="
				.. tostring(errorCount)
		)
	end)
end)
end

return AiEditAction
