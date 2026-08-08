local function shutdownApp(doneFunc, progressFunc)
	-- Catalog backup and application teardown can suspend Lightroom's task and
	-- process-launching facilities. Do not perform HTTP, task, file, logging, or
	-- shell work here: Lightroom must never wait on StyleAI while closing.
	if type(doneFunc) == "function" then
		pcall(doneFunc)
	end
end

return {
	LrShutdownFunction = shutdownApp,
}
