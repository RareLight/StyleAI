local LrTasks = import("LrTasks")
local LrPathUtils = import("LrPathUtils")
local LrFileUtils = import("LrFileUtils")
local LrHttp = import("LrHttp")
local LrDialogs = import("LrDialogs")

require("APISearchIndex")

TaskDiagnostics = {}

function TaskDiagnostics.generateReport()
    LrTasks.startAsyncTask(function()
        LrDialogs.showBezel("Generating Diagnostic Report...", 2)

        local logs, err = SearchIndexAPI.getLogs()
        if err then
            logs = { backend_error = tostring(err) }
        end
        local health = SearchIndexAPI.getBackendHealth() or {}
        local detailedHealth = SearchIndexAPI.getDetailedHealth() or {}

        local html = {}
        table.insert(html, "<html><head><title>StyleAI Diagnostics Report</title>")
        table.insert(html, "<style>")
        table.insert(html, "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #1e1e1e; color: #d4d4d4; margin: 20px; }")
        table.insert(html, "h1, h2 { color: #569cd6; border-bottom: 1px solid #333; padding-bottom: 5px; }")
        table.insert(html, ".section { background: #252526; border: 1px solid #333; padding: 15px; border-radius: 6px; margin-bottom: 20px; }")
        table.insert(html, "pre { background: #1e1e1e; color: #d4d4d4; padding: 10px; overflow-x: auto; font-family: 'Courier New', Courier, monospace; font-size: 13px; border-radius: 4px; }")
        table.insert(html, ".success { color: #4CAF50; }")
        table.insert(html, ".error { color: #F44336; }")
        table.insert(html, "</style>")
        table.insert(html, "</head><body>")

        table.insert(html, "<h1>StyleAI Diagnostics Report</h1>")
        table.insert(html, "<p>Generated on: " .. os.date("%Y-%m-%d %H:%M:%S") .. "</p>")

        -- System Health
        table.insert(html, "<div class='section'>")
        table.insert(html, "<h2>System Health</h2>")
        table.insert(html, "<ul>")
        table.insert(html, "<li>Backend Server: " .. (detailedHealth.backend and "<span class='success'>Connected</span>" or "<span class='error'>Offline</span>") .. "</li>")

        table.insert(html, "<li>Ollama (Local): " .. (detailedHealth.ollama and "<span class='success'>Running</span>" or "Not Detected") .. "</li>")
        table.insert(html, "<li>LM Studio (Local): " .. (detailedHealth.lmstudio and "<span class='success'>Running</span>" or "Not Detected") .. "</li>")
        table.insert(html, "</ul>")
        
        table.insert(html, "<h3>Backend Internal Health</h3>")
        table.insert(html, "<pre>" .. JSON:encode(health, { indent = true }) .. "</pre>")
        table.insert(html, "</div>")

        -- Logs
        table.insert(html, "<div class='section'>")
        table.insert(html, "<h2>Backend Logs</h2>")
        if logs and logs.backend then
            -- Escape HTML
            local safeLogs = logs.backend:gsub("<", "&lt;"):gsub(">", "&gt;")
            table.insert(html, "<pre>" .. safeLogs .. "</pre>")
        elseif logs and logs.backend_error then
            table.insert(html, "<p class='error'>Error fetching logs: " .. tostring(logs.backend_error) .. "</p>")
        else
            table.insert(html, "<p>No backend logs available.</p>")
        end
        table.insert(html, "</div>")

        table.insert(html, "</body></html>")

        local tempDir = LrPathUtils.getStandardFilePath("temp")
        local reportPath = LrPathUtils.child(tempDir, "StyleAI_Diagnostics.html")

        local f = io.open(reportPath, "w")
        if f then
            f:write(table.concat(html, "\n"))
            f:close()
            LrHttp.openUrlInBrowser("file://" .. reportPath)
        else
            LrDialogs.message("Error", "Could not write diagnostic report to " .. tostring(reportPath), "critical")
        end
    end)
end

return TaskDiagnostics
