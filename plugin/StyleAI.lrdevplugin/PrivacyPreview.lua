local LrDialogs = import("LrDialogs")
local LrTasks = import("LrTasks")
local LrView = import("LrView")
local LrStringUtils = import("LrStringUtils")
local LrPathUtils = import("LrPathUtils")
local LrFileUtils = import("LrFileUtils")
local LrApplication = import("LrApplication")

local SearchIndexAPI = require("APISearchIndex")

local PrivacyPreview = {}

function PrivacyPreview.showPreviewFlow(photos, progressScope, sensitivity)
    if not photos or #photos == 0 then return true end

    local f = LrView.osFactory()
    local maxPreviews = 6
    local numPreviews = math.min(#photos, maxPreviews)

    progressScope:setCaption("Fetching face blur previews...")
    progressScope:setPortionComplete(0, 1)

    local batchForPreview = {}
    for i = 1, numPreviews do
        local photo = photos[i]
        local photoId = SearchIndexAPI.getPhotoIdForPhoto(photo)
        local jpegData = SearchIndexAPI.getJpegThumbnailForPhoto(photo, 1024, 1024)
        if jpegData then
            local b64 = LrStringUtils.encodeBase64(jpegData)
            table.insert(batchForPreview, { photo_id = photoId or tostring(i), image = b64, filename = "preview_" .. i .. ".jpg" })
        end
    end

    if #batchForPreview == 0 then
        return true
    end

    local success, response = SearchIndexAPI.previewBlurredFacesBatch(batchForPreview, sensitivity)
    if not success or not response or not response.images then
        LrDialogs.showError("Failed to fetch previews from the local backend.")
        return false
    end

    local tempDir = LrPathUtils.getStandardFilePath('temp')
    local previewTempPaths = {}
    for i, imgData in ipairs(response.images) do
        if imgData.image then
            local b64String = imgData.image
            if b64String:match("^data:image") then
                b64String = b64String:gsub("^data:image/jpeg;base64,", "")
            end
            local raw = LrStringUtils.decodeBase64(b64String)
            local path = LrPathUtils.child(tempDir, "styleai_preview_" .. i .. ".jpg")
            local file, err = io.open(path, "wb")
            if file then
                file:write(raw)
                file:close()
                table.insert(previewTempPaths, path)
            end
        end
    end

    if #previewTempPaths == 0 then
        LrDialogs.showError("Could not save temporary preview images.")
        return false
    end

    local customViewElements = { spacing = f:control_spacing() }
    
    local current_row = {}
    for i, p in ipairs(previewTempPaths) do
        table.insert(current_row, f:picture {
            value = p,
            width = 250,
            height = 250,
        })
        if #current_row == 3 or i == #previewTempPaths then
            table.insert(customViewElements, f:row {
                spacing = f:control_spacing(),
                unpack(current_row)
            })
            current_row = {}
        end
    end

    table.insert(customViewElements, f:static_text {
        title = "Note: These are low-res previews. The final applied blur will use full resolution.",
        alignment = "center",
    })

    table.insert(customViewElements, f:row {
        spacing = f:control_spacing(),
            f:push_button {
                title = "Export all " .. #photos .. " blurred previews to folder...",
                action = function()
                    local LrFunctionContext = import("LrFunctionContext")
                    LrTasks.startAsyncTask(function()
                        local exportDir = LrDialogs.runOpenPanel({
                            title = "Choose Export Location for Previews",
                            canChooseFiles = false,
                            canChooseDirectories = true,
                            canCreateDirectories = true,
                            allowsMultipleSelection = false,
                        })
                        if exportDir and exportDir[1] then
                            local baseDir = exportDir[1]
                            
                            local dir = baseDir

                            local exportScope = import("LrProgressScope")({
                                title = "Exporting Blurred Previews...",
                            })
                            local batchCount = 0
                            local batchReq = {}
                            
                            for i, photo in ipairs(photos) do
                                if exportScope:isCanceled() then break end
                                exportScope:setPortionComplete(i, #photos)
                                
                                local pId = SearchIndexAPI.getPhotoIdForPhoto(photo)
                                local jpegData = SearchIndexAPI.getJpegThumbnailForPhoto(photo, 1024, 1024)
                                if jpegData then
                                    local b64 = LrStringUtils.encodeBase64(jpegData)
                                    table.insert(batchReq, { photo_id = pId or tostring(i), image = b64, filename = "img_" .. i .. ".jpg" })
                                end
                            
                                if #batchReq >= 10 or i == #photos then
                                    if #batchReq > 0 then
                                        local s, res = SearchIndexAPI.previewBlurredFacesBatch(batchReq, sensitivity)
                                        if s and res and res.images then
                                            for _, imgData in ipairs(res.images) do
                                                if imgData.image then
                                                    local b64String = imgData.image
                                                    if b64String:match("^data:image") then
                                                        b64String = b64String:gsub("^data:image/jpeg;base64,", "")
                                                    end
                                                    local raw = LrStringUtils.decodeBase64(b64String)
                                                    local outPath = LrPathUtils.child(dir, "blurred_preview_" .. (batchCount + 1) .. ".jpg")
                                                    local outF, err = io.open(outPath, "wb")
                                                    if outF then
                                                        if raw then outF:write(raw) end
                                                        outF:close()
                                                        batchCount = batchCount + 1
                                                    else
                                                        LrDialogs.showError("Failed to save image to disk: " .. tostring(err))
                                                    end
                                                end
                                            end
                                        end
                                        batchReq = {}
                                    end
                                end
                            end
                            exportScope:done()
                            
                            if batchCount == 0 then
                                LrDialogs.message("Export Finished", "0 previews were exported. This usually means the image thumbnails could not be fetched, or the backend failed to return the processed images.", "critical")
                            else
                                LrDialogs.message("Export Complete", "Exported " .. batchCount .. " blurred previews to:\n" .. dir)
                            end
                        end
                    end)
                end
            }
        }
    )

    local customView = f:column(customViewElements)
    local result = LrDialogs.presentModalDialog {
        title = "Preview Blurred Faces",
        contents = customView,
        actionVerb = "Proceed",
        cancelVerb = "Cancel",
    }
    
    return (result == "ok")
end

return PrivacyPreview
