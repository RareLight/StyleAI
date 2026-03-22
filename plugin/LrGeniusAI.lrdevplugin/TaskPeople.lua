--[[
    People: list face clusters (persons), assign names, and show photos in Library.
]]

--- Decodiert Base64-JPEG in eine Temp-Datei (für Lazy-Load). Gibt Pfad oder nil zurück.
local function writePersonThumbnailFile(base64Thumb, personId, index)
    if not base64Thumb or base64Thumb == "" then return nil end
    local tempDir = LrPathUtils.getStandardFilePath('temp')
    local safeId = (personId and personId ~= "") and personId or ("person_" .. tostring(index))
    local safeIdClean = safeId:gsub("[^%w_-]", "_")
    local tempFile = LrPathUtils.child(tempDir, "lrgenius_person_" .. safeIdClean .. ".jpg")
    local fh = io.open(tempFile, "wb")
    if fh then
        fh:write(LrStringUtils.decodeBase64(base64Thumb))
        fh:close()
        return tempFile
    end
    return nil
end

--- Minimal 1×1-JPEG als Platzhalter, bis echte Thumbnails geladen sind (gebunden an f:picture).
local _thumbPlaceholderPath
local function ensureThumbPlaceholderPath()
    if _thumbPlaceholderPath then return _thumbPlaceholderPath end
    local tempDir = LrPathUtils.getStandardFilePath('temp')
    local path = LrPathUtils.child(tempDir, "lrgenius_person_thumb_placeholder.jpg")
    local fh = io.open(path, "wb")
    if fh then
        local tiny = "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCwAA8A/9k="
        fh:write(LrStringUtils.decodeBase64(tiny))
        fh:close()
        _thumbPlaceholderPath = path
    end
    return _thumbPlaceholderPath or ""
end

--- Namen zuerst (nach photo_count absteigend), dann Unbenannte (nach photo_count absteigend).
local function sortPersonsForDisplay(persons)
    if not persons or #persons < 2 then return end
    local function hasName(p)
        return p and type(p.name) == "string" and p.name ~= ""
    end
    local function photoCount(p)
        return tonumber(p and p.photo_count) or 0
    end
    table.sort(persons, function(a, b)
        local aNamed, bNamed = hasName(a), hasName(b)
        if aNamed ~= bNamed then
            return aNamed
        end
        return photoCount(a) > photoCount(b)
    end)
end

--- Lädt Personenliste vom Server (ohne Thumbnails; die werden im Dialog per Lazy-Load geholt).
local function loadPersonsFromServer()
    local resp, err = SearchIndexAPI.getPersons()
    if err then
        return {}, (LOC "$$$/LrGeniusAI/People/LoadError=Could not load persons. Check server connection. Try 'Cluster faces' or close and reopen.")
    end
    local persons = (resp and resp.persons) and resp.persons or {}
    sortPersonsForDisplay(persons)
    return persons, nil
end

local function showSetNameDialog(currentName)
    local resultName
    LrFunctionContext.callWithContext("showSetNameDialog", function(context)
        local f = LrView.osFactory()
        local bind = LrView.bind
        local nameProps = LrBinding.makePropertyTable(context)
        nameProps.name = currentName
        local result = LrDialogs.presentModalDialog {
            title = LOC "$$$/LrGeniusAI/People/SetNameTitle=Set name for person",
            contents = f:column {
                bind_to_object = nameProps,
                f:row {
                    f:static_text { title = LOC "$$$/LrGeniusAI/People/Name=Name:", width = 80 },
                    f:edit_field { value = bind "name", width_in_chars = 25 },
                },
            },
            actionVerb = LOC "$$$/LrGeniusAI/common/Save=Save",
            cancelVerb = LOC "$$$/LrGeniusAI/common/Cancel=Cancel",
        }
        if result == "ok" then
            resultName = nameProps.name or ""
        end
    end)
    return resultName
end

--- Zeigt den Personen-Dialog. persons ohne Thumbnails; Thumbnails per GET /faces/persons/<id>/thumbnail (Lazy-Load im Hintergrund).
-- Kein DataGrid im SDK: Raster aus f:row / f:column; je Zelle Thumbnail, Name, Foto-Anzahl, "Name setzen".
local function showPeopleDialog(ctx, persons, loadError)
    local f = LrView.osFactory()
    local bind = LrView.bind
    local share = LrView.share

    persons = persons or {}

    local props = LrBinding.makePropertyTable(ctx)
    props.persons = persons
    props.selectedPersonIndex = (#persons > 0) and 1 or 0

    if #persons > 0 then
        local ph = ensureThumbPlaceholderPath()
        for idx = 1, #persons do
            props["personThumb_" .. idx] = ph
        end
    end

    -- Muss vor Aufbau der Zellen existieren (Buttons in jeder Zelle).
    local pendingSetNamePayload = nil

    local GRID_COLS = 4
    local THUMB_SIZE = 96

    local function photoCountLabel(pc)
        pc = tonumber(pc) or 0
        local unit = (pc == 1) and (LOC "$$$/LrGeniusAI/People/Photo=photo") or (LOC "$$$/LrGeniusAI/People/Photos=photos")
        return string.format("%d %s", pc, unit)
    end

    local listScroller
    local peopleListBlock
    if #persons == 0 then
        peopleListBlock = f:static_text {
            title = loadError or LOC "$$$/LrGeniusAI/People/NoPersons=No persons yet. Run 'Cluster faces' after indexing photos with face embeddings.",
        }
        listScroller = peopleListBlock
    else
        local gridRows = {}
        for startIdx = 1, #persons, GRID_COLS do
            local rowCells = {}
            for c = 0, GRID_COLS - 1 do
                local idx = startIdx + c
                if idx <= #persons then
                    local p = persons[idx]
                    local displayName = (p.name and p.name ~= "") and p.name or LOC "$$$/LrGeniusAI/People/Unnamed=Unnamed"
                    local thumbKey = "personThumb_" .. idx
                    local thumbView = f:picture {
                        value = bind(thumbKey),
                        width = THUMB_SIZE,
                        height = THUMB_SIZE,
                    }
                    rowCells[#rowCells + 1] = f:column {
                        spacing = 6,
                        width = share "personCell",
                        alignment = "center",
                        thumbView,
                        f:static_text {
                            title = displayName,
                            alignment = "center",
                        },
                        f:static_text {
                            title = photoCountLabel(p.photo_count),
                            size = "small",
                            alignment = "center",
                        },
                        f:push_button {
                            title = LOC "$$$/LrGeniusAI/People/SetName=Set name...",
                            action = function()
                                local person = props.persons[idx]
                                if not person or not person.person_id or person.person_id == "" then return end
                                pendingSetNamePayload = {
                                    person_id = person.person_id,
                                    currentName = person.name or "",
                                }
                                LrDialogs.stopModalWithResult(listScroller, "set_name")
                            end,
                        },
                        f:radio_button {
                            value = bind "selectedPersonIndex",
                            checked_value = idx,
                            title = LOC "$$$/LrGeniusAI/People/SelectForLibrary=Library",
                        },
                    }
                else
                    rowCells[#rowCells + 1] = f:spacer { width = share "personCell" }
                end
            end
            gridRows[#gridRows + 1] = f:row {
                spacing = 14,
                alignment = "center",
                unpack(rowCells),
            }
        end

        listScroller = f:scrolled_view {
            horizontal_scroller = false,
            vertical_scroller = true,
            width = 540,
            height = 320,
            alignment = "center",
            f:column {
                spacing = 12,
                unpack(gridRows),
            },
        }

        peopleListBlock = f:group_box {
            title = LOC "$$$/LrGeniusAI/People/TableGroupTitle=People",
            fill_horizontal = 1,
            listScroller,
        }
    end

    local contents = f:column {
        bind_to_object = props,
        spacing = f:control_spacing(),
        fill_horizontal = 1,

        f:row {
            f:push_button {
                title = LOC "$$$/LrGeniusAI/People/ClusterFaces=Cluster faces",
                action = function()
                    local clusterResp, err = SearchIndexAPI.clusterFaces()
                    if err then
                        ErrorHandler.handleError(LOC "$$$/LrGeniusAI/People/ClusterError=Face clustering failed", err)
                        return
                    end
                    LrDialogs.message(LOC "$$$/LrGeniusAI/People/ClusterDone=Clustering done",
                        LOC("$$$/LrGeniusAI/People/ClusterSummaryAndReopen=^1 persons, ^2 faces. Close this dialog and open 'People...' again to see the updated list.", tostring(clusterResp and clusterResp.person_count or 0), tostring(clusterResp and clusterResp.face_count or 0)))
                end,
            },
        },

        f:static_text {
            title = LOC "$$$/LrGeniusAI/People/ListTitle=Choose Library on a tile, then Show in Library; or Set name on each tile:",
            font = "<system/bold>",
        },

        peopleListBlock,
    }

    local thumbLoaderDone = false
    if #persons > 0 then
        LrTasks.startAsyncTask(function()
            for idx = 1, #persons do
                if thumbLoaderDone then return end
                local p = persons[idx]
                if p and p.person_id and p.person_id ~= "" then
                    local resp = SearchIndexAPI.getPersonThumbnail(p.person_id)
                    if not thumbLoaderDone and resp and type(resp.thumbnail) == "string" and resp.thumbnail ~= "" then
                        local path = writePersonThumbnailFile(resp.thumbnail, p.person_id, idx)
                        if path and not thumbLoaderDone then
                            props["personThumb_" .. idx] = path
                        end
                    end
                end
                LrTasks.yield()
            end
        end)
    end

    local dialogResult = LrDialogs.presentModalDialog {
        title = LOC "$$$/LrGeniusAI/People/WindowTitle=People",
        contents = contents,
        actionVerb = LOC "$$$/LrGeniusAI/common/Close=Close",
        otherVerb = LOC "$$$/LrGeniusAI/People/ShowInLibrary=Show in Library",
    }
    thumbLoaderDone = true
    if dialogResult == "set_name" and pendingSetNamePayload then
        local payload = pendingSetNamePayload
        pendingSetNamePayload = nil
        return "set_name", nil, payload
    end
    -- Wenn User "Show in Library" (otherVerb) geklickt hat: Auswahl aus props auslesen
    local pendingShowInLibrary = nil
    if dialogResult == "other" then
        local idx = props.selectedPersonIndex
        if type(props.persons) == "table" and idx and idx >= 1 and idx <= #props.persons then
            local person = props.persons[idx]
            if type(person) == "table" and person.person_id and person.person_id ~= "" then
                local name = (type(person.name) == "string" and person.name ~= "") and person.name or nil
                pendingShowInLibrary = { person_id = person.person_id, person_name = name }
            end
        end
    end
    return dialogResult, pendingShowInLibrary, nil
end

--- Führt "Show in Library" aus: Fotos laden, Collection anlegen, Ansicht wechseln. Läuft im Async-Task (Yielding erlaubt).
local function doShowInLibrary(person_id, person_name)
    local resp, err = SearchIndexAPI.getPhotosForPerson(person_id)
    if err or type(resp) ~= "table" then
        ErrorHandler.handleError(LOC "$$$/LrGeniusAI/People/GetPhotosError=Could not get photos for person", err or "No data")
        return
    end
    local photoIds = type(resp.photo_ids) == "table" and resp.photo_ids or (type(resp.photo_uuids) == "table" and resp.photo_uuids or {})
    if #photoIds == 0 then
        LrDialogs.message(LOC "$$$/LrGeniusAI/People/NoPhotos=No photos", LOC "$$$/LrGeniusAI/People/NoPhotosForPerson=No photos found for this person.")
        return
    end
    local catalog = LrApplication.activeCatalog()
    local photos = SearchIndexAPI.findPhotosByPhotoIds(photoIds)
    if #photos == 0 then
        LrDialogs.message(LOC "$$$/LrGeniusAI/People/NoPhotosInCatalog=Not in catalog", LOC "$$$/LrGeniusAI/People/PersonPhotosNotInCatalog=Photos for this person are not in the current catalog.")
        return
    end
    local personDisplayName = (type(person_name) == "string" and person_name ~= "") and person_name or (LOC "$$$/LrGeniusAI/People/Unnamed=Unnamed")
    local collectionName = string.format("%s @ %s", tostring(personDisplayName), LrDate.timeToW3CDate(LrDate.currentTime()))

    local collectionSet, collection
    catalog:withWriteAccessDo("Create Collection Set", function()
        collectionSet = catalog:createCollectionSet(LOC "$$$/LrGeniusAI/People/CollectionSetName=People", nil, true)
    end, Defaults.catalogWriteAccessOptions)
    if not collectionSet then
        ErrorHandler.handleError(LOC "$$$/LrGeniusAI/People/CollectionSetError=Collection set error", LOC "$$$/LrGeniusAI/People/CollectionSetErrorMessage=Could not create collection set for people.")
        return
    end

    catalog:withWriteAccessDo("Create Collection", function()
        collection = catalog:createCollection(collectionName, collectionSet, false)
    end, Defaults.catalogWriteAccessOptions)
    if not collection then
        ErrorHandler.handleError(LOC "$$$/LrGeniusAI/People/CollectionError=Collection error", LOC "$$$/LrGeniusAI/People/CollectionErrorMessage=Could not create collection for this person.")
        return
    end

    catalog:withWriteAccessDo("Add Photos to Collection", function()
        collection:addPhotos(photos)
    end, Defaults.catalogWriteAccessOptions)

    catalog:setActiveSources({collection})
    LrApplicationView.gridView()
    LrDialogs.message(LOC "$$$/LrGeniusAI/People/Done=Done", LOC("$$$/LrGeniusAI/People/CollectionCreated=^1 photo(s) added to collection \"^2\".", tostring(#photos), collectionName))
end

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext("TaskPeople", function(context)
        if not Util.waitForServerDialog() then return end
        local persons, loadError = loadPersonsFromServer()
        while true do
            local ok, r, pending, setPayload = LrTasks.pcall(showPeopleDialog, context, persons, loadError)
            if not ok then
                ErrorHandler.handleError(LOC "$$$/LrGeniusAI/People/ErrorTitle=Error", tostring(r))
                return
            end
            if r == "other" and pending and type(pending) == "table" and pending.person_id then
                doShowInLibrary(pending.person_id, pending.person_name)
                return
            end
            if r == "set_name" and type(setPayload) == "table" and setPayload.person_id then
                local newName = showSetNameDialog(setPayload.currentName)
                if newName ~= nil then
                    local nameOk, nameErr = SearchIndexAPI.setPersonName(setPayload.person_id, newName)
                    if not nameOk then
                        ErrorHandler.handleError(LOC "$$$/LrGeniusAI/People/SetNameError=Could not set name", nameErr)
                    end
                end
                LrTasks.yield()
                persons, loadError = loadPersonsFromServer()
                -- Reopen main People dialog with fresh list (name line includes server data).
            else
                break
            end
        end
    end)
end)
