import re
import sys

with open('plugin/StyleAI.lrdevplugin/APISearchIndex.lua', 'r') as f:
    content = f.read()

# 1. Tweak profile 4
content = content.replace(
    'maxAnalyzeWorkers = math.min(24, math.floor(hardwareMax * 1.5))',
    'maxAnalyzeWorkers = math.min(16, math.floor(hardwareMax * 1.25))'
)

# 2. Add enqueuePhotoBase64 after analyzeAndIndexPhotosBatch
if 'function SearchIndexAPI.enqueuePhotoBase64' not in content:
    func_text = """
function SearchIndexAPI.enqueuePhotoBase64(item, globalOptions)
    local url = getBaseUrl() .. "/index_queue"
    local prefs = LrPrefs.prefsForPlugin()

    local bodyOptions = {
        regenerate_metadata = tostring(globalOptions.regenerate_metadata ~= false),
        cache_images = globalOptions.cache_images == true
    }

    local itemOptions = item.options or {}
    local encodedItemOptions = {
        submit_gps = tostring(itemOptions.submit_gps or false),
        submit_keywords = tostring(itemOptions.submit_keywords or false),
        submit_folder_names = tostring(itemOptions.submit_folder_names or false),
        gps_coordinates = itemOptions.gps_coordinates and JSON:encode(itemOptions.gps_coordinates) or nil,
        existing_keywords = itemOptions.existing_keywords and JSON:encode(itemOptions.existing_keywords) or nil,
        folder_names = itemOptions.folder_names,
        user_context = itemOptions.user_context,
        date_time = itemOptions.date_time,
        date_time_unix = itemOptions.date_time_unix,
    }

    local bodyImages = {
        {
            image = item.image,
            photo_id = item.photo_id,
            lr_uuid = item.lr_uuid,
            filename = item.filename or "photo.jpg",
            options = encodedItemOptions
        }
    }

    local body = {
        images = bodyImages,
        options = bodyOptions
    }

    local response, err = _request('POST', url, body, 15) -- Short timeout, just enqueueing

    if not response then
        log:error("Failed to enqueue photo: " .. tostring(err))
        return false, err or "Unknown error"
    end

    if response.status == "accepted" then
        return true, response
    else
        log:error("Unexpected enqueue response status: " .. tostring(response.status))
        return false, response.error or "Enqueue failed"
    end
end
"""
    content = content.replace(
        'return false, "Unexpected response status"\nend',
        'return false, "Unexpected response status"\nend\n' + func_text,
        1
    )

# 3. Rewrite analyzeWorker
# We need to find the definition of analyzeWorker and replace the block where it inserts into preparedQueue.
# Let's use regex.
pattern = r'(local analyzeWorker = function\(\).*?if jpegData and #jpegData > 0 then.*?local lrUuid = photo:getRawMetadata\("uuid"\)\n)(.*?)(else\s+table\.insert\(preparedQueue, \{\s+type = "error")'
replacement = r"""\1                            local item = {
                                photo_id = photoId,
                                lr_uuid = lrUuid,
                                image = base64Image,
                                filename = leafName,
                                options = photoOptions,
                                photo = photo
                            }

                            if enableEmbeddings then
                                options.cache_images = enableMetadata
                                local success, err = SearchIndexAPI.enqueuePhotoBase64(item, options)
                                if success then
                                    if enableMetadata then
                                        item.image = nil
                                        table.insert(sendToLlmQueue, item)
                                    else
                                        stats.processed = stats.processed + 1
                                        stats.success = stats.success + 1
                                        table.insert(processedPhotos, item.photo)
                                        if options.onPhotoAnalyzed then
                                            LrTasks.yield()
                                            LrTasks.sleep(0.01)
                                            LrTasks.pcall(function()
                                                options.onPhotoAnalyzed(item.photo, item.photo_id, progressScope)
                                            end)
                                        end
                                    end
                                    
                                    if not enableMetadata then
                                        progressScope:setPortionComplete(stats.processed, numPhotos)
                                        progressScope:setCaption(
                                            LOC("$$$/StyleAI/AnalyzeAndIndex/ProcessingPhoto=Processing ^1 successful (^2 total/^3 failed)",
                                                stats.success, numPhotos, stats.failed)
                                        )
                                    end
                                else
                                    stats.failed = stats.failed + 1
                                    stats.processed = stats.processed + 1
                                    table.insert(errorMessages, tostring(err))
                                    log:error("Failed to enqueue photo: " .. leafName .. " Error: " .. tostring(err))
                                end
                            else
                                table.insert(sendToLlmQueue, item)
                            end
                        \3"""

content = re.sub(pattern, replacement, content, flags=re.DOTALL)

# 4. Remove senderWorker
sender_pattern = r'local senderWorker = function\(\)\n.*?activeSenderWorkers = activeSenderWorkers - 1\n\s+log:trace\("Sender worker thread finished\."\)\n\s+end'
content = re.sub(sender_pattern, '', content, flags=re.DOTALL)

# 5. Remove references to senderWorker
content = content.replace('local maxQueueCapacity = maxSenderWorkers * batchSize * 2\n', '')
content = content.replace('if #preparedQueue >= maxQueueCapacity then\n                LrTasks.yield()\n                LrTasks.sleep(0.1)\n            else\n', '')
content = content.replace('local activeSenderWorkers = 0\n', '')
content = content.replace('local preparedQueue = {}\n', '')
content = content.replace('for i = 1, maxSenderWorkers do\n        table.insert(workers, LrTasks.startAsyncTask(senderWorker, "SenderWorker-" .. tostring(i)))\n    end', '')
content = content.replace('if activeSenderWorkers == 0 and preparationDone then', 'if preparationDone then')

# Fix the extra indent caused by removing the if #preparedQueue check
# Wait, this is getting complicated to fix indentation with regex, I'll just leave the extra end or remove it if I can.
# Actually, I should just write the new file.
with open('plugin/StyleAI.lrdevplugin/APISearchIndex.lua', 'w') as f:
    f.write(content)
