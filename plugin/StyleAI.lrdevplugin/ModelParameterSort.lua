local ModelParameterSort = {}

local UNIT_MULTIPLIERS = {
	K = 1e3,
	M = 1e6,
	B = 1e9,
	T = 1e12,
}

local function scaledParameterCount(numberText, unit)
	local number = tonumber(numberText)
	local multiplier = UNIT_MULTIPLIERS[string.upper(tostring(unit or ""))]
	if not number or not multiplier then return nil end
	return number * multiplier
end

local function simpleParameterCount(value)
	if value == nil then return nil end
	local text = tostring(value)
	if string.find(string.lower(text), "x", 1, true) then return nil end
	local numberText, unit = string.match(text, "(%d+%.?%d*)%s*([TtBbMmKk])")
	return scaledParameterCount(numberText, unit)
end

local function conventionalNameCount(value)
	if value == nil then return nil end
	local text = tostring(value)
	local best = nil
	local searchFrom = 1
	while true do
		local startAt, endAt, numberText, unit = string.find(text, "(%d+%.?%d*)%s*([TtBbMmKk])", searchFrom)
		if not startAt then break end
		local preceding = startAt > 1 and string.lower(string.sub(text, startAt - 1, startAt - 1)) or ""
		-- A4B/E4B and 8x7B describe active experts or an expert layout,
		-- not the conventional total parameter class used to order models.
		if preceding ~= "a" and preceding ~= "e" and preceding ~= "x" then
			local count = scaledParameterCount(numberText, unit)
			if count and (not best or count > best) then best = count end
		end
		searchFrom = endAt + 1
	end
	return best
end

local function compoundParameterCount(value)
	if value == nil then return nil end
	local experts, numberText, unit = string.match(tostring(value), "(%d+)%s*[xX]%s*(%d+%.?%d*)%s*([TtBbMmKk])")
	local perExpert = scaledParameterCount(numberText, unit)
	if not experts or not perExpert then return nil end
	return tonumber(experts) * perExpert
end

function ModelParameterSort.parameterCount(model)
	if type(model) ~= "table" then return nil end
	local details = type(model.details) == "table" and model.details or {}
	local numericCount = tonumber(details.parameter_count or details.parameters)
	if numericCount and numericCount > 0 then return numericCount end

	local count = simpleParameterCount(details.params_string)
	if count then return count end

	local names = {}
	for _, value in pairs({
		displayName = details.display_name,
		model = model.model,
		title = model.title,
		key = model.key,
	}) do
		if value ~= nil then table.insert(names, value) end
	end
	local nameCount = nil
	for _, value in ipairs(names) do
		count = conventionalNameCount(value)
		if count and (not nameCount or count > nameCount) then nameCount = count end
	end
	if nameCount then return nameCount end

	-- Some model families expose only an expert layout (for example 8x7B).
	-- Its nominal product is a better final ordering hint than treating it as unknown.
	return compoundParameterCount(details.params_string)
end

function ModelParameterSort.descending(models)
	table.sort(models, function(a, b)
		local aCount = ModelParameterSort.parameterCount(a)
		local bCount = ModelParameterSort.parameterCount(b)
		if aCount ~= bCount then
			if aCount == nil then return false end
			if bCount == nil then return true end
			return aCount > bCount
		end
		local aTitle = string.lower(tostring((type(a) == "table" and a.title) or ""))
		local bTitle = string.lower(tostring((type(b) == "table" and b.title) or ""))
		if aTitle ~= bTitle then return aTitle < bTitle end
		return tostring((type(a) == "table" and a.key) or "") < tostring((type(b) == "table" and b.key) or "")
	end)
	return models
end

return ModelParameterSort
