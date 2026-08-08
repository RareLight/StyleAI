-- RenderingStateCapability.lua
-- Pure helpers for the Phase 0 Lightroom rendering-state capability spike.

local RenderingStateCapability = {}

RenderingStateCapability.SCHEMA_VERSION = "styleai-rendering-sdk-spike-v1"
RenderingStateCapability.PROFILE_KEYS = { "CameraProfile", "CameraProfileRaw", "Look" }
RenderingStateCapability.HDR_KEYS = { "HDREditMode", "HDR" }

local function deepCopy(value, seen)
	if type(value) ~= "table" then
		return value
	end
	seen = seen or {}
	if seen[value] then
		return seen[value]
	end
	local copy = {}
	seen[value] = copy
	for key, child in pairs(value) do
		copy[deepCopy(key, seen)] = deepCopy(child, seen)
	end
	return copy
end

local function deepEqual(left, right, seen)
	if type(left) ~= type(right) then
		return false
	end
	if type(left) ~= "table" then
		return left == right
	end
	seen = seen or {}
	if seen[left] == right then
		return true
	end
	seen[left] = right
	for key, value in pairs(left) do
		if not deepEqual(value, right[key], seen) then
			return false
		end
	end
	for key in pairs(right) do
		if left[key] == nil then
			return false
		end
	end
	return true
end

local function copyPresent(settings, keys)
	local result = {}
	for _, key in ipairs(keys) do
		if settings[key] ~= nil then
			result[key] = deepCopy(settings[key])
		end
	end
	return result
end

function RenderingStateCapability.deepCopy(value)
	return deepCopy(value)
end

function RenderingStateCapability.deepEqual(left, right)
	return deepEqual(left, right)
end

function RenderingStateCapability.captureRenderingState(settings)
	settings = type(settings) == "table" and settings or {}
	local hdrLike = {}
	for key, value in pairs(settings) do
		if type(key) == "string" and string.find(string.lower(key), "hdr", 1, true) then
			hdrLike[key] = deepCopy(value)
		end
	end
	return {
		profile = copyPresent(settings, RenderingStateCapability.PROFILE_KEYS),
		hdr = copyPresent(settings, RenderingStateCapability.HDR_KEYS),
		hdr_like_observed = hdrLike,
	}
end

function RenderingStateCapability.profileDisplayName(renderingState)
	local profile = renderingState and renderingState.profile or {}
	if type(profile.Look) == "table" and type(profile.Look.Name) == "string" and profile.Look.Name ~= "" then
		return profile.Look.Name
	end
	if type(profile.CameraProfile) == "string" and profile.CameraProfile ~= "" then
		return profile.CameraProfile
	end
	if type(profile.CameraProfileRaw) == "string" and profile.CameraProfileRaw ~= "" then
		return profile.CameraProfileRaw
	end
	return nil
end

function RenderingStateCapability.isHdr(renderingState)
	local hdr = renderingState and renderingState.hdr or {}
	return hdr.HDREditMode == true or hdr.HDREditMode == 1 or hdr.HDR == true or hdr.HDR == 1
end

function RenderingStateCapability.cameraCompatibilityKey(cameraMake, cameraModel)
	local make = type(cameraMake) == "string" and string.match(cameraMake, "^%s*(.-)%s*$") or ""
	local model = type(cameraModel) == "string" and string.match(cameraModel, "^%s*(.-)%s*$") or ""
	if make == "" or model == "" then
		return nil
	end
	make = string.lower(make)
	model = string.lower(model)
	return tostring(#make) .. ":" .. make .. "|" .. tostring(#model) .. ":" .. model
end

function RenderingStateCapability.buildCandidateSettings(baseline, targetRenderingState, keys)
	local candidate = deepCopy(baseline or {})
	local profile = targetRenderingState and targetRenderingState.profile or {}
	local hdr = targetRenderingState and targetRenderingState.hdr or {}
	for _, key in ipairs(keys or {}) do
		local value = profile[key]
		if value == nil then
			value = hdr[key]
		end
		if value ~= nil then
			candidate[key] = deepCopy(value)
		end
	end
	return candidate
end

function RenderingStateCapability.keysMatch(renderingState, targetRenderingState, keys)
	local actualProfile = renderingState and renderingState.profile or {}
	local actualHdr = renderingState and renderingState.hdr or {}
	local targetProfile = targetRenderingState and targetRenderingState.profile or {}
	local targetHdr = targetRenderingState and targetRenderingState.hdr or {}
	for _, key in ipairs(keys or {}) do
		local actual = actualProfile[key]
		if actual == nil then
			actual = actualHdr[key]
		end
		local target = targetProfile[key]
		if target == nil then
			target = targetHdr[key]
		end
		if target == nil or not deepEqual(actual, target) then
			return false
		end
	end
	return true
end

return RenderingStateCapability
