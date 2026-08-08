-- Shared Lightroom UI helpers for learned-style and recommendation workspaces.

local StyleUI = {}

function StyleUI.resolveSelectedIndex(rawValue, items)
	local index = tonumber(rawValue)
	if type(rawValue) == "table" then
		if rawValue.value then
			index = tonumber(rawValue.value)
		elseif rawValue[1] ~= nil then
			index = tonumber(rawValue[1])
		else
			for _, item in ipairs(items or {}) do
				if item == rawValue or item.title == rawValue.title then
					index = tonumber(item.value)
					break
				end
			end
		end
	end
	return index
end

function StyleUI.keepSelection(items, currentValue)
	local current = StyleUI.resolveSelectedIndex(currentValue, items)
	for _, item in ipairs(items or {}) do
		if tonumber(item.value) == current then
			return current
		end
	end
	return items and items[1] and items[1].value or 0
end

function StyleUI.filteredListGroup(f, props)
	-- Lightroom simple_list and popup_menu controls are unreliable when they
	-- share dynamic widths with mixed controls. Keep this one tested, bounded
	-- width while allowing the surrounding resizable dialog to grow naturally.
	local listWidth = props.listWidth or 600
	local fillVertical = props.fillVertical == false and nil or (props.fillVertical or 1)
	return f:group_box({
		title = props.title,
		fill_horizontal = 1,
		fill_vertical = fillVertical,
		f:column({
			spacing = f:control_spacing(),
			fill_horizontal = 1,
			fill_vertical = fillVertical,
			f:static_text({ title = props.filterLabel }),
			f:popup_menu({
				items = props.filterItems,
				value = props.filterValue,
				width = listWidth,
			}),
			f:simple_list({
				items = props.listItems,
				value = props.selectedValue,
				allows_multiple_selection = false,
				height_in_lines = props.heightInLines or 12,
				width = listWidth,
			}),
		}),
	})
end

return StyleUI
