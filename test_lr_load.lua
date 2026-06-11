-- Mock LR environment
_G.import = function() return setmetatable({}, {__index=function() return function()end end, __call=function() return {} end}) end
_G.LOC = function() return "" end
_G.LrTasks = {startAsyncTask=function()end, yield=function()end, sleep=function()end}
_G.LrDate = {currentTime=function()return 0 end}
_G.LrPathUtils = {}
_G.LrFileUtils = {exists=function()return false end}
_G.LrDialogs = {message=function()end}
_G.LrHttp = {}
_G.log = {trace=function()end, info=function()end, warn=function()end, error=function()end, enable=function()end}
_G.MAC_ENV = true
_G.WIN_ENV = false
_G.prefs = {logging=true}
_G.LrPrefs = {prefsForPlugin=function() return prefs end}

-- Add plugin dir to package path
package.path = package.path .. ";./plugin/StyleAI.lrdevplugin/?.lua"

-- Test Init.lua
dofile("plugin/StyleAI.lrdevplugin/Init.lua")
print("Init.lua loaded.")
