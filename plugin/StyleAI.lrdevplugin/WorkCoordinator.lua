-- WorkCoordinator.lua
-- Process-wide cooperative lanes for Lightroom SDK resources. Backend compute
-- admission is handled separately by the catalog-local operation controller.

local WorkCoordinator = {}

local lanes = {
    render = { limit = 1, inUse = 0, queue = {} },
    catalog_write = { limit = 1, inUse = 0, queue = {} },
    develop_ui = { limit = 1, inUse = 0, queue = {} },
}
local nextTicketId = 0

local function laneFor(name)
    if not lanes[name] then
        lanes[name] = { limit = 1, inUse = 0, queue = {} }
    end
    return lanes[name]
end

local function removeTicket(lane, ticket)
    for index, queued in ipairs(lane.queue) do
        if queued == ticket then
            table.remove(lane.queue, index)
            return
        end
    end
end

function WorkCoordinator.acquire(name, progressScope)
    local lane = laneFor(name)
    nextTicketId = nextTicketId + 1
    local ticket = { id = nextTicketId, lane = name, released = false }
    table.insert(lane.queue, ticket)

    while true do
        if progressScope and progressScope:isCanceled() then
            removeTicket(lane, ticket)
            return nil, "canceled"
        end
        if lane.queue[1] == ticket and lane.inUse < lane.limit then
            table.remove(lane.queue, 1)
            lane.inUse = lane.inUse + 1
            return ticket
        end
        LrTasks.yield()
        LrTasks.sleep(0.01)
    end
end

function WorkCoordinator.configureLane(name, limit)
    local lane = laneFor(name)
    lane.limit = math.max(1, math.floor(tonumber(limit) or 1))
    return lane.limit
end

function WorkCoordinator.release(ticket)
    if not ticket or ticket.released then return end
    ticket.released = true
    local lane = laneFor(ticket.lane)
    lane.inUse = math.max(0, lane.inUse - 1)
end

function WorkCoordinator.acquirePhoto(kind, photoId, progressScope)
    if not photoId or photoId == "" then return nil, "missing photo ID" end
    return WorkCoordinator.acquire("photo:" .. tostring(kind) .. ":" .. tostring(photoId), progressScope)
end

function WorkCoordinator.snapshot()
    local result = {}
    for name, lane in pairs(lanes) do
        result[name] = {
            limit = lane.limit,
            in_use = lane.inUse,
            waiting = #lane.queue,
        }
    end
    return result
end

return WorkCoordinator
