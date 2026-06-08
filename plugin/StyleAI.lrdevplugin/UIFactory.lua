local LrView = import("LrView")
local LrColor = import("LrColor")
local LrTasks = import("LrTasks")
local LrHttp = import("LrHttp")

UIFactory = {}

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
