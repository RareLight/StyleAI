local function shutdownApp(doneFunc, progressFunc)
    -- Instead of shutting down the backend, we now only unload models and collections from memory
    -- to free up resources while keeping the service running.
    if SearchIndexAPI.isBackendOnLocalhost() then
        LrTasks.startAsyncTask(function()
            LrTasks.pcall(function()
                SearchIndexAPI.unloadResources()
            end)
            doneFunc()
        end)
    else
        doneFunc()
    end
end

return {
    LrShutdownFunction = shutdownApp,
}
