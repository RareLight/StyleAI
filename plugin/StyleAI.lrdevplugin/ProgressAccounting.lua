local ProgressAccounting = {}

local function count(value)
    value = tonumber(value) or 0
    if value < 0 then return 0 end
    return math.floor(value)
end

local function bounded(success, failed, total)
    total = count(total)
    success = math.min(count(success), total)
    failed = math.min(count(failed), math.max(0, total - success))
    return {
        success = success,
        failed = failed,
        processed = success + failed,
        total = total,
    }
end

function ProgressAccounting.live(preSuccess, preFailed, itemStateCounts, total)
    itemStateCounts = itemStateCounts or {}
    local succeeded = count(itemStateCounts.succeeded) + count(itemStateCounts.committing)
    local failed = count(preFailed)
        + count(itemStateCounts.failed)
        + count(itemStateCounts.canceled)
        + count(itemStateCounts.interrupted)
    return bounded(count(preSuccess) + succeeded, failed, total)
end

function ProgressAccounting.terminal(preSuccess, preFailed, succeeded, operationFailed, total)
    return bounded(
        count(preSuccess) + count(succeeded),
        count(preFailed) + count(operationFailed),
        total
    )
end

return ProgressAccounting
