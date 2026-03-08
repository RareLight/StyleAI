local function showCullDialog(ctx)
    local f = LrView.osFactory()
    local bind = LrView.bind
    local share = LrView.share

    local props = LrBinding.makePropertyTable(ctx)
    props.scope = prefs.cullScope or "selected"
    props.timeDeltaSeconds = prefs.cullTimeDeltaSeconds or 2
    props.createDuplicatesCollection = prefs.cullCreateDuplicatesCollection ~= false

    local contents = f:column {
        bind_to_object = props,
        spacing = f:control_spacing(),
        f:group_box {
            title = LOC "$$$/LrGeniusAI/CullTask/ScopeGroup=Scope",
            fill_horizontal = 1,
            f:row {
                f:static_text {
                    title = LOC "$$$/LrGeniusAI/CullTask/ScopeLabel=Apply to:",
                    width = share 'labelWidth',
                },
                f:popup_menu {
                    value = bind 'scope',
                    items = {
                        { title = LOC "$$$/LrGeniusAI/common/ScopeSelected=Selected photos only", value = 'selected' },
                        { title = LOC "$$$/LrGeniusAI/common/ScopeView=Current view", value = 'view' },
                    },
                    width = 260,
                },
            },
        },
        f:group_box {
            title = LOC "$$$/LrGeniusAI/CullTask/OptionsGroup=Options",
            fill_horizontal = 1,
            f:row {
                f:static_text {
                    title = LOC "$$$/LrGeniusAI/CullTask/TimeDeltaLabel=Burst time window (seconds):",
                    width = share 'labelWidth',
                },
                f:combo_box {
                    value = bind 'timeDeltaSeconds',
                    items = {
                        { title = "1", value = 1 },
                        { title = "2", value = 2 },
                        { title = "3", value = 3 },
                        { title = "5", value = 5 },
                    },
                    width = 120,
                },
            },
            f:row {
                f:checkbox {
                    value = bind 'createDuplicatesCollection',
                },
                f:static_text {
                    title = LOC "$$$/LrGeniusAI/CullTask/CreateDuplicates=Create 'Duplicates / Near Duplicates' collection",
                },
            },
        },
    }

    local result = LrDialogs.presentModalDialog {
        title = LOC "$$$/LrGeniusAI/CullTask/WindowTitle=Cull Similar Photos",
        contents = contents,
        actionVerb = LOC "$$$/LrGeniusAI/CullTask/Run=Cull",
        cancelVerb = LOC "$$$/LrGeniusAI/common/Cancel=Cancel",
    }

    if result ~= "ok" then
        return nil
    end

    prefs.cullScope = props.scope
    prefs.cullTimeDeltaSeconds = props.timeDeltaSeconds
    prefs.cullCreateDuplicatesCollection = props.createDuplicatesCollection

    return {
        scope = props.scope,
        timeDeltaSeconds = props.timeDeltaSeconds,
        createDuplicatesCollection = props.createDuplicatesCollection,
    }
end


local function dedupePhotoIds(photoIds)
    local result = {}
    local seen = {}
    for _, photoId in ipairs(photoIds or {}) do
        if photoId and not seen[photoId] then
            table.insert(result, photoId)
            seen[photoId] = true
        end
    end
    return result
end


local function photosFromIds(photoIds, photoById)
    local photos = {}
    for _, photoId in ipairs(dedupePhotoIds(photoIds)) do
        local photo = photoById[photoId]
        if photo then
            table.insert(photos, photo)
        else
            log:warn("Cull task: photo not found in catalog for photo_id " .. tostring(photoId))
        end
    end
    return photos
end


LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext("TaskCullPhotos", function(context)
        if not Util.waitForServerDialog() then return end

        local options = showCullDialog(context)
        if not options then return end

        local photosToProcess, status = PhotoSelector.getPhotosInScope(options.scope)
        if not photosToProcess or #photosToProcess == 0 then
            if status == "Invalid view" then
                LrDialogs.message(
                    LOC "$$$/LrGeniusAI/common/InvalidViewTitle=Invalid View",
                    LOC "$$$/LrGeniusAI/common/InvalidViewMessage=The 'Current view' scope only works when a folder or collection is selected."
                )
            else
                LrDialogs.message(
                    LOC "$$$/LrGeniusAI/common/NoPhotosTitle=No Photos Found",
                    LOC "$$$/LrGeniusAI/common/NoPhotosMessage=No photos found in the selected scope."
                )
            end
            return
        end

        local photoIds = {}
        local photoById = {}
        for _, photo in ipairs(photosToProcess) do
            local photoId, photoIdErr = SearchIndexAPI.getPhotoIdForPhoto(photo)
            if photoId then
                table.insert(photoIds, photoId)
                photoById[photoId] = photo
            else
                log:error("Cull task: skipping photo due to missing photo_id: " .. tostring(photoIdErr))
            end
        end

        if #photoIds == 0 then
            LrDialogs.message(
                LOC "$$$/LrGeniusAI/CullTask/NoPhotoIdsTitle=No usable photos",
                LOC "$$$/LrGeniusAI/CullTask/NoPhotoIdsMessage=No usable photo IDs could be computed for the selected photos."
            )
            return
        end

        local progressScope = LrProgressScope({
            title = LOC "$$$/LrGeniusAI/CullTask/ProgressTitle=Culling similar photos...",
            functionContext = context,
        })
        progressScope:setPortionComplete(0, 1)

        local groups, err = SearchIndexAPI.groupSimilarPhotos(photoIds, {
            phash_threshold = "auto",
            clip_threshold = "auto",
            time_delta_seconds = options.timeDeltaSeconds,
        })

        progressScope:setPortionComplete(1, 1)
        progressScope:done()

        if err or type(groups) ~= "table" then
            ErrorHandler.handleError(
                LOC "$$$/LrGeniusAI/CullTask/ErrorTitle=Culling failed",
                err or LOC "$$$/LrGeniusAI/CullTask/ErrorMessage=Could not create culling groups."
            )
            return
        end

        if #groups == 0 then
            LrDialogs.message(
                LOC "$$$/LrGeniusAI/CullTask/NoGroupsTitle=No groups found",
                LOC "$$$/LrGeniusAI/CullTask/NoGroupsMessage=The selected photos could not be grouped for culling."
            )
            return
        end

        local picksIds = {}
        local alternateIds = {}
        local rejectIds = {}
        local duplicateIds = {}

        for _, group in ipairs(groups) do
            local winnerPhotoId = group["winner_photo_id"]
            local alternatePhotoIds = group["alternate_photo_ids"] or {}
            local rejectCandidatePhotoIds = group["reject_candidate_photo_ids"] or {}
            local groupType = group["group_type"]
            local groupPhotoIds = group["photo_ids"] or {}

            if winnerPhotoId then
                table.insert(picksIds, winnerPhotoId)
            end
            for _, photoId in ipairs(alternatePhotoIds) do
                table.insert(alternateIds, photoId)
            end
            for _, photoId in ipairs(rejectCandidatePhotoIds) do
                table.insert(rejectIds, photoId)
            end
            if options.createDuplicatesCollection and groupType == "near_duplicate" then
                for _, photoId in ipairs(groupPhotoIds) do
                    if photoId ~= winnerPhotoId then
                        table.insert(duplicateIds, photoId)
                    end
                end
            end
        end

        local picksPhotos = photosFromIds(picksIds, photoById)
        local alternatePhotos = photosFromIds(alternateIds, photoById)
        local rejectPhotos = photosFromIds(rejectIds, photoById)
        local duplicatePhotos = photosFromIds(duplicateIds, photoById)

        local catalog = LrApplication.activeCatalog()
        local timestamp = LrDate.timeToW3CDate(LrDate.currentTime())
        local resultSet = nil
        local picksCollection = nil

        catalog:withWriteAccessDo("Create culling collections", function()
            resultSet = catalog:createCollectionSet(
                LOC("$$$/LrGeniusAI/CullTask/ResultSet=Culling Results @ ^1", timestamp),
                nil,
                true
            )

            local function createResultCollection(name, photos)
                local collection = catalog:createCollection(name, resultSet, false)
                if photos and #photos > 0 then
                    collection:addPhotos(photos)
                end
                return collection
            end

            picksCollection = createResultCollection(
                LOC "$$$/LrGeniusAI/CullTask/Picks=Picks",
                picksPhotos
            )
            createResultCollection(
                LOC "$$$/LrGeniusAI/CullTask/Alternates=Alternates",
                alternatePhotos
            )
            createResultCollection(
                LOC "$$$/LrGeniusAI/CullTask/Rejects=Reject Candidates",
                rejectPhotos
            )
            if options.createDuplicatesCollection then
                createResultCollection(
                    LOC "$$$/LrGeniusAI/CullTask/Duplicates=Duplicates / Near Duplicates",
                    duplicatePhotos
                )
            end

            if picksCollection then
                catalog:setActiveSources({ picksCollection })
                LrApplicationView.gridView()
            end
        end, Defaults.catalogWriteAccessOptions)

        LrDialogs.message(
            LOC "$$$/LrGeniusAI/CullTask/CompletionTitle=Culling Complete",
            LOC(
                "$$$/LrGeniusAI/CullTask/CompletionMessage=Created culling collections for ^1 groups. Picks: ^2, Alternates: ^3, Reject candidates: ^4.",
                tostring(#groups),
                tostring(#picksPhotos),
                tostring(#alternatePhotos),
                tostring(#rejectPhotos)
            )
        )
    end)
end)
