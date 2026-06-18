---
-- @module TaskAiEditPhotos
-- @description The execution side of the Advanced Style Detection & AI Editing pipeline.
-- Evaluates selected photos, gathers context, and asks the backend to generate a highly 
-- personalized Lightroom edit recipe. If `use_training_style` is true, the request is routed 
-- to the new Style Engine, which queries ChromaDB for visually and semantically matching 
-- "AI Training Examples" (stored via TaskTrainFromEdits) to apply a custom edit.
---

require("DevelopEditManager")
local UIFactory = require("UIFactory")

local ENABLE_DEBUG_STYLE_OVERRIDE = true

local function copyOptions(source)
	local copied = {}
	for key, value in pairs(source or {}) do
		copied[key] = value
	end
	return copied
end

local function safePromptTable(rawPrompts)
	if type(rawPrompts) ~= "table" then
		log:warn(
			"AI Edit prompt table invalid type: " .. tostring(type(rawPrompts)) .. ". Falling back to default prompt."
		)
		return { Default = Defaults.defaultEditSystemInstruction }
	end
	return rawPrompts
end

local function buildModelItems()
	local items = {}
	local openaiKey = nil
	local geminiKey = nil
	if prefs then
		openaiKey = import("LrPasswords").retrieve("StyleAI", "chatgptApiKey")
		geminiKey = import("LrPasswords").retrieve("StyleAI", "geminiApiKey")
	end
	openaiKey = Util.nilOrEmpty(openaiKey) and nil or openaiKey
	geminiKey = Util.nilOrEmpty(geminiKey) and nil or geminiKey

	local modelsResp = SearchIndexAPI.getModels(openaiKey, geminiKey)
	if modelsResp and modelsResp.models then
		for provider, modelList in pairs(modelsResp.models) do
			for _, model in ipairs(modelList) do
				table.insert(items, {
					title = provider .. ": " .. model,
					value = provider .. "::" .. model,
				})
			end
		end
	end
	table.sort(items, function(a, b)
		return a.title < b.title
	end)
	return items
end

local function getEditIntentPresetInstruction(presetValue)
	for _, preset in ipairs(Defaults.editIntentPresets or {}) do
		if preset.value == presetValue then
			return preset.instruction
		end
	end
	return nil
end

local function hasEditIntentPresetValue(presetValue)
	for _, preset in ipairs(Defaults.editIntentPresets or {}) do
		if preset.value == presetValue then
			return true
		end
	end
	return false
end

local function buildEditIntentPresetItems()
	local items = {}
	for _, preset in ipairs(Defaults.editIntentPresets or {}) do
		table.insert(items, { title = preset.title, value = preset.value })
	end
	if #items == 0 then
		table.insert(items, {
			title = LOC("$$$/StyleAI/TaskAiEditPhotos/Custom=Custom"),
			value = Defaults.editIntentCustomValue or "custom",
		})
	end
	return items
end

local function hasCompositionModeValue(value)
	for _, item in ipairs(Defaults.compositionModes or {}) do
		if item.value == value then
			return true
		end
	end
	return false
end

local function showPhotoInstructionDialog(ctx, photo)
	local f = LrView.osFactory()
	local bind = LrView.bind

	local props = LrBinding.makePropertyTable(ctx)
	props.photoContextData = photo:getPropertyForPlugin(_PLUGIN, "photoContext") or ""
	props.skipFromHere = false

	local dialogView = f:column({
		bind_to_object = props,
		spacing = f:control_spacing(),
		f:row({
			f:static_text({
				title = photo:getFormattedMetadata("fileName") or "Photo",
			}),
		}),
		f:row({
			alignment = "center",
			f:catalog_photo({
				photo = photo,
				width = 300,
			}),
		}),
		f:row({
			f:static_text({
				title = LOC("$$$/StyleAI/TaskAiEditPhotos/PerPhotoInstructions=Per-photo edit instructions"),
			}),
		}),
		f:row({
			f:edit_field({
				value = bind("photoContextData"),
				width_in_chars = 50,
				height_in_lines = 10,
				allow_newlines = true,
			}),
		}),
		f:row({
			f:checkbox({
				value = bind("skipFromHere"),
			}),
			f:static_text({
				title = LOC(
					"$$$/StyleAI/TaskAiEditPhotos/UseForFollowing=Use these instructions for all following photos."
				),
			}),
		}),
	})

	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/TaskAiEditPhotos/PhotoSpecificInstructions=Photo-specific edit instructions"),
		contents = dialogView,
		actionVerb = LOC("$$$/StyleAI/common/Continue=Continue"),
	})

	return result, props.photoContextData, props.skipFromHere
end

local function showAiEditDialog(ctx)
	log:trace("showAiEditDialog: start")
	local f = LrView.osFactory()
	local bind = LrView.bind
	local share = LrView.share
	local props = LrBinding.makePropertyTable(ctx)

	props.scope = prefs.aiEditScope or "selected"
	props.modelKey = prefs.aiEditModelKey or prefs.modelKey
	if prefs.blurFacesForCloud == nil then
		props.blurFacesForCloud = false
	else
		props.blurFacesForCloud = prefs.blurFacesForCloud
	end

	if prefs.previewBlurredFaces == nil then
		props.previewBlurredFaces = false
	else
		props.previewBlurredFaces = prefs.previewBlurredFaces
	end

	props.faceBlurSensitivity = prefs.faceBlurSensitivity or "balanced"
	props.temperature = prefs.aiEditTemperature or prefs.temperature or 0.1
	props.language = prefs.aiEditLanguage or prefs.generateLanguage or "English"
	props.styleStrength = prefs.aiEditStyleStrength or Defaults.defaultEditStyleStrength or 0.5
	props.editIntentPresetItems = buildEditIntentPresetItems()
	props.customEditIntentText = prefs.aiEditIntentCustomText or prefs.aiEditIntent or Defaults.defaultEditIntent
	if type(props.customEditIntentText) ~= "string" or props.customEditIntentText == "" then
		props.customEditIntentText = Defaults.defaultEditIntent
	end
	props.editIntentPreset = prefs.aiEditIntentPreset
		or Defaults.defaultEditIntentPresetValue
		or (Defaults.editIntentCustomValue or "custom")
	if not hasEditIntentPresetValue(props.editIntentPreset) then
		props.editIntentPreset = Defaults.editIntentCustomValue or "custom"
	end
	props.isCustomEditIntent = props.editIntentPreset == (Defaults.editIntentCustomValue or "custom")
	if props.isCustomEditIntent then
		props.editIntent = props.customEditIntentText
	else
	props.editIntent = getEditIntentPresetInstruction(props.editIntentPreset) or Defaults.defaultEditIntent
	end
	props.createVirtualCopies = prefs.aiEditCreateVirtualCopies ~= false
	props.reviewBeforeApply = prefs.aiEditReviewBeforeApply ~= false
	props.applyMasks = prefs.aiEditApplyMasks ~= false
	props.adjustWhiteBalance = prefs.aiEditAdjustWhiteBalance ~= false
	props.adjustBasicTone = prefs.aiEditAdjustBasicTone ~= false
	props.adjustPresence = prefs.aiEditAdjustPresence ~= false
	props.adjustColorMix = prefs.aiEditAdjustColorMix ~= false
	props.doColorGrading = prefs.aiEditDoColorGrading ~= false
	props.useToneCurve = prefs.aiEditUseToneCurve ~= false
	props.usePointCurve = prefs.aiEditUsePointCurve ~= false
	props.adjustDetail = prefs.aiEditAdjustDetail ~= false
	props.adjustEffects = prefs.aiEditAdjustEffects ~= false
	props.adjustLensCorrections = prefs.aiEditAdjustLensCorrections ~= false
	props.allowAutoCrop = prefs.aiEditAllowAutoCrop == true
	props.compositionModes = Defaults.compositionModes or {}
	props.compositionMode = prefs.aiEditCompositionMode or Defaults.defaultCompositionMode or "subtle"
	if not hasCompositionModeValue(props.compositionMode) then
		props.compositionMode = Defaults.defaultCompositionMode or "subtle"
	end
	props.submitKeywords = prefs.aiEditSubmitKeywords ~= false
	props.submitFolderName = prefs.aiEditSubmitFolderName or false
	props.showPhotoContextDialog = prefs.aiEditShowPhotoContextDialog ~= false
	props.useTrainingStyle = prefs.aiEditUseTrainingStyle ~= false
	
	props.editingStyle = prefs.aiEditEditingStyle or "trained"
	props.styleStrength = prefs.aiEditStyleStrength or Defaults.defaultEditStyleStrength or 0.5
	props.showLlmOptions = (props.editingStyle == "creative")
	
	props.promptTitles = {}
	props.prompts = safePromptTable(prefs.editPrompts or { Default = Defaults.defaultEditSystemInstruction })
	log:trace("showAiEditDialog: prompt source type=" .. tostring(type(props.prompts)))
	props.prompt = prefs.editPrompt or Defaults.defaultEditPromptName
	if type(props.prompt) ~= "string" or props.prompt == "" then
		props.prompt = Defaults.defaultEditPromptName
	end
	props.selectedPrompt = props.prompts[props.prompt]
	if type(props.selectedPrompt) ~= "string" or props.selectedPrompt == "" then
		props.prompt = Defaults.defaultEditPromptName
		props.selectedPrompt = props.prompts[props.prompt] or Defaults.defaultEditSystemInstruction
	end

	for title, prompt in pairs(props.prompts) do
		if type(title) == "string" and title ~= "" and type(prompt) == "string" then
			table.insert(props.promptTitles, { title = title, value = title })
		end
	end
	log:trace("showAiEditDialog: promptTitles count=" .. tostring(#props.promptTitles))
	if #props.promptTitles == 0 then
		props.prompts = { Default = Defaults.defaultEditSystemInstruction }
		props.prompt = Defaults.defaultEditPromptName
		props.selectedPrompt = Defaults.defaultEditSystemInstruction
		table.insert(
			props.promptTitles,
			{ title = Defaults.defaultEditPromptName, value = Defaults.defaultEditPromptName }
		)
	end
	table.sort(props.promptTitles, function(a, b)
		return a.title < b.title
	end)

	props:addObserver("prompt", function(properties, key, newValue)
		properties.selectedPrompt = properties.prompts[newValue]
	end)
	props:addObserver("selectedPrompt", function(properties, key, newValue)
		properties.prompts[properties.prompt] = newValue
	end)
	props:addObserver("editIntentPreset", function(properties, key, newValue)
		local customValue = Defaults.editIntentCustomValue or "custom"
		properties.isCustomEditIntent = newValue == customValue
		if properties.isCustomEditIntent then
			properties.editIntent = properties.customEditIntentText or Defaults.defaultEditIntent
		else
			properties.editIntent = getEditIntentPresetInstruction(newValue) or Defaults.defaultEditIntent
		end
	end)
	props:addObserver("editIntent", function(properties, key, newValue)
		if properties.isCustomEditIntent then
			properties.customEditIntentText = newValue
		end
	end)
	props:addObserver("editingStyle", function(properties, key, newValue)
		properties.showLlmOptions = (newValue == "creative")
	end)
	
	local function updateIsCloudModel(properties, key)
	    local provider = key
	    if key then
	        local sep = string.find(key, "::", 1, true)
	        if sep then provider = string.sub(key, 1, sep - 1) end
	    end
	    properties.isCloudModel = (provider == "chatgpt" or provider == "gemini" or provider == "vertexai")
	end
	
	props:addObserver("modelKey", function(properties, k, newValue)
	    updateIsCloudModel(properties, newValue)
	end)

	local modelItems = buildModelItems()
	log:trace("showAiEditDialog: modelItems count=" .. tostring(#modelItems))
	if #modelItems == 0 then
		table.insert(modelItems, { title = LOC("$$$/StyleAI/TaskAiEditPhotos/NoModels=No AI models available"), value = "none" })
	end
	if not props.modelKey or props.modelKey == "" then
		props.modelKey = modelItems[1].value
	end
	updateIsCloudModel(props, props.modelKey)

	local styleItems = {}
	if ENABLE_DEBUG_STYLE_OVERRIDE then
		local okStyles, fetchedStyles = SearchIndexAPI.listStyles()
		if okStyles and fetchedStyles then
			for _, style in ipairs(fetchedStyles) do
				table.insert(styleItems, { title = style.style_name or style.style_id, value = style.style_id })
			end
		end
	end
	if #styleItems == 0 then
		table.insert(styleItems, { title = LOC("$$$/StyleAI/TaskAiEditPhotos/NoStyles=No styles available"), value = "none" })
	end
	props.overrideStyleEnabled = false
	props.overrideStyleId = styleItems[1].value

	props.promptTitleMenu = f:popup_menu({
		items = bind("promptTitles"),
		value = bind("prompt"),
	})

	local contents = f:column({
		bind_to_object = props,
		spacing = f:control_spacing(),
		f:group_box({
			title = LOC("$$$/StyleAI/TaskAiEditPhotos/Workflow=Workflow"),
			fill_horizontal = 1,
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/EditingStyle=Editing Style:"),
					width = share("labelWidth"),
				}),
				f:popup_menu({
					value = bind("editingStyle"),
					width = 300,
					items = {
						{ title = LOC("$$$/StyleAI/TaskAiEditPhotos/StyleTrained=Predictive ML Edit (Fast & Local)"), value = "trained" },
						{ title = LOC("$$$/StyleAI/TaskAiEditPhotos/StyleCreative=Generative AI Edit (Zero-Shot / Creative)"), value = "creative" },
					},
				}),
			}),
			f:row({
				visible = bind({
					key = "editingStyle",
					transform = function(v) return v == "trained" end
				}),
				f:static_text({
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/TrainedStyleStrength=Trained Style Strength:"),
					width = share("labelWidth"),
				}),
				f:column({
					f:row({
						f:slider({
							value = bind("styleStrength"),
							min = 0.0,
							max = 1.0,
							integral = false,
							width = 300,
						}),
						f:static_text({
							title = bind("styleStrength"),
							width = 40,
						}),
					}),
					f:push_button({
						title = LOC("$$$/StyleAI/common/Reset=Reset"),
						width = 60,
						action = function()
							props.styleStrength = Defaults.defaultEditStyleStrength or 0.5
						end,
					}),
				}),
			}),
			f:row({
				visible = bind({
					key = "editingStyle",
					transform = function(v) return ENABLE_DEBUG_STYLE_OVERRIDE and v == "trained" end
				}),
				f:checkbox({
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/OverrideStyle=Override style (One-time test)"),
					value = bind("overrideStyleEnabled"),
				}),
				f:popup_menu({
					value = bind("overrideStyleId"),
					items = styleItems,
					visible = bind("overrideStyleEnabled"),
					width = 200,
				}),
			}),
		}),
		f:group_box({
			title = LOC("$$$/StyleAI/common/Scope=Scope"),
			fill_horizontal = 1,
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/common/ApplyTo=Apply to:"),
					width = share("labelWidth"),
				}),
				f:popup_menu({
					value = bind("scope"),
					width = 300,
					items = {
						{ title = LOC("$$$/StyleAI/common/ScopeSelected=Selected photos only"), value = "selected" },
						{ title = LOC("$$$/StyleAI/common/ScopeView=Current view"), value = "view" },
						{ title = LOC("$$$/StyleAI/common/ScopeAll=All photos in catalog"), value = "all" },
					},
				}),
			}),
		}),
		f:group_box({
			title = LOC("$$$/StyleAI/common/AiSettings=AI Settings"),
			fill_horizontal = 1,
			visible = bind("showLlmOptions"),
			fill_horizontal = 1,
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/common/AiModel=AI model:"),
					width = share("labelWidth"),
				}),
				f:column({
					f:popup_menu({
						value = bind("modelKey"),
						items = modelItems,
						width = 300,
					}),
					f:static_text {
						title = LOC "$$$/StyleAI/AnalyzeAndIndex/CloudWarning=⚠️ Images will be sent to the internet.",
						-- visible = bind 'isCloudModel', -- disabled for testing
						text_color = LrColor(0.8, 0.5, 0),
						tooltip = LOC "$$$/StyleAI/AnalyzeAndIndex/CloudTooltip=Enterprise APIs typically do not use data for training, but privacy cannot be fully guaranteed. See our Wiki for details.",
					},
				}),
			}),
		}),
		UIFactory.SettingsGroup(f, {
			title = LOC "$$$/StyleAI/UI/PrivacySettings=Privacy & Anonymization",
			fill_horizontal = 1,
			visible = bind("showLlmOptions"),
			f:column({
				spacing = f:control_spacing(),
					f:checkbox({
						title = LOC("$$$/StyleAI/AnalyzeAndIndex/BlurFaces=Blur faces before sending to cloud APIs (Privacy)"),
						value = bind("blurFacesForCloud"),
					}),
					f:row({
						margin_left = 20,
						f:checkbox({
							title = LOC("$$$/StyleAI/TaskAiEditPhotos/PreviewBlur=Preview blurred images before sending"),
							value = bind("previewBlurredFaces"),
							visible = bind { key = 'blurFacesForCloud', transform = function(v) return v == true end },
						}),
					}),
					f:row({
						margin_left = 20,
						f:static_text({ 
							title = "Blur Sensitivity:", 
							width = 100,
							visible = bind { key = 'blurFacesForCloud', transform = function(v) return v == true end },
						}),
						f:popup_menu({
							value = bind("faceBlurSensitivity"),
							items = {
								{ title = "High (Catch more faces)", value = "high" },
								{ title = "Balanced", value = "balanced" },
								{ title = "Low (Fewer false positives)", value = "low" },
							},
							width = 200,
							visible = bind { key = 'blurFacesForCloud', transform = function(v) return v == true end },
						}),
					}),
				}),
			}),
			UIFactory.SettingsGroup(f, {
				title = LOC("$$$/StyleAI/TaskAiEditPhotos/ModelSettings=Model Settings"),
				fill_horizontal = 1,
				visible = bind("showLlmOptions"),
				f:column({
					spacing = f:control_spacing(),
					f:row({
						f:static_text({
							title = LOC("$$$/StyleAI/common/Temperature=Temperature:"),
							width = share("labelWidth"),
						}),
						f:column({
							f:row({
								f:slider({
									value = bind("temperature"),
									min = 0.0,
									max = 0.5,
									integral = false,
									width = 300,
								}),
								f:static_text({
									title = bind("temperature"),
									width = 40,
								}),
							}),
							f:push_button({
								title = LOC("$$$/StyleAI/common/Reset=Reset"),
								width = 60,
								action = function()
									props.temperature = Defaults.defaultTemperature or 0.1
								end,
							}),
						}),
					}),
					f:row({
				f:static_text({
					width = share("labelWidth"),
					title = LOC("$$$/StyleAI/common/Prompt=Prompt:"),
				}),
				props.promptTitleMenu,
				f:push_button({
					title = LOC("$$$/StyleAI/common/Add=Add"),
					action = function()
						local ok, err = LrTasks.pcall(function()
							PromptConfigProvider.addPrompt(props)
						end)
						if not ok then
							log:error("AI Edit prompt add failed: " .. tostring(err))
							LrDialogs.showError(
								LOC("$$$/StyleAI/PromptConfig/AddFailed=Adding prompt failed: ^1"),
								tostring(err)
							)
						end
					end,
				}),
				f:push_button({
					title = LOC("$$$/StyleAI/common/Delete=Delete"),
					action = function()
						local ok, err = LrTasks.pcall(function()
							PromptConfigProvider.deletePrompt(props)
						end)
						if not ok then
							log:error("AI Edit prompt delete failed: " .. tostring(err))
							LrDialogs.showError(
								LOC("$$$/StyleAI/PromptConfig/DeleteFailed=Deleting prompt failed: ^1"),
								tostring(err)
							)
						end
					end,
				}),
			}),
			f:row({
				f:static_text({
					width = share("labelWidth"),
					title = LOC("$$$/StyleAI/common/SystemInstruction=System instruction:"),
				}),
				f:edit_field({
					value = bind("selectedPrompt"),
					width_in_chars = 50,
					height_in_lines = 4,
					allow_newlines = true,
				}),
			}),
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/common/SummaryLanguage=Summary language:"),
					width = share("labelWidth"),
				}),
				f:combo_box({
					value = bind("language"),
					items = Defaults.generateLanguages,
				}),
			}),
		}),
		}),
		f:group_box({
			title = LOC("$$$/StyleAI/TaskAiEditPhotos/EditInstructions=Edit Instructions"),
			fill_horizontal = 1,
			visible = bind("showLlmOptions"),
			fill_horizontal = 1,
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/OverallLook=Overall look:"),
					width = share("labelWidth"),
				}),
				f:popup_menu({
					value = bind("editIntentPreset"),
					items = bind("editIntentPresetItems"),
					width = 300,
				}),
			}),
			f:row({
				f:static_text({
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/CustomIntent=Custom intent:"),
					width = share("labelWidth"),
				}),
				f:edit_field({
					value = bind("editIntent"),
					width_in_chars = 50,
					enabled = bind("isCustomEditIntent"),
				}),
			}),

			f:row({
				f:checkbox({
					value = bind("reviewBeforeApply"),
				}),
				f:static_text({
					title = LOC(
						"$$$/StyleAI/TaskAiEditPhotos/ReviewProposed=Review each proposed edit before applying it"
					),
				}),
			}),
			f:row({
				f:checkbox({
					value = bind("applyMasks"),
				}),
				f:static_text({
					title = LOC("$$$/StyleAI/TaskAiEditPhotos/AskMasks=Ask the AI for subject/sky/background masks"),
				}),
			}),
			f:row({
				f:checkbox({
					value = bind("showPhotoContextDialog"),
				}),
				f:static_text({
					title = LOC(
						"$$$/StyleAI/TaskAiEditPhotos/AllowPerPhoto=Allow per-photo edit instructions before generation"
					),
				}),
			}),
		}),
		f:group_box({
			title = LOC("$$$/StyleAI/common/Context=Context"),
			fill_horizontal = 1,
			visible = bind("showLlmOptions"),
			fill_horizontal = 1,
			f:row({
				f:column({
					spacing = f:control_spacing(),
					f:row({
						f:checkbox({
							value = bind("submitKeywords"),
						}),
						f:static_text({
							title = LOC(
								"$$$/StyleAI/TaskAiEditPhotos/SendKeywords=Send existing Lightroom keywords"
							),
						}),
					}),
				}),
				f:column({
					spacing = f:control_spacing(),
					f:row({
						f:checkbox({
							value = bind("submitFolderName"),
						}),
						f:static_text({
							title = LOC("$$$/StyleAI/TaskAiEditPhotos/SendFolders=Send folder names"),
						}),
					}),
				}),
			}),
		}),
		f:row({
			f:push_button({
				title = LOC("$$$/StyleAI/common/ResetAllDefaults=Reset to Defaults"),
				action = function()
					local confirm = LrDialogs.confirm(
						LOC("$$$/StyleAI/common/ResetAllDefaultsConfirmTitle=Reset Settings"),
						LOC("$$$/StyleAI/common/ResetAllDefaultsConfirmMessage=Are you sure you want to reset all options in this dialog to their default values?")
					)
					if confirm == "ok" then
						props.editingStyle = "trained" 
						props.scope = "selected"
						props.modelKey = (modelItems and modelItems[1]) and modelItems[1].value or "none"
						props.temperature = 0.1
						props.prompt = "Default"
						props.selectedPrompt = Defaults.defaultEditSystemInstruction
						props.language = "English"
						props.editIntentPreset = "natural_pro"
						props.customEditIntentText = Defaults.defaultEditIntent
						props.editIntent = "Natural professional Lightroom edit with balanced contrast, realistic color, and clean detail."
						props.styleStrength = 0.5
						props.createVirtualCopies = true
						props.reviewBeforeApply = true
						props.applyMasks = true
						props.showPhotoContextDialog = true
						props.submitKeywords = true
						props.submitFolderName = false
						props.allowAutoCrop = false
						props.useTrainingStyle = true
						props.faceBlurSensitivity = "balanced"
					end
				end,
			}),
		}),
	})

	local result = LrDialogs.presentModalDialog({
		title = LOC("$$$/StyleAI/TaskAiEditPhotos/DialogTitle=AI Edit Photos in Lightroom"),
		contents = contents,
		actionVerb = LOC("$$$/StyleAI/TaskAiEditPhotos/GenerateEdits=Generate edits"),
	})
	log:trace("showAiEditDialog: dialog result=" .. tostring(result))

	if result ~= "ok" then
		return nil
	end

	prefs.aiEditScope = props.scope
	prefs.aiEditModelKey = props.modelKey
	prefs.blurFacesForCloud = props.blurFacesForCloud
	prefs.previewBlurredFaces = props.previewBlurredFaces
	prefs.faceBlurSensitivity = props.faceBlurSensitivity
	prefs.aiEditTemperature = props.temperature
	prefs.aiEditLanguage = props.language
	prefs.aiEditStyleStrength = props.styleStrength
	prefs.aiEditIntent = props.editIntent
	prefs.aiEditIntentPreset = props.editIntentPreset
	prefs.aiEditIntentCustomText = props.customEditIntentText
	prefs.aiEditCreateVirtualCopies = props.createVirtualCopies
	prefs.aiEditReviewBeforeApply = props.reviewBeforeApply
	prefs.aiEditApplyMasks = props.applyMasks
	prefs.aiEditAdjustWhiteBalance = props.adjustWhiteBalance
	prefs.aiEditAdjustBasicTone = props.adjustBasicTone
	prefs.aiEditAdjustPresence = props.adjustPresence
	prefs.aiEditAdjustColorMix = props.adjustColorMix
	prefs.aiEditDoColorGrading = props.doColorGrading
	prefs.aiEditUseToneCurve = props.useToneCurve
	prefs.aiEditUsePointCurve = props.usePointCurve
	prefs.aiEditAdjustDetail = props.adjustDetail
	prefs.aiEditAdjustEffects = props.adjustEffects
	prefs.aiEditAdjustLensCorrections = props.adjustLensCorrections
	prefs.aiEditAllowAutoCrop = props.allowAutoCrop
	prefs.aiEditCompositionMode = props.compositionMode
	prefs.aiEditSubmitKeywords = props.submitKeywords
	prefs.aiEditSubmitFolderName = props.submitFolderName
	prefs.aiEditShowPhotoContextDialog = props.showPhotoContextDialog
	prefs.aiEditUseTrainingStyle = props.useTrainingStyle
	prefs.aiEditEditingStyle = props.editingStyle
	prefs.editPrompts = props.prompts
	prefs.editPrompt = props.prompt

	local providerFromKey, modelFromKey
	local sep = props.modelKey and string.find(props.modelKey, "::", 1, true) or nil
	if sep then
		providerFromKey = string.sub(props.modelKey, 1, sep - 1)
		modelFromKey = string.sub(props.modelKey, sep + 2)
	else
		providerFromKey = props.modelKey
	end

	local options = {
		scope = props.scope,
		provider = providerFromKey,
		model = modelFromKey,
		language = props.language,
		temperature = props.temperature,
		prompt = props.selectedPrompt,
		edit_intent = props.editIntent,
		style_strength = props.styleStrength,
		include_masks = props.applyMasks,
		applyMasks = props.applyMasks,
		reviewBeforeApply = props.reviewBeforeApply,
		createVirtualCopies = props.createVirtualCopies,
		submit_keywords = props.submitKeywords,
		submit_folder_names = props.submitFolderName,
		showPhotoContextDialog = props.showPhotoContextDialog,
		use_training_style = false,
		enableQuickEdit = props.editingStyle == "trained",
		quickEditStyleStrength = props.styleStrength,
		blurFacesForCloud = props.blurFacesForCloud,
		previewBlurredFaces = props.previewBlurredFaces,
		faceBlurSensitivity = props.faceBlurSensitivity,
	}

	if props.overrideStyleEnabled and props.overrideStyleId then
		options.style_override = props.overrideStyleId
	end

	if providerFromKey == "chatgpt" then
		if prefs then
			local key = import("LrPasswords").retrieve("StyleAI", "chatgptApiKey")
			if not Util.nilOrEmpty(key) then
				options.api_key = key
			else
				LrDialogs.showError(
					LOC(
						"$$$/StyleAI/AnalyzeAndIndex/MissingChatGPTAPIKey=ChatGPT API key is not configured. Please set it in the plugin preferences."
					)
				)
				return nil
			end
		end
	elseif providerFromKey == "gemini" then
		if prefs then
			local key = import("LrPasswords").retrieve("StyleAI", "geminiApiKey")
			if not Util.nilOrEmpty(key) then
				options.api_key = key
			else
				LrDialogs.showError(
					LOC(
						"$$$/StyleAI/AnalyzeAndIndex/MissingGeminiAPIKey=Gemini API key is not configured. Please set it in the plugin preferences."
					)
				)
				return nil
			end
		end
	end
	return options
end

local function enrichPhotoOptions(photo, baseOptions, userContext)
	log:trace("enrichPhotoOptions: start for " .. tostring(photo and photo:getFormattedMetadata("fileName") or "nil"))
	local photoOptions = copyOptions(baseOptions)
	if photoOptions.submit_keywords then
		local keywords = photo:getFormattedMetadata("keywordTagsForExport")
		if keywords then
			if type(keywords) == "string" then
				photoOptions.existing_keywords = Util.string_split(keywords, ",")
			else
				photoOptions.existing_keywords = keywords
			end
		end
	end
	if photoOptions.submit_folder_names then
		local originalFilePath = photo:getRawMetadata("path")
		if originalFilePath then
			photoOptions.folder_names = Util.getStringsFromRelativePath(originalFilePath)
		end
	end
	local datetime = photo:getRawMetadata("dateTime")
	if datetime ~= nil and type(datetime) == "number" then
		photoOptions.date_time = LrDate.timeToW3CDate(datetime)
		photoOptions.capture_time = datetime -- Unix timestamp for style engine
	end

	-- Add EXIF fields for style engine matching using standardized utility.
	local exif = Util.getPhotoExif(photo)
	for k, v in pairs(exif) do
		photoOptions[k] = v
	end
	photoOptions.user_context = userContext or photo:getPropertyForPlugin(_PLUGIN, "photoContext") or ""
	return photoOptions
end

LrTasks.startAsyncTask(function()
	LrFunctionContext.callWithContext("AiEditPhotosTask", function(ctx)
		LrDialogs.attachErrorDialogToFunctionContext(ctx)
		log:info("AI Edit task started")

		-- Check server connection and health (ensure AI providers are configured)
		if not Util.waitForServerDialog({ requireProviders = true }) then
			log:warn("AI Edit task aborted: backend server unavailable")
			return
		end

		local stats = SearchIndexAPI.getTrainingStats()
		if not stats or (stats.count or 0) < 5 then
			LrDialogs.showError(
				LOC("$$$/StyleAI/TaskAiEditPhotos/ColdStartTitle=Cold Start"),
				LOC("$$$/StyleAI/TaskAiEditPhotos/ColdStartMsg=StyleAI needs at least 5 examples to learn your baseline editing style. Please run 'Train AI Style (Save Edits)' first.")
			)
			log:warn("AI Edit task aborted: Cold Start (<5 examples)")
			return
		end

		local options = showAiEditDialog(ctx)
		if not options then
			log:info("AI Edit task canceled by user in options dialog")
			return
		end
		log:trace(
			"AI Edit options selected: scope="
				.. tostring(options.scope)
				.. " provider="
				.. tostring(options.provider)
				.. " model="
				.. tostring(options.model)
				.. " review="
				.. tostring(options.reviewBeforeApply)
				.. " styleStrength="
				.. tostring(options.style_strength)
				.. " masks="
				.. tostring(options.applyMasks)
				.. " wb="
				.. tostring(options.adjust_white_balance)
				.. " basicTone="
				.. tostring(options.adjust_basic_tone)
				.. " presence="
				.. tostring(options.adjust_presence)
				.. " colorMix="
				.. tostring(options.adjust_color_mix)
				.. " grading="
				.. tostring(options.do_color_grading)
				.. " toneCurve="
				.. tostring(options.use_tone_curve)
				.. " pointCurve="
				.. tostring(options.use_point_curve)
				.. " detail="
				.. tostring(options.adjust_detail)
				.. " effects="
				.. tostring(options.adjust_effects)
				.. " lens="
				.. tostring(options.adjust_lens_corrections)
				.. " crop="
				.. tostring(options.allow_auto_crop)
				.. " composition="
				.. tostring(options.composition_mode)
		)

		local photos = PhotoSelector.getPhotosInScope(options.scope)
		if not photos or #photos == 0 then
			LrDialogs.message(
				LOC("$$$/StyleAI/common/NoPhotosTitle=No Photos"),
				LOC("$$$/StyleAI/common/NoPhotosInScope=No photos found in the selected scope."),
				"info"
			)
			log:warn("AI Edit task found no photos in scope: " .. tostring(options.scope))
			return
		end

		local progressScope = LrProgressScope({
			title = LOC("$$$/StyleAI/TaskAiEditPhotos/ProgressTitle=Generating AI Lightroom edits..."),
			functionContext = ctx,
		})

		if #photos >= 50 then
			log:info("Triggering database autosave before processing " .. tostring(#photos) .. " photos")
			progressScope:setCaption(LOC("$$$/StyleAI/TaskAiEditPhotos/CreatingSafetyBackup=Creating safety backup..."))
			local SearchIndexAPI = require("APISearchIndex")
			local prefs = import("LrPrefs").prefsForPlugin(_PLUGIN)
			SearchIndexAPI.triggerBackup(prefs.backupRotationDays)
		end

		local PrivacyPreview = require("PrivacyPreview")
		if options.blurFacesForCloud and options.previewBlurredFaces then
			if not PrivacyPreview.showPreviewFlow(photos, progressScope, options.faceBlurSensitivity) then
				progressScope:done()
				return
			end
		end

		progressScope:setCaption(LOC("$$$/StyleAI/TaskAiEditPhotos/ProgressTitle=Generating AI Lightroom edits..."))
		progressScope:setPortionComplete(0, #photos)

		local successCount = 0
		local skippedCount = 0
		local errorCount = 0
		local errorMessages = {}
		local backendWarnings = {}

		-- Queue state
		local userContexts = {}
		local contextReady = {}
		local results = {}
		local producerDone = false

		-- Pre-fill contexts if no dialog is needed
		if not options.showPhotoContextDialog then
			for i, p in ipairs(photos) do
				userContexts[i] = p:getPropertyForPlugin(_PLUGIN, "photoContext") or ""
				contextReady[i] = true
			end
		end

		local consumerIndex = 1
		local nextIndexToProcess = 1
		local activeProducers = 0
		local profile = tonumber(prefs.indexingPerformanceProfile) or 2
		local maxWorkers = profile * 2
		
		if options.model and (string.find(string.lower(options.model), "lmstudio") or string.find(string.lower(options.model), "ollama")) then
			-- Local LLMs process sequentially. Limit concurrent requests to prevent Waitress deadlock and HTTP timeouts
			maxWorkers = math.min(profile * 2, 4)
			log:info("Local LLM detected. Capping edit producers to " .. tostring(maxWorkers) .. " to prevent Waitress thread exhaustion.")
		end

		local function producerWorker()
			activeProducers = activeProducers + 1
			while not progressScope:isCanceled() do
				local index = nextIndexToProcess
				if index > #photos then break end

				-- Throttle to avoid unbounded memory/disk usage (max workers ahead of consumer)
				if index > consumerIndex + (maxWorkers * 2) then
					LrTasks.yield()
					LrTasks.sleep(0.1)
				else
					nextIndexToProcess = nextIndexToProcess + 1

					-- Wait for consumer to provide context (if dialogs are pending)
					while not contextReady[index] and not progressScope:isCanceled() do
						LrTasks.yield()
						LrTasks.sleep(0.1)
					end
					if progressScope:isCanceled() then break end

					local userContext = userContexts[index]
					local photo = photos[index]
					local fileName = photo:getFormattedMetadata("fileName") or "Photo"
					local resultObj = { fileName = fileName, continueProcessing = true }

					local photoId, photoIdErr = SearchIndexAPI.getPhotoIdForPhoto(photo)
					if not photoId then
						log:error("Failed to resolve photo ID for " .. fileName .. ": " .. tostring(photoIdErr))
						resultObj.errorMsg = fileName .. ": " .. tostring(photoIdErr)
						resultObj.continueProcessing = false
					else
						local photoOptions = enrichPhotoOptions(photo, options, userContext)
						local okSettings, currentSettings = LrTasks.pcall(function()
							local settings = nil
							catalog:withReadAccessDo("getDevelopSettings", function()
								settings = photo:getDevelopSettings()
							end)
							return settings
						end)
						if okSettings and currentSettings then
							photoOptions.current_settings = currentSettings

						end
						local base_path, dark_path, bright_path = SearchIndexAPI.exportBracketedPhotosForIndexing(photo, photoId)
						if not base_path then
							log:error("Failed to export photo for AI edit generation: " .. fileName)
							resultObj.errorMsg = fileName .. ": export failed"
							resultObj.continueProcessing = false
						else
							photoOptions.darkPath = dark_path
							photoOptions.brightPath = bright_path

							local ok, apiOk, apiResponse = LrTasks.pcall(function()
								photoOptions.blurFacesForCloud = options.blurFacesForCloud
								photoOptions.faceBlurSensitivity = options.faceBlurSensitivity
								photoOptions.isBatchProcessing = (#photos > 1)
								if photoOptions.enableQuickEdit then
									photoOptions.style_strength = photoOptions.quickEditStyleStrength
									return SearchIndexAPI.styleEdit(photoId, base_path, photoOptions)
								else
									return SearchIndexAPI.generateEditRecipePhoto(photoId, base_path, photoOptions)
								end
							end)

							LrTasks.pcall(function()
								if base_path and LrFileUtils.exists(base_path) then LrFileUtils.delete(base_path) end
								if photoOptions.darkPath and LrFileUtils.exists(photoOptions.darkPath) then LrFileUtils.delete(photoOptions.darkPath) end
								if photoOptions.brightPath and LrFileUtils.exists(photoOptions.brightPath) then LrFileUtils.delete(photoOptions.brightPath) end
							end)

							if not ok then
								log:error("AI edit generation threw for " .. fileName .. ": " .. tostring(apiOk))
								resultObj.errorMsg = fileName .. ": exception thrown: " .. tostring(apiOk)
								resultObj.continueProcessing = false
							else
								if apiOk and type(apiResponse) == "table" and apiResponse.status == "error" and apiResponse.error == "profile_mismatch" then
									-- Default button is "Cancel" so it is highlighted
									local action = LrDialogs.confirm(
										LOC("$$$/StyleAI/TaskAiEditPhotos/ProfileMismatchTitle=Camera Profile Mismatch"),
										LOC("$$$/StyleAI/TaskAiEditPhotos/ProfileMismatchMessage=No training examples exist for the camera profile used by ^1. Do you want to proceed with a 100% LLM-based edit?", fileName),
										LOC("$$$/StyleAI/TaskAiEditPhotos/Cancel=Cancel"),
										LOC("$$$/StyleAI/TaskAiEditPhotos/ProceedWithLLM=Proceed with LLM")
									)
									if action == "cancel" then -- They clicked the secondary "Proceed with LLM" button
										local llmOk, llmApiOk, llmResponse = LrTasks.pcall(function()
											photoOptions.use_training_style = false
											return SearchIndexAPI.generateEditRecipePhoto(photoId, base_path, photoOptions)
										end)
										ok = llmOk
										apiOk = llmApiOk
										apiResponse = llmResponse
									else
										apiResponse = { status = "error", error = "Cancelled due to profile mismatch" }
									end
								end

								resultObj.response = apiResponse
								if apiResponse and apiResponse.warning then
									resultObj.warning = fileName .. ": " .. tostring(apiResponse.warning)
								end

								if not apiOk or not apiResponse or type(apiResponse) ~= "table" or apiResponse.status ~= "success" then
									local errMsg = "Unknown error"
									if not apiOk then errMsg = tostring(apiResponse)
									elseif type(apiResponse) == "string" then errMsg = apiResponse
									elseif apiResponse and apiResponse.error then errMsg = apiResponse.error end
									resultObj.errorMsg = fileName .. ": " .. errMsg
									resultObj.continueProcessing = false
								end
							end
						end
					end

					results[index] = resultObj
				end
			end
			activeProducers = activeProducers - 1
			if activeProducers <= 0 then
				producerDone = true
			end
		end

		for i = 1, maxWorkers do
			LrTasks.startAsyncTask(function()
				LrFunctionContext.callWithContext("ProducerTask_" .. tostring(i), function(producerCtx)
					producerWorker()
				end)
			end)
		end

		local reuseContext = false
		local sharedContext = ""

		for index, photo in ipairs(photos) do
			if progressScope:isCanceled() then break end

			consumerIndex = index
			local fileName = photo:getFormattedMetadata("fileName") or "Photo"
			progressScope:setCaption("Processing " .. fileName .. " (" .. tostring(index) .. " of " .. tostring(#photos) .. ")")
			progressScope:setPortionComplete(index - 1, #photos)

			if not contextReady[index] then
				local userContext = photo:getPropertyForPlugin(_PLUGIN, "photoContext") or ""
				if not reuseContext then
					local result
					result, sharedContext, reuseContext = showPhotoInstructionDialog(ctx, photo)
					if result == "cancel" then
						progressScope:done()
						return
					end
				end
				userContext = sharedContext or ""
				LrApplication.activeCatalog():withPrivateWriteAccessDo(function()
					photo:setPropertyForPlugin(_PLUGIN, "photoContext", userContext)
				end, Defaults.catalogWriteAccessOptions)

				userContexts[index] = userContext
				contextReady[index] = true

				-- If reuseContext was just set, unlock the rest of the queue
				if reuseContext then
					for j = index + 1, #photos do
						userContexts[j] = sharedContext or ""
						contextReady[j] = true
					end
				end
			end

			-- Wait for producer to finish this photo
			while results[index] == nil and not producerDone and not progressScope:isCanceled() do
				LrTasks.sleep(0.1)
			end

			if progressScope:isCanceled() then break end

			local res = results[index]
			if not res then break end

			if res.warning then
				table.insert(backendWarnings, res.warning)
			end

			if not res.continueProcessing then
				if res.errorMsg then
					table.insert(errorMessages, res.errorMsg)
				end
				errorCount = errorCount + 1
			else
				local response = res.response
				local okPersist, persistErr = LrTasks.pcall(function()
					DevelopEditManager.persistEditRecipe(photo, response, nil, "generated")
				end)
				if not okPersist then
					log:error("Persist generated recipe threw for " .. fileName .. ": " .. tostring(persistErr))
					table.insert(errorMessages, fileName .. ": could not persist recipe: " .. tostring(persistErr))
					errorCount = errorCount + 1
				else
					local applyOptions = { applyGlobal = true, applyMasks = options.applyMasks }

					if options.reviewBeforeApply then
						local result, validated = DevelopEditManager.showValidationDialog(ctx, photo, response, options)
						if result == "cancel" then
							skippedCount = skippedCount + 1
							res.continueProcessing = false
						elseif validated then
							applyOptions = validated
						end
					end

					if res.continueProcessing and not applyOptions.applyGlobal and not applyOptions.applyMasks then
						skippedCount = skippedCount + 1
						res.continueProcessing = false
					end

					if res.continueProcessing then
						local applied, warnings = DevelopEditManager.applyRecipe(photo, response, applyOptions)
						if applied then
							successCount = successCount + 1
						else
							errorCount = errorCount + 1
							table.insert(errorMessages, fileName .. ": failed to apply recipe")
						end
						if warnings and #warnings > 0 then
							log:warn("AI edit warnings for " .. fileName .. ": " .. table.concat(warnings, " | "))
						end
					end
				end
			end
		end

		progressScope:done()

		if errorCount > 0 or #backendWarnings > 0 then
			local uniqueErrors = {}
			local errorList = {}
			for _, msg in ipairs(errorMessages) do
				if not uniqueErrors[msg] then
					uniqueErrors[msg] = true
					table.insert(errorList, "- " .. msg)
					if #errorList >= 5 then
						break
					end
				end
			end

			local combinedReport =
				LOC("$$$/StyleAI/TaskAiEditPhotos/Summary=Applied edits to ^1 photo(s).", tostring(successCount))
			if skippedCount > 0 then
				combinedReport = combinedReport
					.. "\n"
					.. LOC("$$$/StyleAI/common/Skipped=Skipped: ^1", tostring(skippedCount))
			end
			if errorCount > 0 then
				combinedReport = combinedReport
					.. "\n"
					.. LOC("$$$/StyleAI/common/Errors=Errors: ^1", tostring(errorCount))
			end

			if #errorList > 0 then
				combinedReport = combinedReport
					.. "\n\n"
					.. LOC("$$$/StyleAI/common/ErrorDetails=Error details:")
					.. "\n"
					.. table.concat(errorList, "\n")
				if #errorMessages > 5 then
					combinedReport = combinedReport
						.. "\n"
						.. LOC("$$$/StyleAI/common/MoreErrors=... and ^1 more errors", tostring(#errorMessages - 5))
				end
			end

			if #backendWarnings > 0 then
				combinedReport = combinedReport
					.. "\n\n"
					.. LOC("$$$/StyleAI/common/BackendWarnings=Backend Warnings:")
					.. "\n"
				for i = 1, math.min(5, #backendWarnings) do
					combinedReport = combinedReport .. "- " .. backendWarnings[i] .. "\n"
				end
				if #backendWarnings > 5 then
					combinedReport = combinedReport
						.. LOC(
							"$$$/StyleAI/common/MoreWarnings=... and ^1 more warnings",
							tostring(#backendWarnings - 5)
						)
				end
			end

			if errorCount > 0 then
				ErrorHandler.handleError(
					LOC("$$$/StyleAI/TaskAiEditPhotos/CompletionTitle=AI Edit Completed"),
					combinedReport
				)
			else
				LrDialogs.message(
					LOC("$$$/StyleAI/TaskAiEditPhotos/CompletionTitle=AI Edit Completed"),
					combinedReport,
					"warning"
				)
			end
		else
			LrDialogs.message(
				LOC("$$$/StyleAI/TaskAiEditPhotos/SuccessTitle=AI Lightroom Edit"),
				LOC(
					"$$$/StyleAI/TaskAiEditPhotos/SuccessSummary=Applied edits to ^1 photo(s).\nSkipped: ^2",
					tostring(successCount),
					tostring(skippedCount)
				),
				"info"
			)
		end
		log:info(
			"AI Edit task completed. success="
				.. tostring(successCount)
				.. " skipped="
				.. tostring(skippedCount)
				.. " errors="
				.. tostring(errorCount)
		)
	end)
end)
