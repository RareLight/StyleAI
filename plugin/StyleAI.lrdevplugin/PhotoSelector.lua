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

	local ok, targetPhotos = pcall(function() return catalog:getTargetPhotos() end)
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
--   { enableEmbeddings, enableMetadata, enableFaces, regenerateMetadata }
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
	elseif scope == "view" then
		local sources = catalog:getActiveSources()
		if not sources or #sources == 0 then
			return nil, "No active source"
		end
		local addedPhotos = {}

		for _, source in ipairs(sources) do
			if type(source) == "string" then
				if source == "kAllPhotos" then
					photosToProcess = filterPhotos(catalog:getAllPhotos())
					break -- No need to process other sources
				elseif source == "kPreviousImport" then
					local previousImport = filterPhotos(catalog:getPreviousImport())
					if previousImport then
						for _, photo in ipairs(previousImport) do
							local photoId = photo:getRawMetadata("uuid")
							if not addedPhotos[photoId] then
								table.insert(photosToProcess, photo)
								addedPhotos[photoId] = true
							end
						end
					end
				else
					log:warn("Unsupported string source type: " .. source)
				end
			elseif
				source
				and (
					source:type() == "LrCollection"
					or source:type() == "LrFolder"
					or source:type() == "LrPublishedCollection"
				)
			then
				local includeChildren = false
				if source:type() == "LrFolder" then
					includeChildren = true
				end
				local photos = filterPhotos(source:getPhotos(includeChildren))
				for _, photo in ipairs(photos) do
					local photoId = photo:getRawMetadata("uuid")
					if not addedPhotos[photoId] then
						table.insert(photosToProcess, photo)
						addedPhotos[photoId] = true
					end
				end
			elseif source and (source:type() == "LrCollectionSet" or source:type() == "LrPublishedCollectionSet") then
				log:warn("Collection sets are not supported as a source; select individual collections instead.")
				LrDialogs.message(
					LOC("$$$/StyleAI/PhotoSelector/CollectionSetNotSupportedTitle=Collection Sets Not Supported"),
					LOC(
						"$$$/StyleAI/PhotoSelector/CollectionSetNotSupportedMessage=Collection sets cannot be used as a source. Please select individual collections instead."
					),
					"warning"
				)
			else
				if source and source.type then
					log:warn("Unsupported source type for grouping similar photos: " .. source:type())
				else
					log:warn("Unsupported source type for grouping similar photos: " .. type(source))
				end
			end
		end
		if #photosToProcess == 0 then
			return nil, "Invalid view"
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
