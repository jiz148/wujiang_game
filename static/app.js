const state = {
  heroes: [],
  rooms: [],
  room: null,
  battle: null,
  liveBattle: null,
  selectedUnitId: "",
  selectedActionCode: "",
  selectedActionSnapshot: null,
  hoveredActionCode: "",
  hoveredUnitId: "",
  hoverPointer: null,
  hoveredBoardCell: null,
  stagedPayload: null,
  screen: "draft",
  sidebarExpanded: "command",
  roomForm: {
    createName: "",
    joinName: "",
    joinRoomCode: "",
  },
  profileName: "",
  profileDraftName: "",
  profileReady: false,
  profileModalOpen: false,
  playerToken: "",
  lastSyncAt: 0,
  boardZoom: 1,
  lastSeenVisualEventId: 0,
  activeBattleVfx: [],
  replayMode: false,
  replayStepIndex: 0,
  replayOmniscient: false,
  randomRosterSizeDraft: "",
  roomEditSeatId: null,
};

const ROOM_TOKEN_PREFIX = "wujiang-room-token:";
const ROOM_NAME_PREFIX = "wujiang-room-name:";
const PROFILE_NAME_KEY = "wujiang-profile-name";
const PROFILE_READY_KEY = "wujiang-profile-ready";
let pollHandle = null;
let refreshInFlight = false;
let boardOverlayRenderHandle = 0;
let battleVfxCleanupHandle = 0;

const $ = (id) => document.getElementById(id);

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw payload;
  }
  return payload;
}

function hasBattle() {
  return Boolean(state.battle);
}

function hasRoom() {
  return Boolean(state.room);
}

function replayMeta() {
  return state.room?.replay || {
    available: false,
    step_count: 0,
    last_step_index: 0,
    can_use_omniscient: false,
  };
}

function simulationMeta() {
  return state.room?.simulation || {
    enabled: false,
    paused: false,
    speed: 1,
    can_control: false,
    live_step_index: 0,
    speed_options: [0.5, 1, 2, 4],
  };
}

function isReplayMode() {
  return Boolean(state.replayMode && replayMeta().available);
}

function viewerPlayerId() {
  return state.room?.viewer_player_id ?? null;
}

function viewerTeamId() {
  return state.room?.viewer_team_id ?? state.room?.viewer_player_id ?? null;
}

function isGameOver() {
  return Boolean(state.battle?.winner);
}

function canInteract() {
  return Boolean(
    state.battle
      && state.screen === "battle"
      && !isGameOver()
      && !isReplayMode()
      && viewerTeamId() !== null
      && viewerTeamId() === inputPlayer(),
  );
}

function inputPlayer() {
  return state.battle?.input_player ?? 1;
}

function isChainMode() {
  return Boolean(state.battle?.pending_chain);
}

function currentRespawnPrompt() {
  return state.battle?.pending_respawn || null;
}

function isRespawnMode() {
  return Boolean(currentRespawnPrompt());
}

function activeBundles() {
  return state.battle?.active_units ?? [];
}

function bundleFor(unitId) {
  return activeBundles().find((entry) => entry.unit_id === unitId) || null;
}

function allUnits() {
  return state.battle?.units ?? [];
}

function unitById(unitId) {
  return allUnits().find((unit) => unit.id === unitId) || null;
}

function hoveredUnit() {
  return unitById(state.hoveredUnitId);
}

function effectiveSidebarPanel() {
  return state.sidebarExpanded || (isChainMode() ? "command" : "");
}

function toggleSidebarPanel(panel) {
  state.sidebarExpanded = state.sidebarExpanded === panel ? "" : panel;
}

function activeOccupantAt(x, y) {
  const occupants = unitsAtCell(x, y);
  return occupants.find((unit) => !unitIsStealthed(unit)) || occupants[0] || null;
}

function visibleUnitAt(x, y) {
  const occupants = unitsAtCell(x, y);
  return occupants.find((unit) => !unitIsStealthed(unit)) || occupants[0] || null;
}

function unitsAtCell(x, y) {
  return allUnits().filter(
    (unit) => !unit.banished && unitOccupiedCells(unit).some((cell) => cell.x === x && cell.y === y),
  );
}

function unitsCanOverlapOnBoard(left, right) {
  if (!left || !right || left.id === right.id) return false;
  return left.mounted_on_unit_id === right.id
    || right.mounted_on_unit_id === left.id
    || left.ridden_by_unit_id === right.id
    || right.ridden_by_unit_id === left.id;
}

function boardPieceZIndex(unit) {
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

function unitFootprintCellsAt(unit, anchor) {
  if (!unit || !anchor) return [];
  return unitFootprintOffsets(unit).map((offset) => ({
    x: Number(anchor.x) + Number(offset.x),
    y: Number(anchor.y) + Number(offset.y),
  }));
}

function unitHasLargeFootprint(unit) {
  const occupied = unitOccupiedCells(unit);
  const { width, height } = unitFootprintSize(unit);
  return occupied.length > 1 || width > 1 || height > 1;
}

function unitOccupiedCells(unit) {
  if (!unit?.position) return [];
  if (Array.isArray(unit.occupied_cells) && unit.occupied_cells.length) {
    return unit.occupied_cells.filter((cell) => cell && cell.x != null && cell.y != null);
  }
  return [unit.position];
}

function unitFootprintBounds(unit) {
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

function selectedUnit() {
  return unitById(state.selectedUnitId);
}

function stagedTarget() {
  return unitById(state.stagedPayload?.targetUnitId || "");
}

function normalizedCell(cell) {
  if (!cell || cell.x == null || cell.y == null) return null;
  return { x: Number(cell.x), y: Number(cell.y) };
}

function stagedBackstepRetreatCell(action = selectedAction()) {
  if (!action || action.code !== "backstep_shot" || !isChainMode() || state.selectedActionCode !== action.code) {
    return null;
  }
  return normalizedCell(state.stagedPayload?.retreatCell);
}

function setStagedBackstepRetreatCell(cell) {
  const normalized = normalizedCell(cell);
  state.stagedPayload = normalized ? { retreatCell: normalized } : null;
}

function stagedBackstepTargetId(action = selectedAction()) {
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

function backstepFollowUpTargetIds(action, retreatCell = stagedBackstepRetreatCell(action)) {
  if (!action || action.code !== "backstep_shot" || !retreatCell) return [];
  const mapping = action.preview?.follow_up_target_ids_by_cell || {};
  const ids = mapping[positionKey(retreatCell)];
  return Array.isArray(ids) ? ids : [];
}

function backstepSelectionCanComplete(action, retreatCell = stagedBackstepRetreatCell(action)) {
  return Boolean(retreatCell);
}

function roomQueryId() {
  const rawSearch = String(window?.location?.search || "");
  if (typeof URLSearchParams !== "undefined") {
    const roomId = new URLSearchParams(rawSearch).get("room");
    return roomId ? roomId.trim().toUpperCase() : "";
  }
  const match = rawSearch.match(/[?&]room=([^&#]+)/i);
  const roomId = match ? decodeURIComponent(match[1]) : "";
  return roomId ? roomId.trim().toUpperCase() : "";
}

function screenHash(screen) {
  return screen === "battle" ? "#battle" : "#draft";
}

function syncLocation(screen = state.screen, roomId = roomQueryId()) {
  const url = new URL(window.location.href);
  if (roomId) {
    url.searchParams.set("room", roomId);
  } else {
    url.searchParams.delete("room");
  }
  url.hash = screenHash(screen);
  history.replaceState(null, "", url);
}

function roomTokenKey(roomId) {
  return `${ROOM_TOKEN_PREFIX}${roomId}`;
}

function roomNameKey(roomId) {
  return `${ROOM_NAME_PREFIX}${roomId}`;
}

function normalizeProfileName(name) {
  return String(name || "").trim().replace(/\s+/g, " ").slice(0, 20);
}

function effectiveProfileName() {
  return normalizeProfileName(state.profileName) || "未命名玩家";
}

function initializeProfileState() {
  state.profileName = sessionStorage.getItem(PROFILE_NAME_KEY) || "";
  state.profileDraftName = state.profileName;
  state.profileReady = sessionStorage.getItem(PROFILE_READY_KEY) === "1";
  state.profileModalOpen = !state.profileReady;
  state.roomForm.createName = state.profileName;
  state.roomForm.joinName = state.profileName;
}

function saveProfileName(rawName) {
  const normalized = normalizeProfileName(rawName);
  state.profileName = normalized;
  state.profileDraftName = normalized;
  state.profileReady = true;
  state.profileModalOpen = false;
  state.roomForm.createName = normalized;
  state.roomForm.joinName = normalized;
  sessionStorage.setItem(PROFILE_NAME_KEY, normalized);
  sessionStorage.setItem(PROFILE_READY_KEY, "1");
}

function openProfileModal() {
  state.profileDraftName = state.profileName;
  state.profileModalOpen = true;
}

function confirmProfile() {
  saveProfileName(state.profileDraftName);
  render();
}

function profileModalVisible() {
  return state.profileModalOpen || !state.profileReady;
}

function loadStoredIdentity(roomId) {
  if (!roomId) return { token: "", name: "" };
  const tokenKey = roomTokenKey(roomId);
  const nameKey = roomNameKey(roomId);
  return {
    token: localStorage.getItem(tokenKey) || sessionStorage.getItem(tokenKey) || "",
    name: localStorage.getItem(nameKey) || sessionStorage.getItem(nameKey) || "",
  };
}

function clearStoredIdentity(roomId) {
  if (!roomId) return;
  const tokenKey = roomTokenKey(roomId);
  const nameKey = roomNameKey(roomId);
  sessionStorage.removeItem(tokenKey);
  sessionStorage.removeItem(nameKey);
  localStorage.removeItem(tokenKey);
  localStorage.removeItem(nameKey);
}

function resetRoomSession({ rooms = state.rooms, roomId = roomQueryId() } = {}) {
  clearStoredIdentity(roomId);
  state.playerToken = "";
  state.room = null;
  state.battle = null;
  state.liveBattle = null;
  state.replayMode = false;
  state.replayStepIndex = 0;
  state.replayOmniscient = false;
  state.randomRosterSizeDraft = "";
  state.selectedUnitId = "";
  state.roomForm.joinRoomCode = "";
  state.rooms = rooms || [];
  clearActionSelection();
  syncLocation("draft", "");
  syncScreen({ preferBattle: false });
}

function saveStoredIdentity(roomId, token, name) {
  if (!roomId || !token) return;
  const tokenKey = roomTokenKey(roomId);
  const nameKey = roomNameKey(roomId);
  sessionStorage.setItem(tokenKey, token);
  localStorage.setItem(tokenKey, token);
  if (name) {
    sessionStorage.setItem(nameKey, name);
    localStorage.setItem(nameKey, name);
  }
}

function syncIdentityFromUrl() {
  const roomId = roomQueryId();
  if (!roomId || state.playerToken) return;
  const identity = loadStoredIdentity(roomId);
  if (identity.token) {
    state.playerToken = identity.token;
  }
}

function setScreen(screen, { renderAfter = true } = {}) {
  const next = screen === "battle" && hasBattle() ? "battle" : "draft";
  state.screen = next;
  clearActionSelection();
  syncLocation(next);
  if (renderAfter) render();
}

function syncScreen({ preferBattle = false } = {}) {
  const requested = window.location.hash.replace("#", "");
  if (requested === "battle" && hasBattle()) {
    state.screen = "battle";
    return;
  }
  if (requested === "draft") {
    state.screen = "draft";
    return;
  }
  state.screen = preferBattle && hasBattle() ? "battle" : "draft";
  syncLocation(state.screen);
}

function ensureDraftSelection() {
  return;
}

function ensureSelectedUnit() {
  const action = selectedAction();
  if (!state.battle && isRandomRoomMode()) {
    pill.textContent = `æˆ¿é—´ ${state.room.room_id} Â· ${state.room.status === "lobby" ? "å¤§åŽ…ä¸­" : "ç­‰å¾…å¼€å±€"}`;
    topbarSubline.textContent = state.room.viewer_player_id
      ? `ä½ å½“å‰æ˜¯çŽ©å®¶ ${state.room.viewer_player_id}ã€‚å½“å‰æˆ¿é—´ä½¿ç”¨ã€Œ${modeMeta.name}ã€ï¼Œå¼€å±€åŽä¼šéšæœºåˆ†é…æ­¦å°†ã€‚`
      : "ä½ å½“å‰è¿˜æ²¡æœ‰å ç”¨å¸­ä½ã€‚è‹¥æˆ¿é—´ä»æœ‰ç©ºä½ï¼Œè¾“å…¥æ˜µç§°åŽå³å¯åŠ å…¥ã€‚";
    caption.textContent = "å¯¹å±€å°šæœªå¼€å§‹ï¼Œéšæœºé€‰äººæ¨¡å¼ä¸‹æ— éœ€æ‰‹åŠ¨é€‰å°†ã€‚";
    return;
  }
  if (!state.battle) {
    state.selectedUnitId = "";
    return;
  }
  if (isRespawnMode()) {
    state.selectedUnitId = currentRespawnPrompt()?.unit_id || "";
    return;
  }
  if (isChainMode() && !action) {
    state.selectedUnitId = state.battle.pending_chain?.current_unit_id || "";
    return;
  }
  if (isReplayMode()) {
    node.textContent = `å½“å‰æ­£åœ¨æŸ¥çœ‹å›žæ”¾ç¬¬ ${state.replayStepIndex}/${replayMeta().last_step_index} æ­¥ã€‚`;
    return;
  }
  if (isReplayMode()) {
    pill.textContent = `æˆ¿é—´ ${state.room.room_id} Â· å›žæ”¾ ${state.replayStepIndex}/${replayMeta().last_step_index}`;
    caption.textContent = state.replayOmniscient
      ? "å½“å‰æ­£åœ¨ä»¥å…¨çŸ¥è§†è§’æŸ¥çœ‹å›žæ”¾ã€‚"
      : "å½“å‰æ­£åœ¨æŸ¥çœ‹å›žæ”¾ã€‚";
    return;
  }
  if (isGameOver()) {
    if (unitById(state.selectedUnitId)) return;
    state.selectedUnitId = allUnits()[0]?.id || "";
    return;
  }
  if (!state.selectedUnitId) {
    const controllable = activeBundles().map((entry) => entry.unit_id);
    state.selectedUnitId = controllable[0] || allUnits()[0]?.id || "";
    return;
  }
  const controllable = activeBundles().map((entry) => entry.unit_id);
  if (unitById(state.selectedUnitId) && (!controllable.length || controllable.includes(state.selectedUnitId))) {
    return;
  }
  state.selectedUnitId = controllable[0] || allUnits()[0]?.id || "";
}

function syncSelectedUnitAfterStateChange() {
  if (!state.battle) {
    state.selectedUnitId = "";
    return;
  }
  if (isRespawnMode()) {
    state.selectedUnitId = currentRespawnPrompt()?.unit_id || "";
    return;
  }
  if (isChainMode()) {
    state.selectedUnitId = state.battle.pending_chain?.current_unit_id || "";
    return;
  }
  if (isGameOver()) {
    if (!unitById(state.selectedUnitId)) {
      state.selectedUnitId = allUnits()[0]?.id || "";
    }
    return;
  }
  const controllable = activeBundles().map((entry) => entry.unit_id);
  if (!controllable.length) {
    ensureSelectedUnit();
    return;
  }
  if (!unitById(state.selectedUnitId) || !controllable.includes(state.selectedUnitId)) {
    state.selectedUnitId = controllable[0] || allUnits()[0]?.id || "";
  }
}

function clearActionSelection() {
  state.selectedActionCode = "";
  state.selectedActionSnapshot = null;
  state.hoveredActionCode = "";
  state.hoveredUnitId = "";
  state.hoverPointer = null;
  state.hoveredBoardCell = null;
  state.stagedPayload = null;
}

function hydrateStaticLabels() {
  document.title = "\u6b66\u5c06";
  const title = document.querySelector(".topbar h1");
  if (title) title.textContent = "\u6b66\u5c06";
  const profilePill = $("profile-pill");
  if (profilePill) profilePill.textContent = "\u6635\u79f0 \u00b7 \u672a\u547d\u540d\u73a9\u5bb6";
  const editProfile = $("edit-profile");
  if (editProfile) editProfile.textContent = "\u4fee\u6539\u6635\u79f0";
  const identityEdit = $("identity-edit");
  if (identityEdit) identityEdit.textContent = "\u4fee\u6539\u6635\u79f0";
  const identityTitle = $("profile-display-name");
  if (identityTitle) identityTitle.textContent = "\u672a\u547d\u540d\u73a9\u5bb6";
  const identityNote = $("profile-display-note");
  if (identityNote) {
    identityNote.textContent = "\u4e4b\u540e\u521b\u5efa\u623f\u95f4\u3001\u8f93\u5165\u623f\u95f4\u7801\u52a0\u5165\u3001\u6216\u76f4\u63a5\u52a0\u5165\u5df2\u6709\u623f\u95f4\u90fd\u4f1a\u4f7f\u7528\u8fd9\u4e2a\u6635\u79f0\u3002";
  }
  const createCardTitle = document.querySelector(".room-home-grid .room-entry-card:first-child h3");
  if (createCardTitle) createCardTitle.textContent = "\u521b\u5efa\u623f\u95f4";
  const createCardText = document.querySelector(".room-home-grid .room-entry-card:first-child p");
  if (createCardText) {
    createCardText.textContent = "\u76f4\u63a5\u4f7f\u7528\u4f60\u5f53\u524d\u8bbe\u7f6e\u597d\u7684\u6635\u79f0\u521b\u5efa\u623f\u95f4\uff0c\u5e76\u751f\u6210\u53ef\u5206\u4eab\u7ed9\u670b\u53cb\u7684\u9080\u8bf7\u94fe\u63a5\u3002";
  }
  const joinCardTitle = document.querySelector(".room-home-grid .room-entry-card:nth-child(2) h3");
  if (joinCardTitle) joinCardTitle.textContent = "\u52a0\u5165\u623f\u95f4";
  const joinCardText = document.querySelector(".room-home-grid .room-entry-card:nth-child(2) p");
  if (joinCardText) {
    joinCardText.textContent = "\u8f93\u5165\u623f\u95f4\u7801\u540e\uff0c\u76f4\u63a5\u4ee5\u4f60\u5f53\u524d\u8bbe\u7f6e\u597d\u7684\u6635\u79f0\u52a0\u5165\u670b\u53cb\u5df2\u7ecf\u521b\u5efa\u597d\u7684\u623f\u95f4\u3002";
  }
  const roomCodeInput = $("join-room-code");
  if (roomCodeInput) roomCodeInput.placeholder = "\u4f8b\u5982\uff1aA7K9Q2";
  const profileInput = $("profile-name-input");
  if (profileInput) profileInput.placeholder = "\u4f8b\u5982\uff1a\u6697\u4eba\u73a9\u5bb6";
  const profileSave = $("profile-save");
  if (profileSave) profileSave.textContent = "\u8fdb\u5165\u5927\u5385";
  const createButton = $("create-room");
  if (createButton) createButton.textContent = "\u521b\u5efa\u623f\u95f4";
  const joinButton = $("join-room");
  if (joinButton) joinButton.textContent = "\u52a0\u5165\u623f\u95f4";
  const leaveRoomButton = $("leave-room");
  if (leaveRoomButton) leaveRoomButton.textContent = "\u79bb\u5f00\u623f\u95f4";
  const deleteRoomButton = $("delete-room");
  if (deleteRoomButton) deleteRoomButton.textContent = "\u5220\u9664\u623f\u95f4";
  const directoryTitle = document.querySelector(".room-directory-head h3");
  if (directoryTitle) directoryTitle.textContent = "\u5df2\u6709\u623f\u95f4";
  const directoryText = document.querySelector(".room-directory-head p");
  if (directoryText) {
    directoryText.textContent = "\u53ef\u76f4\u63a5\u52a0\u5165\u6b63\u5728\u7b49\u4eba\u7684\u623f\u95f4\uff0c\u6216\u67e5\u770b\u5df2\u7ecf\u5f00\u6218\u7684\u623f\u95f4\u3002";
  }
  const resumeText = $("resume-room-text");
  if (resumeText) {
    resumeText.textContent = "\u68c0\u6d4b\u5230\u8fd9\u4e2a\u6d4f\u89c8\u5668\u4e4b\u524d\u8fdb\u5165\u8fc7\u5f53\u524d\u623f\u95f4\u3002";
  }
  const resumeButton = $("resume-room");
  if (resumeButton) {
    resumeButton.textContent = "\u7ee7\u7eed\u539f\u8eab\u4efd";
  }
  const rematchButton = $("game-over-rematch");
  if (rematchButton) rematchButton.textContent = "\u91cd\u65b0\u5f00\u59cb\u9009\u5c06";
  const backButton = $("game-over-back");
  if (backButton) backButton.textContent = "\u8fd4\u56de\u623f\u95f4\u5927\u5385";
  const surrenderButton = $("surrender-battle");
  if (surrenderButton) surrenderButton.textContent = "\u6295\u964d";
}

function trimNumber(value) {
  const rounded = Math.round(Number(value || 0) * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : String(rounded).replace(/0+$/, "").replace(/\.$/, "");
}

function hpRatio(unit) {
  if (!unit || !unit.max_hp) return 0;
  return Math.max(0, Math.min(1, Number(unit.hp || 0) / Number(unit.max_hp)));
}

function manaValue(unit) {
  return Math.max(0, Number(unit?.mana || 0));
}

function manaDisplayClass(unit) {
  return manaValue(unit) > 5 ? "mana-pips is-compact" : "mana-pips";
}

function manaPipsMarkup(unit) {
  const mana = manaValue(unit);
  if (mana > 5) {
    return `<span class="mana-pip is-filled"></span><span class="mana-count">${trimNumber(mana)}</span>`;
  }
  const fullPips = Math.floor(mana);
  const hasHalfPip = Math.abs(mana - fullPips - 0.5) < 0.001;
  const pips = [];
  for (let index = 0; index < fullPips; index += 1) {
    pips.push(`<span class="mana-pip is-filled"></span>`);
  }
  if (hasHalfPip) {
    pips.push(`<span class="mana-pip is-half"></span>`);
  }
  if (!pips.length) {
    return `<span class="mana-zero">0</span>`;
  }
  return pips.join("");
}

function unitStatusSummary(unit) {
  const entries = [];
  if (!unit) return entries;
  if (unit.banished) {
    entries.push(`消失${unit.banish_turns_remaining > 0 ? `(${unit.banish_turns_remaining})` : ""}`);
  }
  if (unit.total_shields) {
    entries.push(`护盾 ${unit.total_shields}`);
  }
  if (unit.dodge_charges) {
    entries.push(`回避 ${unit.dodge_charges}`);
  }
  unit.statuses.forEach((status) => {
    entries.push(`${status.name}${status.duration ? `(${status.duration})` : ""}`);
  });
  return entries;
}

function fieldEffects() {
  return state.battle?.field_effects || [];
}

function fieldEffectDuration(effect) {
  if (effect?.duration == null) return "持续中";
  return `${trimNumber(effect.duration / 2)}轮`;
}

function displayActions() {
  if (isGameOver()) return [];
  if (isRespawnMode()) return [];
  const bundle = bundleFor(state.selectedUnitId);
  if (!bundle) return [];
  if (isChainMode()) {
    const reactions = (bundle.reactions.actions || []).map((action) => ({
      ...action,
      code: action.action_code,
      kind: action.action_type === "skill" ? "skill" : "reaction_action",
      preview: action.preview || { cells: [], target_unit_ids: [] },
      available: true,
    }));
    reactions.push({
      code: "chain_skip",
      name: "不连锁",
      action_name: "不连锁",
      kind: "chain_skip",
      timing: "reaction",
      chain_speed: 0,
      description: "放弃本次连锁,让原动作按原本声明继续结算。",
      preview: { cells: [], target_unit_ids: [], secondary_cells: [], requires_target: false },
      available: true,
    });
    return reactions;
  }
  return (bundle.actions.actions || []).filter((action) => {
    if (!action.available) return false;
    if (action.kind === "move" || action.kind === "attack") return true;
    return action.timing === "active";
  });
}

function actionByCode(code) {
  return displayActions().find((action) => action.code === code) || null;
}

function selectedAction() {
  if (!state.selectedActionCode) return null;
  const live = actionByCode(state.selectedActionCode);
  if (live) {
    state.selectedActionSnapshot = live;
    return live;
  }
  if (state.selectedActionSnapshot?.code === state.selectedActionCode) {
    return state.selectedActionSnapshot;
  }
  return null;
}

function hoveredAction() {
  return selectedAction() || actionByCode(state.hoveredActionCode);
}

function positionKey(pos) {
  return `${pos.x},${pos.y}`;
}

function positionsToSet(cells = []) {
  return new Set(cells.map((cell) => `${cell.x},${cell.y}`));
}

function targetIdsToSet(targets = []) {
  return new Set(targets);
}

function visualEvents() {
  return state.battle?.visual_events || [];
}

function maxVisualEventId(events = visualEvents()) {
  return events.reduce((maxId, event) => Math.max(maxId, Number(event?.id || 0)), 0);
}

function battleVfxLayer() {
  return $("battle-vfx");
}

function clearBattleVfxCleanupTimer() {
  if (!battleVfxCleanupHandle || typeof window.clearTimeout !== "function") return;
  window.clearTimeout(battleVfxCleanupHandle);
  battleVfxCleanupHandle = 0;
}

function removeBattleVfxEntry(entry) {
  (entry?.nodes || []).forEach(({ node }) => node?.remove?.());
}

function clearBattleVfx() {
  clearBattleVfxCleanupTimer();
  state.activeBattleVfx.forEach(removeBattleVfxEntry);
  state.activeBattleVfx = [];
  const layer = battleVfxLayer();
  if (layer) layer.innerHTML = "";
}

function battleVfxDuration(event) {
  if (!event) return 700;
  if (event.kind === "attack") return 620;
  if (event.kind === "defense") return 760;
  if (event.action_type === "skill_effect") return 840;
  return 900;
}

function battleVfxTheme(event) {
  if (!event) return "arcane";
  if (event.kind === "attack") return event.action_code === "counter" ? "storm" : "attack";
  if (event.kind === "defense") {
    if (event.defense_reason === "magic_immunity") return "void";
    if (event.defense_reason === "dodge") return "wind";
    if (event.defense_reason === "shield_break" || event.defense_reason === "shield_half_break") return "shatter";
    return "barrier";
  }
  const code = `${event.action_code || ""} ${event.display_name || ""}`.toLowerCase();
  if (/(fire|burn|blaze|funeral|missile|judgment|doom)/.test(code)) return "fire";
  if (/(holy|light|sun|judg)/.test(code)) return "holy";
  if (/(dark|shadow|stealth|doomlight|curse|undead)/.test(code)) return "shadow";
  if (/(rock|earth|stone|sand|dust)/.test(code)) return "earth";
  if (/(wind|storm|kick|machine|gun|pierce|shock|thunder|lightning)/.test(code)) return "storm";
  if (/(heal|chant|mana|plasma|laser|ion|quantum|jade|motor)/.test(code)) return "arcane";
  if (/(wall|shield|protect|guard|block)/.test(code)) return "barrier";
  if (/(banish|apocalypse|doom)/.test(code)) return "void";
  return "arcane";
}

function boardCellNodes() {
  return Array.from($("board")?.children || []).filter((node) => node?.dataset?.x != null && node?.dataset?.y != null);
}

function boardCellNodeAt(x, y) {
  return boardCellNodes().find((cell) => Number(cell.dataset.x) === Number(x) && Number(cell.dataset.y) === Number(y)) || null;
}

function nodeCenterRelativeToStage(node) {
  if (!node || typeof node.getBoundingClientRect !== "function") return null;
  const rect = node.getBoundingClientRect();
  const stageRect = $("board-stage")?.getBoundingClientRect?.();
  if (!rect || !stageRect) return null;
  return {
    x: rect.left - stageRect.left + rect.width / 2,
    y: rect.top - stageRect.top + rect.height / 2,
  };
}

function cellCenterPoint(cell) {
  const normalized = normalizedCell(cell);
  if (!normalized) return null;
  return nodeCenterRelativeToStage(boardCellNodeAt(normalized.x, normalized.y));
}

function unitCenterPoint(unit) {
  const cells = unitOccupiedCells(unit);
  if (!cells.length) return null;
  const points = cells.map((cell) => cellCenterPoint(cell)).filter(Boolean);
  if (!points.length) return null;
  const sum = points.reduce((acc, point) => ({ x: acc.x + point.x, y: acc.y + point.y }), { x: 0, y: 0 });
  return { x: sum.x / points.length, y: sum.y / points.length };
}

function battleVfxSourcePoint(event) {
  const sourceCell = normalizedCell(event?.source_cell);
  if (sourceCell) return cellCenterPoint(sourceCell);
  return unitCenterPoint(unitById(event?.actor_id || ""));
}

function battleVfxTargetRefs(event) {
  const cells = Array.isArray(event?.target_cells) ? event.target_cells.map(normalizedCell).filter(Boolean) : [];
  if (cells.length) {
    return cells.map((cell, index) => ({ kind: "cell", key: `cell:${positionKey(cell)}:${index}`, cell }));
  }
  const targetUnitIds = Array.isArray(event?.target_unit_ids) ? event.target_unit_ids : [];
  return targetUnitIds.map((unitId) => ({ kind: "unit", key: `unit:${unitId}`, unitId: String(unitId) }));
}

function battleVfxPointForRef(ref) {
  if (!ref) return null;
  if (ref.kind === "cell") return cellCenterPoint(ref.cell);
  return unitCenterPoint(unitById(ref.unitId || ""));
}

function attachBattleVfxNode(node, layer, entry, type, ref = null) {
  node.dataset.vfxEventId = String(entry.event.id || 0);
  node.dataset.vfxType = type;
  layer.append(node);
  entry.nodes.push({ node, type, ref });
}

function createBattleVfxEntry(event) {
  const layer = battleVfxLayer();
  if (!layer || !event) return null;
  const duration = battleVfxDuration(event);
  const entry = {
    event,
    expiresAt: Date.now() + duration,
    nodes: [],
  };
  const refs = battleVfxTargetRefs(event);
  const theme = battleVfxTheme(event);
  const sourcePoint = battleVfxSourcePoint(event);

  if (event.kind === "attack") {
    refs.forEach((ref) => {
      const projectile = document.createElement("div");
      projectile.className = `battle-vfx-projectile theme-${theme}`;
      projectile.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(projectile, layer, entry, "projectile", ref);
      const impact = document.createElement("div");
      impact.className = `battle-vfx-impact theme-${theme}`;
      impact.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(impact, layer, entry, "impact", ref);
    });
  } else if (event.kind === "skill") {
    if (sourcePoint) {
      const source = document.createElement("div");
      source.className = `battle-vfx-source theme-${theme}`;
      source.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(source, layer, entry, "source");
    }
    const burstRefs = refs.length ? refs : [{ kind: "source", key: "source" }];
    burstRefs.forEach((ref) => {
      const burst = document.createElement("div");
      burst.className = `battle-vfx-burst theme-${theme}`;
      burst.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(burst, layer, entry, "burst", ref.kind === "source" ? null : ref);
    });
  } else if (event.kind === "defense") {
    refs.forEach((ref) => {
      const shield = document.createElement("div");
      shield.className = `battle-vfx-shield theme-${theme}`;
      shield.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(shield, layer, entry, "shield", ref);
    });
  }
  return entry;
}

function positionBattleVfxEntry(entry) {
  const sourcePoint = battleVfxSourcePoint(entry.event);
  entry.nodes.forEach(({ node, type, ref }) => {
    let point = ref ? battleVfxPointForRef(ref) : sourcePoint;
    if (!point && type === "projectile") {
      node.classList.add("hidden");
      return;
    }
    if (type === "projectile") {
      const targetPoint = battleVfxPointForRef(ref);
      if (!sourcePoint || !targetPoint) {
        node.classList.add("hidden");
        return;
      }
      const dx = targetPoint.x - sourcePoint.x;
      const dy = targetPoint.y - sourcePoint.y;
      const length = Math.max(18, Math.sqrt((dx ** 2) + (dy ** 2)));
      const angle = Math.atan2(dy, dx) * (180 / Math.PI);
      node.classList.remove("hidden");
      node.style.left = `${sourcePoint.x}px`;
      node.style.top = `${sourcePoint.y}px`;
      node.style.width = `${length}px`;
      node.style.transform = `translateY(-50%) rotate(${angle}deg)`;
      return;
    }
    if (!point) {
      node.classList.add("hidden");
      return;
    }
    node.classList.remove("hidden");
    node.style.left = `${point.x}px`;
    node.style.top = `${point.y}px`;
  });
}

function renderBattleVfx() {
  const layer = battleVfxLayer();
  if (!layer) return;
  const now = Date.now();
  const alive = [];
  state.activeBattleVfx.forEach((entry) => {
    if (entry.expiresAt <= now) {
      removeBattleVfxEntry(entry);
      return;
    }
    alive.push(entry);
    positionBattleVfxEntry(entry);
  });
  state.activeBattleVfx = alive;
  layer.classList.toggle("is-empty", !alive.length);
  clearBattleVfxCleanupTimer();
  if (!alive.length || typeof window.setTimeout !== "function") return;
  const delay = Math.max(32, Math.min(...alive.map((entry) => Math.max(0, entry.expiresAt - now))) + 20);
  battleVfxCleanupHandle = window.setTimeout(() => {
    battleVfxCleanupHandle = 0;
    renderBattleVfx();
  }, delay);
}

function syncBattleVfxState({ hadBattle = false, boardChanged = false } = {}) {
  if (!state.battle) {
    clearBattleVfx();
    state.lastSeenVisualEventId = 0;
    return;
  }
  const events = visualEvents();
  const newestEventId = maxVisualEventId(events);
  if (!hadBattle || boardChanged) {
    clearBattleVfx();
    state.lastSeenVisualEventId = newestEventId;
    return;
  }
  const unseen = events.filter((event) => Number(event?.id || 0) > state.lastSeenVisualEventId);
  unseen.forEach((event) => {
    const entry = createBattleVfxEntry(event);
    if (entry) state.activeBattleVfx.push(entry);
  });
  state.lastSeenVisualEventId = Math.max(state.lastSeenVisualEventId, newestEventId);
}

function viewerOwnsUnit(unit) {
  return Boolean(unit) && viewerTeamId() !== null && unit.player_id === viewerTeamId();
}

function actingSideCanSeeUnit(unit) {
  const actor = selectedUnit();
  return Boolean(unit)
    && ((viewerOwnsUnit(unit)) || (viewerPlayerId() === null && actor && actor.player_id === unit.player_id));
}

function unitIsSelectableTarget(unit) {
  return Boolean(unit)
    && !unit.banished
    && !unit.cannot_be_targeted
    && (!unit.statuses.some((status) => status.name === "隐身") || actingSideCanSeeUnit(unit));
}

function previewCellsForTargetIds(targetIds = []) {
  return targetIds
    .map((id) => unitById(id))
    .filter((unit) => unit?.position && !unit.banished)
    .flatMap((unit) => unitOccupiedCells(unit))
    .filter(Boolean);
}

function cellInBounds(cell) {
  return Boolean(state.battle)
    && cell.x >= 0
    && cell.y >= 0
    && cell.x < state.battle.board.width
    && cell.y < state.battle.board.height;
}

function unitIdsAtCells(cells = []) {
  const keys = positionsToSet(cells);
  return allUnits()
    .filter((unit) => unit.position && !unit.banished && unitOccupiedCells(unit).some((cell) => keys.has(positionKey(cell))))
    .map((unit) => unit.id);
}

function sameCell(left, right) {
  return Boolean(left && right) && left.x === right.x && left.y === right.y;
}

function patternSelection(action) {
  const mode = action?.preview?.selection?.mode;
  return mode === "pattern_cells" || mode === "choice_pattern" ? action.preview.selection : null;
}

function patternSelectionIsOrdered(action) {
  return Boolean(patternSelection(action)?.ordered);
}

function choicePatternSelection(action) {
  return action?.preview?.selection?.mode === "choice_pattern" ? action.preview.selection : null;
}

function attackChoicePatternSelection(action) {
  return action?.kind === "attack" ? choicePatternSelection(action) : null;
}

function movePathSelection(action) {
  return action?.preview?.selection?.mode === "move_path" ? action.preview.selection : null;
}

function multiUnitSelection(action) {
  return action?.preview?.selection?.mode === "multi_unit" ? action.preview.selection : null;
}

function statCellSelection(action) {
  return action?.preview?.selection?.mode === "stat_cells" ? action.preview.selection : null;
}

function bodyDirectionSelection(action) {
  return action?.preview?.selection?.mode === "body_direction" ? action.preview.selection : null;
}

function normalizedPatternCells(cells = []) {
  const normalized = [];
  const seen = new Set();
  cells.forEach((cell) => {
    if (!cell || cell.x == null || cell.y == null) return;
    const next = { x: Number(cell.x), y: Number(cell.y) };
    const key = positionKey(next);
    if (seen.has(key)) return;
    seen.add(key);
    normalized.push(next);
  });
  return normalized;
}

function selectionPatterns(action) {
  const selection = patternSelection(action);
  if (!selection) return [];
  const rawPatterns = choicePatternSelection(action)
    ? ((selection.choices || []).find((entry) => String(entry.code || "") === stagedPatternChoiceCode(action))?.patterns || [])
    : (Array.isArray(selection.patterns) ? selection.patterns : []);
  return rawPatterns
    .map((pattern) => normalizedPatternCells(pattern))
    .filter((pattern) => pattern.length);
}

function stagedPatternCells(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !patternSelection(action)) return [];
  return normalizedPatternCells(Array.isArray(state.stagedPayload?.cells) ? state.stagedPayload.cells : []);
}

function stagedPatternChoiceCode(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !choicePatternSelection(action)) return "";
  return String(state.stagedPayload?.choiceCode || "").trim();
}

function setStagedPatternChoice(choiceCode) {
  const action = selectedAction();
  if (!choicePatternSelection(action)) return;
  const next = String(choiceCode || "").trim();
  const keepCells = next && next === stagedPatternChoiceCode(action) ? stagedPatternCells(action) : [];
  state.stagedPayload = next || keepCells.length
    ? { ...(next ? { choiceCode: next } : {}), ...(keepCells.length ? { cells: keepCells } : {}) }
    : null;
}

function setStagedPatternCells(cells) {
  const normalized = normalizedPatternCells(cells);
  const choiceCode = stagedPatternChoiceCode();
  state.stagedPayload = normalized.length || choiceCode
    ? { ...(choiceCode ? { choiceCode } : {}), ...(normalized.length ? { cells: normalized } : {}) }
    : null;
}

function stagedMovePath(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !movePathSelection(action)) return [];
  return normalizedPatternCells(Array.isArray(state.stagedPayload?.path) ? state.stagedPayload.path : []);
}

function setStagedMovePath(path) {
  const normalized = normalizedPatternCells(path);
  state.stagedPayload = normalized.length ? { path: normalized } : null;
}

function normalizedTargetIds(ids = []) {
  const normalized = [];
  const seen = new Set();
  ids.forEach((id) => {
    const next = String(id || "").trim();
    if (!next || seen.has(next)) return;
    seen.add(next);
    normalized.push(next);
  });
  return normalized;
}

function stagedMultiTargetIds(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !multiUnitSelection(action)) return [];
  const explicit = Array.isArray(state.stagedPayload?.targetUnitIds) ? state.stagedPayload.targetUnitIds : [];
  return normalizedTargetIds(explicit);
}

function setStagedMultiTargetIds(ids) {
  const normalized = normalizedTargetIds(ids);
  state.stagedPayload = normalized.length ? { targetUnitIds: normalized } : null;
}

function stagedStatName(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !statCellSelection(action)) return "";
  return String(state.stagedPayload?.statName || "").trim();
}

function setStagedStatName(statName) {
  const cells = stagedStatCells();
  const next = String(statName || "").trim();
  state.stagedPayload = next || cells.length ? { statName: next, cells } : null;
}

function stagedStatCells(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !statCellSelection(action)) return [];
  return normalizedPatternCells(Array.isArray(state.stagedPayload?.cells) ? state.stagedPayload.cells : []);
}

function setStagedStatCells(cells) {
  const statName = stagedStatName();
  const normalized = normalizedPatternCells(cells);
  state.stagedPayload = statName || normalized.length ? { statName, cells: normalized } : null;
}

function statCellRequired(action) {
  return Number(statCellSelection(action)?.required_cells || 0);
}

function statCellSelectionCanComplete(action, chosen = stagedStatCells(action)) {
  const selection = statCellSelection(action);
  if (!selection) return false;
  const statName = stagedStatName(action);
  const validStats = new Set((selection.stats || []).map((entry) => String(entry.code || "")));
  return Boolean(statName && validStats.has(statName) && chosen.length === statCellRequired(action));
}

function stagedBodyCells(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !bodyDirectionSelection(action)) return [];
  return normalizedPatternCells(Array.isArray(state.stagedPayload?.cells) ? state.stagedPayload.cells : []);
}

function setStagedBodyCells(cells) {
  const direction = stagedBodyDirection();
  const normalized = normalizedPatternCells(cells);
  state.stagedPayload = normalized.length || direction ? { cells: normalized, ...(direction ? { direction } : {}) } : null;
}

function stagedBodyDirection(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !bodyDirectionSelection(action)) return null;
  const direction = state.stagedPayload?.direction;
  if (!direction || direction.dx == null || direction.dy == null) return null;
  return { dx: Number(direction.dx), dy: Number(direction.dy) };
}

function setStagedBodyDirection(direction) {
  const cells = stagedBodyCells();
  const normalized = direction && direction.dx != null && direction.dy != null
    ? { dx: Number(direction.dx), dy: Number(direction.dy) }
    : null;
  state.stagedPayload = cells.length || normalized ? { cells, ...(normalized ? { direction: normalized } : {}) } : null;
}

function bodyDirectionSelectionCanComplete(action, chosen = stagedBodyCells(action)) {
  const direction = stagedBodyDirection(action);
  return Boolean(bodyDirectionSelection(action) && chosen.length && direction);
}

function movePathMaxSteps(action) {
  return Number(movePathSelection(action)?.max_steps || 0);
}

function movePathHead(action, chosen = stagedMovePath(action)) {
  if (chosen.length) return chosen[chosen.length - 1];
  return selectedUnit()?.position || null;
}

function cellBlockedForMover(unit, cell) {
  if (!unit) return true;
  const footprintCells = unitFootprintCellsAt(unit, cell);
  if (!footprintCells.every(cellInBounds)) return true;
  const occupants = footprintCells.flatMap((footprintCell) => unitsAtCell(footprintCell.x, footprintCell.y))
    .filter((other, index, list) => other.id !== unit.id && list.findIndex((entry) => entry.id === other.id) === index);
  const blockingOccupants = occupants.filter((other) => !unitsCanOverlapOnBoard(unit, other));
  if (!blockingOccupants.length) return false;
  if (unitIsStealthed(unit)) return false;
  return blockingOccupants.some((other) => !unitIsStealthed(other));
}

function moveFootprintCellsForAnchors(action, anchors = []) {
  const unit = selectedUnit();
  if (!movePathSelection(action) || !unit) return [];
  return anchors.flatMap((anchor) => unitFootprintCellsAt(unit, anchor));
}

function movePathAnchorForClickedCell(action, clickedCell, chosen = stagedMovePath(action)) {
  if (!movePathSelection(action)) return null;
  const candidates = nextMovePathCells(action, chosen);
  const direct = candidates.find((anchor) => sameCell(anchor, clickedCell));
  if (direct) return direct;
  return candidates.find((anchor) => unitFootprintCellsAt(selectedUnit(), anchor).some((cell) => sameCell(cell, clickedCell))) || null;
}

function movePathIndexForClickedCell(action, clickedCell, chosen = stagedMovePath(action)) {
  if (!movePathSelection(action)) return -1;
  return chosen.findIndex((anchor) => (
    sameCell(anchor, clickedCell)
    || unitFootprintCellsAt(selectedUnit(), anchor).some((cell) => sameCell(cell, clickedCell))
  ));
}

function nextMovePathCells(action, chosen = stagedMovePath(action)) {
  const unit = selectedUnit();
  const head = movePathHead(action, chosen);
  const maxSteps = movePathMaxSteps(action);
  if (!unit?.position || !head || !state.battle || chosen.length >= maxSteps) return [];
  const next = [];
  const chosenKeys = positionsToSet(chosen);
  for (let dx = -1; dx <= 1; dx += 1) {
    for (let dy = -1; dy <= 1; dy += 1) {
      if (dx === 0 && dy === 0) continue;
      const candidate = { x: head.x + dx, y: head.y + dy };
      if (!cellInBounds(candidate)) continue;
      if (chosenKeys.has(positionKey(candidate))) continue;
      if (cellBlockedForMover(unit, candidate)) continue;
      next.push(candidate);
    }
  }
  return next;
}

function movePathCanComplete(action, chosen = stagedMovePath(action)) {
  return Boolean(movePathSelection(action) && chosen.length);
}

function multiUnitSelectionCanComplete(action, chosen = stagedMultiTargetIds(action)) {
  const selection = multiUnitSelection(action);
  if (!selection) return false;
  const minTargets = Number(selection.min_targets || 1);
  const maxTargets = Number(selection.max_targets || chosen.length || minTargets);
  return chosen.length >= minTargets && chosen.length <= maxTargets;
}

function cellsMatchExactly(left = [], right = []) {
  if (left.length !== right.length) return false;
  const rightKeys = positionsToSet(right);
  return left.every((cell) => rightKeys.has(positionKey(cell)));
}

function matchingSelectionPatterns(action, chosen = stagedPatternCells(action)) {
  const patterns = selectionPatterns(action);
  if (!chosen.length) return patterns;
  if (patternSelectionIsOrdered(action)) {
    return patterns.filter((pattern) => chosen.length <= pattern.length
      && chosen.every((cell, index) => sameCell(cell, pattern[index])));
  }
  return patterns.filter((pattern) => {
    const patternKeys = positionsToSet(pattern);
    return chosen.every((cell) => patternKeys.has(positionKey(cell)));
  });
}

function nextPatternSelectionCells(action, chosen = stagedPatternCells(action)) {
  if (patternSelectionIsOrdered(action)) {
    const next = [];
    const seen = new Set();
    matchingSelectionPatterns(action, chosen).forEach((pattern) => {
      const cell = pattern[chosen.length];
      if (!cell) return;
      const key = positionKey(cell);
      if (seen.has(key)) return;
      seen.add(key);
      next.push(cell);
    });
    return next;
  }
  const chosenKeys = positionsToSet(chosen);
  const next = [];
  const seen = new Set();
  matchingSelectionPatterns(action, chosen).forEach((pattern) => {
    pattern.forEach((cell) => {
      const key = positionKey(cell);
      if (chosenKeys.has(key) || seen.has(key)) return;
      seen.add(key);
      next.push(cell);
    });
  });
  return next;
}

function patternSelectionCanComplete(action, chosen = stagedPatternCells(action)) {
  if (!chosen.length) return false;
  if (patternSelectionIsOrdered(action)) {
    return selectionPatterns(action).some((pattern) => pattern.length === chosen.length
      && chosen.every((cell, index) => sameCell(cell, pattern[index])));
  }
  return matchingSelectionPatterns(action, chosen).some((pattern) => cellsMatchExactly(pattern, chosen));
}

function fieldEffectsByCell() {
  const map = new Map();
  fieldEffects().forEach((effect) => {
    (effect.cells || []).forEach((cell) => {
      const key = positionKey(cell);
      if (!map.has(key)) {
        map.set(key, []);
      }
      map.get(key).push(effect);
    });
  });
  return map;
}

function fieldEffectMarker(effect) {
  const marker = String(effect?.board_marker || effect?.name || "").trim();
  return marker ? marker.slice(0, 2) : "场";
}

function actionManaLabel(action) {
  if (action.kind !== "skill") return "";
  if (action.mana_cost_text) return action.mana_cost_text;
  return action.mana_cost > 0 ? `费 ${trimNumber(action.mana_cost)} 魔` : "不费魔";
}

function actionTierLabel(action) {
  if (action.kind !== "skill") return "基础动作";
  if (action.timing !== "active") return "被动技能";
  if (action.max_uses_per_battle === 1) return "大招";
  return "普通技能";
}

function actionLimitLabel(action) {
  if (action.kind === "chain_skip") return "仅本次连锁";
  if (action.kind !== "skill") {
    return action.kind === "attack" ? "按本回合攻击次数上限" : "每回合一次";
  }
  if (action.window_total_uses != null && action.window_rounds != null) {
    const base = `每${trimNumber(action.window_rounds)}轮最多 ${trimNumber(action.window_total_uses)} 次`;
    if (action.window_active) {
      return `${base}（当前窗口剩余 ${trimNumber(action.window_remaining_uses || 0)} 次）`;
    }
    return base;
  }
  if (action.max_uses_per_turn == null) return "每回合次数不限";
  return `每回合最多 ${action.max_uses_per_turn} 次`;
}

function currentPreview() {
  if (isGameOver()) {
    return { cellKeys: new Set(), targetIds: new Set(), secondaryCellKeys: new Set(), destinationCellKeys: new Set() };
  }
  if (isRespawnMode()) {
    return {
      cellKeys: positionsToSet(currentRespawnPrompt()?.options || []),
      targetIds: new Set(),
      secondaryCellKeys: positionsToSet(currentRespawnPrompt()?.origin ? [currentRespawnPrompt().origin] : []),
      destinationCellKeys: new Set(),
    };
  }
  const action = hoveredAction();
  if (!action) {
    if (isChainMode()) {
      const queued = state.battle?.pending_chain?.queued_action;
      const targetIds = (queued?.target_unit_ids || []).filter((id) => unitIsSelectableTarget(unitById(id)));
      return {
        cellKeys: positionsToSet(queued?.target_cells || []),
        targetIds: targetIdsToSet(targetIds),
        secondaryCellKeys: new Set(),
        destinationCellKeys: new Set(),
      };
    }
    return { cellKeys: new Set(), targetIds: new Set(), secondaryCellKeys: new Set(), destinationCellKeys: new Set() };
  }

  if (state.selectedActionCode === "mana_pull" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    return {
      cellKeys: positionsToSet(manaPullDestinations(target)),
      targetIds: new Set(target ? [target.id] : []),
      secondaryCellKeys: positionsToSet(target?.position ? [target.position] : []),
      destinationCellKeys: new Set(),
    };
  }

  const filteredTargetIds = (action.preview?.target_unit_ids || []).filter((id) => unitIsSelectableTarget(unitById(id)));
  if (action.code === "backstep_shot" && isChainMode()) {
    const retreatCell = stagedBackstepRetreatCell(action);
    if (!retreatCell) {
      return {
        cellKeys: positionsToSet(action.preview?.cells || []),
        targetIds: new Set(),
        secondaryCellKeys: positionsToSet(action.preview?.secondary_cells || []),
        destinationCellKeys: new Set(),
      };
    }
    const followUpTargetIds = backstepFollowUpTargetIds(action, retreatCell)
      .filter((id) => unitIsSelectableTarget(unitById(id)));
    return {
      cellKeys: new Set(),
      targetIds: targetIdsToSet(followUpTargetIds),
      secondaryCellKeys: positionsToSet([...(action.preview?.secondary_cells || []), retreatCell]),
      destinationCellKeys: new Set(),
    };
  }
  if (movePathSelection(action)) {
    const chosenCells = stagedMovePath(action);
    const activeCells = nextMovePathCells(action, chosenCells);
    const secondaryCells = chosenCells.length
      ? chosenCells
      : (selectedUnit()?.position ? [selectedUnit().position] : []);
    const finalAnchors = chosenCells.length ? [chosenCells[chosenCells.length - 1]] : activeCells;
    return {
      cellKeys: positionsToSet(activeCells),
      targetIds: new Set(),
      secondaryCellKeys: positionsToSet(secondaryCells),
      destinationCellKeys: positionsToSet(moveFootprintCellsForAnchors(action, finalAnchors)),
    };
  }
  if (patternSelection(action)) {
    const chosenCells = stagedPatternCells(action);
    const activeCells = nextPatternSelectionCells(action, chosenCells);
    const focusCells = [...chosenCells, ...activeCells];
    return {
      cellKeys: positionsToSet(activeCells),
      targetIds: targetIdsToSet(unitIdsAtCells(focusCells)),
      secondaryCellKeys: positionsToSet(chosenCells),
      destinationCellKeys: new Set(),
    };
  }
  if (multiUnitSelection(action)) {
    const chosenIds = stagedMultiTargetIds(action);
    return {
      cellKeys: positionsToSet(previewCellsForTargetIds(filteredTargetIds)),
      targetIds: targetIdsToSet(filteredTargetIds),
      secondaryCellKeys: positionsToSet(previewCellsForTargetIds(chosenIds)),
      destinationCellKeys: new Set(),
    };
  }
  if (statCellSelection(action)) {
    const chosenCells = stagedStatCells(action);
    const required = statCellRequired(action);
    const chosenKeys = positionsToSet(chosenCells);
    const activeCells = required > chosenCells.length
      ? (action.preview?.cells || []).filter((cell) => !chosenKeys.has(positionKey(cell)))
      : [];
    return {
      cellKeys: positionsToSet(activeCells),
      targetIds: targetIdsToSet(filteredTargetIds),
      secondaryCellKeys: positionsToSet([...(action.preview?.secondary_cells || []), ...chosenCells]),
      destinationCellKeys: new Set(),
    };
  }
  if (bodyDirectionSelection(action)) {
    const chosenCells = stagedBodyCells(action);
    return {
      cellKeys: positionsToSet(action.preview?.cells || []),
      targetIds: new Set(),
      secondaryCellKeys: positionsToSet(chosenCells),
      destinationCellKeys: new Set(),
    };
  }
  const useDirectTargetCells = action.kind === "attack"
    || (action.preview?.requires_target && ["ally", "enemy", "unit"].includes(action.target_mode));
  const previewCells = useDirectTargetCells
    ? previewCellsForTargetIds(filteredTargetIds)
    : (action.preview?.cells || []);

  return {
    cellKeys: positionsToSet(previewCells),
    targetIds: targetIdsToSet(filteredTargetIds),
    secondaryCellKeys: positionsToSet(action.preview?.secondary_cells || []),
    destinationCellKeys: new Set(),
  };
}

function manaPullDestinations(target) {
  if (!target?.position || !state.battle) return [];
  const results = [];
  const directions = [
    [-1, -1], [-1, 0], [-1, 1],
    [0, -1], [0, 1],
    [1, -1], [1, 0], [1, 1],
  ];
  directions.forEach(([dx, dy]) => {
    let current = { ...target.position };
    for (let step = 0; step < 3; step += 1) {
      current = { x: current.x + dx, y: current.y + dy };
      if (
        current.x < 0 ||
        current.y < 0 ||
        current.x >= state.battle.board.width ||
        current.y >= state.battle.board.height
      ) {
        break;
      }
      const occupied = allUnits().some(
        (unit) => !unit.banished && unit.position && unit.id !== target.id && unitOccupiedCells(unit).some((cell) => cell.x === current.x && cell.y === current.y),
      );
      if (occupied) break;
      results.push(current);
    }
  });
  return results;
}

function actionNeedsTarget(action) {
  if (!action) return false;
  if (isChainMode()) return Boolean(action.preview?.requires_target);
  if (action.kind === "move" || action.kind === "attack") return true;
  return Boolean(action.preview?.requires_target);
}

function hasCancelableTargetSelection() {
  if (!canInteract() || isRespawnMode()) return false;
  const action = selectedAction();
  return Boolean(action && actionNeedsTarget(action));
}

function canCompleteTargetSelection() {
  if (!canInteract() || isRespawnMode()) return false;
  const action = selectedAction();
  if (!action) return false;
  if (action.code === "backstep_shot" && isChainMode()) return backstepSelectionCanComplete(action);
  if (movePathSelection(action)) return movePathCanComplete(action);
  if (patternSelection(action)) return patternSelectionCanComplete(action);
  if (multiUnitSelection(action)) return multiUnitSelectionCanComplete(action);
  if (statCellSelection(action)) return statCellSelectionCanComplete(action);
  if (bodyDirectionSelection(action)) return bodyDirectionSelectionCanComplete(action);
  return false;
}

function actionLabel(action) {
  if (action.kind === "move") return "\u79fb";
  if (action.kind === "attack") return "\u653b";
  if (action.kind === "chain_skip") return "\u5426";
  if (action.action_name) return action.action_name.length <= 2 ? action.action_name : action.action_name.slice(0, 2);
  return action.name.length <= 2 ? action.name : action.name.slice(0, 2);
}

function actionTitle(action) {
  return action.action_name || action.name;
}

function actionTimingLabel(action) {
  if (action.kind === "chain_skip") return "放弃";
  const mapping = {
    active: "速度1",
    passive: "速度2",
    reaction: "速度2",
    instant: "速度3",
  };
  return mapping[action.timing] || `速度${action.chain_speed}`;
}

function currentRoomSeat() {
  return state.room?.seats?.find((seat) => seat.player_id === viewerPlayerId()) || null;
}

function controllerTypeLabel(seat) {
  if (!seat) return "";
  if (seat.is_ai || seat.controller_type === "ai") return "AI";
  if (seat.is_human || seat.controller_type === "human") return "真人";
  return "开放";
}

function seatIdentityLabel(seat) {
  if (!seat) return "";
  return `席位 ${seat.player_id} · ${seat.team_name || (Number(seat.team_id) === 1 ? "红队" : "蓝队")} · ${controllerTypeLabel(seat)}`;
}

function editableRoomSeat() {
  const viewerSeat = currentRoomSeat();
  if (!viewerSeat) return null;
  const requestedSeatId = Number(state.roomEditSeatId || viewerSeat.player_id);
  const targetSeat = (state.room?.seats || []).find((seat) => seat.player_id === requestedSeatId) || viewerSeat;
  if (targetSeat.player_id === viewerSeat.player_id) return targetSeat;
  if (state.room?.viewer_is_host && targetSeat.is_ai) return targetSeat;
  return viewerSeat;
}

function setRoomEditSeat(seatId) {
  state.roomEditSeatId = Number(seatId || 0) || viewerPlayerId();
}

function seatHeroCount(seat, heroCode) {
  return Number(seat?.hero_counts?.[heroCode] || 0);
}

function seatHeroTotalCount(seat) {
  return Number(seat?.hero_total_count || 0);
}

function randomRoomRosterSize(room = state.room) {
  return Math.max(1, Number(room?.random_roster_size || 1));
}

function randomRoomFallbackSummary(room = state.room) {
  const count = randomRoomRosterSize(room);
  return `开局后各随机分配 ${count} 个不重复武将`;
}

function sanitizeRandomRosterSizeInput(value) {
  return String(value ?? "").replace(/\D/g, "");
}

function seatHeroSummary(seat, { randomFallback = false, randomRoom = state.room } = {}) {
  if (randomFallback && seat?.occupied && !seat.hero_summary) return randomRoomFallbackSummary(randomRoom);
  if (!seat) return "";
  if (seat.hero_summary) return seat.hero_summary;
  if (randomFallback && seat.occupied) return randomRoomFallbackSummary(randomRoom);
  return "未选择";
}

function roomSummaries() {
  return state.rooms || [];
}

function fallbackJoinName() {
  return effectiveProfileName();
}

function renderProfilePanel() {
  const display = $("profile-display-name");
  const note = $("profile-display-note");
  const pill = $("profile-pill");
  const joinCode = $("join-room-code");
  const createButton = $("create-room");
  const joinButton = $("join-room");
  const displayName = effectiveProfileName();
  if (display) display.textContent = displayName;
  if (pill) pill.textContent = `昵称 · ${displayName}`;
  if (note) {
    note.textContent = state.profileName
      ? `当前会以"${displayName}"参与创建房间、输入房间码加入、以及从房间列表直接加入。`
      : "当前使用自动昵称;你也可以随时修改一个更容易识别的名字。";
  }
  if (createButton) createButton.disabled = !state.profileReady;
  if (joinButton) joinButton.disabled = !state.profileReady || !String(joinCode?.value || "").trim();
}

function renderProfileModal() {
  const modal = $("profile-modal");
  const input = $("profile-name-input");
  const title = $("profile-modal-title");
  const text = $("profile-modal-text");
  const save = $("profile-save");
  if (!modal || !input || !title || !text || !save) return;
  const visible = profileModalVisible();
  modal.classList.toggle("hidden", !visible);
  input.value = state.profileDraftName;
  title.textContent = state.profileReady ? "修改昵称" : "先设置你的昵称";
  text.textContent = state.profileReady
    ? "这个昵称会用于之后创建房间和加入房间。留空也可以,系统会继续使用自动昵称。"
    : "这个昵称会用于创建房间和加入房间。留空也可以,系统会自动给你默认昵称。";
  save.textContent = state.profileReady ? "保存昵称" : "进入大厅";
  if (visible && document.activeElement !== input) {
    window.requestAnimationFrame(() => input.focus());
  }
}

function storedIdentityForCurrentRoom() {
  return loadStoredIdentity(roomQueryId());
}

function canResumeStoredSeat() {
  const identity = storedIdentityForCurrentRoom();
  return Boolean(roomQueryId() && identity.token && !viewerPlayerId() && !state.playerToken);
}

function normalizePlayerNameForSeatMatch(name) {
  const cleaned = String(name || "").trim().split(/\s+/).filter(Boolean).join(" ");
  return (cleaned || "\u672a\u547d\u540d\u73a9\u5bb6").slice(0, 20);
}

function canReclaimSeatByName() {
  if (!roomQueryId() || !hasRoom() || viewerPlayerId() !== null || state.playerToken || !state.profileReady) {
    return false;
  }
  if (state.room.status === "lobby" && !state.room.is_full) {
    return false;
  }
  const currentName = normalizePlayerNameForSeatMatch(effectiveProfileName());
  return (state.room.seats || []).some((seat) => (
    seat.occupied && normalizePlayerNameForSeatMatch(seat.name) === currentName
  ));
}

function renderRecoveryButton() {
  const button = $("recover-room");
  if (!button) return;
  const canResume = canResumeStoredSeat();
  const canReclaim = canReclaimSeatByName();
  const visible = canResume || canReclaim;
  button.classList.toggle("hidden", !visible);
  button.disabled = !visible;
  button.textContent = canResume
    ? "\u7ee7\u7eed\u539f\u8eab\u4efd"
    : "\u7528\u5f53\u524d\u6635\u79f0\u6062\u590d\u5e2d\u4f4d";
}

function roomStateLabel(room) {
  if (!room) return "";
  if (room.status === "battle") return "对战中";
  if (room.status === "finished") return "已结束";
  if (room.can_join) return "可加入";
  if (room.is_full) return "已满";
  return "大厅中";
}

function roomStateClass(room) {
  if (!room) return "";
  if (room.status === "battle") return "is-battle";
  if (room.is_full) return "is-full";
  return "";
}

function shouldShowLobbyPanel() {
  return hasRoom() && (viewerPlayerId() !== null || state.room?.is_full || hasBattle());
}

function applyRoomPayload(payload, { preserveScreen = false } = {}) {
  const hadBattle = Boolean(state.liveBattle || state.battle);
  const previousScreen = state.screen;
  const previousBoardKey = state.battle ? `${state.battle.board.width}x${state.battle.board.height}` : "";
  const previousRoomId = state.room?.room_id || "";
  state.heroes = payload.heroes || [];
  if (payload.rooms) {
    state.rooms = payload.rooms;
  }
  state.room = payload.room || null;
  state.liveBattle = payload.battle || null;
  if (!state.room || state.room.room_id !== previousRoomId || !state.room.replay?.available) {
    state.replayMode = false;
    state.replayStepIndex = 0;
    state.replayOmniscient = false;
  }
  if (!state.replayMode || !state.liveBattle) {
    state.battle = state.liveBattle;
  }
  if (!state.room || state.room.mode !== "random" || state.room.room_id !== previousRoomId) {
    state.randomRosterSizeDraft = "";
  }
  if (!state.room) {
    state.roomEditSeatId = null;
  } else {
    const editableSeatIds = new Set(
      (state.room.seats || [])
        .filter((seat) => seat.player_id === state.room.viewer_player_id || (state.room.viewer_is_host && seat.is_ai))
        .map((seat) => seat.player_id),
    );
    if (!editableSeatIds.has(Number(state.roomEditSeatId))) {
      state.roomEditSeatId = state.room.viewer_player_id || null;
    }
  }
  const nextBoardKey = state.battle ? `${state.battle.board.width}x${state.battle.board.height}` : "";
  if (!state.battle || nextBoardKey !== previousBoardKey) {
    state.boardZoom = 1;
  }
  if (payload.player_token) {
    state.playerToken = payload.player_token;
  }
  if (state.room?.room_id && state.playerToken && state.room.viewer_player_id === null) {
    clearStoredIdentity(state.room.room_id);
    state.playerToken = "";
  }
  if (state.room?.room_id && state.playerToken) {
    saveStoredIdentity(
      state.room.room_id,
      state.playerToken,
      state.room.viewer_name || effectiveProfileName(),
    );
  }
  state.lastSyncAt = Date.now();
  const autoEnterBattle = Boolean(state.liveBattle)
    && Boolean(state.room?.viewer_player_id)
    && (!hadBattle || previousScreen === "battle");
  syncScreen({ preferBattle: autoEnterBattle || (preserveScreen && previousScreen === "battle") });
  syncSelectedUnitAfterStateChange();
  syncBattleVfxState({ hadBattle, boardChanged: Boolean(state.liveBattle) && nextBoardKey !== previousBoardKey });
}

function roomHeroSelectionSummary(heroCode) {
  if (!hasRoom() || !heroCode) return "";
  const pickers = (state.room.seats || [])
    .filter((seat) => seat.hero_code === heroCode)
    .map((seat) => `玩家 ${seat.player_id}`);
  return pickers.length ? pickers.join(" / ") : "";
}

function fallbackRoomModes() {
  return [
    {
      code: "classic",
      name: "标准选将",
      description: "双方先各自选好武将，再在 8x8 战场固定出生开局。",
    },
    {
      code: "random",
      name: "随机选人",
      description: "无需手动选将，开局后随机分配武将，使用更大的战场与随机出生，并按能力值决定先手。",
    },
  ];
}

function availableRoomModes() {
  return state.room?.available_modes?.length ? state.room.available_modes : fallbackRoomModes();
}

function roomModeMeta(modeCode = state.room?.mode) {
  return availableRoomModes().find((mode) => mode.code === modeCode) || fallbackRoomModes()[0];
}

function isRandomRoomMode() {
  return state.room?.mode === "random";
}

function renderScreens() {
  $("draft-screen").classList.toggle("hidden", state.screen !== "draft");
  $("battle-screen").classList.toggle("hidden", state.screen !== "battle");
}

function renderNavigation() {
  const canResume = hasBattle();
  $("nav-draft").classList.toggle("hidden", state.screen !== "battle" || !hasRoom());
  $("nav-battle").classList.toggle("hidden", !(state.screen === "draft" && canResume));
  $("copy-invite-top").classList.toggle("hidden", !state.room?.invite_url);
  $("edit-profile").classList.toggle("hidden", state.screen === "battle");
  $("nav-battle").textContent = isGameOver() ? "查看终局" : "返回战场";
}

function renderHeroCards() {
  const homeCards = $("home-hero-cards");
  const lobbyCards = $("hero-cards");
  const viewerSeat = currentRoomSeat();
  const selectedHeroCode = viewerSeat?.hero_code || "";
  const randomMode = isRandomRoomMode();
  const canSelect = Boolean(hasRoom() && state.room?.status === "lobby" && viewerSeat && !randomMode);

  homeCards.innerHTML = "";
  lobbyCards.innerHTML = "";

  state.heroes.forEach((hero) => {
    const homeCard = document.createElement("article");
    homeCard.className = "hero-card";
    homeCard.innerHTML = `
      <h3>${hero.name}</h3>
      <div class="meta">${hero.role} / ${hero.attribute} / ${hero.race} / 等级 ${hero.level}</div>
      <div class="meta">攻 ${hero.stats.attack} · 守 ${hero.stats.defense} · 速 ${hero.stats.speed} · 范 ${hero.stats.attack_range} · 魔 ${hero.stats.mana}</div>
      <div class="text"><strong>技能:</strong>${hero.raw_skill_text}</div>
      <div class="text"><strong>特性:</strong>${hero.raw_trait_text}</div>
    `;
    homeCards.append(homeCard);

    const lobbyCard = document.createElement("article");
    lobbyCard.className = `hero-card ${selectedHeroCode === hero.code ? "is-selected" : ""}`;
    const selectedBy = roomHeroSelectionSummary(hero.code);
    const selectionText = selectedBy || (randomMode ? randomRoomFallbackSummary(state.room) : "尚无人选择");
    lobbyCard.innerHTML = `
      <h3>${hero.name}</h3>
      <div class="meta">${hero.role} / ${hero.attribute} / ${hero.race} / 等级 ${hero.level}</div>
      <div class="meta">攻 ${hero.stats.attack} · 守 ${hero.stats.defense} · 速 ${hero.stats.speed} · 范 ${hero.stats.attack_range} · 魔 ${hero.stats.mana}</div>
      <div class="text"><strong>技能:</strong>${hero.raw_skill_text}</div>
      <div class="text"><strong>特性:</strong>${hero.raw_trait_text}</div>
      <div class="text"><strong>当前选择:</strong>${selectedBy || "尚无人选择"}</div>
    `;
    const selectionSummary = lobbyCard.querySelector(".text:last-of-type");
    if (selectionSummary) {
      selectionSummary.innerHTML = `<strong>当前选择:</strong>${selectionText}`;
    }
    const pickBtn = document.createElement("button");
    pickBtn.className = selectedHeroCode === hero.code ? "ghost" : "primary";
    pickBtn.textContent = selectedHeroCode === hero.code ? "已选此武将" : "选择该武将";
    pickBtn.disabled = !canSelect;
    pickBtn.addEventListener("click", () => selectRoomHero(hero.code));
    if (randomMode) {
      pickBtn.className = "ghost";
      pickBtn.textContent = "本模式随机分配";
      pickBtn.disabled = true;
    }
    lobbyCard.append(pickBtn);
    lobbyCards.append(lobbyCard);
  });
}

function renderHeader() {
  const pill = $("turn-pill");
  const topbarSubline = $("topbar-subline");
  const caption = $("board-caption");
  const modeMeta = roomModeMeta();
  if (!hasRoom()) {
    pill.textContent = "尚未进入房间";
    topbarSubline.textContent = "创建房间、复制邀请链接、让两位玩家分别进入同一房间后在线对战。";
    caption.textContent = "请先创建房间或加入房间。";
    return;
  }
  if (!state.battle) {
    pill.textContent = `房间 ${state.room.room_id} · ${state.room.status === "lobby" ? "大厅中" : "等待开局"}`;
    topbarSubline.textContent = state.room.viewer_player_id
      ? `你当前是玩家 ${state.room.viewer_player_id}。在大厅里为自己选择武将，双方都选好后开始对局。`
      : "你当前还没有占用席位。若房间仍有空位，输入昵称后即可加入。";
    caption.textContent = "对局尚未开始，请先在房间大厅完成选将。";
    return;
  }
  const viewerSummary = state.room.viewer_player_id
    ? `房间 ${state.room.room_id} 在线对战中。你当前是玩家 ${state.room.viewer_player_id}。`
    : `房间 ${state.room.room_id} 在线对战中。你当前以观战视角查看此房间。`;
  const nextTurnName = state.battle.next_turn_unit_name || "";
  const nextTurnPlayerId = state.battle.next_turn_player_id;
  const nextTurnSummary = nextTurnName && nextTurnPlayerId
    ? `下回合：玩家 ${nextTurnPlayerId} 的 ${nextTurnName}。`
    : "下回合待定。";
  topbarSubline.textContent = `${viewerSummary} ${nextTurnSummary}`;
  if (isGameOver()) {
    pill.textContent = `房间 ${state.room.room_id} · 玩家 ${state.battle.winner} 获胜`;
    caption.textContent = `玩家 ${state.battle.winner} 已获胜，战场已锁定。`;
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    pill.textContent = `房间 ${state.room.room_id} · 玩家 ${inputPlayer()} 重新出现中`;
    caption.textContent = `请选择 ${unit?.name || "消失单位"} 的重新出现位置。`;
    return;
  }
  if (isChainMode()) {
    const current = state.battle.pending_chain?.current_unit_id
      ? unitById(state.battle.pending_chain.current_unit_id)?.name
      : "响应方";
    const sourceSummary = chainQueuedActionPrompt(state.battle.pending_chain);
    pill.textContent = `房间 ${state.room.room_id} · 玩家 ${inputPlayer()} 连锁中`;
    caption.textContent = `等待 ${current} 响应 ${sourceSummary}`;
    return;
  }
  pill.textContent = `房间 ${state.room.room_id} · 第 ${state.battle.round_number} 轮 · 玩家 ${inputPlayer()} 行动`;
  caption.textContent = "点击己方棋子，在棋子周围选择动作。";
}

function renderBoardAlert() {
  const node = $("board-alert");
  if (!state.battle || isGameOver() || state.screen !== "battle") {
    node.className = "board-alert hidden";
    node.innerHTML = "";
    return;
  }

  const action = selectedAction();

  if (isChainMode() && !action) {
    const chain = state.battle.pending_chain;
    const reactor = unitById(chain?.current_unit_id || "");
    const source = unitById(chain?.queued_action?.actor_id || "");
    const sourceSummary = chainQueuedActionSummary(chain);
    node.className = "board-alert is-chain";
    node.innerHTML = `
      <strong>对方可连锁</strong>
      <span>${reactor?.name || "响应单位"} 正在决定是否对 ${source?.name || "来源单位"} 的【${chain?.queued_action?.display_name || "动作"}】进行连锁。</span>
    `;
    node.innerHTML += `<span class="board-alert-detail">${sourceSummary}</span>`;
    return;
  }

  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>重新出现</strong>
      <span>${unit?.name || "该单位"} 即将重新出现。请点击蓝色高亮的最近可用格子。</span>
    `;
    return;
  }

  if (action && movePathSelection(action)) {
    const chosenCells = stagedMovePath(action);
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>${chosenCells.length ? `已选择 ${chosenCells.length} 格移动路径。` : "请逐格点出这次移动的路径。"} 绿色高亮表示单位最后会占据的格子；多格单位可以点击绿色占据区域来选择落点。可以提前点击“完成选择”，也可以点击已选格子回退路径。</span>
    `;
    return;
  }

  if (action?.code === "mana_pull" && !state.stagedPayload?.targetUnitId) {
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>魔力牵引</strong>
      <span>先点击被牵引的单位,再点击 1 到 3 格直线落点。</span>
    `;
    return;
  }

  if (action?.code === "mana_pull" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>魔力牵引</strong>
      <span>已选中 ${target?.name || "目标"},请点击其 1 到 3 格的直线落点。</span>
    `;
    return;
  }

  if (action?.code === "backstep_shot" && isChainMode()) {
    const retreatCell = stagedBackstepRetreatCell(action);
    const source = unitById(state.battle?.pending_chain?.queued_action?.actor_id || "");
    const targetIds = retreatCell ? backstepFollowUpTargetIds(action, retreatCell) : [];
    const canCounter = targetIds.some((id) => unitIsSelectableTarget(unitById(id)));
    node.className = "board-alert is-step";
    if (!retreatCell) {
      node.innerHTML = `
        <strong>${actionTitle(action)}</strong>
        <span>先点击一个直线 2 格的撤步落点。撤步完成后，你可以选择只反击 ${source?.name || "原连锁来源"}，也可以直接点“完成选择”放弃反击。</span>
      `;
      return;
    }
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>${canCounter ? `撤步落点已确定。现在先决定是否反击：点击 ${source?.name || "原连锁来源"} 就会立刻反击；点击“完成选择”则表示不反击。` : "撤步落点已确定，但撤步后已无法攻击原连锁来源。请直接点“完成选择”结算。"} 再点一次已选落点可回到第一步。</span>
    `;
    return;
  }

  if (action && multiUnitSelection(action)) {
    const chosenIds = stagedMultiTargetIds(action);
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>${chosenIds.length ? `已选择 ${chosenIds.length} 个目标。` : "请点击高亮单位来选择目标。"} 选好后可以点击“完成选择”，再次点击同一目标可取消。</span>
    `;
    return;
  }

  if (action && statCellSelection(action)) {
    const chosenCells = stagedStatCells(action);
    const required = statCellRequired(action);
    const statName = stagedStatName(action);
    const statButtons = (statCellSelection(action).stats || []).map((entry) => `
      <button type="button" class="board-alert-choice ${statName === entry.code ? "is-selected" : ""}" data-stat-choice="${entry.code}">${entry.label}</button>
    `).join("");
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>先选择要吸取的能力值，再选择 ${required} 个新增占格。当前已选 ${chosenCells.length} 个新增格。</span>
      <div class="board-alert-actions">${statButtons}</div>
    `;
    return;
  }

  if (action && bodyDirectionSelection(action)) {
    const chosenCells = stagedBodyCells(action);
    const direction = stagedBodyDirection(action);
    const directionButtons = (bodyDirectionSelection(action).directions || []).map((entry) => {
      const selected = direction && Number(entry.dx) === direction.dx && Number(entry.dy) === direction.dy;
      return `<button type="button" class="board-alert-choice ${selected ? "is-selected" : ""}" data-direction-dx="${entry.dx}" data-direction-dy="${entry.dy}">${entry.label}</button>`;
    }).join("");
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>点击岩神身体格选择要发射的部分，然后选择方向。当前已选 ${chosenCells.length} 格；再次点击已选身体格可取消。</span>
      <div class="board-alert-actions">${directionButtons}</div>
    `;
    return;
  }

  if (action && choicePatternSelection(action)) {
    const chosenCells = stagedPatternCells(action);
    const canComplete = patternSelectionCanComplete(action, chosenCells);
    const choiceCode = stagedPatternChoiceCode(action);
    const choiceLabel = (choicePatternSelection(action).choices || []).find(
      (entry) => String(entry.code) === choiceCode,
    )?.label || choiceCode;
    const choiceButtons = (choicePatternSelection(action).choices || []).map((entry) => `
      <button type="button" class="board-alert-choice ${choiceCode === String(entry.code) ? "is-selected" : ""}" data-pattern-choice="${entry.code}">${entry.label}</button>
    `).join("");
    node.className = "board-alert is-step";
    if (action.kind === "attack") {
      if (!choiceCode) {
        node.innerHTML = `
          <strong>${actionTitle(action)}</strong>
          <span>先声明这次普攻的前方方向，再点击该方向外侧高亮出来的可攻击目标。</span>
          <div class="board-alert-actions">${choiceButtons}</div>
        `;
        return;
      }
      node.innerHTML = `
        <strong>${actionTitle(action)}</strong>
        <span>已声明方向“${choiceLabel}”。现在点击该方向外侧高亮出来的目标格或目标单位即可普攻；若想换方向，直接重新点下面的方向按钮。</span>
        <div class="board-alert-actions">${choiceButtons}</div>
      `;
      return;
    }
    if (!choiceCode) {
      node.innerHTML = `
        <strong>${actionTitle(action)}</strong>
        <span>先选择这次的 n，再逐格点击要覆盖的区域。</span>
        <div class="board-alert-actions">${choiceButtons}</div>
      `;
      return;
    }
    if (!chosenCells.length) {
      node.innerHTML = `
        <strong>${actionTitle(action)}</strong>
        <span>已选择 ${choiceCode}。现在请逐格点击这个 n 对应的合法区域；若贴着边界导致剩余格子本应落在棋盘外，可以直接点“完成选择”。</span>
        <div class="board-alert-actions">${choiceButtons}</div>
      `;
      return;
    }
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>已选择 ${choiceCode}，并选中 ${chosenCells.length} 格。${canComplete ? "当前已经可以点击“完成选择”结算；若还想扩大到同一合法区域，可继续点蓝色高亮格子。" : "请继续点击蓝色高亮的剩余格子。"} 点击已选格子可撤回该格。</span>
      <div class="board-alert-actions">${choiceButtons}</div>
    `;
    return;
  }

  if (action && patternSelection(action)) {
    const chosenCells = stagedPatternCells(action);
    const canComplete = patternSelectionCanComplete(action, chosenCells);
    node.className = "board-alert is-step";
    if (!chosenCells.length) {
      node.innerHTML = `
        <strong>${actionTitle(action)}</strong>
        <span>请依次点击要覆盖的格子；若贴着边界导致剩余格子本应落在棋盘外，可以直接点“完成选择”，也可以随时取消。</span>
      `;
      return;
    }
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>已选 ${chosenCells.length} 格。${canComplete ? "当前已经可以点击“完成选择”结算；若还想扩大到同一合法区域，可继续点蓝色高亮格子。" : "请继续点击蓝色高亮的剩余格子。"} 点击已选格子可撤回该格。</span>
    `;
    return;
  }

  if (action && actionNeedsTarget(action)) {
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>请点击棋盘上蓝色高亮的可选目标或范围。</span>
    `;
    return;
  }

  node.className = "board-alert hidden";
  node.innerHTML = "";
}

function renderBattleEffects() {
  const node = $("battle-effects");
  if (!node) return;
  node.innerHTML = "";
  if (!state.battle || state.screen !== "battle") {
    return;
  }
  const effects = fieldEffects();
  if (!effects.length) {
    node.innerHTML = `<div class="effect-pill is-empty">当前无战场状态</div>`;
    return;
  }
  effects.forEach((effect) => {
    const chip = document.createElement("div");
    chip.className = "effect-pill";
    chip.title = effect.description || effect.name;
    chip.innerHTML = `
      <strong>${effect.name}</strong>
      <span>${fieldEffectDuration(effect)}</span>
    `;
    node.append(chip);
  });
}

function renderBoard() {
  const board = $("board");
  board.innerHTML = "";
  const preview = currentPreview();
  const selected = selectedUnit();

  if (!state.battle) {
    board.classList.remove("is-large-board");
    board.style.width = "";
    board.style.minWidth = "";
    board.style.maxWidth = "";
    return;
  }
  const isLargeBoard = state.battle.board.width > 8 || state.battle.board.height > 8;
  board.classList.toggle("is-large-board", isLargeBoard);
  board.style.gridTemplateColumns = `repeat(${state.battle.board.width}, minmax(0, 1fr))`;
  board.style.aspectRatio = `${state.battle.board.width} / ${state.battle.board.height}`;
  const boardPixels = boardBasePixels(state.battle.board);
  const zoom = clampBoardZoom(state.boardZoom);
  state.boardZoom = zoom;
  board.style.width = `${Math.round(boardPixels * zoom)}px`;
  board.style.minWidth = `${Math.round(boardPixels * zoom)}px`;
  board.style.maxWidth = "none";
  const chain = state.battle.pending_chain;
  const chainSource = unitById(chain?.queued_action?.actor_id || "");
  const chainReactor = unitById(chain?.current_unit_id || "");
  const fieldCellMap = fieldEffectsByCell();
  const activeAction = hoveredAction();
  const bodySelectionActive = Boolean(bodyDirectionSelection(activeAction));

  for (let y = 0; y < state.battle.board.height; y += 1) {
    for (let x = 0; x < state.battle.board.width; x += 1) {
      const cell = document.createElement("button");
      cell.className = "cell";
      cell.type = "button";
      cell.dataset.x = x;
      cell.dataset.y = y;
      cell.style.gridColumn = String(x + 1);
      cell.style.gridRow = String(y + 1);
      cell.disabled = false;

      const unitsHere = allUnits().filter(
        (unit) => unit.position && unitOccupiedCells(unit).some((cellPosition) => cellPosition.x === x && cellPosition.y === y),
      );
      const occupant = unitsHere.find((unit) => !unit.banished) || unitsHere[0] || null;
      const ghostUnits = unitsHere.filter((unit) => unit.banished);

      const key = `${x},${y}`;
      const cellEffects = fieldCellMap.get(key) || [];
      if (preview.cellKeys.has(key)) cell.classList.add("is-preview");
      if (preview.secondaryCellKeys.has(key)) cell.classList.add("is-secondary");
      if (preview.destinationCellKeys?.has(key)) cell.classList.add("is-footprint-destination");
      if (occupant && preview.targetIds.has(occupant.id)) cell.classList.add("is-target");
      if (unitOccupiedCells(selected).some((cellPosition) => cellPosition.x === x && cellPosition.y === y)) cell.classList.add("is-selected");
      if (unitOccupiedCells(chainSource).some((cellPosition) => cellPosition.x === x && cellPosition.y === y)) cell.classList.add("is-chain-source");
      if (unitOccupiedCells(chainReactor).some((cellPosition) => cellPosition.x === x && cellPosition.y === y)) cell.classList.add("is-chain-reactor");
      if (cellEffects.length) cell.classList.add("has-field-effect");

      if (cellEffects.length) {
        const markerStack = document.createElement("div");
        markerStack.className = "cell-effects";
        cellEffects.forEach((effect) => {
          const marker = document.createElement("span");
          marker.className = "cell-effect-tag";
          marker.textContent = fieldEffectMarker(effect);
          marker.title = effect.description ? `${effect.name}:${effect.description}` : effect.name;
          markerStack.append(marker);
        });
        cell.append(markerStack);
      }

      if (ghostUnits.length) {
        cell.classList.add("has-ghost");
        const ghostStack = document.createElement("div");
        ghostStack.className = "ghost-stack";
        ghostUnits.forEach((ghostUnit) => {
          const ghost = document.createElement("div");
          ghost.className = `ghost-piece player-${ghostUnit.player_id}`;
          ghost.textContent = `${ghostUnit.name} · 消失${ghostUnit.banish_turns_remaining > 0 ? `(${ghostUnit.banish_turns_remaining})` : ""}`;
          ghostStack.append(ghost);
        });
        cell.append(ghostStack);
      }

      board.append(cell);
    }
  }

  allUnits()
    .filter((unit) => unit.position && !unit.banished)
    .sort((left, right) => {
      const layerGap = boardPieceZIndex(left) - boardPieceZIndex(right);
      if (layerGap !== 0) return layerGap;
      return left.id.localeCompare(right.id);
    })
    .forEach((unit) => {
      const isStealthed = unit.statuses.some((status) => status.name === "隐身");
      const bounds = unitFootprintBounds(unit);
      const occupied = unitOccupiedCells(unit);
      const largeFootprint = unitHasLargeFootprint(unit);
      const footprintCellsMarkup = largeFootprint
        ? `
          <div class="piece-footprint-cells" style="grid-template-columns: repeat(${bounds.width}, minmax(0, 1fr)); grid-template-rows: repeat(${bounds.height}, minmax(0, 1fr));">
            ${occupied.map((cell) => {
              const key = positionKey(cell);
              const classes = ["piece-footprint-cell"];
              if (bodySelectionActive && preview.cellKeys.has(key)) classes.push("is-body-selectable");
              if (bodySelectionActive && preview.secondaryCellKeys.has(key)) classes.push("is-body-selected");
              return `
                <span class="${classes.join(" ")}" style="grid-column: ${Number(cell.x) - bounds.minX + 1}; grid-row: ${Number(cell.y) - bounds.minY + 1};"></span>
              `;
            }).join("")}
          </div>
        `
        : "";
      const piece = document.createElement("div");
      piece.className = `piece board-piece player-${unit.player_id} ${largeFootprint ? "is-footprint" : ""} ${isStealthed ? "is-stealthed" : ""}`;
      piece.style.gridColumn = `${bounds.minX + 1} / span ${bounds.width}`;
      piece.style.gridRow = `${bounds.minY + 1} / span ${bounds.height}`;
      piece.style.zIndex = String(boardPieceZIndex(unit));
      piece.style.setProperty("--hp-angle", `${hpRatio(unit) * 360}deg`);
      piece.innerHTML = `
        ${footprintCellsMarkup}
        <div class="piece-ring ${isStealthed ? "is-stealthed" : ""}">
          <div class="piece-core">
            <div class="piece-name">${unit.name}</div>
          </div>
        </div>
        <div class="${manaDisplayClass(unit)}" aria-label="魔力 ${trimNumber(unit.mana)} / ${trimNumber(unit.max_mana || unit.base_stats?.mana || unit.stats?.max_mana || unit.stats?.mana || unit.mana)}">
          ${manaPipsMarkup(unit)}
        </div>
      `;
      board.append(piece);
    });
}

function renderActionWheel() {
  const wheel = $("action-wheel");
  wheel.innerHTML = "";
  if (!canInteract()) return;
  if (!isChainMode() && state.selectedActionCode) return;
  const unit = selectedUnit();
  if (!unit?.position) return;

  const actions = displayActions();
  if (!actions.length) return;
  if (isChainMode() && state.battle.pending_chain?.current_unit_id !== unit.id) return;
  if (!isChainMode() && unit.player_id !== inputPlayer()) return;

  const selectedCells = [...document.querySelectorAll(".cell")].filter((cell) => (
    unitOccupiedCells(unit).some((occupiedCell) => (
      Number(cell.dataset.x) === occupiedCell.x && Number(cell.dataset.y) === occupiedCell.y
    ))
  ));
  const stageRect = $("board-stage").getBoundingClientRect();
  if (!selectedCells.length) return;
  const rects = selectedCells.map((cell) => cell.getBoundingClientRect());
  const cellRect = {
    left: Math.min(...rects.map((rect) => rect.left)),
    right: Math.max(...rects.map((rect) => rect.right)),
    top: Math.min(...rects.map((rect) => rect.top)),
    bottom: Math.max(...rects.map((rect) => rect.bottom)),
  };
  cellRect.width = cellRect.right - cellRect.left;
  cellRect.height = cellRect.bottom - cellRect.top;

  const centerX = cellRect.left - stageRect.left + cellRect.width / 2;
  const centerY = cellRect.top - stageRect.top + cellRect.height / 2;
  const radius = 100;
  const battleMode = document.body?.classList?.contains?.("battle-mode");
  const buttonHalfWidth = battleMode ? 37 : 42;
  const buttonHalfHeight = battleMode ? 20 : 23;
  const stageWidth = stageRect.width || Math.max(0, stageRect.right - stageRect.left);
  const stageHeight = stageRect.height || Math.max(0, stageRect.bottom - stageRect.top);
  const overlayMargin = 12;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const clampCenter = (value, stageSpan, buttonHalfSpan) => {
    if (!Number.isFinite(stageSpan) || stageSpan <= 0) return value;
    const minCenter = radius + buttonHalfSpan + overlayMargin;
    const maxCenter = stageSpan - radius - buttonHalfSpan - overlayMargin;
    if (minCenter > maxCenter) return stageSpan / 2;
    return clamp(value, minCenter, maxCenter);
  };
  const clampedCenterX = clampCenter(centerX, stageWidth, buttonHalfWidth);
  const clampedCenterY = clampCenter(centerY, stageHeight, buttonHalfHeight);

  actions.forEach((action, index) => {
    const angle = (-90 + (360 / actions.length) * index) * (Math.PI / 180);
    const targetLeft = clampedCenterX + Math.cos(angle) * radius - buttonHalfWidth;
    const targetTop = clampedCenterY + Math.sin(angle) * radius - buttonHalfHeight;
    const maxLeft = Math.max(overlayMargin, stageWidth - buttonHalfWidth * 2 - overlayMargin);
    const maxTop = Math.max(overlayMargin, stageHeight - buttonHalfHeight * 2 - overlayMargin);
    const left = clamp(targetLeft, overlayMargin, maxLeft);
    const top = clamp(targetTop, overlayMargin, maxTop);
    const btn = document.createElement("button");
    btn.className = `action-btn ${state.selectedActionCode === action.code ? "is-selected" : ""}`;
    btn.style.left = `${left}px`;
    btn.style.top = `${top}px`;
    btn.innerHTML = `${actionLabel(action)}<small>${actionTimingLabel(action)}</small>`;
    btn.addEventListener("pointerenter", (event) => {
      state.hoveredActionCode = action.code;
      state.hoverPointer = { x: event.clientX, y: event.clientY };
      renderHoverCard();
    });
    btn.addEventListener("pointermove", (event) => {
      state.hoveredActionCode = action.code;
      state.hoverPointer = { x: event.clientX, y: event.clientY };
      renderHoverCard();
    });
    btn.addEventListener("pointerleave", () => {
      state.hoveredActionCode = "";
      renderHoverCard();
    });
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      onActionClick(action);
    });
    wheel.append(btn);
  });
}

function renderBoardOverlays() {
  renderBattleVfx();
  renderBoardAlert();
  renderActionWheel();
}

function scheduleBoardOverlayRender() {
  if (boardOverlayRenderHandle && typeof window.cancelAnimationFrame === "function") {
    window.cancelAnimationFrame(boardOverlayRenderHandle);
  }
  if (typeof window.requestAnimationFrame === "function") {
    boardOverlayRenderHandle = window.requestAnimationFrame(() => {
      boardOverlayRenderHandle = 0;
      renderBoardOverlays();
    });
    return;
  }
  renderBoardOverlays();
}

function renderUnitHoverCard(unit) {
  const statuses = unitStatusSummary(unit).join(" · ") || "无";
  const traits = unit.traits.map((trait) => trait.name).join(" · ") || "无";
  return `
    <div class="hover-card-tag">悬浮信息</div>
    <strong>${unit.name}</strong>
    <p>${unit.role} · ${unit.attribute} / ${unit.race} · 玩家 ${unit.player_id}</p>
    <p>血 ${trimNumber(unit.hp)} / ${trimNumber(unit.max_hp)} · 魔 ${trimNumber(unit.mana)} / ${trimNumber(unit.max_mana || unit.base_stats?.mana || unit.stats?.max_mana || unit.stats?.mana || unit.mana)} · 魔力点 ${trimNumber(unit.mana_points || unit.stats?.mana_points || 0)}</p>
    <p>盾 ${unit.total_shields} · 闪 ${unit.dodge_charges} · 攻 ${trimNumber(unit.stats.attack)} / 守 ${trimNumber(unit.stats.defense)}</p>
    <p>状态:${statuses}</p>
    <p>特性:${traits}</p>
  `;
}

function renderActionHoverCard(action) {
  return `
    <div class="hover-card-tag">悬浮说明</div>
    <strong>${actionTitle(action)}</strong>
    <p>${action.description}</p>
    <p>${actionTierLabel(action)} · ${actionTimingLabel(action)} · ${actionManaLabel(action) || "不消耗魔力"}</p>
    <p>${actionLimitLabel(action)} · ${actionNeedsTarget(action) ? "需要选取目标" : "无需额外目标"}</p>
  `;
}

function chainQueuedActionSummary(chain) {
  return chain?.queued_action_effect_summary || chain?.queued_action?.description || "\u539f\u52a8\u4f5c\u5c06\u6309\u539f\u58f0\u660e\u7ee7\u7eed\u7ed3\u7b97\u3002";
}

function chainQueuedActionPrompt(chain) {
  const summary = chainQueuedActionSummary(chain);
  if (summary && summary.startsWith("\u3010")) return summary;
  const actionName = chain?.queued_action?.display_name || "\u539f\u52a8\u4f5c";
  return `\u3010${actionName}\u3011\uff1a${summary}`;
}

function chainOptionSummary(options = []) {
  return options
    .map((action) => `${action.action_name}\uff08\u901f\u5ea6${action.chain_speed}\uff1a${action.description}\uff09`)
    .join(" / ");
}

function renderHoverCard() {
  const card = $("hover-card");
  const unit = hoveredUnit();
  const action = !unit ? actionByCode(state.hoveredActionCode) : null;
  if ((!unit && !action) || !state.battle || state.screen !== "battle") {
    card.classList.add("is-empty");
    card.innerHTML = "";
    return;
  }
  card.classList.remove("is-empty");
  card.innerHTML = unit ? renderUnitHoverCard(unit) : renderActionHoverCard(action);
}

function renderSidebarPanels() {
  const expanded = effectiveSidebarPanel();
  document.querySelectorAll(".sidebar-panel").forEach((panel) => {
    const panelCode = panel.dataset.sidebarPanel || "";
    const open = panelCode === expanded;
    panel.classList.toggle("is-open", open);
    panel.classList.toggle("is-collapsed", !open);
  });
  document.querySelectorAll(".sidebar-toggle").forEach((button) => {
    const panelCode = button.dataset.sidebarPanel || "";
    const open = panelCode === expanded;
    button.setAttribute("aria-expanded", open ? "true" : "false");
    button.textContent = open ? "收起" : "展开";
  });
}

function renderSelectedCard() {
  const panel = $("selected-card");
  const unit = selectedUnit();
  if (!unit) {
    panel.textContent = isGameOver()
      ? `玩家 ${state.battle?.winner || ""} 已获胜,战场操作已锁定。`
      : "点击棋子后,这里会显示该武将的数值、技能与状态。";
    return;
  }
  const statusEntries = unit.statuses.map((status) => `${status.name}${status.duration ? `(${status.duration})` : ""}`);
  if (unit.banished) {
    statusEntries.unshift(`消失${unit.banish_turns_remaining > 0 ? `(${unit.banish_turns_remaining})` : ""}`);
  }
  const statuses = statusEntries.join(",") || "无";
  const traits = unit.traits.map((trait) => trait.name).join(",") || "无";
  panel.innerHTML = `
    <strong>${unit.name}</strong>
    <div class="statline">玩家 ${unit.player_id} · ${unit.role} / ${unit.attribute} / ${unit.race} / 等级 ${unit.level}</div>
    <div class="statline">攻 ${trimNumber(unit.stats.attack)} · 守 ${trimNumber(unit.stats.defense)} · 速 ${trimNumber(unit.stats.speed)} · 范 ${trimNumber(unit.stats.attack_range)} · 魔 ${trimNumber(unit.mana)} · 魔力点 ${trimNumber(unit.mana_points || unit.stats?.mana_points || 0)}</div>
    <div class="statline">血 ${trimNumber(unit.hp)} / ${trimNumber(unit.max_hp)} · 固定护盾 ${unit.shields} · 临时护盾 ${unit.temporary_shields} · 闪避 ${unit.dodge_charges}</div>
    <div class="statline"><strong>状态:</strong>${statuses}</div>
    <div class="statline"><strong>特性:</strong>${traits}</div>
    <div class="statline"><strong>原始技能:</strong>${unit.raw_skill_text}</div>
    <div class="statline"><strong>原始特性:</strong>${unit.raw_trait_text}</div>
  `;
}

function renderRoomPanels() {
  const showLobby = shouldShowLobbyPanel();
  const roomId = roomQueryId();
  $("room-home").classList.toggle("hidden", showLobby);
  $("room-lobby").classList.toggle("hidden", !showLobby);
  if ($("create-name")) $("create-name").value = state.roomForm.createName;
  if ($("join-name")) $("join-name").value = state.roomForm.joinName;
  $("join-room-code").value = roomId || state.roomForm.joinRoomCode;

  const title = $("lobby-title");
  const caption = $("lobby-caption");
  const copyInvite = $("copy-invite");
  const roomBattle = $("room-battle");
  const startRoom = $("start-room");

  if (!hasRoom()) {
    title.textContent = "在线房间";
    caption.textContent = "先创建房间或输入房间码加入。进入房间后,每位玩家各自选择自己的武将,再开始对局。";
    copyInvite.classList.add("hidden");
    roomBattle.classList.add("hidden");
    startRoom.classList.add("hidden");
    return;
  }

  if (!showLobby) {
    title.textContent = `加入房间 ${state.room.room_id}`;
    caption.textContent = "这个房间仍在等待玩家占位。输入昵称后点击加入房间,即可进入房间大厅开始选将。";
    copyInvite.classList.remove("hidden");
    roomBattle.classList.toggle("hidden", !hasBattle());
    startRoom.classList.add("hidden");
    return;
  }

  title.textContent = `房间 ${state.room.room_id}`;
  caption.textContent = hasBattle()
    ? "对局已经开始。你可以返回战场继续测试,或留在这里查看房间信息。"
    : "双方玩家在这里各自选择自己的武将,准备完成后开始对局。";

  if (!hasBattle() && isRandomRoomMode()) {
    caption.textContent = `当前使用随机选人模式。房主设置后，开局时双方${randomModeSummary}。`;
  }

  $("room-code-label").textContent = state.room.room_id;
  $("room-status-label").textContent = state.room.status === "lobby"
    ? "等待双方就绪"
    : (isGameOver() ? "对局结束" : "对局进行中");
  $("viewer-seat-label").textContent = state.room.viewer_player_id ? `玩家 ${state.room.viewer_player_id}` : "观战 / 未占位";
  $("viewer-seat-note").textContent = state.room.viewer_name
    ? `${state.room.viewer_name}${state.room.viewer_is_host ? " · 房主" : ""}`
    : "当前浏览器还没有占用席位";
  $("invite-path-label").textContent = state.room.invite_url || state.room.invite_path;

  copyInvite.classList.toggle("hidden", !state.room.invite_url);
  deleteRoomBtn.classList.toggle("hidden", !state.room.viewer_is_host);
  deleteRoomBtn.disabled = !state.room.viewer_is_host;
  roomBattle.classList.toggle("hidden", !hasBattle());
  roomBattle.disabled = !hasBattle();
  startRoom.classList.toggle("hidden", !(state.room.status === "lobby" && state.room.viewer_player_id !== null));
  startRoom.disabled = !state.room.can_start;

  const roomMessage = $("room-message");
  if (hasBattle()) {
    roomMessage.textContent = isGameOver()
      ? `房间 ${state.room.room_id} 的本局对战已经结束。你可以进入战场查看终局盘面。`
      : `房间 ${state.room.room_id} 的对局正在进行中。点击"进入战场"即可查看并继续操作。`;
  } else if (state.room.viewer_player_id === null) {
    roomMessage.textContent = state.room.is_full
      ? "这个房间已经满员。你当前可以观战,但不能代替其中任意一位玩家操作。"
      : "这个房间还有空位。若你是受邀玩家,请在首页的加入房间区域输入昵称并加入。";
  } else if (!currentRoomSeat()?.hero_code) {
    roomMessage.textContent = `你当前是玩家 ${state.room.viewer_player_id},请从下方选择自己的武将。`;
  } else if (!state.room.can_start) {
    roomMessage.textContent = "你已经选好了武将,正在等待另一位玩家加入或完成选将。";
  } else {
    roomMessage.textContent = "双方都已就绪,可以开始这场联机测试对局。";
  }

  const seatCards = $("seat-cards");
  seatCards.innerHTML = "";
  (state.room.seats || []).forEach((seat) => {
    const card = document.createElement("article");
    card.className = `seat-card ${seat.player_id === state.room.viewer_player_id ? "is-viewer" : ""} ${seat.occupied ? "" : "is-empty"}`;
    card.innerHTML = `
      <div class="seat-head">
        <div>
          <div class="seat-name">玩家 ${seat.player_id}</div>
          <div class="seat-note">${seat.name || "尚未加入"}</div>
        </div>
        <span class="seat-badge">${seat.is_host ? "房主" : "席位"}</span>
      </div>
      <div class="seat-hero"><strong>当前武将:</strong>${seat.hero_name || "未选择"}</div>
      <div class="seat-note">${seat.occupied ? "已进入房间" : "等待朋友加入该席位"}</div>
    `;
    seatCards.append(card);
  });

  if (isRandomRoomMode() && !hasBattle()) {
    if (state.room.viewer_player_id === null) {
      roomMessage.textContent = state.room.is_full
        ? "\u8fd9\u4e2a\u623f\u95f4\u5df2\u7ecf\u6ee1\u5458ã€‚ä½ å½“å‰å¯ä»¥è§‚æˆ˜ï¼Œä½†ä¸èƒ½ä»£æ›¿å…¶ä¸­ä»»æ„ä¸€ä½çŽ©å®¶æ“ä½œã€‚"
        : `\u8fd9\u4e2a\u623f\u95f4\u8fd8\u6709\u7a7aä½ã€‚ç‚¹å‡»â€œåŠ å…¥æˆ¿é—´â€åŽï¼Œå°±å¯ä»¥ä»¥â€œ${effectiveProfileName()}â€ä½œä¸ºå¦ä¸€ä½çŽ©å®¶è¿›å…¥ã€‚`;
    } else if (state.room.status === "finished") {
      roomMessage.textContent = "\u672c\u5c40\u5bf9\u6218\u5df2\u7ed3\u675fã€‚å¯ä»¥ç›´æŽ¥é‡æ–°å¼€å§‹é€‰å°†ï¼Œå†æ¥ä¸€å±€éšæœºå¯¹å±€ã€‚";
    } else if (!state.room.can_start) {
      roomMessage.textContent = "\u5f53\u524dä½¿ç”¨éšæœºé€‰äººæ¨¡å¼ã€‚æ— éœ€æ‰‹åŠ¨é€‰å°†ï¼Œæ­£åœ¨ç­‰å¾…å¦ä¸€ä½çŽ©å®¶åŠ å…¥ã€‚";
    } else {
      roomMessage.textContent = "\u53cc\u65b9\u90fdå·²å°±ç»ªã€‚å¼€å±€åŽä¼šéšæœºåˆ†é…æ­¦å°†ï¼Œå¹¶åœ¨æ›´å¤§æˆ˜åœºä¸Šéšæœºå‡ºç”Ÿã€‚";
    }
  }

  if (isRandomRoomMode()) {
    [...seatCards.querySelectorAll(".seat-hero")].forEach((node, index) => {
      const seat = (state.room.seats || [])[index];
      if (!seat) return;
      const heroLabel = seat.hero_name || (seat.occupied ? "\u5f00\u5c40\u540e\u968f\u673a\u5206\u914d" : "\u672a\u9009\u62e9");
      node.innerHTML = `<strong>\u5f53\u524d\u6b66\u5c06\uff1a</strong>${heroLabel}`;
    });
  }
}

function renderRoomListActive() {
  const list = $("room-list");
  if (!list) return;
  list.innerHTML = "";
  if (!roomSummaries().length) {
    const empty = document.createElement("div");
    empty.className = "room-list-empty";
    empty.textContent = "\u5f53\u524d\u8fd8\u6ca1\u6709\u516c\u5f00\u623f\u95f4\u3002\u4f60\u53ef\u4ee5\u5148\u521b\u5efa\u4e00\u95f4\uff0c\u6216\u8005\u7a0d\u540e\u7b49\u670b\u53cb\u5efa\u597d\u623f\u95f4\u540e\u76f4\u63a5\u5728\u8fd9\u91cc\u52a0\u5165\u3002";
    list.append(empty);
    return;
  }

  roomSummaries().forEach((room) => {
    const remembered = loadStoredIdentity(room.room_id);
    const seatSummary = (room.seats || [])
      .map((seat) => {
        const summary = seat.hero_summary || seat.hero_name || (room.mode === "random" && seat.occupied ? "开局后随机分配" : "");
        return `席位 ${seat.player_id}：${seat.team_name || ""} · ${seat.name || controllerTypeLabel(seat)}${summary ? ` · ${summary}` : ""}`;
      })
      .join(" / ");
    const card = document.createElement("article");
    card.className = "room-list-card";
    card.innerHTML = `
      <div class="room-list-head">
        <strong>\u623f\u95f4 ${room.room_id}</strong>
        <span class="room-list-state ${roomStateClass(room)}">${roomStateLabel(room)}</span>
      </div>
      <div class="room-list-meta">\u5e2d\u4f4d ${room.occupied_seat_count}/${room.seat_count} \u00b7 ${room.mode_name || roomModeMeta(room.mode).name} \u00b7 ${room.status === "lobby" ? "\u7b49\u5f85\u73a9\u5bb6\u5c31\u7eea" : "\u6b63\u5728\u8fdb\u884c\u6216\u5df2\u7ed3\u675f"}</div>
      <div class="room-list-seats">${seatSummary}</div>
      <div class="room-list-note">${remembered.token ? "\u8fd9\u4e2a\u6d4f\u89c8\u5668\u4e4b\u524d\u8fdb\u5165\u8fc7\u8be5\u623f\u95f4\u3002\u4f60\u53ef\u4ee5\u7ee7\u7eed\u539f\u6765\u7684\u5e2d\u4f4d\uff0c\u4e5f\u53ef\u4ee5\u76f4\u63a5\u7528\u5f53\u524d\u6635\u79f0\u201c${effectiveProfileName()}\u201d\u4f5c\u4e3a\u65b0\u73a9\u5bb6\u52a0\u5165\u3002" : `\u73b0\u5728\u53ef\u4ee5\u76f4\u63a5\u7528\u5f53\u524d\u6635\u79f0\u201c${effectiveProfileName()}\u201d\u52a0\u5165\u3002`} </div>
    `;

    const actions = document.createElement("div");
    actions.className = "room-list-actions";

    const primary = document.createElement("button");
    primary.className = room.can_join ? "primary" : "ghost";
    primary.textContent = room.can_join ? "\u52a0\u5165\u623f\u95f4" : "\u67e5\u770b\u623f\u95f4";
    primary.addEventListener("click", () => {
      if (room.can_join) {
        joinListedRoom(room.room_id);
        return;
      }
      openListedRoom(room.room_id);
    });
    actions.append(primary);

    if (remembered.token) {
      const resumeBtn = document.createElement("button");
      resumeBtn.className = "ghost";
      resumeBtn.textContent = "\u7ee7\u7eed\u539f\u8eab\u4efd";
      resumeBtn.addEventListener("click", () => resumeStoredSeat(room.room_id));
      actions.append(resumeBtn);
    }

    if (!remembered.token && room.can_join) {
      const fillBtn = document.createElement("button");
      fillBtn.className = "ghost";
      fillBtn.textContent = "\u586b\u5165\u623f\u95f4\u7801";
      fillBtn.addEventListener("click", () => {
        state.roomForm.joinRoomCode = room.room_id;
        joinListedRoom(room.room_id);
        $("lobby-caption").textContent = `\u5df2\u586b\u5165\u623f\u95f4 ${room.room_id}\u3002\u70b9\u51fb\u201c\u52a0\u5165\u623f\u95f4\u201d\u540e\uff0c\u5c31\u4f1a\u4ee5\u201c${effectiveProfileName()}\u201d\u52a0\u5165\u3002`;
        renderProfilePanel();
      });
      actions.append(fillBtn);
    }

    card.append(actions);
    list.append(card);
  });
}

function renderResumePanel() {
  const panel = $("resume-room-panel");
  const text = $("resume-room-text");
  const button = $("resume-room");
  if (!panel || !text) return;
  const identity = storedIdentityForCurrentRoom();
  const canReclaim = canReclaimSeatByName();
  const visible = Boolean(roomQueryId() && !viewerPlayerId() && !state.playerToken && (identity.token || canReclaim));
  panel.classList.toggle("hidden", !visible);
  if (!visible) return;
  if (identity.token) {
    text.textContent = `\u68c0\u6d4b\u5230\u8fd9\u4e2a\u6d4f\u89c8\u5668\u4e4b\u524d\u66fe\u4ee5\u201c${identity.name || "\u672a\u547d\u540d\u73a9\u5bb6"}\u201d\u8fdb\u5165\u5f53\u524d\u623f\u95f4\u3002\u4f60\u53ef\u4ee5\u76f4\u63a5\u7ee7\u7eed\u539f\u6765\u7684\u5e2d\u4f4d\u3002`;
    if (button) button.textContent = "\u7ee7\u7eed\u539f\u8eab\u4efd";
    return;
  }
  text.textContent = `\u5f53\u524d\u6635\u79f0\u201c${effectiveProfileName()}\u201d\u4e0e\u623f\u95f4\u91cc\u7684\u65e7\u5e2d\u4f4d\u5339\u914d\u3002\u5982\u679c\u4f60\u662f\u539f\u73a9\u5bb6\uff0c\u53ef\u4ee5\u7528\u8fd9\u4e2a\u6635\u79f0\u6062\u590d\u5e2d\u4f4d\u3002`;
  if (button) button.textContent = "\u6062\u590d\u5e2d\u4f4d";
}

function renderRoomPanels() {
  const showLobby = shouldShowLobbyPanel();
  const roomId = roomQueryId();
  $("room-home").classList.toggle("hidden", showLobby);
  $("room-lobby").classList.toggle("hidden", !showLobby);
  $("join-room-code").value = roomId || state.roomForm.joinRoomCode;

  const title = $("lobby-title");
  const caption = $("lobby-caption");
  const copyInvite = $("copy-invite");
  const roomBattle = $("room-battle");
  const startRoom = $("start-room");
  const leaveRoomBtn = $("leave-room");
  const deleteRoomBtn = $("delete-room");
  const joinRoomButton = $("join-room");
  const modeLabel = $("room-mode-label");
  const modeNote = $("room-mode-note");
  const modeSelect = $("room-mode-select");
  const randomRosterControl = $("random-roster-size-control");
  const randomRosterInput = $("random-roster-size-input");
  const randomRosterNote = $("random-roster-size-note");
  renderRecoveryButton();

  if (!hasRoom()) {
    title.textContent = "\u5728\u7ebf\u623f\u95f4";
    caption.textContent = "\u5148\u786e\u8ba4\u4f60\u8981\u4f7f\u7528\u7684\u6635\u79f0\uff0c\u7136\u540e\u521b\u5efa\u623f\u95f4\u6216\u8f93\u5165\u623f\u95f4\u7801\u52a0\u5165\u3002\u8fdb\u5165\u623f\u95f4\u540e\uff0c\u6bcf\u4f4d\u73a9\u5bb6\u5404\u81ea\u9009\u62e9\u81ea\u5df1\u7684\u6b66\u5c06\uff0c\u518d\u5f00\u59cb\u5bf9\u5c40\u3002";
    leaveRoomBtn.classList.add("hidden");
    deleteRoomBtn.classList.add("hidden");
    copyInvite.classList.add("hidden");
    roomBattle.classList.add("hidden");
    startRoom.classList.add("hidden");
    joinRoomButton.disabled = !state.profileReady || !String($("join-room-code").value || "").trim();
    return;
  }

  const modeMeta = roomModeMeta();
  const randomModeSummary = randomRoomFallbackSummary(state.room);
  if (modeLabel) modeLabel.textContent = modeMeta.name;
  if (modeNote) {
    const hostHint = state.room.viewer_is_host && state.room.status === "lobby"
      ? "房主可在开局前切换模式，切换后会清空当前选将。"
      : "仅房主可在大厅里切换模式。";
    modeNote.textContent = `${modeMeta.description} ${hostHint}`;
  }
  if (modeSelect) {
    modeSelect.innerHTML = "";
    availableRoomModes().forEach((mode) => {
      const option = document.createElement("option");
      option.value = mode.code;
      option.textContent = mode.name;
      if (mode.code === state.room.mode) option.selected = true;
      modeSelect.append(option);
    });
    modeSelect.disabled = !(state.room.viewer_is_host && state.room.status === "lobby");
  }
  if (randomRosterControl) {
    const enabled = isRandomRoomMode();
    randomRosterControl.classList.toggle("hidden", !enabled);
    if (randomRosterInput) {
      randomRosterInput.value = String(randomRoomRosterSize());
      randomRosterInput.disabled = !(enabled && state.room.viewer_is_host && state.room.status === "lobby");
    }
    if (randomRosterNote) {
      randomRosterNote.textContent = `开局时双方各随机获得 ${randomRoomRosterSize()} 个不重复武将。`;
    }
  }

  if (!showLobby) {
    title.textContent = `\u52a0\u5165\u623f\u95f4 ${state.room.room_id}`;
    caption.textContent = isRandomRoomMode()
      ? `\u8fd9\u4e2a\u623f\u95f4\u5f53\u524dæ˜¯ã€Œ${modeMeta.name}ã€ã€‚ç‚¹å‡»â€œåŠ å…¥æˆ¿é—´â€åŽï¼Œå°±ä¼šä»¥å½“å‰æ˜µç§°â€œ${effectiveProfileName()}â€è¿›å…¥æˆ¿é—´å¤§åŽ…ç­‰å¾…å¼€å±€ã€‚`
      : `\u8fd9\u4e2a\u623f\u95f4\u4ecd\u5728\u7b49\u5f85\u73a9\u5bb6\u5360\u4f4d\u3002\u70b9\u51fb\u201c\u52a0\u5165\u623f\u95f4\u201d\u540e\uff0c\u5c31\u4f1a\u4ee5\u5f53\u524d\u6635\u79f0\u201c${effectiveProfileName()}\u201d\u8fdb\u5165\u623f\u95f4\u5927\u5385\u5f00\u59cb\u9009\u5c06\u3002`;
    if (isRandomRoomMode()) {
      caption.textContent = `这个房间当前使用「${modeMeta.name}」。${randomModeSummary}，点击“加入房间”后会以当前昵称“${effectiveProfileName()}”进入大厅等待开局。`;
    }
    leaveRoomBtn.classList.remove("hidden");
    leaveRoomBtn.disabled = false;
    deleteRoomBtn.classList.toggle("hidden", !state.room.viewer_is_host);
    deleteRoomBtn.disabled = !state.room.viewer_is_host;
    copyInvite.classList.remove("hidden");
    roomBattle.classList.toggle("hidden", !hasBattle());
    startRoom.classList.add("hidden");
    joinRoomButton.disabled = !state.profileReady || !String($("join-room-code").value || "").trim();
    return;
  }

  title.textContent = `\u623f\u95f4 ${state.room.room_id}`;
  caption.textContent = hasBattle()
    ? "\u5bf9\u5c40\u5df2\u7ecf\u5f00\u59cb\u3002\u4f60\u53ef\u4ee5\u8fd4\u56de\u6218\u573a\u7ee7\u7eed\u6d4b\u8bd5\uff0c\u6216\u7559\u5728\u8fd9\u91cc\u67e5\u770b\u623f\u95f4\u4fe1\u606f\u3002"
    : (isRandomRoomMode()
      ? "\u5f53\u524dä½¿ç”¨éšæœºé€‰äººæ¨¡å¼ï¼ŒåŒæ–¹å…¥åœºåŽæ— éœ€æ‰‹åŠ¨é€‰å°†ï¼Œå¼€å±€æ—¶ä¼šè‡ªåŠ¨éšæœºåˆ†é…æ­¦å°†ã€‚"
      : "\u53cc\u65b9\u73a9\u5bb6\u5728\u8fd9\u91cc\u5404\u81ea\u9009\u62e9\u81ea\u5df1\u7684\u6b66\u5c06\uff0c\u51c6\u5907\u5b8c\u6210\u540e\u5f00\u59cb\u5bf9\u5c40\u3002");

  $("room-code-label").textContent = state.room.room_id;
  $("room-status-label").textContent = state.room.status === "lobby"
    ? "\u7b49\u5f85\u53cc\u65b9\u5c31\u7eea"
    : (isGameOver() ? "\u5bf9\u5c40\u7ed3\u675f" : "\u5bf9\u5c40\u8fdb\u884c\u4e2d");
  $("viewer-seat-label").textContent = state.room.viewer_player_id ? `\u73a9\u5bb6 ${state.room.viewer_player_id}` : "\u89c2\u6218 / \u672a\u5360\u4f4d";
  $("viewer-seat-note").textContent = state.room.viewer_name
    ? `${state.room.viewer_name}${state.room.viewer_is_host ? " \u00b7 \u623f\u4e3b" : ""}`
    : "\u5f53\u524d\u6d4f\u89c8\u5668\u8fd8\u6ca1\u6709\u5360\u7528\u5e2d\u4f4d";
  $("invite-path-label").textContent = state.room.invite_url || state.room.invite_path;

  leaveRoomBtn.classList.remove("hidden");
  leaveRoomBtn.disabled = false;
  deleteRoomBtn.classList.toggle("hidden", !state.room.viewer_is_host);
  deleteRoomBtn.disabled = !state.room.viewer_is_host;
  copyInvite.classList.toggle("hidden", !state.room.invite_url);
  roomBattle.classList.toggle("hidden", !hasBattle());
  roomBattle.disabled = !hasBattle();
  roomBattle.textContent = "\u8fdb\u5165\u6218\u573a";
  startRoom.classList.toggle("hidden", !(state.room.viewer_player_id !== null && ["lobby", "finished"].includes(state.room.status)));
  startRoom.disabled = state.room.status === "lobby" ? !state.room.can_start : !state.room.can_rematch;
  startRoom.textContent = state.room.status === "finished"
    ? "\u91cd\u65b0\u5f00\u59cb\u9009\u5c06"
    : (isRandomRoomMode() ? "\u5f00\u59cb\u968f\u673a\u5bf9\u5c40" : "\u5f00\u59cb\u5bf9\u5c40");

  const roomMessage = $("room-message");
  if (hasBattle()) {
    roomMessage.textContent = isGameOver()
      ? `\u623f\u95f4 ${state.room.room_id} \u7684\u672c\u5c40\u5bf9\u6218\u5df2\u7ecf\u7ed3\u675f\u3002\u4f60\u53ef\u4ee5\u8fdb\u5165\u6218\u573a\u67e5\u770b\u7ec8\u5c40\u76d8\u9762\uff0c\u6216\u76f4\u63a5\u91cd\u65b0\u5f00\u59cb\u9009\u5c06\u518d\u6765\u4e00\u5c40\u3002`
      : `\u623f\u95f4 ${state.room.room_id} \u7684\u5bf9\u5c40\u6b63\u5728\u8fdb\u884c\u4e2d\u3002\u70b9\u51fb\u201c\u8fdb\u5165\u6218\u573a\u201d\u5373\u53ef\u67e5\u770b\u5e76\u7ee7\u7eed\u64cd\u4f5c\u3002`;
  } else if (state.room.viewer_player_id === null) {
    roomMessage.textContent = state.room.is_full
      ? "\u8fd9\u4e2a\u623f\u95f4\u5df2\u7ecf\u6ee1\u5458\u3002\u4f60\u5f53\u524d\u53ef\u4ee5\u89c2\u6218\uff0c\u4f46\u4e0d\u80fd\u4ee3\u66ff\u5176\u4e2d\u4efb\u610f\u4e00\u4f4d\u73a9\u5bb6\u64cd\u4f5c\u3002"
      : `\u8fd9\u4e2a\u623f\u95f4\u8fd8\u6709\u7a7a\u4f4d\u3002\u70b9\u51fb\u201c\u52a0\u5165\u623f\u95f4\u201d\u540e\uff0c\u5373\u53ef\u4ee5\u201c${effectiveProfileName()}\u201d\u4f5c\u4e3a\u53e6\u4e00\u4f4d\u73a9\u5bb6\u8fdb\u5165\u3002`;
  } else if (!currentRoomSeat()?.hero_code) {
    roomMessage.textContent = `\u4f60\u5f53\u524d\u662f\u73a9\u5bb6 ${state.room.viewer_player_id}\uff0c\u8bf7\u4ece\u4e0b\u65b9\u9009\u62e9\u81ea\u5df1\u7684\u6b66\u5c06\u3002`;
  } else if (state.room.status === "finished") {
    roomMessage.textContent = "\u672c\u5c40\u5bf9\u6218\u5df2\u7ed3\u675f\u3002\u53ef\u4ee5\u76f4\u63a5\u91cd\u65b0\u5f00\u59cb\u9009\u5c06\uff0c\u4e24\u4f4d\u73a9\u5bb6\u5728\u540c\u4e00\u623f\u95f4\u518d\u6765\u4e00\u5c40\u3002";
  } else if (!state.room.can_start) {
    roomMessage.textContent = "\u4f60\u5df2\u7ecf\u9009\u597d\u4e86\u6b66\u5c06\uff0c\u6b63\u5728\u7b49\u5f85\u53e6\u4e00\u4f4d\u73a9\u5bb6\u52a0\u5165\u6216\u5b8c\u6210\u9009\u5c06\u3002";
  } else {
    roomMessage.textContent = "\u53cc\u65b9\u90fd\u5df2\u5c31\u7eea\uff0c\u53ef\u4ee5\u5f00\u59cb\u8fd9\u573a\u8054\u673a\u6d4b\u8bd5\u5bf9\u5c40\u3002";
  }

  const seatCards = $("seat-cards");
  seatCards.innerHTML = "";
  (state.room.seats || []).forEach((seat) => {
    const card = document.createElement("article");
    card.className = `seat-card ${seat.player_id === state.room.viewer_player_id ? "is-viewer" : ""} ${seat.occupied ? "" : "is-empty"}`;
    card.innerHTML = `
      <div class="seat-head">
        <div>
          <div class="seat-name">\u73a9\u5bb6 ${seat.player_id}</div>
          <div class="seat-note">${seat.name || "\u5c1a\u672a\u52a0\u5165"}</div>
        </div>
        <span class="seat-badge">${seat.is_host ? "\u623f\u4e3b" : "\u5e2d\u4f4d"}</span>
      </div>
      <div class="seat-hero"><strong>\u5f53\u524d\u6b66\u5c06\uff1a</strong>${seat.hero_name || "\u672a\u9009\u62e9"}</div>
      <div class="seat-note">${seat.occupied ? "\u5df2\u8fdb\u5165\u623f\u95f4" : "\u7b49\u5f85\u670b\u53cb\u52a0\u5165\u8be5\u5e2d\u4f4d"}</div>
    `;
    seatCards.append(card);
  });
}

function roomStateLabel(room) {
  if (!room) return "";
  if (room.status === "battle") return "\u5bf9\u6218\u4e2d";
  if (room.status === "finished") return "\u5df2\u7ed3\u675f";
  if (room.can_join) return "\u53ef\u52a0\u5165";
  if (room.is_full) return "\u5df2\u6ee1";
  return "\u5927\u5385\u4e2d";
}

function renderUnitStrip() {
  const strip = $("unit-strip");
  strip.innerHTML = "";
  if (isGameOver()) {
    const item = document.createElement("div");
    item.className = "queue-item";
    item.innerHTML = "<strong>对局已结束</strong><p>所有行动已锁定,可返回选将重新开始。</p>";
    strip.append(item);
    return;
  }
  activeBundles().forEach((entry) => {
    const unit = unitById(entry.unit_id);
    if (!unit) return;
    const btn = document.createElement("button");
    btn.className = `unit-chip ${state.selectedUnitId === unit.id ? "is-selected" : ""}`;
    btn.disabled = !canInteract();
    const stateLabel = unit.banished ? ` · 消失${unit.banish_turns_remaining > 0 ? `(${unit.banish_turns_remaining})` : ""}` : "";
    btn.innerHTML = `
      <div class="chip-main">${unit.name}</div>
      <div class="chip-sub">血 ${trimNumber(unit.hp)} · 魔 ${trimNumber(unit.mana)} · 攻 ${trimNumber(unit.stats.attack)} / 守 ${trimNumber(unit.stats.defense)}${stateLabel}</div>
    `;
    btn.addEventListener("click", () => {
      if (!canInteract()) return;
      state.selectedUnitId = unit.id;
      state.sidebarExpanded = "info";
      clearActionSelection();
      render();
    });
    strip.append(btn);
  });
}

function renderChainPanel() {
  const panel = $("chain-panel");
  const caption = $("chain-caption");
  const skipBtn = $("skip-chain");
  panel.innerHTML = "";
  if (isGameOver()) {
    caption.textContent = "对局已结束,无法再进行连锁。";
    skipBtn.classList.add("hidden");
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    caption.textContent = `${unit?.name || "消失单位"} 正等待重新出现。`;
    skipBtn.classList.add("hidden");
    return;
  }
  if (!isChainMode()) {
    caption.textContent = "当前没有待响应的连锁。";
    skipBtn.classList.add("hidden");
    return;
  }

  const chain = state.battle.pending_chain;
  const sourceUnit = unitById(chain.queued_action.actor_id);
  const currentReactor = unitById(chain.current_unit_id);
  const currentOptions = bundleFor(chain.current_unit_id)?.reactions.actions || [];
  const sourceSummary = chainQueuedActionPrompt(chain);
  caption.textContent = `\u539f\u52a8\u4f5c\uff1a${sourceUnit?.name || "\u672a\u77e5\u5355\u4f4d"} \u7684 ${sourceSummary}`;
  skipBtn.classList.remove("hidden");

  const sourceItem = document.createElement("div");
  sourceItem.className = "queue-item";
  sourceItem.innerHTML = `
    <strong>${sourceUnit?.name || "未知单位"} · ${chain.queued_action.display_name}</strong>
    <p>速度 ${chain.queued_action.speed}。当前等待 ${currentReactor?.name || "响应方"} 选择连锁动作。</p>
  `;
  sourceItem.innerHTML += `<p class="queue-detail">${sourceSummary}</p>`;
  panel.append(sourceItem);

  if (currentOptions.length) {
    const optionsItem = document.createElement("div");
    optionsItem.className = "queue-item current-options";
    optionsItem.innerHTML = `
      <strong>${currentReactor?.name || "\u5f53\u524d\u5355\u4f4d"} \u53ef\u7528\u8fde\u9501</strong>
      <p>${chainOptionSummary(currentOptions)} / \u4e0d\u8fde\u9501</p>
    `;
    panel.append(optionsItem);
  }

  (chain.chosen_reactions || []).slice().reverse().forEach((reaction) => {
    const unit = unitById(reaction.actor_id);
    const item = document.createElement("div");
    item.className = "queue-item";
    item.innerHTML = `
      <strong>${unit?.name || "未知单位"} · ${reaction.display_name}</strong>
      <p>将以更快速度先于原动作结算。</p>
    `;
    panel.append(item);
  });
}

function renderLogs() {
  const logs = $("logs");
  logs.innerHTML = "";
  (state.battle?.logs || []).slice().reverse().forEach((line) => {
    const item = document.createElement("div");
    item.className = "log";
    item.textContent = line;
    logs.append(item);
  });
}

function renderGameOverOverlay() {
  const overlay = $("game-over-overlay");
  const title = $("game-over-title");
  const text = $("game-over-text");
  const rematch = $("game-over-rematch");
  if (!state.battle || !isGameOver() || state.screen !== "battle") {
    overlay.classList.add("hidden");
    return;
  }
  title.textContent = "游戏结束";
  text.textContent = `玩家 ${state.battle.winner} 已获胜。战场上的行动与连锁都已锁定。你可以回到房间大厅,或者直接重新开始选将。`;
  if (rematch) {
    rematch.disabled = !Boolean(state.room?.can_rematch && state.room?.viewer_player_id !== null);
  }
  overlay.classList.remove("hidden");
}

function renderReplayToolbar() {
  const toolbar = $("replay-toolbar");
  if (!toolbar) return;
  const replay = replayMeta();
  const simulation = simulationMeta();
  const visible = hasBattle() && replay.available;
  toolbar.classList.toggle("hidden", !visible);
  if (!visible) return;
  const lastIndex = Number(replay.last_step_index || 0);
  const liveIndex = Number(simulation.live_step_index || 0);
  const currentIndex = isReplayMode()
    ? Math.max(0, Math.min(lastIndex, Number(state.replayStepIndex || 0)))
    : Math.max(0, Math.min(lastIndex, liveIndex));
  const back = $("replay-step-back");
  const pause = $("replay-pause");
  const live = $("replay-live");
  const forward = $("replay-step-forward");
  const speed = $("replay-speed");
  const omniscient = $("replay-omniscient");
  const timeline = $("replay-timeline");
  const status = $("replay-status");
  if (speed) {
    speed.value = String(simulation.speed || 1);
    speed.disabled = !state.room?.viewer_is_host;
  }
  if (omniscient) {
    omniscient.checked = Boolean(state.replayOmniscient);
    omniscient.disabled = !replay.can_use_omniscient;
  }
  if (timeline) {
    timeline.max = String(lastIndex);
    timeline.value = String(currentIndex);
    timeline.disabled = !replay.available;
  }
  if (back) back.disabled = currentIndex <= 0;
  if (forward) forward.disabled = currentIndex >= lastIndex;
  if (live) live.disabled = !isReplayMode();
  if (pause) {
    pause.textContent = simulation.paused ? "ç»§ç»­" : "æš‚åœ";
    pause.disabled = !simulation.can_control;
  }
  if (status) {
    if (isReplayMode()) {
      status.textContent = `å›žæ”¾ ${currentIndex}/${lastIndex}`;
    } else if (simulation.enabled) {
      status.textContent = simulation.paused
        ? `å·²æš‚åœ ${liveIndex}/${lastIndex}`
        : `å®žæ—¶ ${liveIndex}/${lastIndex}`;
    } else {
      status.textContent = `æœ¬å±€ ${currentIndex}/${lastIndex}`;
    }
  }
}

function renderRoomActionButtons() {
  const surrenderBtn = $("surrender-battle");
  if (!surrenderBtn) return;
  const canSurrender = Boolean(
    hasBattle()
      && !isGameOver()
      && !isReplayMode()
      && viewerPlayerId() !== null
      && state.screen === "battle",
  );
  surrenderBtn.classList.toggle("hidden", !canSurrender);
  surrenderBtn.disabled = !canSurrender;
}

function renderMessage() {
  const node = $("message");
  if (!state.battle) {
    node.textContent = hasRoom() ? "房间已建立,但对局还没开始。" : "尚未进入房间。";
    return;
  }
  if (isGameOver()) {
    node.textContent = `玩家 ${state.battle.winner} 已获胜。战场已锁定,可回到房间大厅查看本局房间。`;
    return;
  }
  if (!canInteract()) {
    node.textContent = `当前轮到玩家 ${inputPlayer()} 操作。你可以继续观察战场,等待对手行动完成。`;
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    node.textContent = `${unit?.name || "消失单位"} 需要先重新出现。请点击蓝色高亮的最近落点。`;
    return;
  }
  if (state.selectedActionCode === "mana_pull" && !state.stagedPayload?.targetUnitId) {
    node.textContent = "魔力牵引分两步:先选单位,再选落点。";
    return;
  }
  if (state.stagedPayload?.targetUnitId && state.selectedActionCode === "mana_pull") {
    node.textContent = `已选中 ${stagedTarget()?.name || "被牵引目标"},请点击蓝色高亮落点。`;
    return;
  }
  if (isChainMode()) {
    const current = unitById(state.battle.pending_chain?.current_unit_id || "");
    const source = unitById(state.battle.pending_chain?.queued_action?.actor_id || "");
    const actionName = state.battle.pending_chain?.queued_action?.display_name || "原动作";
    node.textContent = `${current?.name || "当前单位"} 可以对 ${source?.name || "对方单位"} 的【${actionName}】进行连锁,点击其周围动作按钮或放弃连锁。`;
    return;
  }
  const action = selectedAction();
  if (action) {
    node.textContent = `已选择【${actionTitle(action)}】。${actionNeedsTarget(action) ? "请在棋盘上点击蓝色高亮目标。" : "再次点击会立即结算。"} `;
    return;
  }
  node.textContent = `当前由玩家 ${inputPlayer()} 操作。`;
}

function renderTargetCancelButton() {
  const btn = $("cancel-targeting");
  const visible = hasCancelableTargetSelection();
  btn.classList.toggle("hidden", !visible);
  btn.disabled = !visible;
}

function renderTargetCompleteButton() {
  const btn = $("complete-targeting");
  if (!btn) return;
  const action = selectedAction();
  if (action?.code === "backstep_shot" && isChainMode()) {
    const retreatCell = stagedBackstepRetreatCell(action);
    const targetIds = retreatCell ? backstepFollowUpTargetIds(action, retreatCell) : [];
    const canCounter = targetIds.some((id) => unitIsSelectableTarget(unitById(id)));
    btn.textContent = retreatCell ? (canCounter ? "不反击并完成" : "完成撤步") : "完成选择";
  } else {
    btn.textContent = "完成选择";
  }
  const visible = canCompleteTargetSelection();
  btn.classList.toggle("hidden", !visible);
  btn.disabled = !visible;
}

function applyRandomRoomPanelState() {
  const caption = $("lobby-caption");
  const roomMessage = $("room-message");
  const randomRosterControl = $("random-roster-size-control");
  const randomRosterInput = $("random-roster-size-input");
  const randomRosterNote = $("random-roster-size-note");
  if (!hasRoom()) {
    if (randomRosterControl) randomRosterControl.classList.add("hidden");
    return;
  }
  const enabled = isRandomRoomMode();
  if (randomRosterControl) randomRosterControl.classList.toggle("hidden", !enabled);
  if (randomRosterInput) {
    const draftValue = String(state.randomRosterSizeDraft || "");
    const isEditing = typeof document !== "undefined" && document.activeElement === randomRosterInput;
    randomRosterInput.value = enabled && (isEditing || draftValue)
      ? (draftValue || String(randomRoomRosterSize()))
      : String(randomRoomRosterSize());
    randomRosterInput.disabled = !(enabled && state.room.viewer_is_host && state.room.status === "lobby");
  }
  if (randomRosterNote) {
    randomRosterNote.textContent = `开局时双方各随机获得 ${randomRoomRosterSize()} 个不重复武将。`;
  }
  if (!enabled || hasBattle()) return;
  const summary = randomRoomFallbackSummary(state.room);
  if (shouldShowLobbyPanel()) {
    if (caption) {
      caption.textContent = `当前使用随机选人模式。房主设置后，开局时双方${summary}。`;
    }
    if (roomMessage) {
      if (state.room.viewer_player_id === null) {
        roomMessage.textContent = state.room.is_full
          ? "这个房间已经满员。你当前可以观战，但不能代替其中任意一位玩家操作。"
          : `这个房间还有空位。点击“加入房间”后，即可以“${effectiveProfileName()}”作为另一位玩家进入。`;
      } else {
        roomMessage.textContent = state.room.can_start
          ? `双方都已就绪，可以开始随机对局。开局时双方${summary}。`
          : `当前使用随机选人模式。开局时双方${summary}，正在等待另一位玩家加入。`;
      }
    }
    return;
  }
  if (caption) {
    const modeMeta = roomModeMeta();
    caption.textContent = `这个房间当前使用「${modeMeta.name}」。${summary}，点击“加入房间”后会以当前昵称“${effectiveProfileName()}”进入大厅等待开局。`;
  }
}

function render() {
  if (isGameOver()) clearActionSelection();
  document.body.classList.toggle("battle-mode", state.screen === "battle");
  ensureDraftSelection();
  ensureSelectedUnit();
  renderScreens();
  renderNavigation();
  renderProfilePanel();
  renderProfileModal();
  renderRoomPanels();
  applyRandomRoomPanelState();
  renderResumePanel();
  renderRoomListActive();
  renderHeroCards();
  renderHeader();
  renderBoardZoomControls();
  renderMessage();
  renderBattleEffects();
  renderBoard();
  renderBoardOverlays();
  renderHoverCard();
  renderSidebarPanels();
  renderSelectedCard();
  renderUnitStrip();
  renderChainPanel();
  renderLogs();
  renderGameOverOverlay();
  renderReplayToolbar();
  renderRoomActionButtons();
  renderTargetCancelButton();
  renderTargetCompleteButton();
  $("end-turn").disabled = !canInteract() || isChainMode() || isRespawnMode();
  $("skip-chain").disabled = !canInteract() || !isChainMode();
}

async function refreshState({ preserveScreen = true } = {}) {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    syncIdentityFromUrl();
    const roomId = roomQueryId();
    if (!roomId) {
      const payload = await fetchJson("/api/heroes");
      state.heroes = payload.heroes;
      state.rooms = payload.rooms || [];
      state.room = null;
      state.battle = null;
      state.liveBattle = null;
      state.replayMode = false;
      state.replayStepIndex = 0;
      state.replayOmniscient = false;
      state.playerToken = "";
      syncScreen({ preferBattle: false });
      render();
      return;
    }
    const query = new URLSearchParams({ room_id: roomId });
    if (state.playerToken) {
      query.set("player_token", state.playerToken);
    }
    const payload = await fetchJson(`/api/rooms/state?${query.toString()}`);
    applyRoomPayload(payload, { preserveScreen });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen });
      render();
    } else if (!roomQueryId()) {
      $("lobby-caption").textContent = error.error || "加载英雄列表失败。";
    } else {
      render();
      $("message").textContent = error.error || "连接中断，正在保留当前房间身份等待重新同步。";
    }
  } finally {
    refreshInFlight = false;
  }
}

async function createRoom() {
  if (!state.profileReady) {
    openProfileModal();
    render();
    return;
  }
  const playerName = effectiveProfileName();
  state.roomForm.createName = playerName;
  try {
    const payload = await fetchJson("/api/rooms/create", {
      method: "POST",
      body: JSON.stringify({ player_name: playerName }),
    });
    state.playerToken = payload.player_token;
    saveStoredIdentity(payload.room.room_id, payload.player_token, payload.room.viewer_name || playerName);
    syncLocation("draft", payload.room.room_id);
    applyRoomPayload(payload);
    render();
  } catch (error) {
    $("lobby-caption").textContent = error.error || "创建房间失败。";
  }
}

async function joinRoom(roomIdOverride = "") {
  if (!state.profileReady) {
    openProfileModal();
    render();
    return;
  }
  const roomIdSource =
    typeof roomIdOverride === "string" ? roomIdOverride : $("join-room-code").value;
  const roomId = String(roomIdSource || "").trim().toUpperCase();
  const playerName = effectiveProfileName();
  state.playerToken = "";
  state.roomForm.joinRoomCode = roomId;
  state.roomForm.joinName = playerName;
  try {
    const payload = await fetchJson("/api/rooms/join", {
      method: "POST",
      body: JSON.stringify({ room_id: roomId, player_name: playerName }),
    });
    state.playerToken = payload.player_token;
    saveStoredIdentity(payload.room.room_id, payload.player_token, payload.room.viewer_name || playerName);
    syncLocation("draft", payload.room.room_id);
    applyRoomPayload(payload);
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: false });
      render();
    }
    $("lobby-caption").textContent = error.error || "加入房间失败。";
  }
}

function openListedRoom(roomId) {
  state.playerToken = "";
  state.roomForm.joinRoomCode = roomId;
  $("join-room-code").value = roomId;
  syncLocation("draft", roomId);
  refreshState({ preserveScreen: false });
}

function joinListedRoom(roomId) {
  if (!state.profileReady) {
    openProfileModal();
    render();
    return;
  }
  state.playerToken = "";
  state.roomForm.joinRoomCode = roomId;
  $("join-room-code").value = roomId;
  joinRoom(roomId);
}

function resumeStoredSeat(roomId = roomQueryId()) {
  const identity = loadStoredIdentity(roomId);
  if (!identity.token) {
    if (canReclaimSeatByName()) {
      joinRoom(roomId);
      return;
    }
    $("lobby-caption").textContent = "这个房间没有可继续的旧身份,请把昵称改回原来的玩家昵称后再尝试恢复。";
    return;
  }
  state.playerToken = identity.token;
  syncLocation("draft", roomId);
  refreshState({ preserveScreen: false }).then(() => {
    if (!viewerPlayerId()) {
      clearStoredIdentity(roomId);
      state.playerToken = "";
      $("lobby-caption").textContent = "之前保存的房间身份已经失效,请直接使用当前昵称重新加入。";
      render();
    }
  });
}

async function restartRoomDraft() {
  if (!hasRoom() || !state.playerToken) return;
  try {
    const payload = await fetchJson("/api/rooms/rematch", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
      }),
    });
    applyRoomPayload(payload);
    setScreen("draft", { renderAfter: false });
    render();
  } catch (error) {
    const payload = error.state || null;
    if (payload) {
      applyRoomPayload(payload, { preserveScreen: false });
      render();
    }
    $("room-message").textContent = error.error || "重新开始选将失败。";
  }
}

async function deleteRoom() {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  if (!window.confirm(`确定要删除房间 ${state.room.room_id} 吗?删除后双方都需要重新建房。`)) {
    return;
  }
  const deletedRoomId = state.room.room_id;
  try {
    const payload = await fetchJson("/api/rooms/delete", {
      method: "POST",
      body: JSON.stringify({
        room_id: deletedRoomId,
        player_token: state.playerToken,
      }),
    });
    resetRoomSession({ rooms: payload.rooms || [], roomId: deletedRoomId });
    render();
    $("lobby-caption").textContent = `房间 ${deletedRoomId} 已删除。`;
  } catch (error) {
    $("lobby-caption").textContent = error.error || "删除房间失败。";
  }
}

async function leaveRoom() {
  if (!hasRoom()) return;
  const leftRoomId = state.room.room_id;
  const seatLabel = state.room.viewer_player_id ? `玩家 ${state.room.viewer_player_id}` : "当前观战视角";
  if (!window.confirm(`确定要离开房间 ${leftRoomId} 吗?${seatLabel} 将返回大厅。`)) {
    return;
  }
  if (!state.playerToken || state.room.viewer_player_id === null) {
    resetRoomSession({ roomId: leftRoomId });
    await refreshState({ preserveScreen: false });
    $("lobby-caption").textContent = `你已离开房间 ${leftRoomId}。`;
    return;
  }
  try {
    const payload = await fetchJson("/api/rooms/leave", {
      method: "POST",
      body: JSON.stringify({
        room_id: leftRoomId,
        player_token: state.playerToken,
      }),
    });
    resetRoomSession({ rooms: payload.rooms || [], roomId: leftRoomId });
    render();
    $("lobby-caption").textContent = payload.room_deleted
      ? `你已离开房间 ${leftRoomId},该房间因已无玩家而被关闭。`
      : `你已离开房间 ${leftRoomId}。`;
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: false });
      render();
    }
    $("lobby-caption").textContent = error.error || "离开房间失败。";
  }
}

async function surrenderBattle() {
  if (!hasRoom() || !hasBattle() || !state.playerToken || isGameOver()) return;
  if (!window.confirm("确定要投降并立刻结束这局对战吗?")) {
    return;
  }
  try {
    const payload = await fetchJson("/api/rooms/surrender", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("message").textContent = error.error || "投降失败。";
  }
}

async function setRoomMode(modeCode) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  if (!modeCode || modeCode === state.room.mode) return;
  try {
    const payload = await fetchJson("/api/rooms/set-mode", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        mode: modeCode,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    const payload = error.state || null;
    if (payload) {
      applyRoomPayload(payload, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "切换房间模式失败。";
  }
}

async function setRandomRosterSize(rosterSize) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  const normalized = Math.max(1, Number.parseInt(rosterSize, 10) || 1);
  state.randomRosterSizeDraft = String(normalized);
  if (normalized === randomRoomRosterSize()) return;
  try {
    const payload = await fetchJson("/api/rooms/set-random-roster-size", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        random_roster_size: normalized,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    const payload = error.state || null;
    if (payload) {
      applyRoomPayload(payload, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "设置随机模式人数失败。";
  }
}

async function setRoomSeatCount(seatCount) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  const normalized = Math.max(Number(state.room?.seat_count_min || 2), Math.min(Number(state.room?.seat_count_max || 6), Number.parseInt(seatCount, 10) || 2));
  if (normalized === Number(state.room?.seat_count || 2)) return;
  try {
    const payload = await fetchJson("/api/rooms/set-seat-count", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        seat_count: normalized,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "调整席位数失败。";
  }
}

async function setRoomSeatTeam(seatId, teamId) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  try {
    const payload = await fetchJson("/api/rooms/set-seat-team", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        seat_id: Number(seatId),
        team_id: Number(teamId),
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "调整席位队伍失败。";
  }
}

async function setRoomSeatController(seatId, controllerType) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  try {
    const payload = await fetchJson("/api/rooms/set-seat-controller", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        seat_id: Number(seatId),
        controller_type: controllerType,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "调整席位状态失败。";
  }
}

async function setSeatRandomQuota(seatId, quota) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host || !isRandomRoomMode()) return;
  const normalized = Math.max(0, Number.parseInt(quota, 10) || 0);
  try {
    const payload = await fetchJson("/api/rooms/set-seat-random-quota", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        seat_id: Number(seatId),
        quota: normalized,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "调整随机配额失败。";
  }
}

async function selectRoomHero(heroCode, delta = 1, seatId = null) {
  if (!hasRoom() || !state.playerToken) return;
  try {
    const payload = await fetchJson("/api/rooms/select-hero", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        hero_code: heroCode,
        delta,
        seat_id: seatId != null ? Number(seatId) : undefined,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    const payload = error.state || null;
    if (payload) {
      applyRoomPayload(payload, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "选将失败。";
  }
}

async function startRoomBattle() {
  if (!hasRoom() || !state.playerToken) return;
  if (state.room.status === "finished") {
    await restartRoomDraft();
    return;
  }
  try {
    const payload = await fetchJson("/api/rooms/start", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
      }),
    });
    applyRoomPayload(payload);
    setScreen("battle", { renderAfter: false });
    render();
  } catch (error) {
    const payload = error.state || null;
    if (payload) {
      applyRoomPayload(payload, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "开始对局失败。";
  }
}

async function copyInviteLink() {
  if (!state.room?.invite_url) return;
  try {
    await navigator.clipboard.writeText(state.room.invite_url);
    $("lobby-caption").textContent = "邀请链接已复制,发给另一位玩家就能加入同一房间。";
  } catch {
    $("lobby-caption").textContent = `请手动复制这个链接:${state.room.invite_url}`;
  }
}

function leaveReplayMode({ renderAfter = true } = {}) {
  state.replayMode = false;
  state.replayStepIndex = replayMeta().last_step_index || 0;
  state.battle = state.liveBattle;
  syncSelectedUnitAfterStateChange();
  if (renderAfter) render();
}

async function loadReplayStep(stepIndex, { omniscient = state.replayOmniscient } = {}) {
  if (!hasRoom() || !replayMeta().available) return;
  const query = new URLSearchParams({
    room_id: state.room.room_id,
    step_index: String(Math.max(0, Number(stepIndex) || 0)),
  });
  if (state.playerToken) {
    query.set("player_token", state.playerToken);
  }
  if (omniscient) {
    query.set("omniscient", "1");
  }
  try {
    const payload = await fetchJson(`/api/rooms/replay?${query.toString()}`);
    state.replayMode = true;
    state.replayStepIndex = Number(payload.replay?.step_index || 0);
    state.replayOmniscient = Boolean(payload.replay?.omniscient);
    state.battle = payload.battle || null;
    syncSelectedUnitAfterStateChange();
    render();
  } catch (error) {
    $("message").textContent = error.error || "åŠ è½½å›žæ”¾æ­¥æ•°å¤±è´¥ã€‚";
  }
}

async function controlSimulation(action, speed = null) {
  if (!hasRoom() || !state.playerToken) return;
  try {
    const payload = await fetchJson("/api/rooms/simulation-control", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        action,
        ...(speed == null ? {} : { speed }),
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    if (!state.replayMode) {
      state.battle = state.liveBattle;
    }
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("message").textContent = error.error || "æŽ§åˆ¶ AI æ¨¡æ‹Ÿå¤±è´¥ã€‚";
  }
}

async function performAction(payload) {
  try {
    const response = await fetchJson("/api/rooms/action", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room?.room_id,
        player_token: state.playerToken,
        action: payload,
      }),
    });
    applyRoomPayload(response);
    clearActionSelection();
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("message").textContent = error.error || "执行失败。";
  }
}

function onActionClick(action) {
  if (!canInteract()) return;
  state.sidebarExpanded = "command";
  if (isChainMode()) {
    if (action.code === "chain_skip") {
      performAction({ type: "chain_skip" });
      return;
    }
    if (actionNeedsTarget(action)) {
      if (state.selectedActionCode === action.code) {
        clearActionSelection();
      } else {
        state.selectedActionCode = action.code;
        state.selectedActionSnapshot = action;
        state.hoveredActionCode = "";
        state.hoveredBoardCell = null;
        state.stagedPayload = null;
      }
      render();
      return;
    }
    performAction({
      type: "chain_react",
      unit_id: state.selectedUnitId,
      action_code: action.code,
    });
    return;
  }

  if (!actionNeedsTarget(action)) {
    if (action.kind === "skill") {
      performAction({
        type: "skill",
        unit_id: state.selectedUnitId,
        skill_code: action.code,
      });
      return;
    }
    return;
  }

  if (state.selectedActionCode === action.code) {
    clearActionSelection();
  } else {
    state.selectedActionCode = action.code;
    state.selectedActionSnapshot = action;
    state.hoveredActionCode = "";
    state.hoveredBoardCell = null;
    state.stagedPayload = null;
  }
  render();
}

function attackTargetIdAtCell(action, x, y, occupant) {
  const preview = currentPreview();
  if (occupant && preview.targetIds.has(occupant.id) && unitIsSelectableTarget(occupant)) {
    return occupant.id;
  }
  return unitsAtCell(x, y)
    .filter((unit) => preview.targetIds.has(unit.id) && unitIsSelectableTarget(unit))
    .map((unit) => unit.id)[0] || "";
}

function onBoardClick(x, y, occupant) {
  if (!canInteract()) return;
  const preview = currentPreview();
  const action = selectedAction();
  const key = positionKey({ x, y });
  let canUseCell = preview.cellKeys.has(key);
  let canUseUnit = occupant ? preview.targetIds.has(occupant.id) : false;
  const usesStructuredSelection = Boolean(
    action && (
      movePathSelection(action)
      || patternSelection(action)
      || multiUnitSelection(action)
      || statCellSelection(action)
      || bodyDirectionSelection(action)
      || (isChainMode() && action.code === "backstep_shot")
    ),
  );

  if (action && !canUseCell && !canUseUnit && !usesStructuredSelection) {
    const rawCellKeys = positionsToSet(action.preview?.cells || []);
    const rawTargetIds = targetIdsToSet(action.preview?.target_unit_ids || []);
    canUseCell = rawCellKeys.has(key);
    canUseUnit = occupant ? rawTargetIds.has(occupant.id) : false;
  }

  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    if (!prompt || !canUseCell) return;
    performAction({
      type: "respawn_select",
      unit_id: prompt.unit_id,
      x,
      y,
    });
    return;
  }

  if (!state.selectedActionCode) {
    state.selectedUnitId = occupant?.id || "";
    if (occupant) state.sidebarExpanded = "info";
    clearActionSelection();
    render();
    return;
  }

  if (!action) {
    clearActionSelection();
    render();
    return;
  }

  if (action.code === "backstep_shot" && isChainMode()) {
    const retreatCell = stagedBackstepRetreatCell(action);
    if (!retreatCell) {
      if (!canUseCell) return;
      setStagedBackstepRetreatCell({ x, y });
      render();
      return;
    }
    if (sameCell(retreatCell, { x, y })) {
      setStagedBackstepRetreatCell(null);
      render();
      return;
    }
    const followUpTargetIds = backstepFollowUpTargetIds(action, retreatCell)
      .filter((id) => unitIsSelectableTarget(unitById(id)));
    if (!(occupant && followUpTargetIds.includes(occupant.id) && canUseUnit)) return;
    performAction({
      type: "chain_react",
      unit_id: state.selectedUnitId,
      action_code: action.code,
      x: retreatCell.x,
      y: retreatCell.y,
      target_unit_id: occupant.id,
    });
    return;
  }

  if (movePathSelection(action)) {
    const chosenPath = stagedMovePath(action);
    const clickedCell = { x, y };
    const existingIndex = movePathIndexForClickedCell(action, clickedCell, chosenPath);
    if (existingIndex >= 0) {
      setStagedMovePath(chosenPath.slice(0, existingIndex));
      render();
      return;
    }
    const nextAnchor = movePathAnchorForClickedCell(action, clickedCell, chosenPath);
    if (!nextAnchor) return;
    setStagedMovePath([...chosenPath, nextAnchor]);
    render();
    return;
  }

  if (attackChoicePatternSelection(action)) {
    const choiceCode = stagedPatternChoiceCode(action);
    if (!choiceCode) return;
    if (!canUseCell && !canUseUnit) return;
    const targetUnitId = attackTargetIdAtCell(action, x, y, occupant);
    if (!targetUnitId) return;
    performAction({
      type: "attack",
      unit_id: state.selectedUnitId,
      target_unit_id: targetUnitId,
      x,
      y,
      choice_code: choiceCode,
      ...(action.attack_payload || {}),
    });
    return;
  }

  if (patternSelection(action)) {
    const chosenCells = stagedPatternCells(action);
    const existingIndex = chosenCells.findIndex((cell) => sameCell(cell, { x, y }));
    if (existingIndex >= 0) {
      setStagedPatternCells(
        patternSelectionIsOrdered(action)
          ? chosenCells.slice(0, existingIndex)
          : chosenCells.filter((cell) => !sameCell(cell, { x, y })),
      );
      render();
      return;
    }
    if (!canUseCell) return;
    setStagedPatternCells([...chosenCells, { x, y }]);
    render();
    return;
  }

  if (multiUnitSelection(action)) {
    if (!(occupant && canUseUnit)) return;
    const chosenIds = stagedMultiTargetIds(action);
    const maxTargets = Number(multiUnitSelection(action)?.max_targets || chosenIds.length + 1);
    if (chosenIds.includes(occupant.id)) {
      setStagedMultiTargetIds(chosenIds.filter((id) => id !== occupant.id));
    } else {
      if (chosenIds.length >= maxTargets) return;
      setStagedMultiTargetIds([...chosenIds, occupant.id]);
    }
    render();
    return;
  }

  if (statCellSelection(action)) {
    const chosenCells = stagedStatCells(action);
    const existingIndex = chosenCells.findIndex((cell) => sameCell(cell, { x, y }));
    if (existingIndex >= 0) {
      setStagedStatCells(chosenCells.filter((cell) => !sameCell(cell, { x, y })));
      render();
      return;
    }
    if (!canUseCell) return;
    if (chosenCells.length >= statCellRequired(action)) return;
    setStagedStatCells([...chosenCells, { x, y }]);
    render();
    return;
  }

  if (bodyDirectionSelection(action)) {
    const bodyKeys = positionsToSet(action.preview?.cells || []);
    if (!bodyKeys.has(key)) return;
    const chosenCells = stagedBodyCells(action);
    const existingIndex = chosenCells.findIndex((cell) => sameCell(cell, { x, y }));
    if (existingIndex >= 0) {
      setStagedBodyCells(chosenCells.filter((cell) => !sameCell(cell, { x, y })));
    } else {
      setStagedBodyCells([...chosenCells, { x, y }]);
    }
    render();
    return;
  }

  if (isChainMode()) {
    if (!actionNeedsTarget(action)) return;
    if (!canUseCell && !canUseUnit) return;
    const payload = {
      type: "chain_react",
      unit_id: state.selectedUnitId,
      action_code: action.code,
    };
    if (occupant && canUseUnit) {
      payload.target_unit_id = occupant.id;
      payload.x = x;
      payload.y = y;
    } else if (canUseCell) {
      payload.x = x;
      payload.y = y;
    }
    performAction(payload);
    return;
  }

  if (!canUseCell && !canUseUnit) {
    clearActionSelection();
    state.selectedUnitId = occupant?.id || "";
    if (occupant) state.sidebarExpanded = "info";
    render();
    return;
  }

  if (action.code === "move") {
    performAction({
      type: "move",
      unit_id: state.selectedUnitId,
      x,
      y,
    });
    return;
  }

  if (action.kind === "attack") {
    const targetUnitId = attackTargetIdAtCell(action, x, y, occupant);
    if (!targetUnitId) return;
    performAction({
      type: "attack",
      unit_id: state.selectedUnitId,
      target_unit_id: targetUnitId,
      x,
      y,
      ...(action.attack_payload || {}),
    });
    return;
  }

  if (action.code === "mana_pull") {
    if (!state.stagedPayload?.targetUnitId) {
      state.stagedPayload = { targetUnitId: occupant.id };
      render();
      return;
    }
    performAction({
      type: "skill",
      unit_id: state.selectedUnitId,
      skill_code: action.code,
      target_unit_id: state.stagedPayload.targetUnitId,
      dest_x: x,
      dest_y: y,
    });
    return;
  }

  if (action.preview?.requires_target) {
    if (action.target_mode === "cell" || action.kind === "move") {
      performAction({
        type: "skill",
        unit_id: state.selectedUnitId,
        skill_code: action.code,
        x,
        y,
      });
      return;
    }
    performAction({
      type: "skill",
      unit_id: state.selectedUnitId,
      skill_code: action.code,
      target_unit_id: occupant.id,
      x,
      y,
    });
  }
}

function bindEvents() {
  $("profile-name-input").addEventListener("input", (event) => {
    state.profileDraftName = normalizeProfileName(event.target.value);
  });
  $("profile-name-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      confirmProfile();
    }
  });
  $("join-room-code").addEventListener("input", (event) => {
    event.target.value = event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
    state.roomForm.joinRoomCode = event.target.value;
    renderProfilePanel();
  });
  const roomModeSelect = $("room-mode-select");
  if (roomModeSelect) {
    roomModeSelect.addEventListener("change", (event) => {
      setRoomMode(event.target.value);
    });
  }
  const seatCountInput = $("room-seat-count-input");
  if (seatCountInput) {
    seatCountInput.addEventListener("change", (event) => {
      setRoomSeatCount(event.target.value);
    });
    seatCountInput.addEventListener("blur", (event) => {
      setRoomSeatCount(event.target.value);
    });
    seatCountInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        setRoomSeatCount(event.target.value);
      }
    });
  }
  const randomRosterInput = $("random-roster-size-input");
  if (randomRosterInput) {
    randomRosterInput.addEventListener("input", (event) => {
      const normalized = sanitizeRandomRosterSizeInput(event.target.value);
      state.randomRosterSizeDraft = normalized;
      event.target.value = normalized;
    });
    randomRosterInput.addEventListener("change", (event) => {
      setRandomRosterSize(event.target.value);
    });
    randomRosterInput.addEventListener("blur", (event) => {
      setRandomRosterSize(event.target.value);
    });
    randomRosterInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        setRandomRosterSize(event.target.value);
      }
    });
  }
  $("profile-save").addEventListener("click", confirmProfile);
  $("edit-profile").addEventListener("click", () => {
    openProfileModal();
    render();
  });
  $("identity-edit").addEventListener("click", () => {
    openProfileModal();
    render();
  });
  $("create-room").addEventListener("click", createRoom);
  $("join-room").addEventListener("click", () => joinRoom());
  $("resume-room").addEventListener("click", () => resumeStoredSeat());
  $("recover-room").addEventListener("click", () => resumeStoredSeat());
  $("leave-room").addEventListener("click", leaveRoom);
  $("delete-room").addEventListener("click", deleteRoom);
  $("start-room").addEventListener("click", startRoomBattle);
  $("copy-invite").addEventListener("click", copyInviteLink);
  $("copy-invite-top").addEventListener("click", copyInviteLink);
  $("board-zoom-out").addEventListener("click", () => {
    adjustBoardZoom(-0.15);
  });
  $("board-zoom-reset").addEventListener("click", () => {
    resetBoardZoom();
  });
  $("board-zoom-in").addEventListener("click", () => {
    adjustBoardZoom(0.15);
  });
  $("replay-step-back").addEventListener("click", () => {
    if (!replayMeta().available) return;
    loadReplayStep(Math.max(0, (isReplayMode() ? state.replayStepIndex : replayMeta().last_step_index) - 1));
  });
  $("replay-step-forward").addEventListener("click", () => {
    if (!replayMeta().available) return;
    const lastIndex = Number(replayMeta().last_step_index || 0);
    const nextIndex = Math.min(lastIndex, (isReplayMode() ? state.replayStepIndex : lastIndex) + 1);
    loadReplayStep(nextIndex);
  });
  $("replay-live").addEventListener("click", () => {
    leaveReplayMode();
  });
  $("replay-pause").addEventListener("click", () => {
    if (!simulationMeta().can_control) return;
    controlSimulation(simulationMeta().paused ? "resume" : "pause");
  });
  $("replay-speed").addEventListener("change", (event) => {
    if (!state.room?.viewer_is_host) return;
    controlSimulation("set_speed", Number(event.target.value || 1));
  });
  $("replay-timeline").addEventListener("input", (event) => {
    if (!replayMeta().available) return;
    loadReplayStep(Number(event.target.value || 0), { omniscient: state.replayOmniscient });
  });
  $("replay-omniscient").addEventListener("change", (event) => {
    state.replayOmniscient = Boolean(event.target.checked);
    if (isReplayMode()) {
      loadReplayStep(state.replayStepIndex, { omniscient: state.replayOmniscient });
    } else if (state.replayOmniscient) {
      loadReplayStep(replayMeta().last_step_index || 0, { omniscient: true });
    }
  });
  $("board-stage").addEventListener("scroll", () => {
    scheduleBoardOverlayRender();
  });
  window.addEventListener("resize", () => {
    scheduleBoardOverlayRender();
  });
  $("room-battle").addEventListener("click", () => setScreen("battle"));
  $("nav-draft").addEventListener("click", () => setScreen("draft"));
  $("nav-battle").addEventListener("click", () => setScreen("battle"));
  $("game-over-back").addEventListener("click", () => setScreen("draft"));
  $("game-over-rematch").addEventListener("click", restartRoomDraft);
  $("surrender-battle").addEventListener("click", surrenderBattle);
  $("end-turn").addEventListener("click", () => {
    if (!canInteract()) return;
    performAction({ type: "end_turn" });
  });
  $("skip-chain").addEventListener("click", () => {
    if (!canInteract()) return;
    performAction({ type: "chain_skip" });
  });
  document.querySelectorAll(".sidebar-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      toggleSidebarPanel(button.dataset.sidebarPanel || "");
      render();
    });
  });
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const patternChoiceButton = target?.closest("[data-pattern-choice]");
    if (patternChoiceButton) {
      const action = selectedAction();
      if (!action || !choicePatternSelection(action)) return;
      setStagedPatternChoice(patternChoiceButton.dataset.patternChoice || "");
      render();
      return;
    }
    const statButton = target?.closest("[data-stat-choice]");
    if (statButton) {
      const action = selectedAction();
      if (!action || !statCellSelection(action)) return;
      setStagedStatName(statButton.dataset.statChoice || "");
      render();
      return;
    }
    const directionButton = target?.closest("[data-direction-dx][data-direction-dy]");
    if (directionButton) {
      const action = selectedAction();
      if (!action || !bodyDirectionSelection(action)) return;
      setStagedBodyDirection({
        dx: Number(directionButton.dataset.directionDx),
        dy: Number(directionButton.dataset.directionDy),
      });
      render();
    }
  });
  $("complete-targeting").addEventListener("click", () => {
    if (!canCompleteTargetSelection()) return;
    const action = selectedAction();
    if (!action) return;
    if (movePathSelection(action)) {
      const path = stagedMovePath(action);
      const destination = path[path.length - 1];
      if (!destination) return;
      performAction({
        type: "move",
        unit_id: state.selectedUnitId,
        x: destination.x,
        y: destination.y,
        path,
      });
      return;
    }
    if (isChainMode()) {
      if (action.code === "backstep_shot") {
        const retreatCell = stagedBackstepRetreatCell(action);
        if (!retreatCell) return;
        const payload = {
          type: "chain_react",
          unit_id: state.selectedUnitId,
          action_code: action.code,
          x: retreatCell.x,
          y: retreatCell.y,
        };
        const targetUnitId = stagedBackstepTargetId(action);
        if (targetUnitId) payload.target_unit_id = targetUnitId;
        performAction(payload);
        return;
      }
      const payload = {
        type: "chain_react",
        unit_id: state.selectedUnitId,
        action_code: action.code,
      };
      if (patternSelection(action)) {
        payload.cells = stagedPatternCells(action);
        if (choicePatternSelection(action)) payload.choice_code = stagedPatternChoiceCode(action);
      } else if (multiUnitSelection(action)) {
        payload.target_unit_ids = stagedMultiTargetIds(action);
      } else if (statCellSelection(action)) {
        payload.stat_name = stagedStatName(action);
        payload.cells = stagedStatCells(action);
      } else if (bodyDirectionSelection(action)) {
        payload.cells = stagedBodyCells(action);
        payload.direction = stagedBodyDirection(action);
      }
      performAction(payload);
      return;
    }
    const payload = {
      type: "skill",
      unit_id: state.selectedUnitId,
      skill_code: action.code,
    };
    if (patternSelection(action)) {
      payload.cells = stagedPatternCells(action);
      if (choicePatternSelection(action)) payload.choice_code = stagedPatternChoiceCode(action);
    } else if (multiUnitSelection(action)) {
      payload.target_unit_ids = stagedMultiTargetIds(action);
    } else if (statCellSelection(action)) {
      payload.stat_name = stagedStatName(action);
      payload.cells = stagedStatCells(action);
    } else if (bodyDirectionSelection(action)) {
      payload.cells = stagedBodyCells(action);
      payload.direction = stagedBodyDirection(action);
    }
    performAction(payload);
  });
  $("cancel-targeting").addEventListener("click", () => {
    if (!hasCancelableTargetSelection()) return;
    clearActionSelection();
    render();
  });
  $("board").addEventListener("pointermove", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const cell = target?.closest(".cell");
    if (!cell || !$("board").contains(cell)) {
      if (state.hoveredUnitId || state.hoverPointer) {
        state.hoveredUnitId = "";
        state.hoverPointer = null;
        renderHoverCard();
      }
      return;
    }
    const x = Number(cell.dataset.x);
    const y = Number(cell.dataset.y);
    state.hoveredUnitId = visibleUnitAt(x, y)?.id || "";
    state.hoverPointer = { x: event.clientX, y: event.clientY };
    renderHoverCard();
  });
  $("board").addEventListener("pointerleave", () => {
    state.hoveredUnitId = "";
    state.hoverPointer = null;
    renderHoverCard();
  });
  $("board").addEventListener("pointerdown", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const cell = target?.closest(".cell");
    if (!cell || !$("board").contains(cell)) return;
    event.preventDefault();
    const x = Number(cell.dataset.x);
    const y = Number(cell.dataset.y);
    onBoardClick(x, y, activeOccupantAt(x, y));
  });
  window.addEventListener("hashchange", () => {
    syncScreen({ preferBattle: Boolean(state.battle) });
    render();
  });
}

// Override legacy definitions above so the random-room lobby flow uses a single clean state path.
function ensureSelectedUnit() {
  if (!state.battle) {
    state.selectedUnitId = "";
    return;
  }
  const action = selectedAction();
  if (isRespawnMode()) {
    state.selectedUnitId = currentRespawnPrompt()?.unit_id || "";
    return;
  }
  if (isChainMode() && !action) {
    state.selectedUnitId = state.battle.pending_chain?.current_unit_id || "";
    return;
  }
  if (isGameOver()) {
    if (unitById(state.selectedUnitId)) return;
    state.selectedUnitId = allUnits()[0]?.id || "";
    return;
  }
  if (!state.selectedUnitId) {
    const controllable = activeBundles().map((entry) => entry.unit_id);
    state.selectedUnitId = controllable[0] || allUnits()[0]?.id || "";
    return;
  }
  if (unitById(state.selectedUnitId)) {
    return;
  }
  const controllable = activeBundles().map((entry) => entry.unit_id);
  state.selectedUnitId = controllable[0] || allUnits()[0]?.id || "";
}

function renderHeader() {
  const pill = $("turn-pill");
  const topbarSubline = $("topbar-subline");
  const caption = $("board-caption");
  const modeMeta = roomModeMeta();
  if (!hasRoom()) {
    pill.textContent = "\u5c1a\u672a\u8fdb\u5165\u623f\u95f4";
    topbarSubline.textContent = "\u521b\u5efa\u623f\u95f4\u3001\u590d\u5236\u9080\u8bf7\u94fe\u63a5\u3001\u8ba9\u4e24\u4f4d\u73a9\u5bb6\u5206\u522b\u8fdb\u5165\u540c\u4e00\u623f\u95f4\u540e\u5728\u7ebf\u5bf9\u6218\u3002";
    caption.textContent = "\u8bf7\u5148\u521b\u5efa\u623f\u95f4\u6216\u52a0\u5165\u623f\u95f4\u3002";
    return;
  }
  if (!state.battle) {
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 ${state.room.status === "lobby" ? "\u5927\u5385\u4e2d" : "\u7b49\u5f85\u5f00\u5c40"}`;
    if (isRandomRoomMode()) {
      topbarSubline.textContent = state.room.viewer_player_id
        ? `\u4f60\u5f53\u524d\u662f\u73a9\u5bb6 ${state.room.viewer_player_id}\u3002\u5f53\u524d\u623f\u95f4\u4f7f\u7528\u300c${modeMeta.name}\u300d\uff0c\u5f00\u5c40\u540e\u4f1a\u968f\u673a\u5206\u914d\u6b66\u5c06\uff0c\u5e76\u5728\u66f4\u5927\u7684\u6218\u573a\u4e0a\u968f\u673a\u51fa\u751f\u3002`
        : "\u4f60\u5f53\u524d\u8fd8\u6ca1\u6709\u5360\u7528\u5e2d\u4f4d\u3002\u82e5\u623f\u95f4\u4ecd\u6709\u7a7a\u4f4d\uff0c\u8f93\u5165\u6635\u79f0\u540e\u5373\u53ef\u52a0\u5165\u3002";
      caption.textContent = "\u5bf9\u5c40\u5c1a\u672a\u5f00\u59cb\uff0c\u968f\u673a\u9009\u4eba\u6a21\u5f0f\u4e0b\u65e0\u9700\u624b\u52a8\u9009\u5c06\u3002";
      return;
    }
    topbarSubline.textContent = state.room.viewer_player_id
      ? `\u4f60\u5f53\u524d\u662f\u73a9\u5bb6 ${state.room.viewer_player_id}\u3002\u5728\u5927\u5385\u91cc\u4e3a\u81ea\u5df1\u9009\u62e9\u6b66\u5c06\uff0c\u53cc\u65b9\u90fd\u9009\u597d\u540e\u5f00\u59cb\u5bf9\u5c40\u3002`
      : "\u4f60\u5f53\u524d\u8fd8\u6ca1\u6709\u5360\u7528\u5e2d\u4f4d\u3002\u82e5\u623f\u95f4\u4ecd\u6709\u7a7a\u4f4d\uff0c\u8f93\u5165\u6635\u79f0\u540e\u5373\u53ef\u52a0\u5165\u3002";
    caption.textContent = "\u5bf9\u5c40\u5c1a\u672a\u5f00\u59cb\uff0c\u8bf7\u5148\u5728\u623f\u95f4\u5927\u5385\u5b8c\u6210\u9009\u5c06\u3002";
    return;
  }
  topbarSubline.textContent = state.room.viewer_player_id
    ? `\u623f\u95f4 ${state.room.room_id} \u5728\u7ebf\u5bf9\u6218\u4e2d\u3002\u4f60\u5f53\u524d\u662f\u73a9\u5bb6 ${state.room.viewer_player_id}\u3002`
    : `\u623f\u95f4 ${state.room.room_id} \u5728\u7ebf\u5bf9\u6218\u4e2d\u3002\u4f60\u5f53\u524d\u4ee5\u89c2\u6218\u89c6\u89d2\u67e5\u770b\u6b64\u623f\u95f4\u3002`;
  if (isGameOver()) {
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u73a9\u5bb6 ${state.battle.winner} \u83b7\u80dc`;
    caption.textContent = `\u73a9\u5bb6 ${state.battle.winner} \u5df2\u83b7\u80dc\uff0c\u6218\u573a\u5df2\u9501\u5b9a\u3002`;
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u73a9\u5bb6 ${inputPlayer()} \u91cd\u65b0\u51fa\u73b0\u4e2d`;
    caption.textContent = `\u8bf7\u9009\u62e9 ${unit?.name || "\u6d88\u5931\u5355\u4f4d"} \u7684\u91cd\u65b0\u51fa\u73b0\u4f4d\u7f6e\u3002`;
    return;
  }
  if (isChainMode()) {
    const current = state.battle.pending_chain?.current_unit_id
      ? unitById(state.battle.pending_chain.current_unit_id)?.name
      : "\u54cd\u5e94\u65b9";
    const sourceSummary = chainQueuedActionPrompt(state.battle.pending_chain);
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u73a9\u5bb6 ${inputPlayer()} \u8fde\u9501\u4e2d`;
    caption.textContent = `\u7b49\u5f85 ${current} \u54cd\u5e94 ${sourceSummary}`;
    return;
  }
  pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u7b2c ${state.battle.round_number} \u8f6e \u00b7 \u73a9\u5bb6 ${inputPlayer()} \u884c\u52a8`;
  caption.textContent = "\u70b9\u51fb\u5df1\u65b9\u68cb\u5b50\uff0c\u5728\u68cb\u5b50\u5468\u56f4\u9009\u62e9\u52a8\u4f5c\u3002";
}

function renderRoomPanels() {
  const showLobby = shouldShowLobbyPanel();
  const roomId = roomQueryId();
  $("room-home").classList.toggle("hidden", showLobby);
  $("room-lobby").classList.toggle("hidden", !showLobby);
  if ($("create-name")) $("create-name").value = state.roomForm.createName;
  if ($("join-name")) $("join-name").value = state.roomForm.joinName;
  $("join-room-code").value = roomId || state.roomForm.joinRoomCode;

  const title = $("lobby-title");
  const caption = $("lobby-caption");
  const copyInvite = $("copy-invite");
  const roomBattle = $("room-battle");
  const startRoom = $("start-room");
  const leaveRoomBtn = $("leave-room");
  const deleteRoomBtn = $("delete-room");
  const joinRoomButton = $("join-room");
  const modeLabel = $("room-mode-label");
  const modeNote = $("room-mode-note");
  const modeSelect = $("room-mode-select");
  renderRecoveryButton();

  if (!hasRoom()) {
    title.textContent = "\u5728\u7ebf\u623f\u95f4";
    caption.textContent = "\u5148\u786e\u8ba4\u4f60\u8981\u4f7f\u7528\u7684\u6635\u79f0\uff0c\u7136\u540e\u521b\u5efa\u623f\u95f4\u6216\u8f93\u5165\u623f\u95f4\u7801\u52a0\u5165\u3002\u8fdb\u5165\u623f\u95f4\u540e\uff0c\u6bcf\u4f4d\u73a9\u5bb6\u5404\u81ea\u9009\u62e9\u81ea\u5df1\u7684\u6b66\u5c06\uff0c\u518d\u5f00\u59cb\u5bf9\u5c40\u3002";
    if (modeLabel) modeLabel.textContent = fallbackRoomModes()[0].name;
    if (modeNote) modeNote.textContent = "\u8fdb\u5165\u623f\u95f4\u540e\u53ef\u7531\u623f\u4e3b\u9009\u62e9\u6218\u6597\u6a21\u5f0f\u3002";
    if (modeSelect) {
      modeSelect.innerHTML = "";
      availableRoomModes().forEach((mode) => {
        const option = document.createElement("option");
        option.value = mode.code;
        option.textContent = mode.name;
        modeSelect.append(option);
      });
      modeSelect.disabled = true;
    }
    if (randomRosterControl) randomRosterControl.classList.add("hidden");
    leaveRoomBtn.classList.add("hidden");
    deleteRoomBtn.classList.add("hidden");
    copyInvite.classList.add("hidden");
    roomBattle.classList.add("hidden");
    startRoom.classList.add("hidden");
    joinRoomButton.disabled = !state.profileReady || !String($("join-room-code").value || "").trim();
    return;
  }

  const modeMeta = roomModeMeta();
  if (modeLabel) modeLabel.textContent = modeMeta.name;
  if (modeNote) {
    const hostHint = state.room.viewer_is_host && state.room.status === "lobby"
      ? "\u623f\u4e3b\u53ef\u5728\u5f00\u5c40\u524d\u5207\u6362\u6a21\u5f0f\uff0c\u5207\u6362\u540e\u4f1a\u6e05\u7a7a\u5f53\u524d\u9009\u5c06\u3002"
      : "\u4ec5\u623f\u4e3b\u53ef\u5728\u5927\u5385\u91cc\u5207\u6362\u6a21\u5f0f\u3002";
    modeNote.textContent = `${modeMeta.description} ${hostHint}`;
  }
  if (modeSelect) {
    modeSelect.innerHTML = "";
    availableRoomModes().forEach((mode) => {
      const option = document.createElement("option");
      option.value = mode.code;
      option.textContent = mode.name;
      if (mode.code === state.room.mode) option.selected = true;
      modeSelect.append(option);
    });
    modeSelect.disabled = !(state.room.viewer_is_host && state.room.status === "lobby");
  }

  if (!showLobby) {
    title.textContent = `\u52a0\u5165\u623f\u95f4 ${state.room.room_id}`;
    caption.textContent = isRandomRoomMode()
      ? `\u8fd9\u4e2a\u623f\u95f4\u5f53\u524d\u4f7f\u7528\u300c${modeMeta.name}\u300d\u3002\u70b9\u51fb\u201c\u52a0\u5165\u623f\u95f4\u201d\u540e\uff0c\u5c31\u4f1a\u4ee5\u5f53\u524d\u6635\u79f0\u201c${effectiveProfileName()}\u201d\u8fdb\u5165\u623f\u95f4\u5927\u5385\u7b49\u5f85\u5f00\u5c40\u3002`
      : `\u8fd9\u4e2a\u623f\u95f4\u4ecd\u5728\u7b49\u5f85\u73a9\u5bb6\u5360\u4f4d\u3002\u70b9\u51fb\u201c\u52a0\u5165\u623f\u95f4\u201d\u540e\uff0c\u5c31\u4f1a\u4ee5\u5f53\u524d\u6635\u79f0\u201c${effectiveProfileName()}\u201d\u8fdb\u5165\u623f\u95f4\u5927\u5385\u5f00\u59cb\u9009\u5c06\u3002`;
    leaveRoomBtn.classList.remove("hidden");
    leaveRoomBtn.disabled = false;
    deleteRoomBtn.classList.toggle("hidden", !state.room.viewer_is_host);
    deleteRoomBtn.disabled = !state.room.viewer_is_host;
    copyInvite.classList.remove("hidden");
    roomBattle.classList.toggle("hidden", !hasBattle());
    startRoom.classList.add("hidden");
    joinRoomButton.disabled = !state.profileReady || !String($("join-room-code").value || "").trim();
    return;
  }

  title.textContent = `\u623f\u95f4 ${state.room.room_id}`;
  caption.textContent = hasBattle()
    ? "\u5bf9\u5c40\u5df2\u7ecf\u5f00\u59cb\u3002\u4f60\u53ef\u4ee5\u8fd4\u56de\u6218\u573a\u7ee7\u7eed\u6d4b\u8bd5\uff0c\u6216\u7559\u5728\u8fd9\u91cc\u67e5\u770b\u623f\u95f4\u4fe1\u606f\u3002"
    : (isRandomRoomMode()
      ? "\u5f53\u524d\u4f7f\u7528\u968f\u673a\u9009\u4eba\u6a21\u5f0f\uff0c\u53cc\u65b9\u5165\u573a\u540e\u65e0\u9700\u624b\u52a8\u9009\u5c06\uff0c\u5f00\u5c40\u65f6\u4f1a\u81ea\u52a8\u968f\u673a\u5206\u914d\u6b66\u5c06\u3002"
      : "\u53cc\u65b9\u73a9\u5bb6\u5728\u8fd9\u91cc\u5404\u81ea\u9009\u62e9\u81ea\u5df1\u7684\u6b66\u5c06\uff0c\u51c6\u5907\u5b8c\u6210\u540e\u5f00\u59cb\u5bf9\u5c40\u3002");

  $("room-code-label").textContent = state.room.room_id;
  $("room-status-label").textContent = state.room.status === "lobby"
    ? "\u7b49\u5f85\u53cc\u65b9\u5c31\u7eea"
    : (isGameOver() ? "\u5bf9\u5c40\u7ed3\u675f" : "\u5bf9\u5c40\u8fdb\u884c\u4e2d");
  $("viewer-seat-label").textContent = state.room.viewer_player_id ? `\u73a9\u5bb6 ${state.room.viewer_player_id}` : "\u89c2\u6218 / \u672a\u5360\u4f4d";
  $("viewer-seat-note").textContent = state.room.viewer_name
    ? `${state.room.viewer_name}${state.room.viewer_is_host ? " \u00b7 \u623f\u4e3b" : ""}`
    : "\u5f53\u524d\u6d4f\u89c8\u5668\u8fd8\u6ca1\u6709\u5360\u7528\u5e2d\u4f4d";
  $("invite-path-label").textContent = state.room.invite_url || state.room.invite_path;

  leaveRoomBtn.classList.remove("hidden");
  leaveRoomBtn.disabled = false;
  deleteRoomBtn.classList.toggle("hidden", !state.room.viewer_is_host);
  deleteRoomBtn.disabled = !state.room.viewer_is_host;
  copyInvite.classList.toggle("hidden", !state.room.invite_url);
  roomBattle.classList.toggle("hidden", !hasBattle());
  roomBattle.disabled = !hasBattle();
  roomBattle.textContent = "\u8fdb\u5165\u6218\u573a";
  startRoom.classList.toggle("hidden", !(state.room.viewer_player_id !== null && ["lobby", "finished"].includes(state.room.status)));
  startRoom.disabled = state.room.status === "lobby" ? !state.room.can_start : !state.room.can_rematch;
  startRoom.textContent = state.room.status === "finished"
    ? "\u91cd\u65b0\u5f00\u59cb\u9009\u5c06"
    : (isRandomRoomMode() ? "\u5f00\u59cb\u968f\u673a\u5bf9\u5c40" : "\u5f00\u59cb\u5bf9\u5c40");

  const roomMessage = $("room-message");
  if (hasBattle()) {
    if (state.room.viewer_player_id === null && !isGameOver()) {
      roomMessage.textContent = canReclaimSeatByName()
        ? `\u623f\u95f4 ${state.room.room_id} \u7684\u5bf9\u5c40\u6b63\u5728\u8fdb\u884c\u4e2d\u3002\u5f53\u524d\u6635\u79f0\u4e0e\u65e7\u5e2d\u4f4d\u5339\u914d\uff0c\u70b9\u51fb\u201c\u6062\u590d\u5e2d\u4f4d\u201d\u540e\u53ef\u7ee7\u7eed\u64cd\u4f5c\u3002`
        : `\u623f\u95f4 ${state.room.room_id} \u7684\u5bf9\u5c40\u6b63\u5728\u8fdb\u884c\u4e2d\u3002\u4f60\u5f53\u524d\u662f\u89c2\u6218\u8eab\u4efd\uff1b\u5982\u679c\u4f60\u662f\u539f\u73a9\u5bb6\uff0c\u8bf7\u5148\u628a\u6635\u79f0\u6539\u56de\u539f\u6765\u7684\u540d\u5b57\u518d\u6062\u590d\u5e2d\u4f4d\u3002`;
    } else {
      roomMessage.textContent = isGameOver()
        ? `\u623f\u95f4 ${state.room.room_id} \u7684\u672c\u5c40\u5bf9\u6218\u5df2\u7ecf\u7ed3\u675f\u3002\u4f60\u53ef\u4ee5\u8fdb\u5165\u6218\u573a\u67e5\u770b\u7ec8\u5c40\u76d8\u9762\uff0c\u6216\u76f4\u63a5\u91cd\u65b0\u5f00\u59cb\u9009\u5c06\u518d\u6765\u4e00\u5c40\u3002`
        : `\u623f\u95f4 ${state.room.room_id} \u7684\u5bf9\u5c40\u6b63\u5728\u8fdb\u884c\u4e2d\u3002\u70b9\u51fb\u201c\u8fdb\u5165\u6218\u573a\u201d\u5373\u53ef\u67e5\u770b\u5e76\u7ee7\u7eed\u64cd\u4f5c\u3002`;
    }
  } else if (isRandomRoomMode()) {
    if (state.room.viewer_player_id === null) {
      roomMessage.textContent = state.room.is_full
        ? "\u8fd9\u4e2a\u623f\u95f4\u5df2\u7ecf\u6ee1\u5458\u3002\u4f60\u5f53\u524d\u53ef\u4ee5\u89c2\u6218\uff0c\u4f46\u4e0d\u80fd\u4ee3\u66ff\u5176\u4e2d\u4efb\u610f\u4e00\u4f4d\u73a9\u5bb6\u64cd\u4f5c\u3002"
        : `\u8fd9\u4e2a\u623f\u95f4\u8fd8\u6709\u7a7a\u4f4d\u3002\u70b9\u51fb\u201c\u52a0\u5165\u623f\u95f4\u201d\u540e\uff0c\u5373\u53ef\u4ee5\u201c${effectiveProfileName()}\u201d\u4f5c\u4e3a\u53e6\u4e00\u4f4d\u73a9\u5bb6\u8fdb\u5165\u3002`;
    } else if (state.room.status === "finished") {
      roomMessage.textContent = "\u672c\u5c40\u5bf9\u6218\u5df2\u7ed3\u675f\u3002\u53ef\u4ee5\u76f4\u63a5\u91cd\u65b0\u5f00\u59cb\u9009\u5c06\uff0c\u518d\u6765\u4e00\u5c40\u968f\u673a\u5bf9\u5c40\u3002";
    } else if (!state.room.can_start) {
      roomMessage.textContent = "\u5f53\u524d\u4f7f\u7528\u968f\u673a\u9009\u4eba\u6a21\u5f0f\uff0c\u65e0\u9700\u624b\u52a8\u9009\u5c06\uff0c\u6b63\u5728\u7b49\u5f85\u53e6\u4e00\u4f4d\u73a9\u5bb6\u52a0\u5165\u3002";
    } else {
      roomMessage.textContent = "\u53cc\u65b9\u90fd\u5df2\u5c31\u7eea\uff0c\u5f00\u5c40\u540e\u4f1a\u968f\u673a\u5206\u914d\u6b66\u5c06\uff0c\u5e76\u5728\u66f4\u5927\u7684\u6218\u573a\u4e0a\u968f\u673a\u51fa\u751f\u3002";
    }
  } else if (state.room.viewer_player_id === null) {
    roomMessage.textContent = state.room.is_full
      ? "\u8fd9\u4e2a\u623f\u95f4\u5df2\u7ecf\u6ee1\u5458\u3002\u4f60\u5f53\u524d\u53ef\u4ee5\u89c2\u6218\uff0c\u4f46\u4e0d\u80fd\u4ee3\u66ff\u5176\u4e2d\u4efb\u610f\u4e00\u4f4d\u73a9\u5bb6\u64cd\u4f5c\u3002"
      : `\u8fd9\u4e2a\u623f\u95f4\u8fd8\u6709\u7a7a\u4f4d\u3002\u70b9\u51fb\u201c\u52a0\u5165\u623f\u95f4\u201d\u540e\uff0c\u5373\u53ef\u4ee5\u201c${effectiveProfileName()}\u201d\u4f5c\u4e3a\u53e6\u4e00\u4f4d\u73a9\u5bb6\u8fdb\u5165\u3002`;
  } else if (!currentRoomSeat()?.hero_code) {
    roomMessage.textContent = `\u4f60\u5f53\u524d\u662f\u73a9\u5bb6 ${state.room.viewer_player_id}\uff0c\u8bf7\u4ece\u4e0b\u65b9\u9009\u62e9\u81ea\u5df1\u7684\u6b66\u5c06\u3002`;
  } else if (state.room.status === "finished") {
    roomMessage.textContent = "\u672c\u5c40\u5bf9\u6218\u5df2\u7ed3\u675f\u3002\u53ef\u4ee5\u76f4\u63a5\u91cd\u65b0\u5f00\u59cb\u9009\u5c06\uff0c\u4e24\u4f4d\u73a9\u5bb6\u5728\u540c\u4e00\u623f\u95f4\u518d\u6765\u4e00\u5c40\u3002";
  } else if (!state.room.can_start) {
    roomMessage.textContent = "\u4f60\u5df2\u7ecf\u9009\u597d\u4e86\u6b66\u5c06\uff0c\u6b63\u5728\u7b49\u5f85\u53e6\u4e00\u4f4d\u73a9\u5bb6\u52a0\u5165\u6216\u5b8c\u6210\u9009\u5c06\u3002";
  } else {
    roomMessage.textContent = "\u53cc\u65b9\u90fd\u5df2\u5c31\u7eea\uff0c\u53ef\u4ee5\u5f00\u59cb\u8fd9\u573a\u8054\u673a\u6d4b\u8bd5\u5bf9\u5c40\u3002";
  }

  const seatCards = $("seat-cards");
  seatCards.innerHTML = "";
  (state.room.seats || []).forEach((seat) => {
    const heroLabel = isRandomRoomMode()
      ? (seat.hero_name || (seat.occupied ? "\u5f00\u5c40\u540e\u968f\u673a\u5206\u914d" : "\u672a\u9009\u62e9"))
      : (seat.hero_name || "\u672a\u9009\u62e9");
    const card = document.createElement("article");
    card.className = `seat-card ${seat.player_id === state.room.viewer_player_id ? "is-viewer" : ""} ${seat.occupied ? "" : "is-empty"}`;
    card.innerHTML = `
      <div class="seat-head">
        <div>
          <div class="seat-name">\u73a9\u5bb6 ${seat.player_id}</div>
          <div class="seat-note">${seat.name || "\u5c1a\u672a\u52a0\u5165"}</div>
        </div>
        <span class="seat-badge">${seat.is_host ? "\u623f\u4e3b" : "\u5e2d\u4f4d"}</span>
      </div>
      <div class="seat-hero"><strong>\u5f53\u524d\u6b66\u5c06\uff1a</strong>${heroLabel}</div>
      <div class="seat-note">${seat.occupied ? "\u5df2\u8fdb\u5165\u623f\u95f4" : "\u7b49\u5f85\u670b\u53cb\u52a0\u5165\u8be5\u5e2d\u4f4d"}</div>
    `;
    seatCards.append(card);
  });
}

function roomHeroSelectionSummary(heroCode) {
  if (!hasRoom() || !heroCode) return "";
  const pickers = (state.room.seats || [])
    .map((seat) => {
      const count = seatHeroCount(seat, heroCode);
      return count > 0 ? `玩家 ${seat.player_id} × ${count}` : "";
    })
    .filter(Boolean);
  return pickers.length ? pickers.join(" / ") : "";
}

function fallbackRoomModes() {
  return [
    {
      code: "classic",
      name: "标准选将",
      description: "双方各自选择多个武将，按固定出生与交替行动顺序开始对局。",
    },
    {
      code: "random",
      name: "随机选人",
      description: "无需手动选将，开局后随机分配武将，使用更大的战场与随机出生，并按能力值决定先手。",
    },
  ];
}

function boardBasePixels(boardState = state.battle?.board) {
  if (!boardState) return 880;
  const maxDim = Math.max(Number(boardState.width || 0), Number(boardState.height || 0), 1);
  if (maxDim <= 8) return 880;
  if (maxDim <= 10) return Math.max(920, maxDim * 96);
  return Math.max(980, maxDim * 84);
}

function clampBoardZoom(value) {
  return Math.max(0.45, Math.min(1.85, Math.round(Number(value || 1) * 100) / 100));
}

function adjustBoardZoom(delta) {
  state.boardZoom = clampBoardZoom((state.boardZoom || 1) + Number(delta || 0));
  renderBoardZoomControls();
  renderBoard();
  scheduleBoardOverlayRender();
}

function resetBoardZoom() {
  state.boardZoom = 1;
  renderBoardZoomControls();
  renderBoard();
  scheduleBoardOverlayRender();
}

function renderBoardZoomControls() {
  const wrap = $("board-zoom-controls");
  const value = $("board-zoom-value");
  const zoomOut = $("board-zoom-out");
  const zoomReset = $("board-zoom-reset");
  const zoomIn = $("board-zoom-in");
  if (!wrap || !value || !zoomOut || !zoomReset || !zoomIn) return;
  const visible = Boolean(state.battle);
  wrap.classList.toggle("hidden", !visible);
  if (!visible) return;
  const zoom = clampBoardZoom(state.boardZoom);
  state.boardZoom = zoom;
  value.textContent = `${Math.round(zoom * 100)}%`;
  zoomOut.disabled = zoom <= 0.46;
  zoomIn.disabled = zoom >= 1.84;
  zoomReset.disabled = Math.abs(zoom - 1) < 0.01;
}

function renderHeroCards() {
  const homeCards = $("home-hero-cards");
  const lobbyCards = $("hero-cards");
  const viewerSeat = currentRoomSeat();
  const editingSeat = editableRoomSeat();
  const randomMode = isRandomRoomMode();
  const canSelect = Boolean(hasRoom() && state.room?.status === "lobby" && editingSeat && !randomMode);

  homeCards.innerHTML = "";
  lobbyCards.innerHTML = "";

  state.heroes.forEach((hero) => {
    const homeCard = document.createElement("article");
    homeCard.className = "hero-card";
    homeCard.innerHTML = `
      <h3>${hero.name}</h3>
      <div class="meta">${hero.role} / ${hero.attribute} / ${hero.race} / 等级 ${hero.level}</div>
      <div class="meta">攻 ${hero.stats.attack} · 守 ${hero.stats.defense} · 速 ${hero.stats.speed} · 范 ${hero.stats.attack_range} · 魔 ${hero.stats.mana}</div>
      <div class="text"><strong>技能:</strong>${hero.raw_skill_text}</div>
      <div class="text"><strong>特性:</strong>${hero.raw_trait_text}</div>
    `;
    homeCards.append(homeCard);

    const selectedCount = seatHeroCount(editingSeat, hero.code);
    const lobbyCard = document.createElement("article");
    lobbyCard.className = `hero-card ${selectedCount > 0 ? "is-selected" : ""}`;
    const selectedBy = roomHeroSelectionSummary(hero.code);
    const selectionText = selectedBy || (randomMode ? randomRoomFallbackSummary(state.room) : "尚无人选择");
    lobbyCard.innerHTML = `
      <h3>${hero.name}</h3>
      <div class="meta">${hero.role} / ${hero.attribute} / ${hero.race} / 等级 ${hero.level}</div>
      <div class="meta">攻 ${hero.stats.attack} · 守 ${hero.stats.defense} · 速 ${hero.stats.speed} · 范 ${hero.stats.attack_range} · 魔 ${hero.stats.mana}</div>
      <div class="text"><strong>技能:</strong>${hero.raw_skill_text}</div>
      <div class="text"><strong>特性:</strong>${hero.raw_trait_text}</div>
      <div class="text"><strong>当前选择:</strong>${selectionText}</div>
    `;
    if (randomMode) {
      const pickBtn = document.createElement("button");
      pickBtn.className = "ghost";
      pickBtn.textContent = "本模式随机分配";
      pickBtn.disabled = true;
      lobbyCard.append(pickBtn);
      lobbyCards.append(lobbyCard);
      return;
    }
    const counter = document.createElement("div");
    counter.className = "hero-card-counter";
    counter.innerHTML = `
      <button type="button" class="ghost hero-count-btn" data-hero-delta="-1">-1</button>
      <span class="hero-count-value">已选 ${selectedCount}</span>
      <button type="button" class="primary hero-count-btn" data-hero-delta="1">+1</button>
    `;
    const [minusBtn, plusBtn] = counter.querySelectorAll("button");
    minusBtn.disabled = !canSelect || selectedCount <= 0;
    plusBtn.disabled = !canSelect;
    minusBtn.addEventListener("click", () => selectRoomHero(hero.code, -1, editingSeat?.player_id));
    plusBtn.addEventListener("click", () => selectRoomHero(hero.code, 1, editingSeat?.player_id));
    lobbyCard.append(counter);
    lobbyCards.append(lobbyCard);
  });
}

function renderHeader() {
  const pill = $("turn-pill");
  const topbarSubline = $("topbar-subline");
  const caption = $("board-caption");
  const modeMeta = roomModeMeta();
  if (!hasRoom()) {
    pill.textContent = "尚未进入房间";
    topbarSubline.textContent = "创建房间、复制邀请链接，让两位玩家分别进入同一房间后在线对战。";
    caption.textContent = "请先创建房间或加入房间。";
    return;
  }
  if (!state.battle) {
    pill.textContent = `房间 ${state.room.room_id} · ${state.room.status === "lobby" ? "大厅中" : "等待开局"}`;
    if (isRandomRoomMode()) {
      topbarSubline.textContent = state.room.viewer_player_id
        ? `你当前是玩家 ${state.room.viewer_player_id}。当前房间使用「${modeMeta.name}」，开局后会随机分配武将，并在更大的战场上随机出生。`
        : "你当前还没有占用席位。若房间仍有空位，输入昵称后即可加入。";
      caption.textContent = "对局尚未开始，随机选人模式下无需手动选将。";
      return;
    }
    topbarSubline.textContent = state.room.viewer_player_id
      ? `你当前是玩家 ${state.room.viewer_player_id}。在大厅里用 +1 / -1 配置自己的多武将阵容，双方都准备好后开始对局。`
      : "你当前还没有占用席位。若房间仍有空位，输入昵称后即可加入。";
    caption.textContent = "对局尚未开始，请先在房间大厅完成选将。";
    return;
  }
  const viewerSummary = state.room.viewer_player_id
    ? `房间 ${state.room.room_id} 在线对战中。你当前是玩家 ${state.room.viewer_player_id}。`
    : `房间 ${state.room.room_id} 在线对战中。你当前以观战视角查看此房间。`;
  const nextTurnName = state.battle.next_turn_unit_name || "";
  const nextTurnPlayerId = state.battle.next_turn_player_id;
  const nextTurnSummary = nextTurnName && nextTurnPlayerId
    ? `下回合：玩家 ${nextTurnPlayerId} 的 ${nextTurnName}。`
    : "下回合待定。";
  topbarSubline.textContent = `${viewerSummary} ${nextTurnSummary}`;
  if (isGameOver()) {
    pill.textContent = `房间 ${state.room.room_id} · 玩家 ${state.battle.winner} 获胜`;
    caption.textContent = `玩家 ${state.battle.winner} 已获胜，战场已锁定。`;
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    pill.textContent = `房间 ${state.room.room_id} · 玩家 ${inputPlayer()} 重新出现中`;
    caption.textContent = `请为 ${unit?.name || "消失单位"} 选择重新出现的位置。`;
    return;
  }
  if (isChainMode()) {
    const current = state.battle.pending_chain?.current_unit_id
      ? unitById(state.battle.pending_chain.current_unit_id)?.name
      : "响应方";
    const sourceSummary = chainQueuedActionPrompt(state.battle.pending_chain);
    pill.textContent = `房间 ${state.room.room_id} · 玩家 ${inputPlayer()} 连锁中`;
    caption.textContent = `等待 ${current} 响应 ${sourceSummary}`;
    return;
  }
  const activeName = state.battle.active_turn_unit_name || "当前武将";
  pill.textContent = `房间 ${state.room.room_id} · 第 ${state.battle.round_number} 轮 · ${activeName}`;
  caption.textContent = `当前由玩家 ${inputPlayer()} 的 ${activeName} 行动。${nextTurnSummary}`;
}

function renderRoomPanels() {
  const showLobby = shouldShowLobbyPanel();
  const roomId = roomQueryId();
  $("room-home").classList.toggle("hidden", showLobby);
  $("room-lobby").classList.toggle("hidden", !showLobby);
  if ($("create-name")) $("create-name").value = state.roomForm.createName;
  if ($("join-name")) $("join-name").value = state.roomForm.joinName;
  $("join-room-code").value = roomId || state.roomForm.joinRoomCode;

  const title = $("lobby-title");
  const caption = $("lobby-caption");
  const copyInvite = $("copy-invite");
  const roomBattle = $("room-battle");
  const startRoom = $("start-room");
  const leaveRoomBtn = $("leave-room");
  const deleteRoomBtn = $("delete-room");
  const joinRoomButton = $("join-room");
  const modeLabel = $("room-mode-label");
  const modeNote = $("room-mode-note");
  const modeSelect = $("room-mode-select");
  const seatCountInput = $("room-seat-count-input");
  const seatCountNote = $("room-seat-count-note");
  const randomRosterControl = $("random-roster-size-control");
  const randomRosterInput = $("random-roster-size-input");
  const randomRosterNote = $("random-roster-size-note");
  const viewerSeat = currentRoomSeat();
  const editingSeat = editableRoomSeat();
  renderRecoveryButton();

  if (!hasRoom()) {
    title.textContent = "在线房间";
    caption.textContent = "先确认你要使用的昵称，然后创建房间或输入房间码加入。进入房间后，每位玩家都可以为自己选择多个武将。";
    if (modeLabel) modeLabel.textContent = fallbackRoomModes()[0].name;
    if (modeNote) modeNote.textContent = "进入房间后可由房主选择战斗模式。";
    if (modeSelect) {
      modeSelect.innerHTML = "";
      availableRoomModes().forEach((mode) => {
        const option = document.createElement("option");
        option.value = mode.code;
        option.textContent = mode.name;
        modeSelect.append(option);
      });
      modeSelect.disabled = true;
    }
    if (seatCountInput) {
      seatCountInput.value = "2";
      seatCountInput.disabled = true;
    }
    if (seatCountNote) {
      seatCountNote.textContent = "进入房间后可由房主调整到 2~6 席。";
    }
    if (randomRosterControl) randomRosterControl.classList.add("hidden");
    leaveRoomBtn.classList.add("hidden");
    deleteRoomBtn.classList.add("hidden");
    copyInvite.classList.add("hidden");
    roomBattle.classList.add("hidden");
    startRoom.classList.add("hidden");
    joinRoomButton.disabled = !state.profileReady || !String($("join-room-code").value || "").trim();
    return;
  }

  const modeMeta = roomModeMeta();
  if (modeLabel) modeLabel.textContent = modeMeta.name;
  if (modeNote) {
    const hostHint = state.room.viewer_is_host && state.room.status === "lobby"
      ? "房主可在开局前切换模式，切换后会清空当前选将。"
      : "仅房主可在大厅里切换模式。";
    modeNote.textContent = `${modeMeta.description} ${hostHint}`;
  }
    if (modeSelect) {
      modeSelect.innerHTML = "";
      availableRoomModes().forEach((mode) => {
        const option = document.createElement("option");
        option.value = mode.code;
      option.textContent = mode.name;
      if (mode.code === state.room.mode) option.selected = true;
      modeSelect.append(option);
      });
      modeSelect.disabled = !(state.room.viewer_is_host && state.room.status === "lobby");
    }
    if (seatCountInput) {
      seatCountInput.value = String(state.room.seat_count || 2);
      seatCountInput.min = String(state.room.seat_count_min || 2);
      seatCountInput.max = String(state.room.seat_count_max || 6);
      seatCountInput.disabled = !(state.room.viewer_is_host && state.room.status === "lobby");
    }
    if (seatCountNote) {
      seatCountNote.textContent = `当前房间 ${state.room.seat_count}/${state.room.seat_count_max} 席。只有房主可在大厅里调整席位数。`;
    }
    if (randomRosterControl) {
      randomRosterControl.classList.toggle("hidden", !isRandomRoomMode());
    }
    if (randomRosterInput) {
      const draftValue = String(state.randomRosterSizeDraft || "").trim();
      randomRosterInput.value = isRandomRoomMode()
        ? (draftValue || String(randomRoomRosterSize()))
        : String(randomRoomRosterSize());
      randomRosterInput.disabled = !(state.room.viewer_is_host && state.room.status === "lobby" && isRandomRoomMode());
    }
    if (randomRosterNote) {
      randomRosterNote.textContent = `开局时每队各随机获得 ${randomRoomRosterSize()} 个不重复武将。`;
    }

  if (!showLobby) {
    title.textContent = `加入房间 ${state.room.room_id}`;
    caption.textContent = isRandomRoomMode()
      ? `这个房间当前使用「${modeMeta.name}」。点击“加入房间”后，会以当前昵称“${effectiveProfileName()}”进入房间大厅等待开局。`
      : `这个房间仍在等待玩家占位。点击“加入房间”后，会以当前昵称“${effectiveProfileName()}”进入房间大厅开始配置阵容。`;
    leaveRoomBtn.classList.remove("hidden");
    leaveRoomBtn.disabled = false;
    deleteRoomBtn.classList.toggle("hidden", !state.room.viewer_is_host);
    deleteRoomBtn.disabled = !state.room.viewer_is_host;
    copyInvite.classList.remove("hidden");
    roomBattle.classList.toggle("hidden", !hasBattle());
    startRoom.classList.add("hidden");
    joinRoomButton.disabled = !state.profileReady || !String($("join-room-code").value || "").trim();
    return;
  }

  title.textContent = `房间 ${state.room.room_id}`;
  caption.textContent = hasBattle()
    ? "对局已经开始。你可以返回战场继续测试，或留在这里查看房间信息。"
    : (isRandomRoomMode()
      ? "当前使用随机选人模式，双方入场后无需手动选将，开局时会自动随机分配武将。"
      : "双方玩家在这里各自配置自己的多武将阵容，准备完成后开始对局。");

  $("room-code-label").textContent = state.room.room_id;
  $("room-status-label").textContent = state.room.status === "lobby"
    ? "等待双方就绪"
    : (isGameOver() ? "对局结束" : "对局进行中");
  $("viewer-seat-label").textContent = state.room.viewer_player_id
    ? `席位 ${state.room.viewer_player_id}`
    : "观战 / 未占位";
  $("viewer-seat-note").textContent = state.room.viewer_name
    ? `${state.room.viewer_name} · ${(viewerSeat?.team_name || "未分队")}${state.room.viewer_is_host ? " · 房主" : ""}`
    : "当前浏览器还没有占用席位";
  $("invite-path-label").textContent = state.room.invite_url || state.room.invite_path;

  leaveRoomBtn.classList.remove("hidden");
  leaveRoomBtn.disabled = false;
  deleteRoomBtn.classList.toggle("hidden", !state.room.viewer_is_host);
  deleteRoomBtn.disabled = !state.room.viewer_is_host;
  copyInvite.classList.toggle("hidden", !state.room.invite_url);
  roomBattle.classList.toggle("hidden", !hasBattle());
  roomBattle.disabled = !hasBattle();
  roomBattle.textContent = "进入战场";
  startRoom.classList.toggle("hidden", !(state.room.viewer_player_id !== null && ["lobby", "finished"].includes(state.room.status)));
  startRoom.disabled = state.room.status === "lobby" ? !state.room.can_start : !state.room.can_rematch;
  startRoom.textContent = state.room.status === "finished"
    ? "重新开始选将"
    : (isRandomRoomMode() ? "开始随机对局" : "开始对局");

  const roomMessage = $("room-message");
  if (hasBattle()) {
    if (state.room.viewer_player_id === null && !isGameOver()) {
      roomMessage.textContent = canReclaimSeatByName()
        ? `房间 ${state.room.room_id} 的对局正在进行中。当前昵称与旧席位匹配，点击“恢复席位”后可继续操作。`
        : `房间 ${state.room.room_id} 的对局正在进行中。你当前是观战身份；如果你是原玩家，请先把昵称改回原来的名字再恢复席位。`;
    } else {
      roomMessage.textContent = isGameOver()
        ? `房间 ${state.room.room_id} 的本局对战已经结束。你可以进入战场查看终局盘面，或直接重新开始选将再来一局。`
        : `房间 ${state.room.room_id} 的对局正在进行中。点击“进入战场”即可查看并继续操作。`;
    }
  } else if (state.room.viewer_player_id === null) {
    roomMessage.textContent = state.room.is_full
      ? "这个房间已经满员。你当前可以观战，但不能代替其中任意一位玩家操作。"
      : `这个房间还有空位。点击“加入房间”后，即可以“${effectiveProfileName()}”作为另一位玩家进入。`;
  } else if (isRandomRoomMode()) {
    roomMessage.textContent = state.room.can_start
      ? "房间已满足开局条件，可以开始随机对局。"
      : (state.room.start_blocker || "当前使用随机选人模式，请继续完成席位与配额配置。");
  } else if (seatHeroTotalCount(editingSeat) <= 0) {
    roomMessage.textContent = editingSeat && editingSeat.player_id !== viewerPlayerId()
      ? `你当前正在为 ${seatIdentityLabel(editingSeat)} 配置阵容。`
      : `你当前是 ${seatIdentityLabel(viewerSeat)}，请用下方卡片的 +1 / -1 配置自己的阵容。`;
  } else if (!state.room.can_start) {
    roomMessage.textContent = state.room.start_blocker || "房间还没有满足开局条件。";
  } else {
    roomMessage.textContent = "房间已满足开局条件，可以开始这场联机测试对局。";
  }

  const seatCards = $("seat-cards");
  seatCards.innerHTML = "";
  (state.room.seats || []).forEach((seat) => {
    const card = document.createElement("article");
    const heroLabel = seatHeroSummary(seat, { randomFallback: isRandomRoomMode(), randomRoom: state.room });
    const isEditingSeat = editingSeat?.player_id === seat.player_id;
    card.className = `seat-card ${seat.player_id === state.room.viewer_player_id ? "is-viewer" : ""} ${seat.occupied ? "" : "is-empty"} ${isEditingSeat ? "is-editing" : ""}`;
    const controls = [];
    if (state.room.viewer_is_host && state.room.status === "lobby") {
      controls.push(`
        <label class="seat-control-row">
          <span>队伍</span>
          <select data-seat-team="${seat.player_id}">
            <option value="1" ${Number(seat.team_id) === 1 ? "selected" : ""}>红队</option>
            <option value="2" ${Number(seat.team_id) === 2 ? "selected" : ""}>蓝队</option>
          </select>
        </label>
      `);
      const controllerOptions = seat.is_human
        ? `<option value="human" selected>真人</option>`
        : `
          <option value="open" ${seat.controller_type === "open" ? "selected" : ""}>开放</option>
          <option value="ai" ${seat.controller_type === "ai" ? "selected" : ""}>AI</option>
        `;
      controls.push(`
        <label class="seat-control-row">
          <span>状态</span>
          <select data-seat-controller="${seat.player_id}" ${seat.is_human ? "disabled" : ""}>
            ${controllerOptions}
          </select>
        </label>
      `);
      if (isRandomRoomMode()) {
        controls.push(`
          <label class="seat-control-row">
            <span>随机配额</span>
            <input data-seat-quota="${seat.player_id}" type="number" min="0" step="1" value="${Number(seat.random_quota || 0)}" />
          </label>
        `);
      }
      if (!isRandomRoomMode() && seat.is_ai) {
        controls.push(`
          <div class="seat-selection-actions">
            <button type="button" class="${isEditingSeat ? "primary" : "ghost"}" data-edit-seat="${seat.player_id}">
              ${isEditingSeat ? "正在配置此 AI" : "配置这个 AI 席位"}
            </button>
          </div>
        `);
      }
    }
    card.innerHTML = `
      <div class="seat-head">
        <div>
          <div class="seat-name">席位 ${seat.player_id}</div>
          <div class="seat-note">${seat.team_name} · ${controllerTypeLabel(seat)}</div>
        </div>
        <span class="seat-badge">${seat.is_host ? "房主" : "席位"}</span>
      </div>
      <div class="seat-note">${seat.name || "尚未加入"}</div>
      <div class="seat-hero"><strong>当前阵容:</strong>${heroLabel}</div>
      <div class="seat-note">${seat.occupied ? `已配置 ${seatHeroTotalCount(seat)} 个武将` : "等待朋友加入或由房主改成 AI"}</div>
      ${controls.length ? `<div class="seat-controls">${controls.join("")}</div>` : ""}
    `;
    const teamSelect = card.querySelector(`[data-seat-team="${seat.player_id}"]`);
    if (teamSelect) {
      teamSelect.addEventListener("change", (event) => {
        setRoomSeatTeam(seat.player_id, event.target.value);
      });
    }
    const controllerSelect = card.querySelector(`[data-seat-controller="${seat.player_id}"]`);
    if (controllerSelect) {
      controllerSelect.addEventListener("change", (event) => {
        setRoomSeatController(seat.player_id, event.target.value);
      });
    }
    const quotaInput = card.querySelector(`[data-seat-quota="${seat.player_id}"]`);
    if (quotaInput) {
      const commitQuota = () => setSeatRandomQuota(seat.player_id, quotaInput.value);
      quotaInput.addEventListener("change", commitQuota);
      quotaInput.addEventListener("blur", commitQuota);
      quotaInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") commitQuota();
      });
    }
    const editButton = card.querySelector(`[data-edit-seat="${seat.player_id}"]`);
    if (editButton) {
      editButton.addEventListener("click", () => {
        setRoomEditSeat(seat.player_id);
        render();
      });
    }
    seatCards.append(card);
  });

  const heroHead = document.querySelector(".room-hero-head p");
  if (heroHead) {
    if (isRandomRoomMode()) {
      heroHead.textContent = "随机模式下不手动选将；房主需要先把每个席位在本队的随机配额配好。";
    } else if (editingSeat && editingSeat.player_id !== viewerPlayerId()) {
      heroHead.textContent = `当前正在为 ${seatIdentityLabel(editingSeat)} 配置阵容。`;
    } else {
      heroHead.textContent = "每位玩家默认只为自己选将；房主也可以切到某个 AI 席位来配置它的阵容。";
    }
  }
}

function renderRoomList() {
  const list = $("room-list");
  if (!list) return;
  list.innerHTML = "";
  if (!roomSummaries().length) {
    const empty = document.createElement("div");
    empty.className = "room-list-empty";
    empty.textContent = "当前还没有公开房间。你可以先创建一间，或者稍后等朋友建好房间后直接在这里加入。";
    list.append(empty);
    return;
  }

  roomSummaries().forEach((room) => {
    const remembered = loadStoredIdentity(room.room_id);
    const seatSummary = (room.seats || [])
      .map((seat) => `玩家 ${seat.player_id}：${seat.name || "空位"}${seat.occupied ? ` · ${seatHeroSummary(seat, { randomFallback: room.mode === "random", randomRoom: room })}` : ""}`)
      .join(" / ");

    const card = document.createElement("article");
    card.className = "room-list-card";
    card.innerHTML = `
      <div class="room-list-head">
        <strong>${room.room_id}</strong>
        <span class="room-list-state ${room.status === "battle" ? "is-battle" : ""} ${room.is_full ? "is-full" : ""}">${roomStateLabel(room)}</span>
      </div>
      <div class="room-list-meta">席位 ${room.occupied_seat_count}/${room.seat_count} · ${room.mode_name || roomModeMeta(room.mode).name} · ${room.status === "lobby" ? "等待玩家就绪" : "正在进行或已结束"}</div>
      <div class="room-list-seats">${seatSummary}</div>
      <div class="room-list-note">${remembered.token ? `这个浏览器之前进入过该房间。你可以继续原来的席位，也可以直接用当前昵称“${effectiveProfileName()}”作为新玩家加入。` : `现在可以直接用当前昵称“${effectiveProfileName()}”加入。`}</div>
    `;

    const actions = document.createElement("div");
    actions.className = "room-list-actions";

    const primary = document.createElement("button");
    primary.className = room.can_join ? "primary" : "ghost";
    primary.textContent = room.can_join ? "加入房间" : "查看房间";
    primary.addEventListener("click", () => {
      if (room.can_join) {
        $("join-room-code").value = room.room_id;
        $("lobby-caption").textContent = `已填入房间 ${room.room_id}。点击“加入房间”后，就会以“${effectiveProfileName()}”加入。`;
        renderProfilePanel();
        return;
      }
      syncLocation("draft", room.room_id);
      refreshState({ preserveScreen: false });
    });
    actions.append(primary);

    if (remembered.token || canReclaimSeatByName()) {
      const resume = document.createElement("button");
      resume.className = "ghost";
      resume.textContent = remembered.token ? "继续原席位" : "恢复席位";
      resume.addEventListener("click", () => {
        syncLocation("draft", room.room_id);
        state.roomForm.joinRoomCode = room.room_id;
        resumeStoredSeat(room.room_id);
      });
      actions.append(resume);
    } else if (room.can_join) {
      const fillBtn = document.createElement("button");
      fillBtn.className = "ghost";
      fillBtn.textContent = "填入房间码";
      fillBtn.addEventListener("click", () => {
        $("join-room-code").value = room.room_id;
        $("lobby-caption").textContent = `已填入房间 ${room.room_id}。点击“加入房间”后，就会以“${effectiveProfileName()}”加入。`;
        renderProfilePanel();
      });
      actions.append(fillBtn);
    }

    card.append(actions);
    list.append(card);
  });
}

async function selectRoomHero(heroCode, delta = 1, seatId = null) {
  if (!hasRoom() || !state.playerToken) return;
  try {
    const payload = await fetchJson("/api/rooms/select-hero", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        hero_code: heroCode,
        delta,
        seat_id: seatId != null ? Number(seatId) : undefined,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "选将失败。";
  }
}

function ensureSelectedUnit() {
  const action = selectedAction();
  if (!state.battle) {
    state.selectedUnitId = "";
    return;
  }
  if (isRespawnMode()) {
    state.selectedUnitId = currentRespawnPrompt()?.unit_id || "";
    return;
  }
  if (isChainMode() && !action) {
    state.selectedUnitId = state.battle.pending_chain?.current_unit_id || "";
    return;
  }
  if (isGameOver()) {
    if (unitById(state.selectedUnitId)) return;
    state.selectedUnitId = allUnits()[0]?.id || "";
    return;
  }
  if (!state.selectedUnitId) {
    const controllable = activeBundles().map((entry) => entry.unit_id);
    state.selectedUnitId = controllable[0] || allUnits()[0]?.id || "";
    return;
  }
  const controllable = activeBundles().map((entry) => entry.unit_id);
  if (unitById(state.selectedUnitId) && (!controllable.length || controllable.includes(state.selectedUnitId))) {
    return;
  }
  state.selectedUnitId = controllable[0] || allUnits()[0]?.id || "";
}

function renderMessage() {
  const node = $("message");
  if (!state.battle) {
    node.textContent = hasRoom() ? "æˆ¿é—´å·²å»ºç«‹,ä½†å¯¹å±€è¿˜æ²¡å¼€å§‹ã€‚" : "å°šæœªè¿›å…¥æˆ¿é—´ã€‚";
    return;
  }
  if (isReplayMode()) {
    node.textContent = `å½“å‰æ­£åœ¨æŸ¥çœ‹å›žæ”¾ç¬¬ ${state.replayStepIndex}/${replayMeta().last_step_index} æ­¥ã€‚`;
    return;
  }
  if (isGameOver()) {
    node.textContent = `çŽ©å®¶ ${state.battle.winner} å·²èŽ·èƒœã€‚æˆ˜åœºå·²é”å®š,å¯å›žåˆ°æˆ¿é—´å¤§åŽ…æŸ¥çœ‹æœ¬å±€æˆ¿é—´ã€‚`;
    return;
  }
  if (!canInteract()) {
    node.textContent = `å½“å‰è½®åˆ°çŽ©å®¶ ${inputPlayer()} æ“ä½œã€‚ä½ å¯ä»¥ç»§ç»­è§‚å¯Ÿæˆ˜åœº,ç­‰å¾…å¯¹æ‰‹è¡ŒåŠ¨å®Œæˆã€‚`;
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    node.textContent = `${unit?.name || "æ¶ˆå¤±å•ä½"} éœ€è¦å…ˆé‡æ–°å‡ºçŽ°ã€‚è¯·ç‚¹å‡»è“è‰²é«˜äº®çš„æœ€è¿‘è½ç‚¹ã€‚`;
    return;
  }
  if (state.selectedActionCode === "mana_pull" && !state.stagedPayload?.targetUnitId) {
    node.textContent = "é­”åŠ›ç‰µå¼•åˆ†ä¸¤æ­¥:å…ˆé€‰å•ä½,å†é€‰è½ç‚¹ã€‚";
    return;
  }
  if (state.stagedPayload?.targetUnitId && state.selectedActionCode === "mana_pull") {
    node.textContent = `å·²é€‰ä¸­ ${stagedTarget()?.name || "è¢«ç‰µå¼•ç›®æ ‡"},è¯·ç‚¹å‡»è“è‰²é«˜äº®è½ç‚¹ã€‚`;
    return;
  }
  if (isChainMode()) {
    const current = unitById(state.battle.pending_chain?.current_unit_id || "");
    const source = unitById(state.battle.pending_chain?.queued_action?.actor_id || "");
    const actionName = state.battle.pending_chain?.queued_action?.display_name || "åŽŸåŠ¨ä½œ";
    node.textContent = `${current?.name || "å½“å‰å•ä½"} å¯ä»¥å¯¹ ${source?.name || "å¯¹æ–¹å•ä½"} çš„ã€${actionName}ã€‘è¿›è¡Œè¿žé”,ç‚¹å‡»å…¶å‘¨å›´åŠ¨ä½œæŒ‰é’®æˆ–æ”¾å¼ƒè¿žé”ã€‚`;
    return;
  }
  const action = selectedAction();
  if (action) {
    node.textContent = `å·²é€‰æ‹©ã€${actionTitle(action)}ã€‘ã€‚${actionNeedsTarget(action) ? "è¯·åœ¨æ£‹ç›˜ä¸Šç‚¹å‡»è“è‰²é«˜äº®ç›®æ ‡ã€‚" : "å†æ¬¡ç‚¹å‡»ä¼šç«‹å³ç»“ç®—ã€‚"} `;
    return;
  }
  node.textContent = `å½“å‰ç”±çŽ©å®¶ ${inputPlayer()} æ“ä½œã€‚`;
}

function renderHeader() {
  const pill = $("turn-pill");
  const topbarSubline = $("topbar-subline");
  const caption = $("board-caption");
  const modeMeta = roomModeMeta();
  if (!hasRoom()) {
    pill.textContent = "å°šæœªè¿›å…¥æˆ¿é—´";
    topbarSubline.textContent = "åˆ›å»ºæˆ¿é—´ã€å¤åˆ¶é‚€è¯·é“¾æŽ¥ï¼Œè®©ä¸¤ä½çŽ©å®¶åˆ†åˆ«è¿›å…¥åŒä¸€æˆ¿é—´åŽåœ¨çº¿å¯¹æˆ˜ã€‚";
    caption.textContent = "è¯·å…ˆåˆ›å»ºæˆ¿é—´æˆ–åŠ å…¥æˆ¿é—´ã€‚";
    return;
  }
  if (!state.battle) {
    pill.textContent = `æˆ¿é—´ ${state.room.room_id} Â· ${state.room.status === "lobby" ? "å¤§åŽ…ä¸­" : "ç­‰å¾…å¼€å±€"}`;
    if (isRandomRoomMode()) {
      topbarSubline.textContent = state.room.viewer_player_id
        ? `ä½ å½“å‰æ˜¯çŽ©å®¶ ${state.room.viewer_player_id}ã€‚å½“å‰æˆ¿é—´ä½¿ç”¨ã€Œ${modeMeta.name}ã€ï¼Œå¼€å±€åŽä¼šéšæœºåˆ†é…æ­¦å°†ï¼Œå¹¶åœ¨æ›´å¤§çš„æˆ˜åœºä¸Šéšæœºå‡ºç”Ÿã€‚`
        : "ä½ å½“å‰è¿˜æ²¡æœ‰å ç”¨å¸­ä½ã€‚è‹¥æˆ¿é—´ä»æœ‰ç©ºä½ï¼Œè¾“å…¥æ˜µç§°åŽå³å¯åŠ å…¥ã€‚";
      caption.textContent = "å¯¹å±€å°šæœªå¼€å§‹ï¼Œéšæœºé€‰äººæ¨¡å¼ä¸‹æ— éœ€æ‰‹åŠ¨é€‰å°†ã€‚";
      return;
    }
    topbarSubline.textContent = state.room.viewer_player_id
      ? `ä½ å½“å‰æ˜¯çŽ©å®¶ ${state.room.viewer_player_id}ã€‚åœ¨å¤§åŽ…é‡Œç”¨ +1 / -1 é…ç½®è‡ªå·±çš„å¤šæ­¦å°†é˜µå®¹ï¼ŒåŒæ–¹éƒ½å‡†å¤‡å¥½åŽå¼€å§‹å¯¹å±€ã€‚`
      : "ä½ å½“å‰è¿˜æ²¡æœ‰å ç”¨å¸­ä½ã€‚è‹¥æˆ¿é—´ä»æœ‰ç©ºä½ï¼Œè¾“å…¥æ˜µç§°åŽå³å¯åŠ å…¥ã€‚";
    caption.textContent = "å¯¹å±€å°šæœªå¼€å§‹ï¼Œè¯·å…ˆåœ¨æˆ¿é—´å¤§åŽ…å®Œæˆé€‰å°†ã€‚";
    return;
  }
  const viewerSummary = state.room.viewer_player_id
    ? `æˆ¿é—´ ${state.room.room_id} åœ¨çº¿å¯¹æˆ˜ä¸­ã€‚ä½ å½“å‰æ˜¯çŽ©å®¶ ${state.room.viewer_player_id}ã€‚`
    : `æˆ¿é—´ ${state.room.room_id} åœ¨çº¿å¯¹æˆ˜ä¸­ã€‚ä½ å½“å‰ä»¥è§‚æˆ˜è§†è§’æŸ¥çœ‹æ­¤æˆ¿é—´ã€‚`;
  const nextTurnName = state.battle.next_turn_unit_name || "";
  const nextTurnPlayerId = state.battle.next_turn_player_id;
  const nextTurnSummary = nextTurnName && nextTurnPlayerId
    ? `ä¸‹å›žåˆï¼šçŽ©å®¶ ${nextTurnPlayerId} çš„ ${nextTurnName}ã€‚`
    : "ä¸‹å›žåˆå¾…å®šã€‚";
  topbarSubline.textContent = `${viewerSummary} ${nextTurnSummary}`;
  if (isReplayMode()) {
    pill.textContent = `æˆ¿é—´ ${state.room.room_id} Â· å›žæ”¾ ${state.replayStepIndex}/${replayMeta().last_step_index}`;
    caption.textContent = state.replayOmniscient
      ? "å½“å‰æ­£åœ¨ä»¥å…¨çŸ¥è§†è§’æŸ¥çœ‹å›žæ”¾ã€‚"
      : "å½“å‰æ­£åœ¨æŸ¥çœ‹å›žæ”¾ã€‚";
    return;
  }
  if (isGameOver()) {
    pill.textContent = `æˆ¿é—´ ${state.room.room_id} Â· çŽ©å®¶ ${state.battle.winner} èŽ·èƒœ`;
    caption.textContent = `çŽ©å®¶ ${state.battle.winner} å·²èŽ·èƒœï¼Œæˆ˜åœºå·²é”å®šã€‚`;
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    pill.textContent = `æˆ¿é—´ ${state.room.room_id} Â· çŽ©å®¶ ${inputPlayer()} é‡æ–°å‡ºçŽ°ä¸­`;
    caption.textContent = `è¯·ä¸º ${unit?.name || "æ¶ˆå¤±å•ä½"} é€‰æ‹©é‡æ–°å‡ºçŽ°çš„ä½ç½®ã€‚`;
    return;
  }
  if (isChainMode()) {
    const current = state.battle.pending_chain?.current_unit_id
      ? unitById(state.battle.pending_chain.current_unit_id)?.name
      : "å“åº”æ–¹";
    const sourceSummary = chainQueuedActionPrompt(state.battle.pending_chain);
    pill.textContent = `æˆ¿é—´ ${state.room.room_id} Â· çŽ©å®¶ ${inputPlayer()} è¿žé”ä¸­`;
    caption.textContent = `ç­‰å¾… ${current} å“åº” ${sourceSummary}`;
    return;
  }
  const activeName = state.battle.active_turn_unit_name || "å½“å‰æ­¦å°†";
  pill.textContent = `æˆ¿é—´ ${state.room.room_id} Â· ç¬¬ ${state.battle.round_number} è½® Â· ${activeName}`;
  caption.textContent = `å½“å‰ç”±çŽ©å®¶ ${inputPlayer()} çš„ ${activeName} è¡ŒåŠ¨ã€‚${nextTurnSummary}`;
}

function renderMessage() {
  const node = $("message");
  if (!state.battle) {
    node.textContent = hasRoom() ? "\u623f\u95f4\u5df2\u5efa\u7acb,\u4f46\u5bf9\u5c40\u8fd8\u6ca1\u5f00\u59cb\u3002" : "\u5c1a\u672a\u8fdb\u5165\u623f\u95f4\u3002";
    return;
  }
  if (isReplayMode()) {
    node.textContent = `\u5f53\u524d\u6b63\u5728\u67e5\u770b\u56de\u653e\u7b2c ${state.replayStepIndex}/${replayMeta().last_step_index} \u6b65\u3002`;
    return;
  }
  if (isGameOver()) {
    node.textContent = `\u73a9\u5bb6 ${state.battle.winner} \u5df2\u83b7\u80dc\u3002\u6218\u573a\u5df2\u9501\u5b9a,\u53ef\u56de\u5230\u623f\u95f4\u5927\u5385\u67e5\u770b\u672c\u5c40\u623f\u95f4\u3002`;
    return;
  }
  if (!canInteract()) {
    node.textContent = `\u5f53\u524d\u8f6e\u5230\u73a9\u5bb6 ${inputPlayer()} \u64cd\u4f5c\u3002\u4f60\u53ef\u4ee5\u7ee7\u7eed\u89c2\u5bdf\u6218\u573a,\u7b49\u5f85\u5bf9\u624b\u884c\u52a8\u5b8c\u6210\u3002`;
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    node.textContent = `${unit?.name || "\u6d88\u5931\u5355\u4f4d"} \u9700\u8981\u5148\u91cd\u65b0\u51fa\u73b0\u3002\u8bf7\u70b9\u51fb\u84dd\u8272\u9ad8\u4eae\u7684\u6700\u8fd1\u843d\u70b9\u3002`;
    return;
  }
  if (state.selectedActionCode === "mana_pull" && !state.stagedPayload?.targetUnitId) {
    node.textContent = "\u9b54\u529b\u7275\u5f15\u5206\u4e24\u6b65:\u5148\u9009\u5355\u4f4d,\u518d\u9009\u843d\u70b9\u3002";
    return;
  }
  if (state.stagedPayload?.targetUnitId && state.selectedActionCode === "mana_pull") {
    node.textContent = `\u5df2\u9009\u4e2d ${stagedTarget()?.name || "\u88ab\u7275\u5f15\u76ee\u6807"},\u8bf7\u70b9\u51fb\u84dd\u8272\u9ad8\u4eae\u843d\u70b9\u3002`;
    return;
  }
  if (isChainMode()) {
    const current = unitById(state.battle.pending_chain?.current_unit_id || "");
    const source = unitById(state.battle.pending_chain?.queued_action?.actor_id || "");
    const actionName = state.battle.pending_chain?.queued_action?.display_name || "\u539f\u52a8\u4f5c";
    node.textContent = `${current?.name || "\u5f53\u524d\u5355\u4f4d"} \u53ef\u4ee5\u5bf9 ${source?.name || "\u5bf9\u65b9\u5355\u4f4d"} \u7684\u3010${actionName}\u3011\u8fdb\u884c\u8fde\u9501,\u70b9\u51fb\u5176\u5468\u56f4\u52a8\u4f5c\u6309\u94ae\u6216\u653e\u5f03\u8fde\u9501\u3002`;
    return;
  }
  const action = selectedAction();
  if (action) {
    node.textContent = `\u5df2\u9009\u62e9\u3010${actionTitle(action)}\u3011\u3002${actionNeedsTarget(action) ? "\u8bf7\u5728\u68cb\u76d8\u4e0a\u70b9\u51fb\u84dd\u8272\u9ad8\u4eae\u76ee\u6807\u3002" : "\u518d\u6b21\u70b9\u51fb\u4f1a\u7acb\u5373\u7ed3\u7b97\u3002"} `;
    return;
  }
  node.textContent = `\u5f53\u524d\u7531\u73a9\u5bb6 ${inputPlayer()} \u64cd\u4f5c\u3002`;
}

function renderHeader() {
  const pill = $("turn-pill");
  const topbarSubline = $("topbar-subline");
  const caption = $("board-caption");
  const modeMeta = roomModeMeta();
  if (!hasRoom()) {
    pill.textContent = "\u5c1a\u672a\u8fdb\u5165\u623f\u95f4";
    topbarSubline.textContent = "\u521b\u5efa\u623f\u95f4\u3001\u590d\u5236\u9080\u8bf7\u94fe\u63a5\uff0c\u8ba9\u4e24\u4f4d\u73a9\u5bb6\u5206\u522b\u8fdb\u5165\u540c\u4e00\u623f\u95f4\u540e\u5728\u7ebf\u5bf9\u6218\u3002";
    caption.textContent = "\u8bf7\u5148\u521b\u5efa\u623f\u95f4\u6216\u52a0\u5165\u623f\u95f4\u3002";
    return;
  }
  if (!state.battle) {
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 ${state.room.status === "lobby" ? "\u5927\u5385\u4e2d" : "\u7b49\u5f85\u5f00\u5c40"}`;
    if (isRandomRoomMode()) {
      topbarSubline.textContent = state.room.viewer_player_id
        ? `\u4f60\u5f53\u524d\u662f\u73a9\u5bb6 ${state.room.viewer_player_id}\u3002\u5f53\u524d\u623f\u95f4\u4f7f\u7528\u300c${modeMeta.name}\u300d\uff0c\u5f00\u5c40\u540e\u4f1a\u968f\u673a\u5206\u914d\u6b66\u5c06\uff0c\u5e76\u5728\u66f4\u5927\u7684\u6218\u573a\u4e0a\u968f\u673a\u51fa\u751f\u3002`
        : "\u4f60\u5f53\u524d\u8fd8\u6ca1\u6709\u5360\u7528\u5e2d\u4f4d\u3002\u82e5\u623f\u95f4\u4ecd\u6709\u7a7a\u4f4d\uff0c\u8f93\u5165\u6635\u79f0\u540e\u5373\u53ef\u52a0\u5165\u3002";
      caption.textContent = "\u5bf9\u5c40\u5c1a\u672a\u5f00\u59cb\uff0c\u968f\u673a\u9009\u4eba\u6a21\u5f0f\u4e0b\u65e0\u9700\u624b\u52a8\u9009\u5c06\u3002";
      return;
    }
    topbarSubline.textContent = state.room.viewer_player_id
      ? `\u4f60\u5f53\u524d\u662f\u73a9\u5bb6 ${state.room.viewer_player_id}\u3002\u5728\u5927\u5385\u91cc\u7528 +1 / -1 \u914d\u7f6e\u81ea\u5df1\u7684\u591a\u6b66\u5c06\u9635\u5bb9\uff0c\u53cc\u65b9\u90fd\u51c6\u5907\u597d\u540e\u5f00\u59cb\u5bf9\u5c40\u3002`
      : "\u4f60\u5f53\u524d\u8fd8\u6ca1\u6709\u5360\u7528\u5e2d\u4f4d\u3002\u82e5\u623f\u95f4\u4ecd\u6709\u7a7a\u4f4d\uff0c\u8f93\u5165\u6635\u79f0\u540e\u5373\u53ef\u52a0\u5165\u3002";
    caption.textContent = "\u5bf9\u5c40\u5c1a\u672a\u5f00\u59cb\uff0c\u8bf7\u5148\u5728\u623f\u95f4\u5927\u5385\u5b8c\u6210\u9009\u5c06\u3002";
    return;
  }
  const viewerSummary = state.room.viewer_player_id
    ? `\u623f\u95f4 ${state.room.room_id} \u5728\u7ebf\u5bf9\u6218\u4e2d\u3002\u4f60\u5f53\u524d\u662f\u73a9\u5bb6 ${state.room.viewer_player_id}\u3002`
    : `\u623f\u95f4 ${state.room.room_id} \u5728\u7ebf\u5bf9\u6218\u4e2d\u3002\u4f60\u5f53\u524d\u4ee5\u89c2\u6218\u89c6\u89d2\u67e5\u770b\u6b64\u623f\u95f4\u3002`;
  const nextTurnName = state.battle.next_turn_unit_name || "";
  const nextTurnPlayerId = state.battle.next_turn_player_id;
  const nextTurnSummary = nextTurnName && nextTurnPlayerId
    ? `\u4e0b\u56de\u5408\uff1a\u73a9\u5bb6 ${nextTurnPlayerId} \u7684 ${nextTurnName}\u3002`
    : "\u4e0b\u56de\u5408\u5f85\u5b9a\u3002";
  topbarSubline.textContent = `${viewerSummary} ${nextTurnSummary}`;
  if (isReplayMode()) {
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u56de\u653e ${state.replayStepIndex}/${replayMeta().last_step_index}`;
    caption.textContent = state.replayOmniscient
      ? "\u5f53\u524d\u6b63\u5728\u4ee5\u5168\u77e5\u89c6\u89d2\u67e5\u770b\u56de\u653e\u3002"
      : "\u5f53\u524d\u6b63\u5728\u67e5\u770b\u56de\u653e\u3002";
    return;
  }
  if (isGameOver()) {
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u73a9\u5bb6 ${state.battle.winner} \u83b7\u80dc`;
    caption.textContent = `\u73a9\u5bb6 ${state.battle.winner} \u5df2\u83b7\u80dc\uff0c\u6218\u573a\u5df2\u9501\u5b9a\u3002`;
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u73a9\u5bb6 ${inputPlayer()} \u91cd\u65b0\u51fa\u73b0\u4e2d`;
    caption.textContent = `\u8bf7\u4e3a ${unit?.name || "\u6d88\u5931\u5355\u4f4d"} \u9009\u62e9\u91cd\u65b0\u51fa\u73b0\u7684\u4f4d\u7f6e\u3002`;
    return;
  }
  if (isChainMode()) {
    const current = state.battle.pending_chain?.current_unit_id
      ? unitById(state.battle.pending_chain.current_unit_id)?.name
      : "\u54cd\u5e94\u65b9";
    const sourceSummary = chainQueuedActionPrompt(state.battle.pending_chain);
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u73a9\u5bb6 ${inputPlayer()} \u8fde\u9501\u4e2d`;
    caption.textContent = `\u7b49\u5f85 ${current} \u54cd\u5e94 ${sourceSummary}`;
    return;
  }
  const activeName = state.battle.active_turn_unit_name || "\u5f53\u524d\u6b66\u5c06";
  pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u7b2c ${state.battle.round_number} \u8f6e \u00b7 ${activeName}`;
  caption.textContent = `\u5f53\u524d\u7531\u73a9\u5bb6 ${inputPlayer()} \u7684 ${activeName} \u884c\u52a8\u3002${nextTurnSummary}`;
}

function renderReplayToolbar() {
  const toolbar = $("replay-toolbar");
  if (!toolbar) return;
  const replay = replayMeta();
  const simulation = simulationMeta();
  const visible = hasBattle() && replay.available;
  toolbar.classList.toggle("hidden", !visible);
  if (!visible) return;
  const lastIndex = Number(replay.last_step_index || 0);
  const liveIndex = Number(simulation.live_step_index || 0);
  const currentIndex = isReplayMode()
    ? Math.max(0, Math.min(lastIndex, Number(state.replayStepIndex || 0)))
    : Math.max(0, Math.min(lastIndex, liveIndex));
  const back = $("replay-step-back");
  const pause = $("replay-pause");
  const live = $("replay-live");
  const forward = $("replay-step-forward");
  const speed = $("replay-speed");
  const omniscient = $("replay-omniscient");
  const timeline = $("replay-timeline");
  const status = $("replay-status");
  if (speed) {
    speed.value = String(simulation.speed || 1);
    speed.disabled = !state.room?.viewer_is_host;
  }
  if (omniscient) {
    omniscient.checked = Boolean(state.replayOmniscient);
    omniscient.disabled = !replay.can_use_omniscient;
  }
  if (timeline) {
    timeline.max = String(lastIndex);
    timeline.value = String(currentIndex);
    timeline.disabled = !replay.available;
  }
  if (back) back.disabled = currentIndex <= 0;
  if (forward) forward.disabled = currentIndex >= lastIndex;
  if (live) live.disabled = !isReplayMode();
  if (pause) {
    pause.textContent = simulation.paused ? "\u7ee7\u7eed" : "\u6682\u505c";
    pause.disabled = !simulation.can_control;
  }
  if (status) {
    if (isReplayMode()) {
      status.textContent = `\u56de\u653e ${currentIndex}/${lastIndex}`;
    } else if (simulation.enabled) {
      status.textContent = simulation.paused
        ? `\u5df2\u6682\u505c ${liveIndex}/${lastIndex}`
        : `\u5b9e\u65f6 ${liveIndex}/${lastIndex}`;
    } else {
      status.textContent = `\u672c\u5c40 ${currentIndex}/${lastIndex}`;
    }
  }
}

function ensureDynamicUiScaffolding() {
  const heroHeadCopy = document.querySelector(".room-hero-head p");
  if (heroHeadCopy) {
    heroHeadCopy.textContent = "每位玩家可以为自己选择多个武将。使用下方卡片的 +1 / -1 来配置自己的阵容。";
  }
  if ($("board-zoom-controls")) return;
  const boardHead = document.querySelector(".board-wrap .section-head");
  const legend = boardHead?.querySelector(".legend");
  if (!boardHead) return;
  const controls = document.createElement("div");
  controls.id = "board-zoom-controls";
  controls.className = "zoom-controls hidden";
  controls.innerHTML = `
    <button id="board-zoom-out" type="button" class="ghost">缩小</button>
    <button id="board-zoom-reset" type="button" class="ghost">重置</button>
    <button id="board-zoom-in" type="button" class="ghost">放大</button>
    <span id="board-zoom-value" class="zoom-value">100%</span>
  `;
  if (legend) {
    const tools = document.createElement("div");
    tools.className = "board-tools";
    legend.replaceWith(tools);
    tools.append(legend, controls);
  } else {
    boardHead.append(controls);
  }
  const footer = document.querySelector(".board-footer");
  const endTurnButton = $("end-turn");
  if (footer && endTurnButton && !$("replay-toolbar")) {
    const toolbar = document.createElement("div");
    toolbar.id = "replay-toolbar";
    toolbar.className = "replay-toolbar hidden";
    toolbar.innerHTML = `
      <button id="replay-step-back" class="ghost" type="button">åŽé€€</button>
      <button id="replay-pause" class="ghost" type="button">æš‚åœ</button>
      <button id="replay-live" class="ghost" type="button">å›žåˆ°å®žæ—¶</button>
      <button id="replay-step-forward" class="ghost" type="button">å‰è¿›</button>
      <label class="replay-speed-control" for="replay-speed">
        <span>é€Ÿåº¦</span>
        <select id="replay-speed">
          <option value="0.5">0.5x</option>
          <option value="1" selected>1x</option>
          <option value="2">2x</option>
          <option value="4">4x</option>
        </select>
      </label>
      <label class="replay-omniscient-toggle">
        <input id="replay-omniscient" type="checkbox" />
        <span>å…¨çŸ¥</span>
      </label>
      <input id="replay-timeline" class="replay-timeline" type="range" min="0" max="0" step="1" value="0" />
      <span id="replay-status" class="replay-status">å®žæ—¶</span>
    `;
    footer.insertBefore(toolbar, endTurnButton);
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  hydrateStaticLabels();
  initializeProfileState();
  syncIdentityFromUrl();
  ensureDynamicUiScaffolding();
  bindEvents();
  await refreshState({ preserveScreen: false });
  pollHandle = window.setInterval(() => {
    if (!roomQueryId()) {
      refreshState({ preserveScreen: false });
      return;
    }
    refreshState();
  }, 1500);
});
