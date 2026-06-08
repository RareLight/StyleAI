local LrView = import("LrView")
local LrColor = import("LrColor")
local LrTasks = import("LrTasks")
local LrHttp = import("LrHttp")

UIFactory = {}

--- Creates a standardized Collapsible Group Box / Section
-- @param f LrViewFactory
-- @param props table Property table containing title and elements
function UIFactory.SettingsGroup(f, props)
    return f:group_box {
        title = props.title or "",
        fill_horizontal = props.fill_horizontal or 1,
        font = "<system/bold>",
        margin_top = 5,
        margin_bottom = 5,
        unpack(props)
    }
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
