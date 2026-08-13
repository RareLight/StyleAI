-- TaskRenderingStateCapabilitySpike.lua
-- Destructive-on-disposable-copies Phase 0 probe for profile/HDR SDK behavior.

local LrApplication = import("LrApplication")
local LrDialogs = import("LrDialogs")
local LrFunctionContext = import("LrFunctionContext")
local LrPathUtils = import("LrPathUtils")
local LrTasks = import("LrTasks")

require("JSON")
local Defaults = require("Defaults")
local Capability = require("RenderingStateCapability")

local MAX_PHOTOS = 8

local function raw(photo, key)
	local ok, value = LrTasks.pcall(function()
		return photo:getRawMetadata(key)
	end)
	return ok and value or nil
end

local function formatted(photo, key)
	local ok, value = LrTasks.pcall(function()
		return photo:getFormattedMetadata(key)
	end)
	return ok and value or nil
end

local function readSettings(photo)
	local ok, value = LrTasks.pcall(function()
		return photo:getDevelopSettings()
	end)
	if not ok or type(value) ~= "table" then
		return nil, tostring(value)
	end
	return value, nil
end

local function applySettings(catalog, photo, settings, label)
	local ok, err = LrTasks.pcall(function()
		catalog:withWriteAccessDo(label, function()
			photo:applyDevelopSettings(settings)
		end, Defaults.catalogWriteAccessOptions)
	end)
	if ok then
		return true, nil
	end
	return false, tostring(err)
end

local function photoRecord(photo, index)
	local settings, err = readSettings(photo)
	if not settings then
		return nil, err
	end
	local cameraMake = formatted(photo, "cameraMake") or raw(photo, "cameraMake")
	local cameraModel = formatted(photo, "cameraModel") or raw(photo, "cameraModel")
	return {
		index = index,
		file_name = formatted(photo, "fileName"),
		is_virtual_copy = raw(photo, "isVirtualCopy") == true,
		camera_make = cameraMake,
		camera_model = cameraModel,
		compatibility_key = Capability.cameraCompatibilityKey(cameraMake, cameraModel),
		settings = Capability.deepCopy(settings),
		rendering_state = Capability.captureRenderingState(settings),
		profile_display_name = Capability.profileDisplayName(Capability.captureRenderingState(settings)),
		is_hdr = Capability.isHdr(Capability.captureRenderingState(settings)),
	}, nil
end

local function presentKeys(state, allowedKeys)
	local keys = {}
	for _, key in ipairs(allowedKeys) do
		if state.profile[key] ~= nil or state.hdr[key] ~= nil then
			table.insert(keys, key)
		end
	end
	return keys
end

local function uniqueRenderingStateCount(records, category)
	local unique = {}
	for _, record in ipairs(records) do
		local candidate = record.rendering_state[category]
		local found = false
		for _, existing in ipairs(unique) do
			if Capability.deepEqual(existing, candidate) then
				found = true
				break
			end
		end
		if not found then
			table.insert(unique, candidate)
		end
	end
	return #unique
end

local function runScenario(catalog, source, target, keys, kind)
	local candidate = Capability.buildCandidateSettings(source.settings, target.rendering_state, keys)
	local applied, applyError = applySettings(catalog, source.photo, candidate, "StyleAI SDK spike: " .. kind)
	local afterSettings, readError = readSettings(source.photo)
	local afterState = afterSettings and Capability.captureRenderingState(afterSettings) or nil
	local preservedOtherCategory = false
	if afterState then
		if kind == "profile_key" or kind == "profile_bundle" then
			preservedOtherCategory = Capability.deepEqual(afterState.hdr, source.rendering_state.hdr)
		elseif kind == "hdr_key" then
			preservedOtherCategory = Capability.deepEqual(afterState.profile, source.rendering_state.profile)
		else
			preservedOtherCategory = true
		end
	end
	local matched = applied
		and afterState
		and Capability.keysMatch(afterState, target.rendering_state, keys)
		and preservedOtherCategory
		or false

	local restored, restoreError = applySettings(
		catalog,
		source.photo,
		source.settings,
		"StyleAI SDK spike: restore disposable copy"
	)
	local restoredSettings, restoreReadError = readSettings(source.photo)
	local restoredState = restoredSettings and Capability.captureRenderingState(restoredSettings) or nil
	local restoreVerified = restored
		and restoredState ~= nil
		and Capability.deepEqual(restoredState, source.rendering_state)

	return {
		kind = kind,
		source_index = source.index,
		target_index = target.index,
		keys = keys,
		applied = applied,
		apply_error = applyError,
		read_error = readError,
		matched = matched,
		preserved_other_category = preservedOtherCategory,
		readback = afterState,
		restored = restored,
		restore_error = restoreError,
		restore_read_error = restoreReadError,
		restore_verified = restoreVerified,
	}
end

local function writeReport(report)
	local desktop = LrPathUtils.getStandardFilePath("desktop")
	local name = "StyleAI-rendering-sdk-spike-" .. os.date("%Y%m%d-%H%M%S") .. ".json"
	local path = LrPathUtils.child(desktop, name)
	local file, err = io.open(path, "w")
	if not file then
		return nil, err
	end
	file:write(JSON:encode(report))
	file:close()
	return path, nil
end

local function runSpike()
	local catalog = LrApplication.activeCatalog()
	local versionOk, versionOrError = LrTasks.pcall(function()
		return LrApplication.versionString()
	end)
	local photos = catalog:getTargetPhotos() or {}
	if #photos < 2 or #photos > MAX_PHOTOS then
		LrDialogs.message(
			LOC("$$$/StyleAI/RenderingSpike/InvalidSelectionTitle=Rendering-State Spike Not Started"),
			LOC("$$$/StyleAI/RenderingSpike/InvalidSelection=Select between 2 and 8 disposable virtual copies first."),
			"warning"
		)
		return
	end

	for _, photo in ipairs(photos) do
		if raw(photo, "isVirtualCopy") ~= true then
			LrDialogs.message(
				LOC("$$$/StyleAI/RenderingSpike/InvalidSelectionTitle=Rendering-State Spike Not Started"),
				LOC("$$$/StyleAI/RenderingSpike/VirtualCopiesOnly=Every selected photo must be a disposable virtual copy. Originals are never accepted by this test."),
				"warning"
			)
			return
		end
	end

	local confirm = LrDialogs.confirm(
		LOC("$$$/StyleAI/RenderingSpike/ConfirmTitle=Run Rendering-State SDK Spike?"),
		LOC("$$$/StyleAI/RenderingSpike/Confirm=StyleAI will temporarily apply profile and HDR representations copied exactly from the selected virtual copies, verify Lightroom readback, and restore each copy. Use only disposable virtual copies prepared according to the Developer Guide."),
		LOC("$$$/StyleAI/RenderingSpike/Run=Run Spike"),
		LOC("$$$/StyleAI/common/Cancel=Cancel")
	)
	if confirm == "cancel" then
		return
	end

	local report = {
		schema_version = Capability.SCHEMA_VERSION,
		generated_at = os.date("!%Y-%m-%dT%H:%M:%SZ"),
		lightroom_version = versionOk and versionOrError or nil,
		lightroom_version_error = nil,
		plugin_sdk_target = 14.0,
		profile_enumeration = {
			status = "not_available_through_documented_plugin_api",
			fallback = "catalog_local_observed_profiles_only",
		},
		photos = {},
		tests = {},
		skipped_incompatible_pairs = {},
		manual_gates = {
			built_in_profile_identified = false,
			camera_matching_profile_identified = false,
			custom_profile_identified = false,
			unavailable_custom_profile_verified = false,
			undo_redo_verified = false,
		},
	}
	if not versionOk then
		report.lightroom_version_error = tostring(versionOrError)
	end

	local records = {}
	for index, photo in ipairs(photos) do
		local record, err = photoRecord(photo, index)
		if not record then
			error("Could not inspect selected virtual copy " .. tostring(index) .. ": " .. tostring(err))
		end
		record.photo = photo
		table.insert(records, record)
		local serializable = Capability.deepCopy(record)
		serializable.photo = nil
		serializable.settings = nil
		table.insert(report.photos, serializable)
	end

	local profileStateCount = uniqueRenderingStateCount(records, "profile")
	local hdrStateCount = uniqueRenderingStateCount(records, "hdr")
	report.observed_coverage = {
		profile_state_count = profileStateCount,
		hdr_state_count = hdrStateCount,
		sufficient_for_application_tests = profileStateCount >= 2 and hdrStateCount >= 2,
	}
	if not report.observed_coverage.sufficient_for_application_tests then
		report.summary = {
			test_count = 0,
			matched_count = 0,
			restore_failure_count = 0,
			gate_passed = false,
			gate_reason = "At least two distinct observed profile states and both SDR/HDR states are required.",
			stopped_after_restore_failure = false,
		}
		local incompletePath, writeError = writeReport(report)
		if not incompletePath then
			error("Could not write rendering-state spike report: " .. tostring(writeError))
		end
		LrDialogs.message(
			LOC("$$$/StyleAI/RenderingSpike/IncompleteTitle=Rendering-State Specimens Are Incomplete"),
			LOC("$$$/StyleAI/RenderingSpike/Incomplete=The selection contains ^1 distinct profile state(s) and ^2 HDR state(s). Prepare at least two different profiles and both SDR and HDR virtual copies from the same camera, then rerun the spike. No Develop settings were changed. Report: ^3", profileStateCount, hdrStateCount, incompletePath),
			"warning"
		)
		return
	end

	local restoreBlocked = false
	for _, source in ipairs(records) do
		for _, target in ipairs(records) do
			if
				source.index ~= target.index
				and (source.compatibility_key == nil or source.compatibility_key ~= target.compatibility_key)
			then
				table.insert(report.skipped_incompatible_pairs, {
					source_index = source.index,
					target_index = target.index,
					reason = source.compatibility_key == nil and "source_camera_identity_missing"
						or target.compatibility_key == nil and "target_camera_identity_missing"
						or "camera_make_model_mismatch",
				})
			end
			if
				not restoreBlocked
				and source.index ~= target.index
				and source.compatibility_key ~= nil
				and source.compatibility_key == target.compatibility_key
			then
				local profileKeys = presentKeys(target.rendering_state, Capability.PROFILE_KEYS)
				for _, key in ipairs(profileKeys) do
					if not Capability.keysMatch(source.rendering_state, target.rendering_state, { key }) then
						local result = runScenario(catalog, source, target, { key }, "profile_key")
						table.insert(report.tests, result)
						restoreBlocked = not result.restore_verified
						if restoreBlocked then break end
					end
				end
				if
					not restoreBlocked
					and #profileKeys > 1
					and not Capability.keysMatch(source.rendering_state, target.rendering_state, profileKeys)
				then
					local result = runScenario(catalog, source, target, profileKeys, "profile_bundle")
					table.insert(report.tests, result)
					restoreBlocked = not result.restore_verified
				end

				local hdrKeys = presentKeys(target.rendering_state, Capability.HDR_KEYS)
				for _, key in ipairs(hdrKeys) do
					if
						not restoreBlocked
						and not Capability.keysMatch(source.rendering_state, target.rendering_state, { key })
					then
						local result = runScenario(catalog, source, target, { key }, "hdr_key")
						table.insert(report.tests, result)
						restoreBlocked = not result.restore_verified
					end
				end

				local combinedKeys = {}
				for _, key in ipairs(profileKeys) do table.insert(combinedKeys, key) end
				for _, key in ipairs(hdrKeys) do table.insert(combinedKeys, key) end
				if
					not restoreBlocked
					and #profileKeys > 0
					and #hdrKeys > 0
					and not Capability.keysMatch(source.rendering_state, target.rendering_state, profileKeys)
					and not Capability.keysMatch(source.rendering_state, target.rendering_state, hdrKeys)
				then
					local result = runScenario(catalog, source, target, combinedKeys, "profile_and_hdr")
					table.insert(report.tests, result)
					restoreBlocked = not result.restore_verified
				end
			end
		end
	end

	local passCount = 0
	local restoreFailureCount = 0
	for _, result in ipairs(report.tests) do
		if result.matched then passCount = passCount + 1 end
		if not result.restore_verified then restoreFailureCount = restoreFailureCount + 1 end
	end
	report.summary = {
		test_count = #report.tests,
		matched_count = passCount,
		restore_failure_count = restoreFailureCount,
		gate_passed = false,
		gate_reason = "Manual profile classification and Undo/Redo verification are still required.",
		stopped_after_restore_failure = restoreBlocked,
	}

	local reportPath, writeError = writeReport(report)
	if not reportPath then
		error("Could not write rendering-state spike report: " .. tostring(writeError))
	end
	log:info("Rendering-state SDK spike report written to " .. reportPath)
	LrDialogs.message(
		LOC("$$$/StyleAI/RenderingSpike/CompleteTitle=Rendering-State Spike Complete"),
		LOC("$$$/StyleAI/RenderingSpike/Complete=Completed ^1 application/readback tests; ^2 matched and ^3 restores failed. The production gate remains closed until the report is annotated with profile types and the manual Undo/Redo checks pass. Report: ^4", #report.tests, passCount, restoreFailureCount, reportPath),
		restoreFailureCount == 0 and "info" or "critical"
	)
end

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("renderingStateCapabilitySpike", function()
		local ok, err = LrTasks.pcall(runSpike)
		if not ok then
			log:error("Rendering-state SDK spike failed: " .. tostring(err))
			ErrorHandler.handleError(
				LOC("$$$/StyleAI/RenderingSpike/FailedTitle=Rendering-State Spike Failed"),
				tostring(err)
			)
		end
	end)
end)
