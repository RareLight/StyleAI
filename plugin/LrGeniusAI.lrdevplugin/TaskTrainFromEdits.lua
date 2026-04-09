-- TaskTrainFromEdits.lua
-- Allows the user to save their current Lightroom develop settings for selected
-- photos as AI style training examples.  These are stored on the backend and
-- injected as few-shot context the next time AI Edit Photos runs.

require "DevelopEditManager"

local function showTrainDialog(ctx, photoCount)
    local f = LrView.osFactory()
    local bind = LrView.bind
    local props = LrBinding.makePropertyTable(ctx)

    props.label = prefs.trainingLabel or ""
    props.summary = prefs.trainingSummary or ""

    local contents = f:column {
        bind_to_object = props,
        spacing = f:control_spacing(),
        f:group_box {
            title = LOC "$$$/LrGeniusAI/Training/StyleGroup=Edit Style",
            fill_horizontal = 1,
            f:row {
                f:static_text {
                    title = LOC "$$$/LrGeniusAI/Training/LabelLabel=Style label (optional):",
                    width = 180,
                },
                f:edit_field {
                    value = bind "label",
                    width_in_chars = 30,
                    placeholder_string = LOC "$$$/LrGeniusAI/Training/LabelPlaceholder=e.g. Wedding, Portrait, Street",
                },
            },
            f:row {
                f:static_text {
                    title = LOC "$$$/LrGeniusAI/Training/SummaryLabel=Description (optional):",
                    width = 180,
                },
                f:edit_field {
                    value = bind "summary",
                    width_in_chars = 30,
                    height_in_lines = 2,
                },
            },
        },
        f:row {
            f:static_text {
                title = string.format(
                    LOC "$$$/LrGeniusAI/Training/PhotoCount=%d photo(s) will be saved as training examples.",
                    photoCount
                ),
            },
        },
    }

    local result = LrDialogs.presentModalDialog({
        title = LOC "$$$/LrGeniusAI/Training/DialogTitle=Save Edits as AI Training Examples",
        contents = contents,
        actionVerb = LOC "$$$/LrGeniusAI/Training/SaveButton=Save Examples",
    })

    if result ~= "ok" then
        return nil
    end

    prefs.trainingLabel = props.label
    prefs.trainingSummary = props.summary

    return {
        label = props.label,
        summary = props.summary,
    }
end

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext("TrainFromEditsTask", function(ctx)
        LrDialogs.attachErrorDialogToFunctionContext(ctx)
        log:info("Save Training Examples task started")

        if not Util.waitForServerDialog() then
            log:warn("Train task aborted: backend server unavailable")
            return
        end

        local catalog = LrApplication.activeCatalog()
        local photos = catalog:getTargetPhotos()
        if not photos or #photos == 0 then
            LrDialogs.message(
                LOC "$$$/LrGeniusAI/Training/NoPhotosTitle=No Photos",
                LOC "$$$/LrGeniusAI/Training/NoPhotosMsg=Please select one or more photos first.",
                "info"
            )
            return
        end

        local options = showTrainDialog(ctx, #photos)
        if not options then
            log:info("Train task cancelled by user")
            return
        end

        local progressScope = LrProgressScope({
            title = LOC "$$$/LrGeniusAI/Training/Progress=Saving training examples...",
            functionContext = ctx,
        })
        progressScope:setPortionComplete(0, #photos)

        local successCount = 0
        local errorCount = 0
        local errorMessages = {}

        for index, photo in ipairs(photos) do
            if progressScope:isCanceled() then
                break
            end

            local fileName = photo:getFormattedMetadata("fileName") or "Photo"
            progressScope:setCaption(
                string.format(
                    LOC "$$$/LrGeniusAI/Training/ProgressCaption=Processing %s (%d of %d)",
                    fileName,
                    index,
                    #photos
                )
            )
            progressScope:setPortionComplete(index - 1, #photos)

            -- Read current develop settings.
            local developSettings = nil
            local okGet, devOrErr = LrTasks.pcall(function()
                return photo:getDevelopSettings()
            end)
            if okGet and type(devOrErr) == "table" then
                developSettings = devOrErr
            else
                log:warn("Could not read develop settings for " .. fileName .. ": " .. tostring(devOrErr))
                developSettings = {}
            end

            -- Get a stable photo ID.
            local photoId, photoIdErr = SearchIndexAPI.getPhotoIdForPhoto(photo)
            if not photoId then
                log:error("Failed to resolve photo ID for " .. fileName .. ": " .. tostring(photoIdErr))
                table.insert(errorMessages, fileName .. ": " .. tostring(photoIdErr))
                errorCount = errorCount + 1
            else
                -- Export a JPEG thumbnail for CLIP embedding computation.
                local exportedPath = SearchIndexAPI.exportPhotoForIndexing(photo)

                local ok, resp = SearchIndexAPI.addTrainingExample(
                    photoId,
                    exportedPath,  -- may be nil; server will still store settings
                    developSettings,
                    options
                )

                -- Clean up temp file.
                if exportedPath then
                    LrTasks.pcall(function()
                        if LrFileUtils.exists(exportedPath) then
                            LrFileUtils.delete(exportedPath)
                        end
                    end)
                end

                if ok then
                    successCount = successCount + 1
                    log:info("Saved training example for " .. fileName)
                else
                    errorCount = errorCount + 1
                    table.insert(errorMessages, fileName .. ": " .. tostring(resp))
                    log:error("Failed to save training example for " .. fileName .. ": " .. tostring(resp))
                end
            end

            progressScope:setPortionComplete(index, #photos)
        end

        progressScope:done()

        -- Summary dialog.
        if errorCount == 0 then
            LrDialogs.message(
                LOC "$$$/LrGeniusAI/Training/DoneTitle=Training Examples Saved",
                string.format(
                    LOC "$$$/LrGeniusAI/Training/DoneMsg=%d training example(s) were saved successfully.\n\nAI Edit Photos will use your style when editing visually similar photos.",
                    successCount
                ),
                "info"
            )
        else
            local errText = table.concat(errorMessages, "\n")
            LrDialogs.message(
                LOC "$$$/LrGeniusAI/Training/DoneWithErrorsTitle=Training Examples – Partial Success",
                string.format(
                    LOC "$$$/LrGeniusAI/Training/DoneWithErrorsMsg=%d saved, %d failed:\n%s",
                    successCount,
                    errorCount,
                    errText
                ),
                "warning"
            )
        end
    end)
end)
