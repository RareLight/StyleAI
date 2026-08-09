PromptConfigProvider = {}

local function trim(value)
	return tostring(value or ""):match("^%s*(.-)%s*$")
end

local function sortPromptTitles(items)
	table.sort(items, function(a, b)
		if a.value == "Default" then return true end
		if b.value == "Default" then return false end
		return string.lower(a.title or "") < string.lower(b.title or "")
	end)
end

local function copyPrompts(source)
	local copied = {}
	for name, prompt in pairs(source or {}) do
		copied[name] = prompt
	end
	return copied
end

function PromptConfigProvider.deletePrompt(props)
	local promptTitle = props.prompt
	if promptTitle == "Default" then
		LrDialogs.showError(
			LOC("$$$/StyleAI/PromptConfig/DefaultPromptCannotDelete=Default prompt cannot be deleted.")
		)
		return nil
	end

	if
		LrDialogs.confirm(
			LOC("$$$/StyleAI/PromptConfig/DeletePromptConfirm=Delete the prompt “^1”?", tostring(promptTitle))
		) == "ok"
	then
		for k, v in ipairs(props.promptTitles) do
			if v.title == promptTitle then
				table.remove(props.promptTitles, k)
				break
			end
		end
		props.prompts[promptTitle] = nil
		props.promptTitleMenu.items = props.promptTitles

		if props.prompt == promptTitle then
			props.prompt = "Default"
		end
	end
end

function PromptConfigProvider.renamePrompt(props)
	local promptTitle = props.prompt
	if promptTitle == "Default" then
		LrDialogs.showError(LOC("$$$/StyleAI/PromptConfig/DefaultPromptCannotRename=The Default prompt cannot be renamed."))
		return nil
	end
	local newName = LrDialogs.runTextInputDialog(
		LOC("$$$/StyleAI/PromptConfig/RenameTitle=Rename Prompt"),
		LOC("$$$/StyleAI/PromptConfig/RenameMessage=Enter a new name for this prompt:"),
		promptTitle
	)
	newName = trim(newName)
	if newName == "" or newName == promptTitle then return nil end
	if props.prompts[newName] ~= nil then
		LrDialogs.showError(LOC("$$$/StyleAI/PromptConfig/NameExists=A prompt with that name already exists."))
		return nil
	end
	props.prompts[newName] = props.prompts[promptTitle]
	props.prompts[promptTitle] = nil
	for _, item in ipairs(props.promptTitles) do
		if item.value == promptTitle then
			item.title = newName
			item.value = newName
			break
		end
	end
	sortPromptTitles(props.promptTitles)
	props.prompt = newName
	props.promptTitleMenu.items = props.promptTitles
	return newName
end

function PromptConfigProvider.restoreDefaultPrompt(props, defaultText)
	local confirmed = LrDialogs.confirm(
		LOC("$$$/StyleAI/PromptConfig/RestoreTitle=Restore the Default Prompt?"),
		LOC("$$$/StyleAI/PromptConfig/RestoreMessage=This replaces only the shipped Default prompt text. Your named custom prompts are not changed."),
		LOC("$$$/StyleAI/PromptConfig/RestoreAction=Restore Default"),
		LOC("$$$/StyleAI/common/Cancel=Cancel")
	)
	if confirmed ~= "ok" then return false end
	props.prompts.Default = defaultText
	local found = false
	for _, item in ipairs(props.promptTitles) do
		if item.value == "Default" then
			found = true
			break
		end
	end
	if not found then
		table.insert(props.promptTitles, {
			title = LOC("$$$/StyleAI/PromptConfig/DefaultName=Default"),
			value = "Default",
		})
		sortPromptTitles(props.promptTitles)
	end
	props.prompt = "Default"
	props.selectedPrompt = defaultText
	props.promptTitleMenu.items = props.promptTitles
	return true
end

function PromptConfigProvider.addPrompt(props)
	local f = LrView.osFactory()
	local bind = LrView.bind
	local share = LrView.share
	local UIFactory = require("UIFactory")

	local propertyTable = {}
	propertyTable.name = ""
	propertyTable.prompt = ""

	local dialogView = UIFactory.DialogColumn(f, {
		bind_to_object = propertyTable,
		spacing = f:control_spacing(),
		width = 620,
		f:row({
			fill_horizontal = 1,
			f:static_text({
				width = share("labelWidth"),
				title = LOC("$$$/StyleAI/PromptConfig/PromptName=Prompt name"),
			}),
			f:edit_field({
				value = bind("name"),
				fill_horizontal = 1,
			}),
		}),
		f:row({
			fill_horizontal = 1,
			f:static_text({
				width = share("labelWidth"),
				title = LOC("$$$/StyleAI/PromptConfig/PromptField=Prompt"),
			}),
			f:scrolled_view({
				fill_horizontal = 1,
				horizontal_scroller = false,
				vertical_scroller = true,
				height = 300,
				f:edit_field({
					value = bind("prompt"),
					fill_horizontal = 1,
					height_in_lines = 30,
					wraps = true,
					allow_newlines = true,
				}),
			}),
		}),
	})

	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/PromptConfig/AddNewPrompt=Add new prompt"),
		contents = dialogView,
		resizable = true,
	})

	if result == "ok" then
		local name = trim(propertyTable.name)
		local prompt = trim(propertyTable.prompt)
		if name == "" or prompt == "" then
			LrDialogs.showError(LOC("$$$/StyleAI/PromptConfig/NameAndPromptRequired=A prompt name and prompt text are required."))
			return nil
		end
		if props.prompts[name] ~= nil then
			LrDialogs.showError(LOC("$$$/StyleAI/PromptConfig/NameExists=A prompt with that name already exists."))
			return nil
		end
		props.prompts[name] = prompt
		props.prompt = name
		table.insert(props.promptTitles, { title = name, value = name })
		sortPromptTitles(props.promptTitles)
		props.promptTitleMenu.items = props.promptTitles
		return name
	end

	return nil
end

function PromptConfigProvider.showPromptConfigDialog(propertyTable)
	local f = LrView.osFactory()
	local bind = LrView.bind
	local share = LrView.share
	local UIFactory = require("UIFactory")

	propertyTable.promptTitles = {}
	for title in pairs(prefs.prompts) do
		table.insert(propertyTable.promptTitles, { title = title, value = title })
	end

	propertyTable.prompts = copyPrompts(prefs.prompts)

	propertyTable.prompt = prefs.prompt

	propertyTable.selectedPrompt = prefs.prompts[prefs.prompt]

	propertyTable:addObserver("prompt", function(properties, key, newValue)
		properties.selectedPrompt = properties.prompts[newValue]
	end)

	propertyTable:addObserver("selectedPrompt", function(properties, key, newValue)
		properties.prompts[properties.prompt] = newValue
	end)

	local dropDown = f:popup_menu({
		items = bind("promptTitles"),
		value = bind("prompt"),
		width = 280,
	})
	propertyTable.promptTitleMenu = dropDown

	local dialogView = UIFactory.DialogColumn(f, {
		bind_to_object = propertyTable,
		spacing = f:control_spacing(),
		width = 700,
		f:row({
			fill_horizontal = 1,
			f:static_text({
				width = share("labelWidth"),
				title = LOC("$$$/StyleAI/PromptConfig/PromptName=Prompt name"),
			}),
			dropDown,
			f:push_button({
				title = LOC("$$$/StyleAI/PromptConfig/Add=Add"),
				action = function(button)
					PromptConfigProvider.addPrompt(propertyTable)
				end,
			}),
			f:push_button({
				title = LOC("$$$/StyleAI/PromptConfig/Rename=Rename"),
				action = function()
					PromptConfigProvider.renamePrompt(propertyTable)
				end,
			}),
			f:push_button({
				title = LOC("$$$/StyleAI/PromptConfig/Delete=Delete"),
				action = function(button)
					PromptConfigProvider.deletePrompt(propertyTable)
				end,
			}),
			f:push_button({
				title = LOC("$$$/StyleAI/PromptConfig/RestoreAction=Restore Default"),
				action = function()
					PromptConfigProvider.restoreDefaultPrompt(propertyTable, Defaults.defaultSystemInstruction)
				end,
			}),
		}),
		f:row({
			fill_horizontal = 1,
			f:static_text({
				width = share("labelWidth"),
				title = LOC("$$$/StyleAI/PromptConfig/PromptField=Prompt"),
			}),
			f:edit_field({
				value = bind("selectedPrompt"),
				fill_horizontal = 1,
				height_in_lines = 10,
				wraps = true,
				allow_newlines = true,
			}),
		}),
	})

	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/PromptConfig/ConfigurePrompts=Configure Prompts"),
		contents = dialogView,
		resizable = true,
	})

	if result == "ok" then
		prefs.prompts = propertyTable.prompts
		prefs.prompt = propertyTable.prompt
	end
end

return PromptConfigProvider
