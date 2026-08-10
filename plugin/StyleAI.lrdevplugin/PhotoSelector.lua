PhotoSelector = {}

local function filterPhotos(photos)
	if not photos then
		return {}
	end
	local filteredPhotos = {}
	for _, photo in ipairs(photos) do
		if not photo:getRawMetadata("isVideo") then
			table.insert(filteredPhotos, photo)
		end
	end
	return filteredPhotos
end

---
-- Capture Lightroom's current target-photo selection as a new Lua array.
-- Target photos are live UI state and can change while a modal dialog is open,
-- so tasks using the "selected" scope must call this before showing any UI.
-- @return table Array of selected, non-video LrPhoto objects.
--
function PhotoSelector.snapshotSelectedPhotos()
	local catalog = LrApplication.activeCatalog()
	if not catalog then
		log:warn("Could not snapshot selected photos because no catalog is active")
		return {}
	end

	local ok, targetPhotos = LrTasks.pcall(function() return catalog:getTargetPhotos() end)
	if not ok then
		log:error("Could not snapshot selected photos: " .. tostring(targetPhotos))
		return {}
	end

	local snapshot = filterPhotos(targetPhotos)
	log:info("Captured " .. tostring(#snapshot) .. " selected photo(s) before opening task UI")
	return snapshot
end

---
-- @param scope string 'selected'|'view'|'all'|'missing'
-- @param taskOptions table|boolean|nil For scope 'missing': task options table
--   { enableEmbeddings, enableMetadata, regenerateMetadata }
--   to check backend for unprocessed photos. Or boolean for legacy (requireEmbeddings).
--   Nil/omitted = legacy true (photos not in index with embeddings).
-- @param lookupProgressScope LrProgressScope|nil For scope 'missing': optional progress for lookup (may be the task's main scope).
-- @param selectedPhotosSnapshot table|nil Immutable target-photo snapshot captured
--   before task UI is shown. Used only for the 'selected' scope.
--
function PhotoSelector.getPhotosInScope(scope, taskOptions, lookupProgressScope, selectedPhotosSnapshot)
	local catalog = LrApplication.activeCatalog()
	local photosToProcess = {}
	local status = "ok"

	if scope == "selected" then
		if type(selectedPhotosSnapshot) == "table" then
			-- Make another defensive copy so downstream stack mutation can never
			-- alter the selection snapshot retained by the task.
			photosToProcess = filterPhotos(selectedPhotosSnapshot)
		else
			photosToProcess = filterPhotos(catalog:getTargetPhotos())
		end
	elseif scope == "all" then
		photosToProcess = filterPhotos(catalog:getAllPhotos())
	elseif scope == "missing" then
		local success
		success, photosToProcess = SearchIndexAPI.getMissingPhotosFromIndex(taskOptions, lookupProgressScope)
		status = success and "ok" or "indexerror"
	elseif scope == "indexed" then
		local SearchIndexAPI = require("APISearchIndex")
		local indexedIds, err = SearchIndexAPI.getAllIndexedPhotoIds()
		if type(indexedIds) == "table" then
			if #indexedIds == 0 then
				-- Skip if there are no indexed photos
				photosToProcess = {}
			else
				local indexedSet = {}
				for _, id in ipairs(indexedIds) do indexedSet[id] = true end
				local allPhotos = catalog:getAllPhotos()
				local totalPhotos = #allPhotos
				local updateInterval = math.max(1, math.floor(totalPhotos / 50))
				for i, photo in ipairs(allPhotos) do
					local Util = require("Util")
					local pId = Util.getGlobalPhotoIdForPhoto(photo)
					if pId and indexedSet[pId] then
						table.insert(photosToProcess, photo)
					end
					if i % updateInterval == 0 then
						LrTasks.yield()
					end
				end
			end
		end
	end

	local finalPhotosToProcess = {}
	if photosToProcess and #photosToProcess > 0 then
		finalPhotosToProcess = photosToProcess
	end

	return finalPhotosToProcess, status
end

return PhotoSelector
