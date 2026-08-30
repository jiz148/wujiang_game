// HTTP access to the backend plus the formatting helpers every screen uses.
import { screenRoute } from '../core/router.js';
import { ANALYTICS_SESSION_KEY, ROOM_NAME_PREFIX, ROOM_TOKEN_PREFIX, state } from '../core/state.js';
import { positionKey, selectedAction } from '../tactical/vfx.js';
import { $ } from './dom.js';


export async function fetchJson(url, options = {}) {
  const { timeoutMs = 0, ...rest } = options;
  const headers = {
    "Content-Type": "application/json",
    ...(state.authToken ? { Authorization: `Bearer ${state.authToken}` } : {}),
    ...(rest.headers || {}),
  };
  const controller = timeoutMs ? new AbortController() : null;
  const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : 0;
  try {
    const response = await fetch(url, {
      ...rest,
      headers,
      signal: rest.signal || controller?.signal,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw payload;
    }
    return payload;
  } catch (error) {
    if (error?.name === "AbortError") {
      throw { error: "请求超时。" };
    }
    throw error;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export function hasBattle() {
  return Boolean(state.battle);
}

export function hasRoom() {
  return Boolean(state.room);
}

export function replayMeta() {
  return state.room?.replay || {
    available: false,
    step_count: 0,
    last_step_index: 0,
    can_use_omniscient: false,
  };
}

export function simulationMeta() {
  return state.room?.simulation || {
    enabled: false,
    paused: false,
    speed: 1,
    can_control: false,
    live_step_index: 0,
    speed_options: [0.5, 1, 2, 4],
  };
}

export function isReplayMode() {
  return Boolean(state.replayMode && replayMeta().available);
}

export function viewerPlayerId() {
  return state.room?.viewer_player_id ?? null;
}

export function viewerTeamId() {
  return state.room?.viewer_team_id ?? state.room?.viewer_player_id ?? null;
}

export function isGameOver() {
  return Boolean(state.battle?.winner);
}

export function canInteract() {
  return Boolean(
    state.battle
      && state.screen === "battle"
      && !isGameOver()
      && !isReplayMode()
      && viewerTeamId() !== null
      && viewerTeamId() === inputPlayer(),
  );
}

export function inputPlayer() {
  return state.battle?.input_player ?? 1;
}

export function isChainMode() {
  return Boolean(state.battle?.pending_chain);
}

export function currentRespawnPrompt() {
  return state.battle?.pending_respawn || null;
}

export function isRespawnMode() {
  return Boolean(currentRespawnPrompt());
}

export function activeBundles() {
  return state.battle?.active_units ?? [];
}

export function bundleFor(unitId) {
  return activeBundles().find((entry) => entry.unit_id === unitId) || null;
}

export function allUnits() {
  return state.battle?.units ?? [];
}

export function unitById(unitId) {
  return allUnits().find((unit) => unit.id === unitId) || null;
}

export function hoveredUnit() {
  return unitById(state.hoveredUnitId);
}

function effectiveSidebarPanel() {
  return state.rightRailCollapsed ? "" : "logs";
}

function analyticsSessionId() {
  let sessionId = localStorage.getItem(ANALYTICS_SESSION_KEY) || "";
  if (!sessionId) {
    sessionId = globalThis.crypto?.randomUUID?.()
      || `visitor-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    localStorage.setItem(ANALYTICS_SESSION_KEY, sessionId);
  }
  return sessionId;
}

export async function recordProductEvent(eventName, properties = {}) {
  try {
    await fetchJson("/api/analytics/events", {
      method: "POST",
      body: JSON.stringify({
        event_name: eventName,
        anonymous_session_id: analyticsSessionId(),
        properties,
      }),
    });
  } catch (_error) {
    // Analytics must never block the player path.
  }
}

export function recordStrategyEventOnce(campaign, suffix, eventName, properties = {}) {
  if (!campaign?.id) return;
  const key = `wujiang-strategy-analytics-${campaign.id}-${suffix}`;
  if (localStorage.getItem(key)) return;
  localStorage.setItem(key, "1");
  recordProductEvent(eventName, { campaign_id: String(campaign.id), ...properties });
}

export function recordStrategyConclusionIfNeeded(campaign) {
  const conclusion = campaign?.world?.strategic_status?.conclusion;
  if (!conclusion?.state) return;
  recordStrategyEventOnce(campaign, "complete", "strategy_campaign_complete", {
    month: String(conclusion.concluded_month || campaign.world?.current_month || ""),
    reason: conclusion.reason || "unknown",
  });
}

export function toggleSidebarPanel(panel) {
  if (panel !== "logs") return;
  state.rightRailCollapsed = !state.rightRailCollapsed;
}

export function activeOccupantAt(x, y) {
  const occupants = unitsAtCell(x, y);
  return occupants.find((unit) => !unitIsStealthed(unit)) || occupants[0] || null;
}

export function visibleUnitAt(x, y) {
  const occupants = unitsAtCell(x, y);
  return occupants.find((unit) => !unitIsStealthed(unit)) || occupants[0] || null;
}

export function unitsAtCell(x, y) {
  return allUnits().filter(
    (unit) => !unit.banished && unitOccupiedCells(unit).some((cell) => cell.x === x && cell.y === y),
  );
}

export function unitsCanOverlapOnBoard(left, right) {
  if (!left || !right || left.id === right.id) return false;
  return left.mounted_on_unit_id === right.id
    || right.mounted_on_unit_id === left.id
    || left.ridden_by_unit_id === right.id
    || right.ridden_by_unit_id === left.id
    || (left.allow_enemy_destination_overlap && left.player_id !== right.player_id)
    || (right.allow_enemy_destination_overlap && right.player_id !== left.player_id);
}

export function boardPieceZIndex(unit) {
  if (!unit) return 6;
  if (unit.ridden_by_unit_id) return 5;
  if (unit.mounted_on_unit_id) return 7;
  return 6;
}

function unitFootprintSize(unit) {
  const occupied = unitOccupiedCells(unit);
  if (occupied.length) return unitFootprintBounds(unit);
  const footprint = unit?.footprint || {};
  if (Number(footprint.width) > 0 && Number(footprint.height) > 0) return { width: Number(footprint.width), height: Number(footprint.height) };
  return { width: 1, height: 1 };
}

function unitFootprintOffsets(unit) {
  const footprint = unit?.footprint || {};
  if (Array.isArray(footprint.offsets) && footprint.offsets.length) {
    return footprint.offsets
      .filter((cell) => cell && cell.x != null && cell.y != null)
      .map((cell) => ({ x: Number(cell.x), y: Number(cell.y) }));
  }
  const occupied = unitOccupiedCells(unit);
  if (unit?.position && occupied.length) {
    return occupied.map((cell) => ({
      x: Number(cell.x) - Number(unit.position.x),
      y: Number(cell.y) - Number(unit.position.y),
    }));
  }
  const width = Number(footprint.width || 1);
  const height = Number(footprint.height || 1);
  const offsets = [];
  for (let dx = 0; dx < width; dx += 1) {
    for (let dy = 0; dy < height; dy += 1) {
      offsets.push({ x: dx, y: dy });
    }
  }
  return offsets;
}

export function unitFootprintCellsAt(unit, anchor) {
  if (!unit || !anchor) return [];
  return unitFootprintOffsets(unit).map((offset) => ({
    x: Number(anchor.x) + Number(offset.x),
    y: Number(anchor.y) + Number(offset.y),
  }));
}

export function unitHasLargeFootprint(unit) {
  const occupied = unitOccupiedCells(unit);
  const { width, height } = unitFootprintSize(unit);
  return occupied.length > 1 || width > 1 || height > 1;
}

export function unitOccupiedCells(unit) {
  if (!unit?.position) return [];
  if (Array.isArray(unit.occupied_cells) && unit.occupied_cells.length) {
    return unit.occupied_cells.filter((cell) => cell && cell.x != null && cell.y != null);
  }
  return [unit.position];
}

export function unitFootprintBounds(unit) {
  const occupied = unitOccupiedCells(unit);
  if (!occupied.length) {
    const x = Number(unit?.position?.x || 0);
    const y = Number(unit?.position?.y || 0);
    return { minX: x, minY: y, maxX: x, maxY: y, width: 1, height: 1 };
  }
  const xs = occupied.map((cell) => Number(cell.x));
  const ys = occupied.map((cell) => Number(cell.y));
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const maxX = Math.max(...xs);
  const maxY = Math.max(...ys);
  return { minX, minY, maxX, maxY, width: maxX - minX + 1, height: maxY - minY + 1 };
}

function unitIsStealthed(unit) {
  return Boolean(unit?.statuses?.some((status) => status.name === "隐身"));
}

export function selectedUnit() {
  return unitById(state.selectedUnitId);
}

export function inspectedUnit() {
  return unitById(state.inspectedUnitId) || selectedUnit();
}

export function inspectBoardUnit(unit, { openInfo = true, adoptIfControllable = false } = {}) {
  if (!unit) {
    state.inspectedUnitId = "";
    return;
  }
  state.inspectedUnitId = unit.id;
  if (openInfo) {
    state.battleDockTab = "info";
    state.rightRailCollapsed = false;
    state.sidebarExpanded = "info";
  }
  if (!adoptIfControllable) return;
  const controllable = activeBundles().map((entry) => entry.unit_id);
  if (!controllable.length || controllable.includes(unit.id)) {
    state.selectedUnitId = unit.id;
  }
}

export function stagedTarget() {
  return unitById(state.stagedPayload?.targetUnitId || "");
}

export function normalizedCell(cell) {
  if (!cell || cell.x == null || cell.y == null) return null;
  return { x: Number(cell.x), y: Number(cell.y) };
}

export function stagedBackstepRetreatCell(action = selectedAction()) {
  if (!action || action.code !== "backstep_shot" || !isChainMode() || state.selectedActionCode !== action.code) {
    return null;
  }
  return normalizedCell(state.stagedPayload?.retreatCell);
}

export function setStagedBackstepRetreatCell(cell) {
  const normalized = normalizedCell(cell);
  state.stagedPayload = normalized ? { retreatCell: normalized } : null;
}

export function stagedBackstepTargetId(action = selectedAction()) {
  if (!action || action.code !== "backstep_shot" || !isChainMode() || state.selectedActionCode !== action.code) {
    return "";
  }
  return String(state.stagedPayload?.targetUnitId || "").trim();
}

function setStagedBackstepTargetId(targetUnitId) {
  const retreatCell = stagedBackstepRetreatCell();
  const nextTargetUnitId = String(targetUnitId || "").trim();
  if (!retreatCell && !nextTargetUnitId) {
    state.stagedPayload = null;
    return;
  }
  state.stagedPayload = {
    ...(retreatCell ? { retreatCell } : {}),
    ...(nextTargetUnitId ? { targetUnitId: nextTargetUnitId } : {}),
  };
}

export function backstepFollowUpTargetIds(action, retreatCell = stagedBackstepRetreatCell(action)) {
  if (!action || action.code !== "backstep_shot" || !retreatCell) return [];
  const mapping = action.preview?.follow_up_target_ids_by_cell || {};
  const ids = mapping[positionKey(retreatCell)];
  return Array.isArray(ids) ? ids : [];
}

export function backstepSelectionCanComplete(action, retreatCell = stagedBackstepRetreatCell(action)) {
  return Boolean(retreatCell);
}

export function roomQueryId() {
  const rawSearch = String(window?.location?.search || "");
  if (typeof URLSearchParams !== "undefined") {
    const roomId = new URLSearchParams(rawSearch).get("room");
    return roomId ? roomId.trim().toUpperCase() : "";
  }
  const match = rawSearch.match(/[?&]room=([^&#]+)/i);
  const roomId = match ? decodeURIComponent(match[1]) : "";
  return roomId ? roomId.trim().toUpperCase() : "";
}

export function syncLocation(screen = state.screen, roomId = roomQueryId()) {
  const url = new URL(window.location.href);
  if (roomId) {
    url.searchParams.set("room", roomId);
  } else {
    url.searchParams.delete("room");
  }
  const route = screenRoute(screen);
  url.hash = route ? `#${route}` : "";
  history.replaceState(null, "", url);
}

export function roomTokenKey(roomId) {
  return `${ROOM_TOKEN_PREFIX}${roomId}`;
}

export function roomNameKey(roomId) {
  return `${ROOM_NAME_PREFIX}${roomId}`;
}
