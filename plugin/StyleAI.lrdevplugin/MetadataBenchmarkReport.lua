local Report = {}
Report.IMPLEMENTATION_VERSION = 8

local function roundTo(value, decimalPlaces)
	local factor = 10 ^ (decimalPlaces or 0)
	if value >= 0 then return math.floor(value * factor + 0.5) / factor end
	return math.ceil(value * factor - 0.5) / factor
end

local function roundTimingValues(timing)
	local rounded = {}
	for key, value in pairs(timing or {}) do
		if type(value) == "number" and string.match(tostring(key), "_ms$") then
			rounded[key] = roundTo(value, 0)
		elseif type(value) == "number" and (
			string.match(tostring(key), "_seconds$")
			or string.match(tostring(key), "_minutes$")
			or string.match(tostring(key), "_per_second$")
			or string.match(tostring(key), "_per_minute$")
		) then
			rounded[key] = roundTo(value, 1)
		else
			rounded[key] = value
		end
	end
	return rounded
end

local function sdkFunction(owner, ownerName, methodName)
	if type(owner) ~= "table" or type(owner[methodName]) ~= "function" then
		return nil, ownerName .. "." .. methodName .. " is unavailable in this Lightroom runtime"
	end
	return owner[methodName]
end

local function protectedCall(fn, ...)
	if type(LrTasks) ~= "table" or type(LrTasks.pcall) ~= "function" then
		return false, "LrTasks.pcall is unavailable in this Lightroom runtime"
	end
	local argumentCount = select("#", ...)
	local first, second = ...
	return LrTasks.pcall(function()
		if argumentCount == 0 then return fn() end
		if argumentCount == 1 then return fn(first) end
		return fn(first, second)
	end)
end

local function fileExists(path)
	local exists, err = sdkFunction(LrFileUtils, "LrFileUtils", "exists")
	if not exists then return false, err end
	local callOk, result = protectedCall(exists, path)
	if not callOk then return false, tostring(result) end
	return result ~= nil and result ~= false
end

local function writeAll(path, data, mode)
	local file, err = io.open(path, mode or "w")
	if not file then return false, err end
	local callOk, writeError = protectedCall(function()
		file:write(data)
		file:flush()
		file:close()
	end)
	if not callOk then
		protectedCall(function() file:close() end)
		return false, tostring(writeError)
	end
	return true
end

local function encodePretty(value)
	if type(JSON) ~= "table" or type(JSON.encode_pretty) ~= "function" then
		return nil, "JSON.encode_pretty is unavailable"
	end
	local callOk, encoded = protectedCall(function() return JSON:encode_pretty(value) end)
	if not callOk then return nil, tostring(encoded) end
	if type(encoded) ~= "string" then return nil, "JSON.encode_pretty returned no data" end
	return encoded
end

local function replaceFile(temporary, path)
	local move = type(LrFileUtils) == "table" and LrFileUtils.move or nil
	if type(move) == "function" then
		local callOk, moveResult = protectedCall(move, temporary, path)
		local destinationExists = fileExists(path)
		local temporaryExists = fileExists(temporary)
		if destinationExists and not temporaryExists then return true end

		if destinationExists then
			local delete = type(LrFileUtils.delete) == "function" and LrFileUtils.delete or nil
			if delete then protectedCall(delete, path) end
			callOk, moveResult = protectedCall(move, temporary, path)
			destinationExists = fileExists(path)
			temporaryExists = fileExists(temporary)
			if destinationExists and not temporaryExists then return true end
		end
		if not callOk then log:error("Benchmark manifest move failed; using copy fallback: " .. tostring(moveResult)) end
	end

	-- Some Lightroom/Lua combinations omit LrFileUtils.move. Copying the completed
	-- temporary file and then deleting it is a safe compatibility fallback.
	local copy, copyError = sdkFunction(LrFileUtils, "LrFileUtils", "copy")
	local delete, deleteError = sdkFunction(LrFileUtils, "LrFileUtils", "delete")
	if not copy then return false, copyError end
	if not delete then return false, deleteError end
	if fileExists(path) then
		local deleteOk, result = protectedCall(delete, path)
		if not deleteOk then return false, tostring(result) end
		if fileExists(path) then return false, "Could not replace the existing benchmark manifest" end
	end
	local copyOk, result = protectedCall(copy, temporary, path)
	if not copyOk then return false, tostring(result) end
	if not fileExists(path) then return false, "Benchmark manifest copy did not create its destination" end
	local deleteOk, result = protectedCall(delete, temporary)
	if not deleteOk then return false, tostring(result) end
	if fileExists(temporary) then return false, "Could not remove the temporary benchmark manifest" end
	return true
end

local function writeJsonAtomic(path, value)
	local temporary = path .. ".tmp"
	local encoded, encodeError = encodePretty(value)
	if not encoded then return false, encodeError end
	local ok, err = writeAll(temporary, encoded, "w")
	if not ok then return false, err end
	return replaceFile(temporary, path)
end

local function csvCell(value)
	local text = tostring(value == nil and "" or value)
	return '"' .. text:gsub('"', '""') .. '"'
end

local function html(value)
	return tostring(value == nil and "" or value)
		:gsub("&", "&amp;")
		:gsub("<", "&lt;")
		:gsub(">", "&gt;")
		:gsub('"', "&quot;")
		:gsub("'", "&#39;")
end

local function flattenKeywords(value, output, path)
	output = output or {}
	path = path or {}
	if type(value) == "string" then
		table.insert(output, value)
	elseif type(value) == "table" then
		if value.name then
			table.insert(output, tostring(value.name))
		else
			local numeric = #value > 0
			if numeric then
				for _, child in ipairs(value) do flattenKeywords(child, output, path) end
			else
				local keys = {}
				for key in pairs(value) do table.insert(keys, tostring(key)) end
				table.sort(keys)
				for _, key in ipairs(keys) do flattenKeywords(value[key], output, path) end
			end
		end
	end
	return output
end

local function percentile(values, fraction)
	if #values == 0 then return nil end
	local sorted = {}
	for i, value in ipairs(values) do sorted[i] = value end
	table.sort(sorted)
	local index = math.max(1, math.min(#sorted, math.ceil(#sorted * fraction)))
	return sorted[index]
end

local function mergeEvidence(current, value)
	if value == nil then return current end
	if current == nil then return value end
	if current == value then return current end
	return "mixed"
end

local function modelKey(result)
	local key = tostring(result.provider or "") .. "::" .. tostring(result.model or "")
	if result.benchmark_variant == "speculative" then
		return key .. "::" .. tostring(result.speculation_mode or "full_draft") .. "::"
			.. tostring(result.draft_model_requested or (result.inference or {}).used_draft_model or "integrated")
	end
	return key .. "::baseline"
end

function Report.new(reportDirectory, manifest)
	local runtimeOk, runtimeError = Report.validateRuntime()
	if not runtimeOk then return nil, runtimeError end
	if type(reportDirectory) ~= "string" or reportDirectory == "" then return nil, "Benchmark report directory is missing" end
	if type(manifest) ~= "table" then return nil, "Benchmark report manifest is missing" end
	if not fileExists(reportDirectory) then
		local createDirectories = LrFileUtils.createAllDirectories
		local callOk, created = protectedCall(createDirectories, reportDirectory)
		if not callOk or created == false or not fileExists(reportDirectory) then
			return nil, "Could not create benchmark report directory: " .. tostring(created)
		end
	end
	local self = {
		directory = reportDirectory,
		manifest = manifest,
		results = {},
		manifestPath = LrPathUtils.child(reportDirectory, "manifest.json"),
		jsonlPath = LrPathUtils.child(reportDirectory, "results.jsonl"),
		comparisonPath = LrPathUtils.child(reportDirectory, "comparison.csv"),
		summaryPath = LrPathUtils.child(reportDirectory, "summary.csv"),
		htmlPath = LrPathUtils.child(reportDirectory, "report.html"),
	}
	manifest.state = manifest.state or "preparing"
	manifest.completed_results = 0
	local ok, err = writeJsonAtomic(self.manifestPath, manifest)
	if not ok then return nil, err end
	local jsonlOk, jsonlErr = writeAll(self.jsonlPath, "", "w")
	if not jsonlOk then return nil, jsonlErr end
	return self
end

function Report.validateRuntime()
	local requirements = {
		{ LrTasks, "LrTasks", "pcall" },
		{ LrFileUtils, "LrFileUtils", "exists" },
		{ LrFileUtils, "LrFileUtils", "createAllDirectories" },
		{ LrFileUtils, "LrFileUtils", "copy" },
		{ LrFileUtils, "LrFileUtils", "delete" },
		{ LrPathUtils, "LrPathUtils", "child" },
		{ LrDate, "LrDate", "currentTime" },
	}
	for _, requirement in ipairs(requirements) do
		local _, err = sdkFunction(requirement[1], requirement[2], requirement[3])
		if err then return false, err end
	end
	if type(io) ~= "table" or type(io.open) ~= "function" then return false, "io.open is unavailable in this Lightroom runtime" end
	if type(JSON) ~= "table" or type(JSON.encode) ~= "function" or type(JSON.encode_pretty) ~= "function" then
		return false, "The benchmark JSON encoder is unavailable"
	end
	return true
end

function Report.updateManifest(self, updates)
	for key, value in pairs(updates or {}) do self.manifest[key] = value end
	self.manifest.completed_results = #self.results
	return writeJsonAtomic(self.manifestPath, self.manifest)
end

function Report.append(self, result)
	if type(self) ~= "table" or type(self.results) ~= "table" then return false, "Benchmark report is not initialized" end
	if type(result) ~= "table" then return false, "Benchmark result is not an object" end
	self.appendSequence = (self.appendSequence or 0) + 1
	if result.photo_id == nil or tostring(result.photo_id) == "" then
		result.photo_id = "missing-photo-id-" .. tostring(self.appendSequence)
		result.warning = result.warning or "The local model response omitted the benchmark photo ID"
	end
	if type(result.timing) ~= "table" then result.timing = {} end
	result.timing = roundTimingValues(result.timing)
	if result.proxy ~= nil and type(result.proxy) ~= "table" then result.proxy = nil end
	if result.status == nil then result.status = result.error and "failed" or "succeeded" end
	table.insert(self.results, result)
	local file, err = io.open(self.jsonlPath, "a")
	if not file then
		table.remove(self.results)
		return false, err
	end
	local callOk, encoded = protectedCall(function() return JSON:encode(result) end)
	if not callOk or type(encoded) ~= "string" then
		file:close()
		table.remove(self.results)
		return false, "Could not encode benchmark result: " .. tostring(encoded)
	end
	local writeOk, writeError = protectedCall(function()
		file:write(encoded, "\n")
		file:flush()
		file:close()
	end)
	if not writeOk then
		protectedCall(function() file:close() end)
		table.remove(self.results)
		return false, tostring(writeError)
	end
	self.manifest.completed_results = #self.results
	return true
end

local function writeComparison(self)
	local rows = {
		table.concat({
			"photo_id", "source_photo_id", "filename", "provider", "model", "benchmark_variant", "requested_speculation_mode", "effective_speculation_mode", "vision_input_present", "speculation_active_for_vision_request", "verification_status", "fallback_reason", "failure_stage", "failure_category", "failure_reason", "draft_depth", "lmstudio_sdk_version", "load_context_length", "draft_model_requested", "draft_model_used", "speculation_configuration", "status", "warmup",
			"keywords", "title", "caption", "alt_text", "error", "warning",
			"retry_count", "input_tokens", "output_tokens", "total_ms",
			"inference_ms", "tokens_per_second", "total_draft_tokens", "accepted_draft_tokens", "rejected_draft_tokens", "ignored_draft_tokens", "draft_acceptance_rate", "proxy_sha256", "proxy_bytes",
			"structured_output_valid", "all_requested_fields_present", "keyword_count", "distinct_keyword_count", "keyword_limit_compliant", "duplicate_keyword_count", "forbidden_placeholder_count", "caption_word_count", "alt_text_word_count", "caption_alt_text_lexical_overlap", "caption_alt_text_excessive_overlap",
		}, ","),
	}
	for _, result in ipairs(self.results) do
		local timing = result.timing or {}
		local proxy = result.proxy or {}
		local inference = result.inference or {}
		local contract = result.contract_metrics or {}
		local keywords = table.concat(flattenKeywords(result.keywords), "; ")
		local values = {
			result.photo_id, result.source_photo_id, result.filename, result.provider, result.model,
			result.benchmark_variant, inference.requested_speculation_mode or result.speculation_mode,
			inference.effective_speculation_mode, inference.vision_input_present,
			inference.speculation_active_for_vision_request, inference.verification_status,
			inference.fallback_reason, inference.failure_stage,
			inference.failure_category, inference.failure_reason, inference.draft_depth,
			inference.lmstudio_sdk_version, (inference.load_config or {}).context_length,
			result.draft_model_requested, inference.used_draft_model, inference.speculation_configuration, result.status,
			result.warmup == true, keywords, result.title, result.caption, result.alt_text,
			result.error, result.warning, result.retry_count, result.input_tokens,
			result.output_tokens, timing.benchmark_item_total_ms,
			timing.inference_ms, timing.tokens_per_second, inference.total_draft_tokens,
			inference.accepted_draft_tokens, inference.rejected_draft_tokens,
			inference.ignored_draft_tokens, inference.draft_acceptance_rate,
			proxy.sha256, proxy.byte_count,
			contract.structured_output_valid, contract.all_requested_fields_present,
			contract.keyword_count, contract.distinct_keyword_count,
			contract.keyword_limit_compliant, contract.duplicate_keyword_count,
			contract.forbidden_placeholder_count, contract.caption_word_count,
			contract.alt_text_word_count, contract.caption_alt_text_lexical_overlap,
			contract.caption_alt_text_excessive_overlap,
		}
		local encoded = {}
		for i, value in ipairs(values) do encoded[i] = csvCell(value) end
		table.insert(rows, table.concat(encoded, ","))
	end
	return writeAll(self.comparisonPath, table.concat(rows, "\n") .. "\n", "w")
end

local function summarize(self)
	local groups = {}
	for _, result in ipairs(self.results) do
		local key = modelKey(result)
		groups[key] = groups[key] or {
			provider = result.provider,
			model = result.model,
			benchmark_variant = result.benchmark_variant or "baseline",
			speculation_mode = result.speculation_mode or "baseline",
			draft_model = result.draft_model_requested,
			success = 0,
			failed = 0,
			latencies = {},
			tokensPerSecond = {},
			inputTokens = 0,
			outputTokens = 0,
			totalDraftTokens = 0,
			acceptedDraftTokens = 0,
			speculationConfiguration = nil,
			verificationStatus = nil,
			effectiveSpeculationMode = nil,
			visionInputPresent = nil,
			speculationActiveForVisionRequest = nil,
			fallbackReason = nil,
			failureStage = nil,
			failureCategory = nil,
			failureReason = nil,
			lmstudioSdkVersion = nil,
			warmup_ms = nil,
			warmup_status = nil,
			contractCount = 0,
			structuredOutputValid = 0,
			allRequestedFieldsPresent = 0,
			keywordLimitCompliant = 0,
			keywordTotal = 0,
			duplicateKeywordCount = 0,
			forbiddenPlaceholderCount = 0,
			excessiveOverlapCount = 0,
		}
		local group = groups[key]
		local inference = result.inference or {}
		group.failureStage = group.failureStage or inference.failure_stage
		group.failureCategory = group.failureCategory or inference.failure_category
		group.failureReason = group.failureReason or inference.failure_reason
		if result.warmup == true then
			group.warmup_status = result.status
			group.warmup_ms = tonumber((result.timing or {}).benchmark_item_total_ms)
		else
			local contract = result.contract_metrics or {}
			if next(contract) ~= nil then
				group.contractCount = group.contractCount + 1
				if contract.structured_output_valid == true then group.structuredOutputValid = group.structuredOutputValid + 1 end
				if contract.all_requested_fields_present == true then group.allRequestedFieldsPresent = group.allRequestedFieldsPresent + 1 end
				if contract.keyword_limit_compliant == true then group.keywordLimitCompliant = group.keywordLimitCompliant + 1 end
				if contract.caption_alt_text_excessive_overlap == true then group.excessiveOverlapCount = group.excessiveOverlapCount + 1 end
				group.keywordTotal = group.keywordTotal + (tonumber(contract.keyword_count) or 0)
				group.duplicateKeywordCount = group.duplicateKeywordCount + (tonumber(contract.duplicate_keyword_count) or 0)
				group.forbiddenPlaceholderCount = group.forbiddenPlaceholderCount + (tonumber(contract.forbidden_placeholder_count) or 0)
			end
			if result.status == "succeeded" then group.success = group.success + 1 else group.failed = group.failed + 1 end
			local timing = result.timing or {}
			if tonumber(timing.benchmark_item_total_ms) then table.insert(group.latencies, tonumber(timing.benchmark_item_total_ms)) end
			if tonumber(timing.tokens_per_second) and tonumber(timing.tokens_per_second) > 0 then
				table.insert(group.tokensPerSecond, tonumber(timing.tokens_per_second))
			end
			group.inputTokens = group.inputTokens + (tonumber(result.input_tokens) or 0)
			group.outputTokens = group.outputTokens + (tonumber(result.output_tokens) or 0)
			group.speculationConfiguration = group.speculationConfiguration or inference.speculation_configuration
			group.verificationStatus = mergeEvidence(group.verificationStatus, inference.verification_status)
			group.effectiveSpeculationMode = mergeEvidence(group.effectiveSpeculationMode, inference.effective_speculation_mode)
			group.visionInputPresent = mergeEvidence(group.visionInputPresent, inference.vision_input_present)
			group.speculationActiveForVisionRequest = mergeEvidence(group.speculationActiveForVisionRequest, inference.speculation_active_for_vision_request)
			group.fallbackReason = group.fallbackReason or inference.fallback_reason
			group.lmstudioSdkVersion = group.lmstudioSdkVersion or inference.lmstudio_sdk_version
			group.totalDraftTokens = group.totalDraftTokens + (tonumber(inference.total_draft_tokens) or 0)
			group.acceptedDraftTokens = group.acceptedDraftTokens + (tonumber(inference.accepted_draft_tokens) or 0)
		end
	end
	local summaries = {}
	for key, group in pairs(groups) do
		local total = 0
		for _, value in ipairs(group.latencies) do total = total + value end
		local count = #group.latencies
		local mean = count > 0 and total / count or nil
		local variance = 0
		if mean then
			for _, value in ipairs(group.latencies) do variance = variance + ((value - mean) ^ 2) end
			variance = variance / count
		end
		local standardDeviation = mean and math.sqrt(variance) or nil
		local photosPerMinute = total > 0 and count * 60000 / total or nil
		local photosPerHour = photosPerMinute and photosPerMinute * 60 or nil
		table.insert(summaries, {
			model_key = key,
			provider = group.provider,
			model = group.model,
			benchmark_variant = group.benchmark_variant,
			draft_model = group.draft_model,
			speculation_mode = group.speculation_mode,
			speculation_configuration = group.speculationConfiguration,
			verification_status = group.verificationStatus,
			effective_speculation_mode = group.effectiveSpeculationMode,
			vision_input_present = group.visionInputPresent,
			speculation_active_for_vision_request = group.speculationActiveForVisionRequest,
			fallback_reason = group.fallbackReason,
			failure_stage = group.failureStage,
			failure_category = group.failureCategory,
			failure_reason = group.failureReason,
			lmstudio_sdk_version = group.lmstudioSdkVersion,
			success_count = group.success,
			failure_count = group.failed,
			attempted_count = group.success + group.failed,
			success_rate = (group.success + group.failed) > 0 and roundTo(group.success / (group.success + group.failed), 3) or nil,
			total_ms = roundTo(total, 0),
			mean_ms = mean and roundTo(mean, 0) or nil,
			median_ms = count > 0 and roundTo(percentile(group.latencies, 0.5), 0) or nil,
			p90_ms = count > 0 and roundTo(percentile(group.latencies, 0.9), 0) or nil,
			p95_ms = count > 0 and roundTo(percentile(group.latencies, 0.95), 0) or nil,
			standard_deviation_ms = standardDeviation and roundTo(standardDeviation, 0) or nil,
			coefficient_of_variation = standardDeviation and mean > 0 and roundTo(standardDeviation / mean, 3) or nil,
			photos_per_minute = photosPerMinute and roundTo(photosPerMinute, 1) or nil,
			images_per_second = photosPerMinute and roundTo(photosPerMinute / 60, 1) or nil,
			seconds_per_image = mean and roundTo(mean / 1000, 1) or nil,
			photos_per_hour = photosPerHour and roundTo(photosPerHour, 1) or nil,
			projected_1000_photos_hours = photosPerHour and roundTo(1000 / photosPerHour, 1) or nil,
			projected_10000_photos_hours = photosPerHour and roundTo(10000 / photosPerHour, 1) or nil,
			structured_output_valid_rate = group.contractCount > 0 and roundTo(group.structuredOutputValid / group.contractCount, 3) or nil,
			all_requested_fields_present_rate = group.contractCount > 0 and roundTo(group.allRequestedFieldsPresent / group.contractCount, 3) or nil,
			keyword_limit_compliance_rate = group.contractCount > 0 and roundTo(group.keywordLimitCompliant / group.contractCount, 3) or nil,
			mean_keyword_count = group.contractCount > 0 and roundTo(group.keywordTotal / group.contractCount, 1) or nil,
			duplicate_keyword_count = group.duplicateKeywordCount,
			forbidden_placeholder_count = group.forbiddenPlaceholderCount,
			caption_alt_text_excessive_overlap_count = group.excessiveOverlapCount,
			median_tokens_per_second = #group.tokensPerSecond > 0 and roundTo(percentile(group.tokensPerSecond, 0.5), 1) or nil,
			input_tokens = group.inputTokens,
			output_tokens = group.outputTokens,
			draft_acceptance_rate = group.totalDraftTokens > 0 and roundTo(group.acceptedDraftTokens / group.totalDraftTokens, 3) or nil,
			total_draft_tokens = group.totalDraftTokens,
			accepted_draft_tokens = group.acceptedDraftTokens,
			warmup_ms = group.warmup_ms and roundTo(group.warmup_ms, 0) or nil,
			warmup_status = group.warmup_status,
		})
	end
	table.sort(summaries, function(a, b) return a.model_key < b.model_key end)
	return summaries
end

local function writeSummary(self, summaries)
	local headers = {
		"provider", "model", "benchmark_variant", "speculation_mode", "effective_speculation_mode", "vision_input_present", "speculation_active_for_vision_request", "draft_model", "speculation_configuration", "verification_status", "fallback_reason", "failure_stage", "failure_category", "failure_reason", "lmstudio_sdk_version", "success_count", "failure_count", "attempted_count", "success_rate", "total_ms",
		"mean_ms", "median_ms", "p90_ms", "p95_ms", "standard_deviation_ms", "coefficient_of_variation", "photos_per_minute", "images_per_second", "seconds_per_image", "photos_per_hour", "projected_1000_photos_hours", "projected_10000_photos_hours",
		"structured_output_valid_rate", "all_requested_fields_present_rate", "keyword_limit_compliance_rate", "mean_keyword_count", "duplicate_keyword_count", "forbidden_placeholder_count", "caption_alt_text_excessive_overlap_count",
		"median_tokens_per_second", "input_tokens", "output_tokens", "draft_acceptance_rate",
		"total_draft_tokens", "accepted_draft_tokens", "warmup_ms", "warmup_status",
	}
	local rows = { table.concat(headers, ",") }
	for _, summary in ipairs(summaries) do
		local encoded = {}
		for i, key in ipairs(headers) do encoded[i] = csvCell(summary[key]) end
		table.insert(rows, table.concat(encoded, ","))
	end
	return writeAll(self.summaryPath, table.concat(rows, "\n") .. "\n", "w")
end

local function writeHtml(self, summaries)
	local byPhoto = {}
	local photoOrder = {}
	for _, result in ipairs(self.results) do
		if result.warmup ~= true then
			if not byPhoto[result.photo_id] then
				byPhoto[result.photo_id] = { filename = result.filename, results = {} }
				table.insert(photoOrder, result.photo_id)
			end
			table.insert(byPhoto[result.photo_id].results, result)
		end
	end
	local parts = {
		"<!doctype html><html><head><meta charset=\"utf-8\"><title>StyleAI LLM Metadata Benchmark</title>",
		"<style>body{font:15px system-ui;margin:2rem;color:#222}table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #bbb;padding:.5rem;text-align:left;vertical-align:top}th{background:#eee}.photo{margin-top:2.5rem}.error{color:#a00}code{white-space:pre-wrap}</style></head><body>",
		"<h1>StyleAI LLM Metadata Benchmark</h1>",
		"<p>Run state: <strong>" .. html(self.manifest.state) .. "</strong>; collection: " .. html(self.manifest.collection_name) .. ".</p>",
		"<h2>Performance summary</h2><p>End-to-end photo rates are comparable across backends. Token throughput is backend-specific and is retained in the CSV. Contract checks measure format and output discipline, not visual correctness.</p><table><tr><th>Model configuration</th><th>Success</th><th>Failed</th><th>Median ms</th><th>P95 ms</th><th>Seconds/image</th><th>Photos/min</th><th>Photos/hour</th><th>Keyword limit compliance</th><th>MTP active on vision request</th><th>Draft acceptance</th></tr>",
	}
	for _, summary in ipairs(summaries) do
		table.insert(parts, "<tr><td>" .. html(summary.model_key) .. "</td><td>" .. html(summary.success_count) .. "</td><td>" .. html(summary.failure_count) .. "</td><td>" .. html(summary.median_ms) .. "</td><td>" .. html(summary.p95_ms) .. "</td><td>" .. html(summary.seconds_per_image) .. "</td><td>" .. html(summary.photos_per_minute) .. "</td><td>" .. html(summary.photos_per_hour) .. "</td><td>" .. html(summary.keyword_limit_compliance_rate) .. "</td><td>" .. html(summary.speculation_active_for_vision_request) .. "</td><td>" .. html(summary.draft_acceptance_rate) .. "</td></tr>")
	end
	table.insert(parts, "</table><h2>Outputs by photo</h2>")
	for _, photoId in ipairs(photoOrder) do
		local photo = byPhoto[photoId]
		table.insert(parts, "<section class=\"photo\"><h3>" .. html(photo.filename) .. "</h3><p><code>" .. html(photoId) .. "</code></p><table><tr><th>Model</th><th>Keywords</th><th>Title</th><th>Caption</th><th>Alt text</th><th>Timing</th></tr>")
		for _, result in ipairs(photo.results) do
			local timing = result.timing or {}
			local errorText = result.error and ("<p class=\"error\">" .. html(result.error) .. "</p>") or ""
			table.insert(parts, "<tr><td>" .. html(modelKey(result)) .. errorText .. "</td><td>" .. html(table.concat(flattenKeywords(result.keywords), "; ")) .. "</td><td>" .. html(result.title) .. "</td><td>" .. html(result.caption) .. "</td><td>" .. html(result.alt_text) .. "</td><td>" .. html(timing.benchmark_item_total_ms) .. " ms</td></tr>")
		end
		table.insert(parts, "</table></section>")
	end
	table.insert(parts, "</body></html>")
	return writeAll(self.htmlPath, table.concat(parts, "\n"), "w")
end

function Report.finalize(self, state, extraManifest)
	self.manifest.state = state
	self.manifest.completed_at = LrDate.currentTime()
	for key, value in pairs(extraManifest or {}) do self.manifest[key] = value end
	local proxiesByPhoto = {}
	local inputProxies = {}
	local proxyMismatches = {}
	for _, result in ipairs(self.results) do
		if result.warmup ~= true and type(result.proxy) == "table" then
			local prior = proxiesByPhoto[result.photo_id]
			if not prior then
				prior = {
					photo_id = result.photo_id,
					source_photo_id = result.source_photo_id,
					sha256 = result.proxy.sha256,
					byte_count = result.proxy.byte_count,
					width = result.proxy.width,
					height = result.proxy.height,
					format = result.proxy.format,
				}
				proxiesByPhoto[result.photo_id] = prior
				table.insert(inputProxies, prior)
			elseif prior.sha256 ~= result.proxy.sha256 or prior.byte_count ~= result.proxy.byte_count then
				table.insert(proxyMismatches, {
					photo_id = result.photo_id,
					expected_sha256 = prior.sha256,
					observed_sha256 = result.proxy.sha256,
				})
			end
		end
	end
	self.manifest.input_proxies = inputProxies
	self.manifest.proxy_consistency = #proxyMismatches == 0
	self.manifest.proxy_mismatches = proxyMismatches
	local summaries = summarize(self)
	local ok, err = writeComparison(self)
	if not ok then return false, err end
	ok, err = writeSummary(self, summaries)
	if not ok then return false, err end
	ok, err = writeHtml(self, summaries)
	if not ok then return false, err end
	return writeJsonAtomic(self.manifestPath, self.manifest)
end

return Report
