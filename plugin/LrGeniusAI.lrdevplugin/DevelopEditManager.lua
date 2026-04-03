DevelopEditManager = {}

local GLOBAL_KEY_MAP = {
    exposure = "Exposure2012",
    contrast = "Contrast2012",
    highlights = "Highlights2012",
    shadows = "Shadows2012",
    whites = "Whites2012",
    blacks = "Blacks2012",
    temperature = "Temp",
    tint = "Tint",
    texture = "Texture",
    clarity = "Clarity2012",
    dehaze = "Dehaze",
    vibrance = "Vibrance",
    saturation = "Saturation",
    sharpening = "Sharpness",
    noise_reduction = "LuminanceSmoothing",
    color_noise_reduction = "ColorNoiseReduction",
    vignette = "PostCropVignetteAmount",
    grain = "GrainAmount",
}

local MASK_KEY_CANDIDATES = {
    exposure = { "local_Exposure", "Exposure2012", "Exposure" },
    contrast = { "local_Contrast", "Contrast2012", "Contrast" },
    highlights = { "local_Highlights", "Highlights2012", "Highlights" },
    shadows = { "local_Shadows", "Shadows2012", "Shadows" },
    whites = { "local_Whites", "Whites2012", "Whites" },
    blacks = { "local_Blacks", "Blacks2012", "Blacks" },
    temperature = { "local_Temperature", "Temperature", "Temp" },
    tint = { "local_Tint", "Tint" },
    texture = { "local_Texture", "Texture" },
    clarity = { "local_Clarity", "Clarity2012", "Clarity" },
    dehaze = { "local_Dehaze", "Dehaze" },
    saturation = { "local_Saturation", "Saturation" },
    sharpness = { "local_Sharpness", "Sharpness" },
    noise = { "local_Noise", "LuminanceSmoothing" },
    moire = { "local_Moire" },
}

local HSL_LABELS = {
    red = "Red",
    orange = "Orange",
    yellow = "Yellow",
    green = "Green",
    aqua = "Aqua",
    blue = "Blue",
    purple = "Purple",
    magenta = "Magenta",
}

local function appendWarning(warnings, text)
    if warnings and text and text ~= "" then
        table.insert(warnings, text)
    end
end

local function sortedKeys(tbl)
    local keys = {}
    for key in pairs(tbl or {}) do
        table.insert(keys, key)
    end
    table.sort(keys)
    return keys
end

local function tableCount(tbl)
    local count = 0
    for _ in pairs(tbl or {}) do
        count = count + 1
    end
    return count
end

local function getRecipeFromResponse(response)
    if type(response) ~= "table" then
        return nil
    end
    if type(response.edit) == "table" then
        return response.edit
    end
    if type(response.recipe) == "table" then
        return response.recipe
    end
    if type(response.global) == "table" or type(response.masks) == "table" then
        return response
    end
    return nil
end

local function buildHslDevelopSettings(hsl)
    local settings = {}
    if type(hsl) ~= "table" then
        return settings
    end

    for channel, adjustments in pairs(hsl) do
        local label = HSL_LABELS[channel]
        if label and type(adjustments) == "table" then
            if adjustments.hue ~= nil then
                settings["HueAdjustment" .. label] = adjustments.hue
            end
            if adjustments.saturation ~= nil then
                settings["SaturationAdjustment" .. label] = adjustments.saturation
            end
            if adjustments.luminance ~= nil then
                settings["LuminanceAdjustment" .. label] = adjustments.luminance
            end
        end
    end
    return settings
end

local function buildColorGradingDevelopSettings(colorGrading, warnings)
    local settings = {}
    if type(colorGrading) ~= "table" then
        return settings
    end

    local shadows = colorGrading.shadows
    if type(shadows) == "table" then
        if shadows.hue ~= nil then settings.SplitToningShadowHue = shadows.hue end
        if shadows.saturation ~= nil then settings.SplitToningShadowSaturation = shadows.saturation end
        if shadows.luminance ~= nil then
            appendWarning(warnings, "Shadow color grading luminance is not supported by Lightroom develop settings and was ignored.")
        end
    end

    local highlights = colorGrading.highlights
    if type(highlights) == "table" then
        if highlights.hue ~= nil then settings.SplitToningHighlightHue = highlights.hue end
        if highlights.saturation ~= nil then settings.SplitToningHighlightSaturation = highlights.saturation end
        if highlights.luminance ~= nil then
            appendWarning(warnings, "Highlight color grading luminance is not supported by Lightroom develop settings and was ignored.")
        end
    end

    if colorGrading.balance ~= nil then
        settings.SplitToningBalance = colorGrading.balance
    end
    if type(colorGrading.midtones) == "table" then
        appendWarning(warnings, "Midtone color grading is not currently mapped by the Lightroom plugin and was ignored.")
    end
    if type(colorGrading.global) == "table" then
        appendWarning(warnings, "Global color grading is not currently mapped by the Lightroom plugin and was ignored.")
    end
    if colorGrading.blending ~= nil then
        appendWarning(warnings, "Color grading blending is not currently mapped by the Lightroom plugin and was ignored.")
    end

    if next(settings) ~= nil then
        settings.EnableSplitToning = true
    end
    return settings
end

local function buildToneCurveSettings(toneCurve)
    local settings = {}
    if type(toneCurve) ~= "table" then
        return settings
    end

    if toneCurve.highlights ~= nil then settings.ParametricHighlights = toneCurve.highlights end
    if toneCurve.lights ~= nil then settings.ParametricLights = toneCurve.lights end
    if toneCurve.darks ~= nil then settings.ParametricDarks = toneCurve.darks end
    if toneCurve.shadows ~= nil then settings.ParametricShadows = toneCurve.shadows end
    return settings
end

local function buildLensCorrectionSettings(lensCorrections, warnings)
    local settings = {}
    if type(lensCorrections) ~= "table" then
        return settings
    end
    if lensCorrections.enable_profile_corrections ~= nil then
        settings.EnableLensCorrections = lensCorrections.enable_profile_corrections
    end
    if lensCorrections.remove_chromatic_aberration ~= nil then
        appendWarning(warnings, "Chromatic aberration removal is not currently mapped by the Lightroom plugin and was ignored.")
    end
    return settings
end

local function mergeSettings(target, source)
    for key, value in pairs(source or {}) do
        target[key] = value
    end
end

local function formatGlobalSettings(globalSettings)
    local lines = {}
    for _, key in ipairs(sortedKeys(globalSettings or {})) do
        if key ~= "hsl" and key ~= "color_grading" and key ~= "tone_curve" and key ~= "lens_corrections" then
            table.insert(lines, "- " .. tostring(key) .. ": " .. tostring(globalSettings[key]))
        end
    end
    if type(globalSettings.hsl) == "table" then
        table.insert(lines, "- hsl: " .. tostring(tableCount(globalSettings.hsl)) .. " channel(s)")
    end
    if type(globalSettings.color_grading) == "table" then
        table.insert(lines, "- color_grading: enabled")
    end
    if type(globalSettings.tone_curve) == "table" then
        table.insert(lines, "- tone_curve: enabled")
    end
    if type(globalSettings.lens_corrections) == "table" then
        table.insert(lines, "- lens_corrections: enabled")
    end
    return lines
end

function DevelopEditManager.formatRecipeDetails(response)
    local recipe = getRecipeFromResponse(response)
    if not recipe then
        return "No edit recipe available."
    end

    local lines = {}
    table.insert(lines, "Summary")
    table.insert(lines, recipe.summary or "AI-generated Lightroom edit recipe")
    table.insert(lines, "")

    local globalSettings = recipe.global or {}
    table.insert(lines, "Global adjustments")
    local globalLines = formatGlobalSettings(globalSettings)
    if #globalLines == 0 then
        table.insert(lines, "- none")
    else
        for _, line in ipairs(globalLines) do
            table.insert(lines, line)
        end
    end
    table.insert(lines, "")

    table.insert(lines, "Masks")
    local masks = recipe.masks or {}
    if #masks == 0 then
        table.insert(lines, "- none")
    else
        for _, mask in ipairs(masks) do
            local count = tableCount(mask.adjustments or {})
            table.insert(lines, "- " .. tostring(mask.kind or "mask") .. " (" .. tostring(count) .. " adjustment(s))")
        end
    end
    table.insert(lines, "")

    table.insert(lines, "Warnings")
    local warnings = recipe.warnings or {}
    if #warnings == 0 then
        table.insert(lines, "- none")
    else
        for _, warning in ipairs(warnings) do
            table.insert(lines, "- " .. tostring(warning))
        end
    end

    return table.concat(lines, "\n")
end

function DevelopEditManager.persistEditRecipe(photo, response, warnings, status)
    log:trace("DevelopEditManager.persistEditRecipe: start status=" .. tostring(status))
    local okRecipe, recipeOrErr = LrTasks.pcall(function()
        return getRecipeFromResponse(response)
    end)
    if not okRecipe then
        log:error("DevelopEditManager.persistEditRecipe: getRecipeFromResponse failed: " .. tostring(recipeOrErr))
        return
    end
    local recipe = recipeOrErr
    if not photo or not recipe then
        log:error("DevelopEditManager.persistEditRecipe: missing photo or recipe")
        return
    end

    log:trace("DevelopEditManager.persistEditRecipe: recipe resolved, building warnings")
    local allWarnings = {}
    if type(recipe.warnings) == "table" then
        for _, warning in ipairs(recipe.warnings) do
            table.insert(allWarnings, tostring(warning))
        end
    end
    if type(warnings) == "table" then
        for _, warning in ipairs(warnings) do
            table.insert(allWarnings, tostring(warning))
        end
    end

    log:trace("DevelopEditManager.persistEditRecipe: encoding recipe JSON")
    local okEncode, recipeJsonOrErr = LrTasks.pcall(function()
        return JSON:encode(recipe)
    end)
    if not okEncode then
        log:error("DevelopEditManager.persistEditRecipe: JSON encode failed: " .. tostring(recipeJsonOrErr))
        recipeJsonOrErr = "{}"
    end
    local recipeJson = recipeJsonOrErr

    local warningText = #allWarnings > 0 and table.concat(allWarnings, "\n") or ""
    log:trace("DevelopEditManager.persistEditRecipe: warningText length=" .. tostring(#warningText))
    local runDate = (type(response) == "table" and (response.edit_rundate or response.ai_rundate)) or ""
    if runDate == "" then
        runDate = LrDate.timeToW3CDate(LrDate.currentTime())
    end
    local modelName = ""
    if type(response) == "table" then
        modelName = response.edit_model or response.ai_model or ""
    end

    log:trace("DevelopEditManager.persistEditRecipe: entering catalog write")
    local catalog = LrApplication.activeCatalog()
    local okWrite, writeErr = LrTasks.pcall(function()
        -- withPrivateWriteAccessDo signature here is (callback [, options]).
        -- Passing an action-name string first can trigger obscure runtime errors in LR.
        catalog:withPrivateWriteAccessDo(function()
            photo:setPropertyForPlugin(_PLUGIN, "aiEditLastRun", tostring(runDate))
            photo:setPropertyForPlugin(_PLUGIN, "aiEditModel", tostring(modelName))
            photo:setPropertyForPlugin(_PLUGIN, "aiEditSummary", tostring(recipe.summary or ""))
            photo:setPropertyForPlugin(_PLUGIN, "aiEditWarnings", warningText)
            photo:setPropertyForPlugin(_PLUGIN, "aiEditRecipe", tostring(recipeJson or ""))
            photo:setPropertyForPlugin(_PLUGIN, "aiEditStatus", tostring(status or "generated"))
        end, Defaults.catalogWriteAccessOptions)
    end)
    if not okWrite then
        log:error("DevelopEditManager.persistEditRecipe: catalog write failed: " .. tostring(writeErr))
        return
    end
    log:trace("DevelopEditManager.persistEditRecipe: done warningsCount=" .. tostring(#allWarnings))
end

local function buildDevelopSettings(recipe, warnings)
    local developSettings = {}
    local globalSettings = recipe.global or {}

    for key, lrKey in pairs(GLOBAL_KEY_MAP) do
        local value = globalSettings[key]
        if value ~= nil then
            developSettings[lrKey] = value
        end
    end

    mergeSettings(developSettings, buildHslDevelopSettings(globalSettings.hsl))
    mergeSettings(developSettings, buildColorGradingDevelopSettings(globalSettings.color_grading, warnings))
    mergeSettings(developSettings, buildToneCurveSettings(globalSettings.tone_curve))
    mergeSettings(developSettings, buildLensCorrectionSettings(globalSettings.lens_corrections, warnings))

    return developSettings
end

local function focusPhotoInDevelop(photo, warnings)
    local catalog = LrApplication.activeCatalog()
    local ok, err = LrTasks.pcall(function()
        catalog:setActiveSources({ catalog.kAllPhotos })
        LrTasks.sleep(0.2)
        catalog:setSelectedPhotos(photo, { photo })
        LrApplicationView.switchToModule("develop")
        LrTasks.sleep(0.2)
    end)
    if not ok then
        appendWarning(warnings, "Could not switch Lightroom to the Develop module for mask application: " .. tostring(err))
        return false
    end
    return true
end

local function applyGlobalDevelopSettings(photo, recipe, warnings)
    log:trace("DevelopEditManager.applyGlobalDevelopSettings: start")
    local developSettings = buildDevelopSettings(recipe, warnings)
    if next(developSettings) == nil then
        log:trace("DevelopEditManager.applyGlobalDevelopSettings: nothing to apply")
        return true
    end

    local catalog = LrApplication.activeCatalog()
    local ok, err = LrTasks.pcall(function()
        catalog:withWriteAccessDo("Apply AI Lightroom develop settings", function()
            photo:applyDevelopSettings(developSettings)
        end, Defaults.catalogWriteAccessOptions)
    end)
    if not ok then
        appendWarning(warnings, "Failed to apply global develop settings: " .. tostring(err))
        log:error("DevelopEditManager.applyGlobalDevelopSettings failed: " .. tostring(err))
        return false
    end
    log:trace("DevelopEditManager.applyGlobalDevelopSettings: success")
    return true
end

local function supportsMaskAutomation()
    return type(LrDevelopController) == "table"
        and type(LrDevelopController.createNewMask) == "function"
        and type(LrDevelopController.setValue) == "function"
end

local function applyMaskEdits(photo, recipe, warnings)
    log:trace("DevelopEditManager.applyMaskEdits: start")
    local masks = recipe.masks or {}
    if #masks == 0 then
        log:trace("DevelopEditManager.applyMaskEdits: no masks")
        return true
    end

    if not supportsMaskAutomation() then
        appendWarning(warnings, "Lightroom mask automation is unavailable in this Lightroom SDK version. Mask edits were stored but not applied.")
        log:warn("DevelopEditManager.applyMaskEdits: mask automation unavailable")
        return false
    end

    if not focusPhotoInDevelop(photo, warnings) then
        return false
    end
    if type(LrDevelopController.goToMasking) == "function" then
        LrTasks.pcall(function()
            LrDevelopController.goToMasking()
        end)
    end

    local function readMaskList()
        if type(LrDevelopController.getAllMasks) ~= "function" then
            return {}
        end
        local ok, masksOrErr = LrTasks.pcall(function()
            return LrDevelopController.getAllMasks()
        end)
        if not ok or type(masksOrErr) ~= "table" then
            return {}
        end
        return masksOrErr
    end

    local function extractMaskId(maskItem)
        if type(maskItem) == "string" or type(maskItem) == "number" then
            return tostring(maskItem)
        end
        if type(maskItem) == "table" then
            if maskItem.id ~= nil then return tostring(maskItem.id) end
            if maskItem.maskId ~= nil then return tostring(maskItem.maskId) end
            if maskItem.uuid ~= nil then return tostring(maskItem.uuid) end
        end
        return nil
    end

    local function buildMaskIdSet(maskList)
        local ids = {}
        for _, item in ipairs(maskList or {}) do
            local id = extractMaskId(item)
            if id then
                ids[id] = true
            end
        end
        return ids
    end

    local function findNewMaskId(beforeMasks, afterMasks)
        local beforeIds = buildMaskIdSet(beforeMasks)
        for _, item in ipairs(afterMasks or {}) do
            local id = extractMaskId(item)
            if id and not beforeIds[id] then
                return id
            end
        end
        return nil
    end

    local function selectMaskById(maskId)
        if not maskId or type(LrDevelopController.selectMask) ~= "function" then
            return false
        end
        local ok = LrTasks.pcall(function()
            LrDevelopController.selectMask(maskId)
        end)
        return ok == true
    end

    local function createMaskForKind(maskKind)
        -- SDK-valid top-level types: brush, gradient, radialGradient, rangeMask, aiSelection
        -- Map semantic recipe kinds to aiSelection and provide hint where possible.
        local okAiWithHint, errAiWithHint = LrTasks.pcall(function()
            LrDevelopController.createNewMask("aiSelection", maskKind)
        end)
        if okAiWithHint then
            return true, nil
        end

        local okAiFallback, errAiFallback = LrTasks.pcall(function()
            LrDevelopController.createNewMask("aiSelection")
        end)
        if okAiFallback then
            if type(LrDevelopController.selectMaskTool) == "function" then
                -- Best-effort; ignore failures because SDK behavior differs by version.
                LrTasks.pcall(function()
                    LrDevelopController.selectMaskTool(maskKind)
                end)
            end
            return true, nil
        end

        local okBrush, errBrush = LrTasks.pcall(function()
            LrDevelopController.createNewMask("brush")
        end)
        if okBrush then
            appendWarning(warnings, "Mask kind '" .. tostring(maskKind) .. "' fell back to brush; refine manually.")
            return true, nil
        end

        return false, errAiFallback or errAiWithHint or errBrush
    end

    for _, mask in ipairs(masks) do
        local maskKind = tostring(mask.kind or "")
        local ok, err = LrTasks.pcall(function()
            local masksBefore = readMaskList()
            local created, createErr = createMaskForKind(maskKind)
            if not created then
                error("createNewMask failed: " .. tostring(createErr))
            end
            local masksAfter = readMaskList()
            local newMaskId = findNewMaskId(masksBefore, masksAfter)
            if newMaskId then
                local selected = selectMaskById(newMaskId)
                log:trace("DevelopEditManager.applyMaskEdits created mask kind=" .. tostring(maskKind) .. " newMaskId=" .. tostring(newMaskId) .. " selectOk=" .. tostring(selected))
            else
                log:trace("DevelopEditManager.applyMaskEdits created mask kind=" .. tostring(maskKind) .. " but could not identify new mask id")
            end

            -- Best-effort to ensure local adjustment context is active.
            LrTasks.pcall(function()
                LrDevelopController.setValue("local_Amount", 1)
            end)
            if mask.invert and type(LrDevelopController.toggleInvertMaskTool) == "function" then
                LrDevelopController.toggleInvertMaskTool()
            end
            for key, value in pairs(mask.adjustments or {}) do
                local candidates = MASK_KEY_CANDIDATES[key]
                if candidates and #candidates > 0 then
                    local applied = false
                    local lastErr = nil
                    for _, candidate in ipairs(candidates) do
                        local setOk, setErr = LrTasks.pcall(function()
                            LrDevelopController.setValue(candidate, value)
                        end)
                        if setOk then
                            applied = true
                            local readBack = nil
                            if type(LrDevelopController.getValue) == "function" then
                                local rbOk, rbVal = LrTasks.pcall(function()
                                    return LrDevelopController.getValue(candidate)
                                end)
                                if rbOk then
                                    readBack = rbVal
                                end
                            end
                            log:trace("DevelopEditManager.applyMaskEdits applied " .. tostring(key) .. " via " .. tostring(candidate) .. "=" .. tostring(value) .. " readBack=" .. tostring(readBack))
                            break
                        else
                            lastErr = setErr
                            log:trace("DevelopEditManager.applyMaskEdits candidate failed " .. tostring(key) .. " via " .. tostring(candidate) .. ": " .. tostring(setErr))
                        end
                    end
                    if not applied then
                        appendWarning(warnings, "Mask adjustment '" .. tostring(key) .. "' could not be applied for " .. maskKind .. ": " .. tostring(lastErr or "unknown error"))
                    end
                else
                    appendWarning(warnings, "Mask adjustment '" .. tostring(key) .. "' is not currently supported and was ignored.")
                end
            end
        end)
        if not ok then
            appendWarning(warnings, "Mask '" .. maskKind .. "' could not be applied: " .. tostring(err))
            log:error("DevelopEditManager.applyMaskEdits mask failed: " .. tostring(maskKind) .. " err=" .. tostring(err))
        end
    end
    log:trace("DevelopEditManager.applyMaskEdits: done")
    return true
end

function DevelopEditManager.showValidationDialog(context, photo, response, options)
    log:trace("DevelopEditManager.showValidationDialog: start")
    local recipe = getRecipeFromResponse(response)
    if not recipe then
        log:error("DevelopEditManager.showValidationDialog: no recipe in response")
        return "cancel", nil
    end

    local f = LrView.osFactory()
    local bind = LrView.bind
    local props = LrBinding.makePropertyTable(context)
    props.applyGlobal = next(recipe.global or {}) ~= nil
    props.applyMasks = (options and options.applyMasks ~= false) and ((recipe.masks and #recipe.masks > 0) or false)
    props.details = DevelopEditManager.formatRecipeDetails(response)

    local dialogView = f:column {
        bind_to_object = props,
        spacing = f:control_spacing(),
        f:row {
            f:static_text {
                title = photo:getFormattedMetadata("fileName") or "Photo",
            },
        },
        f:row {
            f:checkbox { value = bind "applyGlobal" },
            f:static_text { title = "Apply global develop settings" },
        },
        f:row {
            f:checkbox {
                value = bind "applyMasks",
                enabled = (recipe.masks and #recipe.masks > 0) or false,
            },
            f:static_text { title = "Apply masks when possible" },
        },
        f:row {
            f:edit_field {
                value = bind "details",
                width_in_chars = 70,
                height_in_lines = 22,
            },
        },
    }

    local result = LrDialogs.presentModalDialog({
        title = "Review AI Lightroom Edit",
        contents = dialogView,
        actionVerb = "Apply",
    })
    log:trace("DevelopEditManager.showValidationDialog: result=" .. tostring(result))

    if result == "ok" then
        return result, {
            applyGlobal = props.applyGlobal,
            applyMasks = props.applyMasks,
        }
    end
    return result, nil
end

function DevelopEditManager.applyRecipe(photo, response, options)
    log:trace("DevelopEditManager.applyRecipe: start")
    local recipe = getRecipeFromResponse(response)
    if not recipe then
        log:error("DevelopEditManager.applyRecipe: no recipe")
        return false, { "No edit recipe returned by the AI." }
    end

    local warnings = {}
    if type(recipe.warnings) == "table" then
        for _, warning in ipairs(recipe.warnings) do
            table.insert(warnings, tostring(warning))
        end
    end

    local applyGlobal = options == nil or options.applyGlobal ~= false
    local applyMasks = options ~= nil and options.applyMasks == true

    local globalApplied = true
    if applyGlobal then
        globalApplied = applyGlobalDevelopSettings(photo, recipe, warnings)
    end
    if applyMasks then
        applyMaskEdits(photo, recipe, warnings)
    end

    DevelopEditManager.persistEditRecipe(photo, response, warnings, "applied")
    log:trace("DevelopEditManager.applyRecipe: done globalApplied=" .. tostring(globalApplied) .. " warningsCount=" .. tostring(#warnings))
    return globalApplied, warnings
end
