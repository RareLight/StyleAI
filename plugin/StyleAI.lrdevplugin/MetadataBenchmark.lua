local MetadataBenchmark = {}

local UIFactory = require("UIFactory")
local Report = require("MetadataBenchmarkReport")
local SearchIndexAPI = require("APISearchIndex")
local PhotoSelector = require("PhotoSelector")
local Defaults = require("Defaults")
local WorkCoordinator = require("WorkCoordinator")
local runSequence = 0
local RECOMMENDED_MAX_PHOTOS = 32
local MAX_PROGRESS_MODEL_CHARS = 72

local function roundMilliseconds(value)
	return math.floor((tonumber(value) or 0) + 0.5)
end

local function availableModels()
	return SearchIndexAPI.getModelChoices()
end

local function utf8Characters(value)
	local characters = {}
	local index = 1
	while index <= #value do
		local lead = string.byte(value, index)
		local length = 1
		if lead and lead >= 240 then
			length = 4
		elseif lead and lead >= 224 then
			length = 3
		elseif lead and lead >= 192 then
			length = 2
		end
		table.insert(characters, string.sub(value, index, index + length - 1))
		index = index + length
	end
	return characters
end

local function progressModelTitle(model)
	local title = model and model.details and model.details.label
	if title == nil or tostring(title) == "" then title = model and (model.model or model.title) or "" end
	title = tostring(title)
	local characters = utf8Characters(title)
	if #characters <= MAX_PROGRESS_MODEL_CHARS then return title end
	local suffixLength = 28
	local prefixLength = MAX_PROGRESS_MODEL_CHARS - suffixLength - 3
	return table.concat(characters, "", 1, prefixLength)
		.. "..."
		.. table.concat(characters, "", #characters - suffixLength + 1, #characters)
end

local function setModelProgressCaption(progressScope, modelTitle, photoIndex, photoCount)
	progressScope:setCaption(LOC(
		"$$$/StyleAI/MetadataBenchmark/Model=^1 (^2/^3)",
		modelTitle,
		tostring(photoIndex),
		tostring(photoCount)
	))
end

local function optionalRuntimeValue(owner, methodName, fallback)
	if type(owner) ~= "table" or type(owner[methodName]) ~= "function" then return fallback end
	local ok, value = LrTasks.pcall(function() return owner[methodName]() end)
	if not ok or value == nil then return fallback end
	return value
end

local function validateBenchmarkRuntime()
	local ok, err = Report.validateRuntime()
	if not ok then return false, err end
	local requirements = {
		{ LrApplication, "LrApplication", "activeCatalog" },
		{ LrDate, "LrDate", "timeToW3CDate" },
		{ LrPathUtils, "LrPathUtils", "parent" },
		{ LrPathUtils, "LrPathUtils", "leafName" },
		{ LrStringUtils, "LrStringUtils", "encodeBase64" },
	}
	for _, requirement in ipairs(requirements) do
		if type(requirement[1]) ~= "table" or type(requirement[1][requirement[3]]) ~= "function" then
			return false, requirement[2] .. "." .. requirement[3] .. " is unavailable in this Lightroom runtime"
		end
	end
	return true
end

local function showDialog(ctx, selectedCount, models)
	local f = LrView.osFactory()
	local bind = LrView.bind
	local props = LrBinding.makePropertyTable(ctx)
	props.prompt = Defaults.defaultSystemInstruction
	props.language = Defaults.defaultGenerateLanguage
	props.temperature = Defaults.defaultTemperature
	props.generateKeywords = true
	props.generateTitle = true
	props.generateCaption = true
	props.generateAltText = true
	props.submitGPS = prefs.submitGPS == true
	props.submitKeywords = prefs.submitKeywords == true
	props.submitFolderName = prefs.submitFolderName == true
	props.submitPhotoContext = false
	props.useKeywordHierarchy = prefs.useKeywordHierarchy == true
	props.warmup = true

	local modelControls = {}
	for index, model in ipairs(models) do
		local key = "benchmarkModel" .. tostring(index)
		props[key] = model.key == prefs.modelKey
		table.insert(modelControls, f:checkbox({ title = model.title, value = bind(key) }))
	end
	if #models > 0 then
		local anySelected = false
		for index = 1, #models do anySelected = anySelected or props["benchmarkModel" .. tostring(index)] end
		if not anySelected then props.benchmarkModel1 = true end
	end

	local function updateRequestEstimate()
		local selectedModelCount = 0
		for index = 1, #models do
			if props["benchmarkModel" .. tostring(index)] then selectedModelCount = selectedModelCount + 1 end
		end
		local measuredRequests = selectedCount * selectedModelCount
		local warmupRequests = props.warmup and selectedModelCount or 0
		props.requestEstimate = LOC(
			"$$$/StyleAI/MetadataBenchmark/RequestEstimate=Measured requests: ^1 photos × ^2 models = ^3. Excluded warm-ups: ^4. Total model calls: ^5.",
			tostring(selectedCount),
			tostring(selectedModelCount),
			tostring(measuredRequests),
			tostring(warmupRequests),
			tostring(measuredRequests + warmupRequests)
		)
	end
	for index = 1, #models do props:addObserver("benchmarkModel" .. tostring(index), updateRequestEstimate) end
	props:addObserver("warmup", updateRequestEstimate)
	updateRequestEstimate()

	local modelColumn = { spacing = f:control_spacing(), fill_horizontal = 1 }
	for _, control in ipairs(modelControls) do table.insert(modelColumn, control) end

	local contents = UIFactory.DialogColumn(f, {
		bind_to_object = props,
		width = 720,
		UIFactory.Notice(f, {
			title = LOC("$$$/StyleAI/MetadataBenchmark/Notice=Developer benchmark: generated metadata is written only to a local comparison report, never to Lightroom or StyleAI's image index."),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/MetadataBenchmark/Set=Frozen Benchmark Set"),
			UIFactory.HelpText(f, {
				title = LOC("$$$/StyleAI/MetadataBenchmark/SelectedCount=^1 selected photo(s) will be frozen into a uniquely named StyleAI benchmark collection. A representative set of 24–32 photos is recommended for normal comparisons.", tostring(selectedCount)),
			}),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/MetadataBenchmark/Models=Local Vision Models"),
			f:scrolled_view({
				width = 680,
				height = math.min(220, math.max(80, #models * 26)),
				fill_horizontal = 1,
				f:column(modelColumn),
			}),
			f:checkbox({
				title = LOC("$$$/StyleAI/MetadataBenchmark/Warmup=Run one excluded warm-up photo per model"),
				value = bind("warmup"),
			}),
			UIFactory.HelpText(f, { title = bind("requestEstimate") }),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/MetadataBenchmark/Outputs=Fixed Metadata Settings"),
			f:row({
				spacing = f:control_spacing(),
				f:checkbox({ title = LOC("$$$/StyleAI/PluginInfoDialogSections/keywords=Keywords"), value = bind("generateKeywords") }),
				f:checkbox({ title = LOC("$$$/StyleAI/PluginInfoDialogSections/title=Title"), value = bind("generateTitle") }),
				f:checkbox({ title = LOC("$$$/StyleAI/PluginInfoDialogSections/caption=Caption"), value = bind("generateCaption") }),
				f:checkbox({ title = LOC("$$$/StyleAI/PluginInfoDialogSections/alttext=Alt Text"), value = bind("generateAltText") }),
			}),
			UIFactory.FormRow(f, {
				label = LOC("$$$/StyleAI/PluginInfoDialogSections/language=Language:"),
				f:popup_menu({
					value = bind("language"),
					width = 240,
					items = (function()
						local items = {}
						for _, language in ipairs(Defaults.generateLanguages) do table.insert(items, { title = language, value = language }) end
						return items
					end)(),
				}),
			}),
			UIFactory.FormRow(f, {
				label = LOC("$$$/StyleAI/PluginInfoDialogSections/temperature=Temperature:"),
				f:edit_field({ value = bind("temperature"), width_in_digits = 6, precision = 2, min = 0, max = 2 }),
			}),
			f:checkbox({ title = LOC("$$$/StyleAI/UI/EnableHierarchy=Organize generated keywords in a hierarchy"), value = bind("useKeywordHierarchy") }),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/AnalyzeAndIndex/ContextOptions=Optional Context Sent to the Local Model"),
			f:row({
				spacing = f:control_spacing(),
				f:checkbox({ title = LOC("$$$/StyleAI/PluginInfoDialogSections/gps=GPS"), value = bind("submitGPS") }),
				f:checkbox({ title = LOC("$$$/StyleAI/PluginInfoDialogSections/keywords=Keywords"), value = bind("submitKeywords") }),
				f:checkbox({ title = LOC("$$$/StyleAI/PluginInfoDialogSections/folder=Folder name"), value = bind("submitFolderName") }),
				f:checkbox({ title = LOC("$$$/StyleAI/MetadataBenchmark/PhotoContext=Saved photo instructions"), value = bind("submitPhotoContext") }),
			}),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC("$$$/StyleAI/MetadataBenchmark/Prompt=Exact System Prompt"),
			f:edit_field({ value = bind("prompt"), multiline = true, height_in_lines = 10, width = 680 }),
		}),
	})

	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/MetadataBenchmark/LlmTitle=Local LLM Tagging & Metadata Benchmark"),
		contents = contents,
		actionVerb = LOC("$$$/StyleAI/MetadataBenchmark/Run=Run Benchmark"),
		cancelVerb = LOC("$$$/StyleAI/common/Cancel=Cancel"),
		resizable = true,
	})
	if result ~= "ok" then return nil end

	local selectedModels = {}
	for index, model in ipairs(models) do
		if props["benchmarkModel" .. tostring(index)] then table.insert(selectedModels, model) end
	end
	if #selectedModels == 0 then
		LrDialogs.message(
			LOC("$$$/StyleAI/MetadataBenchmark/NoModelsTitle=No Models Selected"),
			LOC("$$$/StyleAI/MetadataBenchmark/NoModels=Select at least one local vision model."),
			"warning"
		)
		return nil
	end
	if not (props.generateKeywords or props.generateTitle or props.generateCaption or props.generateAltText) then
		LrDialogs.message(
			LOC("$$$/StyleAI/MetadataBenchmark/NoOutputsTitle=No Outputs Selected"),
			LOC("$$$/StyleAI/MetadataBenchmark/NoOutputs=Select at least one metadata output."),
			"warning"
		)
		return nil
	end
	return {
		models = selectedModels,
		prompt = props.prompt,
		language = props.language,
		temperature = tonumber(props.temperature) or Defaults.defaultTemperature,
		generateKeywords = props.generateKeywords,
		generateTitle = props.generateTitle,
		generateCaption = props.generateCaption,
		generateAltText = props.generateAltText,
		submitGPS = props.submitGPS,
		submitKeywords = props.submitKeywords,
		submitFolderName = props.submitFolderName,
		submitPhotoContext = props.submitPhotoContext,
		useKeywordHierarchy = props.useKeywordHierarchy,
		warmup = props.warmup,
	}
end

local function findSet(children, name)
	for _, child in ipairs(children or {}) do if child:getName() == name then return child end end
	return nil
end

local function createBenchmarkCollection(catalog, photos, collectionName)
	local rootSet = findSet(catalog:getChildCollectionSets(), "StyleAI")
	local benchmarksSet = rootSet and findSet(rootSet:getChildCollectionSets(), "Benchmarks") or nil
	local collection
	local writeLease, leaseError = WorkCoordinator.acquire("catalog_write")
	if not writeLease then error(tostring(leaseError)) end
	local writeOk, writeError = LrTasks.pcall(function()
		catalog:withWriteAccessDo(
			LOC("$$$/StyleAI/MetadataBenchmark/CreateCollection=Create metadata benchmark collection"),
			function()
				if not rootSet then rootSet = catalog:createCollectionSet("StyleAI", nil, true) end
				if not benchmarksSet then benchmarksSet = catalog:createCollectionSet("Benchmarks", rootSet, true) end
				collection = catalog:createCollection(collectionName, benchmarksSet, false)
				collection:addPhotos(photos)
			end,
			Defaults.catalogWriteAccessOptions
		)
	end)
	WorkCoordinator.release(writeLease)
	if not writeOk then error(tostring(writeError)) end
	return collection
end

local function readFile(path)
	local file, err = io.open(path, "rb")
	if not file then return nil, err end
	local data = file:read("*all")
	file:close()
	return data
end

local function gpsLocation(gps)
	if type(gps) ~= "table" then return nil end
	local latitude = gps.latitude or gps.lat or gps[1]
	local longitude = gps.longitude or gps.lon or gps.lng or gps[2]
	if tonumber(latitude) and tonumber(longitude) then
		return { gps_latitude = tonumber(latitude), gps_longitude = tonumber(longitude) }
	end
	return nil
end

local function preparePhotos(photos, options, progressScope, runId)
	local prepared = {}
	for index, photo in ipairs(photos) do
		if progressScope:isCanceled() then break end
		progressScope:setCaption(LOC("$$$/StyleAI/MetadataBenchmark/Preparing=Preparing benchmark photo ^1 of ^2...", tostring(index), tostring(#photos)))
		local sourcePhotoId, idError = Util.getGlobalPhotoIdForPhoto(photo)
		if sourcePhotoId then
			local jpegData, previewError
			local renderLease, leaseError = WorkCoordinator.acquire("render", progressScope)
			if renderLease then
				jpegData, previewError = SearchIndexAPI.getJpegThumbnailForPhoto(photo, 1024, 1024, { timeoutSeconds = 30 })
				WorkCoordinator.release(renderLease)
			else
				previewError = leaseError
			end
			if not jpegData and not progressScope:isCanceled() then
				local exported = SearchIndexAPI.exportPhotoForIndexing(photo)
				if exported then
					jpegData, previewError = readFile(exported)
					SearchIndexAPI.cleanupExportedPhoto(exported)
				end
			end
			if jpegData and #jpegData > 0 then
				local filename = photo:getFormattedMetadata("fileName") or ("photo-" .. tostring(index) .. ".jpg")
				local itemOptions = {}
				if options.submitKeywords then
					local keywords = photo:getFormattedMetadata("keywordTagsForExport")
					if type(keywords) == "string" then itemOptions.existing_keywords = Util.string_split(keywords, ",")
					elseif type(keywords) == "table" then itemOptions.existing_keywords = keywords end
					itemOptions.submit_keywords = itemOptions.existing_keywords ~= nil
				end
				if options.submitFolderName then
					local path = photo:getRawMetadata("path")
					if path then itemOptions.folder_names = LrPathUtils.leafName(LrPathUtils.parent(path)) end
					itemOptions.submit_folder_names = itemOptions.folder_names ~= nil
				end
				local exif = Util.getPhotoExif(photo)
				local captureTime = exif and exif.capture_time
				if type(captureTime) == "number" then itemOptions.date_time = LrDate.timeToW3CDate(captureTime) end
				if options.submitGPS then itemOptions.location_data = gpsLocation(photo:getRawMetadata("gps")) end
				if options.submitPhotoContext then itemOptions.user_context = photo:getPropertyForPlugin(_PLUGIN, "photoContext") or "" end
				table.insert(prepared, {
					photo = photo,
					photo_id = runId .. ":photo:" .. tostring(index),
					source_photo_id = sourcePhotoId,
					filename = filename,
					image = LrStringUtils.encodeBase64(jpegData),
					options = itemOptions,
				})
			else
				log:error("Could not prepare benchmark photo: " .. tostring(previewError))
			end
		else
			log:error("Could not identify benchmark photo: " .. tostring(idError))
		end
		progressScope:setPortionComplete(index, math.max(1, #photos * (#options.models + 1)))
		LrTasks.yield()
		if MAC_ENV then LrTasks.sleep(0.01) end
	end
	return prepared
end

local function requestOptions(options, model, operationId)
	return {
		provider = model.provider,
		model = model.model,
		language = options.language,
		temperature = tostring(options.temperature),
		generate_keywords = tostring(options.generateKeywords),
		generate_title = tostring(options.generateTitle),
		generate_caption = tostring(options.generateCaption),
		generate_alt_text = tostring(options.generateAltText),
		submit_keywords = tostring(options.submitKeywords),
		submit_folder_names = tostring(options.submitFolderName),
		prompt = options.prompt,
		keyword_categories = options.useKeywordHierarchy and KeywordConfigProvider.getKeywordCategories() or {},
		operation_id = operationId,
	}
end

local function appendResponse(report, response, warmup)
	if type(response) ~= "table" or type(response.items) ~= "table" then
		error("The local metadata service returned an invalid benchmark response")
	end
	for _, result in ipairs(response.items) do
		if type(result) ~= "table" then error("The local metadata service returned an invalid benchmark result") end
		result.warmup = warmup == true
		local ok, err = Report.append(report, result)
		if not ok then error("Could not append benchmark report: " .. tostring(err)) end
	end
end

function MetadataBenchmark.run(ctx)
	local selectedPhotos = PhotoSelector.snapshotSelectedPhotos()
	if #selectedPhotos == 0 then
		LrDialogs.message(
			LOC("$$$/StyleAI/MetadataBenchmark/NoPhotosTitle=No Photos Selected"),
			LOC("$$$/StyleAI/MetadataBenchmark/NoPhotos=Select one or more photos before starting the benchmark."),
			"warning"
		)
		return
	end
	if not Util.waitForServerDialog({ suppressProgressDialog = false }) then return end
	local models = availableModels()
	if #models == 0 then
		LrDialogs.message(
			LOC("$$$/StyleAI/MetadataBenchmark/NoModelsTitle=No Models Selected"),
			LOC("$$$/StyleAI/MetadataBenchmark/Unavailable=No local vision-capable Ollama or LM Studio models are available."),
			"warning"
		)
		return
	end
	local options = showDialog(ctx, #selectedPhotos, models)
	if not options then return end
	local runtimeOk, runtimeError = validateBenchmarkRuntime()
	if not runtimeOk then
		LrDialogs.message(
			LOC("$$$/StyleAI/MetadataBenchmark/UnavailableTitle=Benchmark Runtime Unavailable"),
			LOC("$$$/StyleAI/MetadataBenchmark/RuntimeError=The benchmark cannot start because a required Lightroom capability is unavailable: ^1", tostring(runtimeError)),
			"critical"
		)
		return
	end
	if #selectedPhotos > RECOMMENDED_MAX_PHOTOS then
		local measuredRequests = #selectedPhotos * #options.models
		local warmupRequests = options.warmup and #options.models or 0
		local confirmation = LrDialogs.confirm(
			LOC("$$$/StyleAI/MetadataBenchmark/LargeTitle=Large Benchmark Set"),
			LOC(
				"$$$/StyleAI/MetadataBenchmark/Large=The recommended normal benchmark size is 24–32 photos. This run will make ^1 measured requests plus ^2 excluded warm-ups (^3 total model calls) and may take a long time. Continue?",
				tostring(measuredRequests),
				tostring(warmupRequests),
				tostring(measuredRequests + warmupRequests)
			),
			LOC("$$$/StyleAI/MetadataBenchmark/Continue=Continue"),
			LOC("$$$/StyleAI/common/Cancel=Cancel")
		)
		if confirmation == "cancel" then return end
	end

	local catalog = LrApplication.activeCatalog()
	local startedAt = LrDate.currentTime()
	runSequence = runSequence + 1
	local milliseconds = math.floor((startedAt - math.floor(startedAt)) * 1000)
	local uniqueToken = Util.formatTimestampSafe(startedAt) .. string.format("-%03d-%02d", milliseconds, runSequence)
	local runId = "llm-metadata-" .. uniqueToken
	local collectionName = "LLM Metadata " .. uniqueToken
	local catalogDirectory = LrPathUtils.parent(catalog:getPath())
	local reportDirectory = LrPathUtils.child(LrPathUtils.child(LrPathUtils.child(catalogDirectory, "styleai.db"), "evaluation_reports"), runId)
	local backendVersion = SearchIndexAPI.getBackendVersion() or {}
	local manifest = {
		schema_version = "styleai_llm_metadata_benchmark_v1",
		report_writer_version = Report.IMPLEMENTATION_VERSION,
		run_id = runId,
		state = "preparing",
		started_at = startedAt,
		collection_name = collectionName,
		photo_count = #selectedPhotos,
		models = options.models,
		model_order = (function() local values = {}; for _, model in ipairs(options.models) do table.insert(values, model.key) end; return values end)(),
		prompt = options.prompt,
		output_contract = {
			normalized = true,
			keywords = options.generateKeywords and {
				hierarchical = options.useKeywordHierarchy,
				categories = options.useKeywordHierarchy and KeywordConfigProvider.getKeywordCategories() or {},
			} or false,
			title = options.generateTitle,
			caption = options.generateCaption,
			alt_text = options.generateAltText,
		},
		qualitative_review_schema = {
			version = 1,
			optional = true,
			model_labels_may_be_blinded = true,
			dimensions = { "correctness", "specificity", "search_usefulness", "alt_text_quality", "hallucination_severity" },
		},
		settings = {
			language = options.language,
			temperature = options.temperature,
			generate_keywords = options.generateKeywords,
			generate_title = options.generateTitle,
			generate_caption = options.generateCaption,
			generate_alt_text = options.generateAltText,
			use_keyword_hierarchy = options.useKeywordHierarchy,
			context = { gps = options.submitGPS, existing_keywords = options.submitKeywords, folder_name = options.submitFolderName, photo_context = options.submitPhotoContext },
			warmup = options.warmup,
			concurrency = 1,
			proxy_max_dimension = 1024,
			keyword_categories = options.useKeywordHierarchy and KeywordConfigProvider.getKeywordCategories() or {},
		},
		backend_version = backendVersion,
		plugin_version = tostring(Info.MAJOR) .. "." .. tostring(Info.MINOR) .. "." .. tostring(Info.REVISION),
		plugin_build = Info.BUILD,
		lightroom_version = optionalRuntimeValue(LrApplication, "versionString", "unknown"),
		operating_system = optionalRuntimeValue(LrSystemInfo, "osVersion", "unknown"),
		model_runs = {},
	}
	local report, reportError = Report.new(reportDirectory, manifest)
	if not report then error("Could not create benchmark report: " .. tostring(reportError)) end

	local progressScope = LrDialogs.showModalProgressDialog({
		title = LOC("$$$/StyleAI/MetadataBenchmark/Running=Running Metadata Benchmark..."),
		functionContext = ctx,
		cannotCancel = false,
	})
	local finalState = "failed"
	local operationId
	local keepWatching = true
	local progressState = { modelIndex = nil, modelTitle = nil, showPhotoProgress = false }
	local ok, runError = LrTasks.pcall(function()
		createBenchmarkCollection(catalog, selectedPhotos, collectionName)
		local prepared = preparePhotos(selectedPhotos, options, progressScope, runId)
		if #prepared == 0 then error("No selected photos could be prepared") end
		manifest.prepared_photo_count = #prepared
		manifest.photos = {}
		for _, item in ipairs(prepared) do
			table.insert(manifest.photos, { photo_id = item.photo_id, source_photo_id = item.source_photo_id, filename = item.filename })
		end
		local manifestOk, manifestError = Report.updateManifest(report, { state = "running", prepared_photo_count = #prepared, photos = manifest.photos })
		if not manifestOk then error("Could not update benchmark manifest: " .. tostring(manifestError)) end

		local operationItemIds = {}
		for modelIndex, model in ipairs(options.models) do
			for photoIndex = 1, #prepared do table.insert(operationItemIds, runId .. ":model:" .. tostring(modelIndex) .. ":photo:" .. tostring(photoIndex)) end
		end
		local operationOk, operation = SearchIndexAPI.startOperation(
			"metadata_benchmark",
			operationItemIds,
			{ run_id = runId, model_count = #options.models, photo_count = #prepared },
			nil,
			0,
			false
		)
		if not operationOk then error(tostring(operation)) end
		operationId = operation.job_id
		LrTasks.startAsyncTask(function()
			while keepWatching do
				local cancelOk, canceled = LrTasks.pcall(function() return progressScope:isCanceled() end)
				if cancelOk and canceled and operationId then SearchIndexAPI.cancelOperation(operationId); break end
				if operationId and progressState.showPhotoProgress then
					local statusOk, operation = SearchIndexAPI.getOperation(operationId, false)
					local details = statusOk and type(operation) == "table" and operation.details or nil
					local currentModel = type(details) == "table" and tonumber(details.current_model_index) or nil
					local currentPhoto = type(details) == "table" and tonumber(details.current_photo_index) or nil
					if currentModel == progressState.modelIndex and currentPhoto and currentPhoto >= 1 and currentPhoto <= #prepared then
						setModelProgressCaption(progressScope, progressState.modelTitle, currentPhoto, #prepared)
					end
				end
				LrTasks.sleep(0.25)
			end
		end)

		local completed = 0
		for modelIndex, model in ipairs(options.models) do
			if progressScope:isCanceled() then break end
			local modelStarted = LrDate.currentTime()
			local modelTitle = progressModelTitle(model)
			progressState.modelIndex = modelIndex
			progressState.modelTitle = modelTitle
			progressState.showPhotoProgress = false
			local modelOptions = requestOptions(options, model, nil)
			if options.warmup then
				progressScope:setCaption(LOC("$$$/StyleAI/MetadataBenchmark/WarmupProgress=^1 (warm-up)", modelTitle))
				local warmupItem = {
					photo_id = prepared[1].photo_id .. ":warmup:" .. tostring(modelIndex),
					source_photo_id = prepared[1].source_photo_id,
					filename = prepared[1].filename,
					image = prepared[1].image,
					options = prepared[1].options,
				}
				local warmupOk, warmupResponse = SearchIndexAPI.runMetadataBenchmarkBatch({ warmupItem }, modelOptions)
				if warmupOk then
					appendResponse(report, warmupResponse, true)
				else
					local appended, appendError = Report.append(report, {
						photo_id = warmupItem.photo_id, source_photo_id = warmupItem.source_photo_id,
						filename = warmupItem.filename, provider = model.provider, model = model.model,
						status = "failed", warmup = true, error = tostring(warmupResponse), timing = {},
					})
					if not appended then error("Could not append benchmark report: " .. tostring(appendError)) end
				end
			end
			progressState.showPhotoProgress = true
			setModelProgressCaption(progressScope, modelTitle, 1, #prepared)

			for batchStart = 1, #prepared, 12 do
				if progressScope:isCanceled() then break end
				local batch = {}
				for photoIndex = batchStart, math.min(#prepared, batchStart + 11) do
					local item = prepared[photoIndex]
					table.insert(batch, {
						photo_id = item.photo_id,
						source_photo_id = item.source_photo_id,
						filename = item.filename,
						image = item.image,
						options = item.options,
						operation_item_id = runId .. ":model:" .. tostring(modelIndex) .. ":photo:" .. tostring(photoIndex),
						model_index = modelIndex,
						photo_index = photoIndex,
					})
				end
				local batchOptions = requestOptions(options, model, operationId)
				local batchOk, response = SearchIndexAPI.runMetadataBenchmarkBatch(batch, batchOptions)
				if batchOk then
					appendResponse(report, response, false)
				else
					local failedUpdates = {}
					for _, item in ipairs(batch) do
						local appended, appendError = Report.append(report, {
							photo_id = item.photo_id, source_photo_id = item.source_photo_id,
							filename = item.filename, provider = model.provider, model = model.model,
							status = "failed", warmup = false, error = tostring(response), timing = {},
						})
						if not appended then error("Could not append benchmark report: " .. tostring(appendError)) end
						table.insert(failedUpdates, { item_id = item.operation_item_id, state = "failed", error = tostring(response) })
					end
					local updateOk, updateError = SearchIndexAPI.updateOperationItems(operationId, failedUpdates)
					if not updateOk then
						error(tostring(response) .. "; operation bookkeeping also failed: " .. tostring(updateError))
					end
				end
				completed = completed + #batch
				progressScope:setPortionComplete(#prepared + completed, #prepared * (#options.models + 1))
				manifestOk, manifestError = Report.updateManifest(report, {})
				if not manifestOk then error("Could not update benchmark manifest: " .. tostring(manifestError)) end
			end
			table.insert(manifest.model_runs, { provider = model.provider, model = model.model, elapsed_ms = roundMilliseconds((LrDate.currentTime() - modelStarted) * 1000) })
		end

		if progressScope:isCanceled() then
			if operationId then SearchIndexAPI.cancelOperation(operationId) end
			finalState = "canceled"
		else
			local completeOk, completion = SearchIndexAPI.completeOperation(operationId)
			if not completeOk then error(tostring(completion)) end
			finalState = completion.state == "succeeded" and "completed" or "partial"
		end
	end)
	keepWatching = false
	if not ok then
		manifest.run_error = tostring(runError)
		if operationId then SearchIndexAPI.cancelOperation(operationId) end
		finalState = progressScope:isCanceled() and "canceled" or "failed"
	end
	local finalizeOk, finalizeError = Report.finalize(report, finalState, {
		elapsed_ms = roundMilliseconds((LrDate.currentTime() - startedAt) * 1000),
		model_runs = manifest.model_runs,
		run_error = manifest.run_error,
	})
	progressScope:done()
	if not finalizeOk then error("Could not finalize benchmark report: " .. tostring(finalizeError)) end
	if type(LrShell) == "table" and type(LrShell.revealInShell) == "function" then
		local revealOk, revealError = LrTasks.pcall(function() LrShell.revealInShell(reportDirectory) end)
		if not revealOk then log:error("Could not reveal benchmark report directory: " .. tostring(revealError)) end
	else
		log:error("Could not reveal benchmark report directory because LrShell.revealInShell is unavailable")
	end
	if not ok then
		LrDialogs.message(
			LOC("$$$/StyleAI/MetadataBenchmark/FailedTitle=Benchmark Incomplete"),
			LOC("$$$/StyleAI/MetadataBenchmark/Failed=The benchmark did not complete: ^1\n\nPartial results were saved to ^2", tostring(runError), reportDirectory),
			"critical"
		)
	else
		LrDialogs.message(
			LOC("$$$/StyleAI/MetadataBenchmark/CompleteTitle=Benchmark Report Ready"),
			LOC("$$$/StyleAI/MetadataBenchmark/Complete=The ^1 benchmark report was saved to:\n^2", finalState, reportDirectory),
			"info"
		)
	end
end

return MetadataBenchmark
