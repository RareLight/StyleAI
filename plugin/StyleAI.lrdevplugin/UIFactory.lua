local LrView = import("LrView")
local LrColor = import("LrColor")
local bind = LrView.bind
local share = LrView.share

UIFactory = {}

-- Lightroom sizes a modal from the intrinsic width of its children. A wrapped
-- static_text with only fill_horizontal can therefore make the initial window
-- as wide as the unwrapped sentence. Workflow dialogs use this bounded root
-- column as their initial reading width; resizable dialogs may still grow on
-- larger displays and their children continue to fill the available space.
function UIFactory.DialogColumn(f, props)
    local children = {}
    for i, child in ipairs(props) do
        children[i] = child
    end
    local column = {
        bind_to_object = props.bind_to_object,
        spacing = props.spacing or f:control_spacing(),
        fill_horizontal = props.fill_horizontal == nil and 1 or props.fill_horizontal,
        width = props.width or 620,
    }
    for key, value in pairs(props) do
        if type(key) ~= "number" and column[key] == nil then
            column[key] = value
        end
    end
    for _, child in ipairs(children) do
        table.insert(column, child)
    end
    return f:column(column)
end

--- Creates a standardized Collapsible Group Box / Section
-- @param f LrViewFactory
-- @param props table Property table containing title and elements
function UIFactory.SettingsGroup(f, props)
    local children = {}
    for i, v in ipairs(props) do
        children[i] = v
        props[i] = nil
    end
    
    local gb_props = {
        title = props.title or "",
        fill_horizontal = props.fill_horizontal or 1,
        margin_top = 5,
        margin_bottom = 5,
    }
    
    -- Forward all other named properties (like visible) to group_box
    for k, v in pairs(props) do
        if gb_props[k] == nil then
            gb_props[k] = v
        end
    end
    
    -- Lightroom group_box requires exactly one child to avoid overlapping
    gb_props[1] = f:column {
        spacing = f:control_spacing(),
        fill_horizontal = 1,
        margin_top = 10,
        unpack(children)
    }
    
    return f:group_box(gb_props)
end

--- Creates a standardized Progress Dialog overlay (placeholder structure)
function UIFactory.ProgressDialog(f, props)
    return f:column {
        spacing = f:control_spacing(),
        f:static_text { title = props.title or LOC("$$$/StyleAI/UI/Processing=Processing..."), font = "<system/bold>" },
        f:static_text { title = bind(props.statusBind or "statusText") },
    }
end

--- Creates a dynamic label/control form row. Label widths may be shared with
--- other labels, but never with the mixed controls that follow them.
function UIFactory.FormRow(f, props)
    local children = {}
    for i, child in ipairs(props) do
        children[i] = child
    end
    local row = {
        fill_horizontal = props.fill_horizontal == nil and 1 or props.fill_horizontal,
        spacing = props.spacing or f:control_spacing(),
        visible = props.visible,
        enabled = props.enabled,
    }
    if props.label then
        table.insert(row, f:static_text {
            title = props.label,
            alignment = props.labelAlignment or "right",
            width = props.labelWidth,
        })
    end
    for _, child in ipairs(children) do
        table.insert(row, child)
    end
    return f:row(row)
end

--- Creates wrapped supporting copy that grows vertically with localization.
function UIFactory.HelpText(f, props)
    local textProps = {
        title = props.title or "",
        fill_horizontal = 1,
        wrap = true,
        text_color = props.text_color,
        visible = props.visible,
    }
    -- Default to Lightroom's regular system text. Callers may still request a
    -- compact caption explicitly, but workflow guidance should not be forced
    -- into the harder-to-read small style.
    if props.size then textProps.size = props.size end
    if props.width then textProps.width = props.width end
    return f:static_text(textProps)
end

--- Creates a status row whose text remains meaningful without its color.
function UIFactory.StatusRow(f, props)
    return UIFactory.FormRow(f, {
        label = props.label,
        labelWidth = props.labelWidth,
        visible = props.visible,
        f:static_text {
            title = props.title,
            text_color = props.text_color,
            fill_horizontal = 1,
            wrap = true,
        },
        props.action,
    })
end

--- Creates a summary block for the effective operation, allowing long text to
--- wrap rather than determining the dialog width.
function UIFactory.Summary(f, props)
    return UIFactory.SettingsGroup(f, {
        title = props.title or LOC("$$$/StyleAI/UI/Summary=Summary"),
        visible = props.visible,
        UIFactory.HelpText(f, {
            title = props.text,
            size = props.size,
        }),
    })
end

--- Keeps destructive actions visually separate from routine controls.
function UIFactory.DestructiveAction(f, props)
    return f:column {
        fill_horizontal = 1,
        spacing = f:control_spacing(),
        visible = props.visible,
        UIFactory.HelpText(f, {
            title = props.explanation or "",
        }),
        f:row {
            f:push_button {
                title = props.title,
                action = props.action,
                enabled = props.enabled,
            },
        },
    }
end

--- Creates a compact, text-first status notice. Color reinforces the message;
--- it never carries the meaning by itself.
function UIFactory.Notice(f, props)
    local kind = props.kind or "info"
    local colors = {
        info = LrColor(0.1, 0.4, 0.9),
        warning = LrColor(0.75, 0.42, 0),
        error = LrColor(0.75, 0.1, 0.1),
    }
    local textProps = {
        title = props.title or "",
        fill_horizontal = 1,
        wrap = true,
        text_color = colors[kind] or colors.info,
    }
    if props.width then textProps.width = props.width end
    if props.height_in_lines then textProps.height_in_lines = props.height_in_lines end
    return f:row {
        fill_horizontal = 1,
        visible = props.visible,
        f:static_text(textProps),
    }
end

--- Creates a standardized LLM Selector row
-- @param f LrViewFactory
-- @param props table Property table
function UIFactory.LLMSelector(f, props)
    return f:row {
        f:static_text { title = props.label or LOC("$$$/StyleAI/PluginInfoDialogSections/aiModel=AI Model:"), width = props.labelWidth or share 'labelWidth' },
        f:popup_menu { value = bind(props.valueBind or 'modelKey'), items = props.items, width = props.width or 300 },
        f:static_text {
            title = bind(props.statusTextBind or 'llmStatusText'),
            text_color = bind(props.statusColorBind or 'llmStatusColor'),
        },
    }
end

return UIFactory
