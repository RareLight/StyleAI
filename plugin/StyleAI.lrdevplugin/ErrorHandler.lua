ErrorHandler = {}

function ErrorHandler.handleError(errorMessage, detailedInfo)
	-- Log the error message
	log:error(LOC("$$$/StyleAI/ErrorHandler/logError=Error: ^1", errorMessage))
	log:error(
		LOC(
			"$$$/StyleAI/ErrorHandler/logDetails=Details: ^1",
			(detailedInfo or LOC("$$$/StyleAI/ErrorHandler/noDetails=No additional details provided."))
		)
	)

	-- Show a dialog to the user with the error message
	-- LrDialogs.message(errorMessage, detailedInfo, "critical")
	ErrorHandler.customErrorDialog(errorMessage, detailedInfo)
end

function ErrorHandler.customErrorDialog(errorMessage, detailedInfo)
	local f = LrView.osFactory()
	local share = LrView.share
	local UIFactory = require("UIFactory")

	local dialogView = f:column({
		spacing = f:control_spacing(),
		fill_horizontal = 1,
		f:row({
			fill_horizontal = 1,
			f:static_text({
				title = LOC("$$$/StyleAI/ErrorHandler/Error=Error"),
				alignment = "left",
				font = "<system/bold>",
				width = share("labelWidth"),
			}),
			f:static_text({
				title = errorMessage,
				alignment = "left",
				font = "<system/bold>",
				fill_horizontal = 1,
				wrap = true,
			}),
		}),
		UIFactory.HelpText(f, {
			title = LOC("$$$/StyleAI/ErrorHandler/Details=Details"),
		}),
		f:scrolled_view({
			fill_horizontal = 1,
			height = 160,
			horizontal_scroller = false,
			vertical_scroller = true,
			f:edit_field({
				value = detailedInfo or LOC("$$$/StyleAI/ErrorHandler/noDetails=No additional details provided."),
				enabled = false,
				fill_horizontal = 1,
				height_in_lines = 8,
				wraps = true,
				allow_newlines = true,
			}),
		}),
	})

	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/ErrorHandler/Error=Error"),
		contents = dialogView,
		cancelVerb = LOC("$$$/StyleAI/ErrorHandler/gatherLogs=Generate report"),
		resizable = true,
	})

	if result == "cancel" then
		LrTasks.startAsyncTask(function()
			Util.copyLogfilesToDesktop({ error = errorMessage, details = detailedInfo })
		end)
	end
end

return ErrorHandler
