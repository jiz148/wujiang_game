const state = {
  heroes: [],
  rooms: [],
  room: null,
  battle: null,
  selectedUnitId: "",
  selectedActionCode: "",
  hoveredActionCode: "",
  hoveredUnitId: "",
  hoverPointer: null,
  hoveredBoardCell: null,
  stagedPayload: null,
  screen: "draft",
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
};

const ROOM_TOKEN_PREFIX = "wujiang-room-token:";
const ROOM_NAME_PREFIX = "wujiang-room-name:";
const PROFILE_NAME_KEY = "wujiang-profile-name";
const PROFILE_READY_KEY = "wujiang-profile-ready";
let pollHandle = null;
let refreshInFlight = false;

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

function viewerPlayerId() {
  return state.room?.viewer_player_id ?? null;
}

function isGameOver() {
  return Boolean(state.battle?.winner);
}

function canInteract() {
  return Boolean(
    state.battle
      && state.screen === "battle"
      && !isGameOver()
      && viewerPlayerId() !== null
      && viewerPlayerId() === inputPlayer(),
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

function activeOccupantAt(x, y) {
  return allUnits().find(
    (unit) => !unit.banished && unit.position && unit.position.x === x && unit.position.y === y,
  ) || null;
}

function visibleUnitAt(x, y) {
  return allUnits().find(
    (unit) => unit.position && unit.position.x === x && unit.position.y === y,
  ) || null;
}

function selectedUnit() {
  return unitById(state.selectedUnitId);
}

function stagedTarget() {
  return unitById(state.stagedPayload?.targetUnitId || "");
}

function roomQueryId() {
  const roomId = new URLSearchParams(window.location.search).get("room");
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
  return {
    token: sessionStorage.getItem(roomTokenKey(roomId)) || "",
    name: sessionStorage.getItem(roomNameKey(roomId)) || "",
  };
}

function clearStoredIdentity(roomId) {
  if (!roomId) return;
  sessionStorage.removeItem(roomTokenKey(roomId));
  sessionStorage.removeItem(roomNameKey(roomId));
}

function resetRoomSession({ rooms = state.rooms, roomId = roomQueryId() } = {}) {
  clearStoredIdentity(roomId);
  state.playerToken = "";
  state.room = null;
  state.battle = null;
  state.selectedUnitId = "";
  state.roomForm.joinRoomCode = "";
  state.rooms = rooms || [];
  clearActionSelection();
  syncLocation("draft", "");
  syncScreen({ preferBattle: false });
}

function saveStoredIdentity(roomId, token, name) {
  if (!roomId || !token) return;
  sessionStorage.setItem(roomTokenKey(roomId), token);
  if (name) {
    sessionStorage.setItem(roomNameKey(roomId), name);
  }
}

function syncIdentityFromUrl() {
  return;
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
  if (!controllable.includes(state.selectedUnitId)) {
    state.selectedUnitId = controllable[0];
  }
}

function clearActionSelection() {
  state.selectedActionCode = "";
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
      description: "放弃本次连锁，让原动作按原本声明继续结算。",
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

function hoveredAction() {
  return actionByCode(state.selectedActionCode) || actionByCode(state.hoveredActionCode);
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

function unitIsSelectableTarget(unit) {
  return Boolean(unit)
    && !unit.banished
    && !unit.cannot_be_targeted
    && !unit.statuses.some((status) => status.name === "隐身");
}

function previewCellsForTargetIds(targetIds = []) {
  return targetIds
    .map((id) => unitById(id))
    .filter((unit) => unit?.position && !unit.banished)
    .map((unit) => unit.position)
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
    .filter((unit) => unit.position && !unit.banished && keys.has(positionKey(unit.position)))
    .map((unit) => unit.id);
}

function sameCell(left, right) {
  return Boolean(left && right) && left.x === right.x && left.y === right.y;
}

function stagedPierceCells() {
  if (state.selectedActionCode !== "pierce") return [];
  return Array.isArray(state.stagedPayload?.pierceCells) ? state.stagedPayload.pierceCells : [];
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

function currentPreview() {
  if (isGameOver()) {
    return { cellKeys: new Set(), targetIds: new Set(), secondaryCellKeys: new Set() };
  }
  if (isRespawnMode()) {
    return {
      cellKeys: positionsToSet(currentRespawnPrompt()?.options || []),
      targetIds: new Set(),
      secondaryCellKeys: positionsToSet(currentRespawnPrompt()?.origin ? [currentRespawnPrompt().origin] : []),
    };
  }
  const action = hoveredAction();
  if (!action) {
    return { cellKeys: new Set(), targetIds: new Set(), secondaryCellKeys: new Set() };
  }

  if (state.selectedActionCode === "mana_pull" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    return {
      cellKeys: positionsToSet(manaPullDestinations(target)),
      targetIds: new Set(target ? [target.id] : []),
      secondaryCellKeys: positionsToSet(target?.position ? [target.position] : []),
    };
  }

  const filteredTargetIds = (action.preview?.target_unit_ids || []).filter((id) => unitIsSelectableTarget(unitById(id)));
  if (action.code === "pierce") {
    const chosenCells = stagedPierceCells();
    const activeCells = (action.preview?.cells || []).filter(
      (cell) => !chosenCells.some((picked) => sameCell(picked, cell)),
    );
    const targetIds = chosenCells.length ? unitIdsAtCells(chosenCells) : [];
    return {
      cellKeys: positionsToSet(activeCells),
      targetIds: targetIdsToSet(targetIds),
      secondaryCellKeys: positionsToSet(chosenCells),
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
        (unit) => !unit.banished && unit.position && unit.id !== target.id && unit.position.x === current.x && unit.position.y === current.y,
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
  const action = state.selectedActionCode ? actionByCode(state.selectedActionCode) : null;
  return Boolean(action && actionNeedsTarget(action));
}

function actionLabel(action) {
  if (action.kind === "move") return "移";
  if (action.kind === "attack") return "攻";
  if (action.kind === "chain_skip") return "否";
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
      ? `当前会以“${displayName}”参与创建房间、输入房间码加入、以及从房间列表直接加入。`
      : "当前使用自动昵称；你也可以随时修改一个更容易识别的名字。";
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
    ? "这个昵称会用于之后创建房间和加入房间。留空也可以，系统会继续使用自动昵称。"
    : "这个昵称会用于创建房间和加入房间。留空也可以，系统会自动给你默认昵称。";
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
  return Boolean(roomQueryId() && identity.token && !state.playerToken);
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
  const hadBattle = Boolean(state.battle);
  const previousScreen = state.screen;
  state.heroes = payload.heroes || [];
  if (payload.rooms) {
    state.rooms = payload.rooms;
  }
  state.room = payload.room || null;
  state.battle = payload.battle || null;
  if (payload.player_token) {
    state.playerToken = payload.player_token;
  }
  if (state.room?.room_id && state.playerToken) {
    saveStoredIdentity(
      state.room.room_id,
      state.playerToken,
      state.room.viewer_name || effectiveProfileName(),
    );
  }
  state.lastSyncAt = Date.now();
  const autoEnterBattle = Boolean(state.battle)
    && Boolean(state.room?.viewer_player_id)
    && (!hadBattle || previousScreen === "battle");
  syncScreen({ preferBattle: autoEnterBattle || (preserveScreen && previousScreen === "battle") });
  syncSelectedUnitAfterStateChange();
}

function roomHeroSelectionSummary(heroCode) {
  if (!hasRoom() || !heroCode) return "";
  const pickers = (state.room.seats || [])
    .filter((seat) => seat.hero_code === heroCode)
    .map((seat) => `玩家 ${seat.player_id}`);
  return pickers.length ? pickers.join(" / ") : "";
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
  const canSelect = Boolean(hasRoom() && state.room?.status === "lobby" && viewerSeat);

  homeCards.innerHTML = "";
  lobbyCards.innerHTML = "";

  state.heroes.forEach((hero) => {
    const homeCard = document.createElement("article");
    homeCard.className = "hero-card";
    homeCard.innerHTML = `
      <h3>${hero.name}</h3>
      <div class="meta">${hero.role} / ${hero.attribute} / ${hero.race} / 等级 ${hero.level}</div>
      <div class="meta">攻 ${hero.stats.attack} · 守 ${hero.stats.defense} · 速 ${hero.stats.speed} · 范 ${hero.stats.attack_range} · 魔 ${hero.stats.mana}</div>
      <div class="text"><strong>技能：</strong>${hero.raw_skill_text}</div>
      <div class="text"><strong>特性：</strong>${hero.raw_trait_text}</div>
    `;
    homeCards.append(homeCard);

    const lobbyCard = document.createElement("article");
    lobbyCard.className = `hero-card ${selectedHeroCode === hero.code ? "is-selected" : ""}`;
    const selectedBy = roomHeroSelectionSummary(hero.code);
    lobbyCard.innerHTML = `
      <h3>${hero.name}</h3>
      <div class="meta">${hero.role} / ${hero.attribute} / ${hero.race} / 等级 ${hero.level}</div>
      <div class="meta">攻 ${hero.stats.attack} · 守 ${hero.stats.defense} · 速 ${hero.stats.speed} · 范 ${hero.stats.attack_range} · 魔 ${hero.stats.mana}</div>
      <div class="text"><strong>技能：</strong>${hero.raw_skill_text}</div>
      <div class="text"><strong>特性：</strong>${hero.raw_trait_text}</div>
      <div class="text"><strong>当前选择：</strong>${selectedBy || "尚无人选择"}</div>
    `;
    const pickBtn = document.createElement("button");
    pickBtn.className = selectedHeroCode === hero.code ? "ghost" : "primary";
    pickBtn.textContent = selectedHeroCode === hero.code ? "已选此武将" : "选择该武将";
    pickBtn.disabled = !canSelect;
    pickBtn.addEventListener("click", () => selectRoomHero(hero.code));
    lobbyCard.append(pickBtn);
    lobbyCards.append(lobbyCard);
  });
}

function renderHeader() {
  const pill = $("turn-pill");
  const topbarSubline = $("topbar-subline");
  const caption = $("board-caption");
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
  topbarSubline.textContent = state.room.viewer_player_id
    ? `房间 ${state.room.room_id} 在线对战中。你当前是玩家 ${state.room.viewer_player_id}。`
    : `房间 ${state.room.room_id} 在线对战中。你当前以观战视角查看此房间。`;
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
    const sourceAction = state.battle.pending_chain?.queued_action?.display_name || "原动作";
    pill.textContent = `房间 ${state.room.room_id} · 玩家 ${inputPlayer()} 连锁中`;
    caption.textContent = `等待 ${current} 响应【${sourceAction}】。`;
    return;
  }
  pill.textContent = `房间 ${state.room.room_id} · 第 ${state.battle.round_number} 轮 · 玩家 ${inputPlayer()} 行动`;
  caption.textContent = "点击己方棋子，在棋子周围选择动作。";
}

function renderHeader() {
  const pill = $("turn-pill");
  const topbarSubline = $("topbar-subline");
  const caption = $("board-caption");
  if (!hasRoom()) {
    pill.textContent = "\u5c1a\u672a\u8fdb\u5165\u623f\u95f4";
    topbarSubline.textContent = "\u521b\u5efa\u623f\u95f4\u3001\u590d\u5236\u9080\u8bf7\u94fe\u63a5\uff0c\u8ba9\u4e24\u4f4d\u73a9\u5bb6\u5206\u522b\u8fdb\u5165\u540c\u4e00\u623f\u95f4\u540e\u5728\u7ebf\u5bf9\u6218\u3002";
    caption.textContent = "\u8bf7\u5148\u521b\u5efa\u623f\u95f4\u6216\u52a0\u5165\u623f\u95f4\u3002";
    return;
  }
  if (!state.battle) {
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 ${state.room.status === "lobby" ? "\u5927\u5385\u4e2d" : "\u7b49\u5f85\u5f00\u5c40"}`;
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
    const sourceAction = state.battle.pending_chain?.queued_action?.display_name || "\u539f\u52a8\u4f5c";
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u73a9\u5bb6 ${inputPlayer()} \u8fde\u9501\u4e2d`;
    caption.textContent = `\u7b49\u5f85 ${current} \u54cd\u5e94\u3010${sourceAction}\u3011\u3002`;
    return;
  }
  pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 \u7b2c ${state.battle.round_number} \u8f6e \u00b7 \u73a9\u5bb6 ${inputPlayer()} \u884c\u52a8`;
  caption.textContent = "\u70b9\u51fb\u5df1\u65b9\u68cb\u5b50\uff0c\u5728\u68cb\u5b50\u5468\u56f4\u9009\u62e9\u52a8\u4f5c\u3002";
}

function renderBoardAlert() {
  const node = $("board-alert");
  if (!state.battle || isGameOver() || state.screen !== "battle") {
    node.className = "board-alert hidden";
    node.innerHTML = "";
    return;
  }

  if (isChainMode()) {
    const chain = state.battle.pending_chain;
    const reactor = unitById(chain?.current_unit_id || "");
    const source = unitById(chain?.queued_action?.actor_id || "");
    node.className = "board-alert is-chain";
    node.innerHTML = `
      <strong>对方可连锁</strong>
      <span>${reactor?.name || "响应单位"} 正在决定是否对 ${source?.name || "来源单位"} 的【${chain?.queued_action?.display_name || "动作"}】进行连锁。</span>
    `;
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

  const action = actionByCode(state.selectedActionCode);
  if (action?.code === "mana_pull" && !state.stagedPayload?.targetUnitId) {
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>魔力牵引</strong>
      <span>先点击被牵引的单位，再点击 1 到 3 格直线落点。</span>
    `;
    return;
  }

  if (action?.code === "mana_pull" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>魔力牵引</strong>
      <span>已选中 ${target?.name || "目标"}，请点击其 1 到 3 格的直线落点。</span>
    `;
    return;
  }

  if (action?.code === "pierce") {
    const chosenCells = stagedPierceCells();
    node.className = "board-alert is-step";
    if (!chosenCells.length) {
      node.innerHTML = `
        <strong>穿刺</strong>
        <span>请先点击第 1 个要被穿刺的格子。</span>
      `;
      return;
    }
    node.innerHTML = `
      <strong>穿刺</strong>
      <span>已选中第 1 格 (${chosenCells[0].x}, ${chosenCells[0].y})，请再点击第 2 个不同的格子。</span>
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

  if (!state.battle) return;
  const chain = state.battle.pending_chain;
  const chainSource = unitById(chain?.queued_action?.actor_id || "");
  const chainReactor = unitById(chain?.current_unit_id || "");
  const fieldCellMap = fieldEffectsByCell();

  for (let y = 0; y < state.battle.board.height; y += 1) {
    for (let x = 0; x < state.battle.board.width; x += 1) {
      const cell = document.createElement("button");
      cell.className = "cell";
      cell.type = "button";
      cell.dataset.x = x;
      cell.dataset.y = y;
      cell.disabled = false;

      const unitsHere = allUnits().filter(
        (unit) => unit.position && unit.position.x === x && unit.position.y === y,
      );
      const occupant = unitsHere.find((unit) => !unit.banished) || unitsHere[0] || null;
      const ghostUnits = unitsHere.filter((unit) => unit.banished);

      const key = `${x},${y}`;
      const cellEffects = fieldCellMap.get(key) || [];
      if (preview.cellKeys.has(key)) cell.classList.add("is-preview");
      if (preview.secondaryCellKeys.has(key)) cell.classList.add("is-secondary");
      if (occupant && preview.targetIds.has(occupant.id)) cell.classList.add("is-target");
      if (selected?.position?.x === x && selected?.position?.y === y) cell.classList.add("is-selected");
      if (chainSource?.position?.x === x && chainSource?.position?.y === y) cell.classList.add("is-chain-source");
      if (chainReactor?.position?.x === x && chainReactor?.position?.y === y) cell.classList.add("is-chain-reactor");
      if (cellEffects.length) cell.classList.add("has-field-effect");

      if (cellEffects.length) {
        const markerStack = document.createElement("div");
        markerStack.className = "cell-effects";
        cellEffects.forEach((effect) => {
          const marker = document.createElement("span");
          marker.className = "cell-effect-tag";
          marker.textContent = fieldEffectMarker(effect);
          marker.title = effect.description ? `${effect.name}：${effect.description}` : effect.name;
          markerStack.append(marker);
        });
        cell.append(markerStack);
      }

      if (occupant && !occupant.banished) {
        const piece = document.createElement("div");
        piece.className = `piece player-${occupant.player_id}`;
        piece.style.setProperty("--hp-angle", `${hpRatio(occupant) * 360}deg`);
        piece.innerHTML = `
          <div class="piece-ring">
            <div class="piece-core">
              <div class="piece-name">${occupant.name}</div>
            </div>
          </div>
          <div class="${manaDisplayClass(occupant)}" aria-label="魔力 ${trimNumber(occupant.mana)} / ${trimNumber(occupant.base_stats?.mana || occupant.stats?.mana || occupant.mana)}">
            ${manaPipsMarkup(occupant)}
          </div>
        `;
        cell.append(piece);
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

  const selectedCell = [...document.querySelectorAll(".cell")].find(
    (cell) => Number(cell.dataset.x) === unit.position.x && Number(cell.dataset.y) === unit.position.y,
  );
  const stageRect = $("board-stage").getBoundingClientRect();
  const cellRect = selectedCell?.getBoundingClientRect();
  if (!selectedCell || !cellRect) return;

  const centerX = cellRect.left - stageRect.left + cellRect.width / 2;
  const centerY = cellRect.top - stageRect.top + cellRect.height / 2;
  const radius = 100;

  actions.forEach((action, index) => {
    const angle = (-90 + (360 / actions.length) * index) * (Math.PI / 180);
    const left = centerX + Math.cos(angle) * radius;
    const top = centerY + Math.sin(angle) * radius;
    const btn = document.createElement("button");
    btn.className = `action-btn ${state.selectedActionCode === action.code ? "is-selected" : ""}`;
    btn.style.left = `${left - 42}px`;
    btn.style.top = `${top - 23}px`;
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

function positionHoverCard(card) {
  if (!state.hoverPointer) return;
  const gap = 18;
  const maxLeft = window.innerWidth - card.offsetWidth - 12;
  const maxTop = window.innerHeight - card.offsetHeight - 12;
  const left = Math.min(maxLeft, state.hoverPointer.x + gap);
  const top = Math.min(maxTop, state.hoverPointer.y + gap);
  card.style.left = `${Math.max(12, left)}px`;
  card.style.top = `${Math.max(12, top)}px`;
}

function renderUnitHoverCard(unit) {
  const statuses = unitStatusSummary(unit).join(" · ") || "无";
  const traits = unit.traits.map((trait) => trait.name).join(" · ") || "无";
  return `
    <strong>${unit.name}</strong>
    <p>${unit.role} · ${unit.attribute} / ${unit.race} · 玩家 ${unit.player_id}</p>
    <p>血 ${trimNumber(unit.hp)} / ${trimNumber(unit.max_hp)} · 魔 ${trimNumber(unit.mana)} / ${trimNumber(unit.base_stats?.mana || unit.stats?.mana || unit.mana)}</p>
    <p>盾 ${unit.total_shields} · 闪 ${unit.dodge_charges} · 攻 ${trimNumber(unit.stats.attack)} / 守 ${trimNumber(unit.stats.defense)}</p>
    <p>状态：${statuses}</p>
    <p>特性：${traits}</p>
  `;
}

function renderActionHoverCard(action) {
  return `
    <strong>${actionTitle(action)}</strong>
    <p>${action.description}</p>
    <p>${actionTimingLabel(action)} · ${actionNeedsTarget(action) ? "需要选取目标" : "无需额外目标"}</p>
  `;
}

function renderHoverCard() {
  const card = $("hover-card");
  const unit = hoveredUnit();
  const action = !unit ? actionByCode(state.hoveredActionCode) : null;
  if ((!unit && !action) || !state.battle || state.screen !== "battle") {
    card.classList.add("hidden");
    card.innerHTML = "";
    return;
  }
  card.classList.remove("hidden");
  card.innerHTML = unit ? renderUnitHoverCard(unit) : renderActionHoverCard(action);
  positionHoverCard(card);
}

function renderSelectedCard() {
  const panel = $("selected-card");
  const unit = selectedUnit();
  if (!unit) {
    panel.textContent = isGameOver()
      ? `玩家 ${state.battle?.winner || ""} 已获胜，战场操作已锁定。`
      : "点击棋子后，这里会显示该武将的数值、技能与状态。";
    return;
  }
  const statusEntries = unit.statuses.map((status) => `${status.name}${status.duration ? `(${status.duration})` : ""}`);
  if (unit.banished) {
    statusEntries.unshift(`消失${unit.banish_turns_remaining > 0 ? `(${unit.banish_turns_remaining})` : ""}`);
  }
  const statuses = statusEntries.join("，") || "无";
  const traits = unit.traits.map((trait) => trait.name).join("，") || "无";
  panel.innerHTML = `
    <strong>${unit.name}</strong>
    <div class="statline">玩家 ${unit.player_id} · ${unit.role} / ${unit.attribute} / ${unit.race} / 等级 ${unit.level}</div>
    <div class="statline">攻 ${trimNumber(unit.stats.attack)} · 守 ${trimNumber(unit.stats.defense)} · 速 ${trimNumber(unit.stats.speed)} · 范 ${trimNumber(unit.stats.attack_range)} · 魔 ${trimNumber(unit.mana)}</div>
    <div class="statline">血 ${trimNumber(unit.hp)} / ${trimNumber(unit.max_hp)} · 固定护盾 ${unit.shields} · 临时护盾 ${unit.temporary_shields} · 闪避 ${unit.dodge_charges}</div>
    <div class="statline"><strong>状态：</strong>${statuses}</div>
    <div class="statline"><strong>特性：</strong>${traits}</div>
    <div class="statline"><strong>原始技能：</strong>${unit.raw_skill_text}</div>
    <div class="statline"><strong>原始特性：</strong>${unit.raw_trait_text}</div>
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
    caption.textContent = "先创建房间或输入房间码加入。进入房间后，每位玩家各自选择自己的武将，再开始对局。";
    copyInvite.classList.add("hidden");
    roomBattle.classList.add("hidden");
    startRoom.classList.add("hidden");
    return;
  }

  if (!showLobby) {
    title.textContent = `加入房间 ${state.room.room_id}`;
    caption.textContent = "这个房间仍在等待玩家占位。输入昵称后点击加入房间，即可进入房间大厅开始选将。";
    copyInvite.classList.remove("hidden");
    roomBattle.classList.toggle("hidden", !hasBattle());
    startRoom.classList.add("hidden");
    return;
  }

  title.textContent = `房间 ${state.room.room_id}`;
  caption.textContent = hasBattle()
    ? "对局已经开始。你可以返回战场继续测试，或留在这里查看房间信息。"
    : "双方玩家在这里各自选择自己的武将，准备完成后开始对局。";

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
      : `房间 ${state.room.room_id} 的对局正在进行中。点击“进入战场”即可查看并继续操作。`;
  } else if (state.room.viewer_player_id === null) {
    roomMessage.textContent = state.room.is_full
      ? "这个房间已经满员。你当前可以观战，但不能代替其中任意一位玩家操作。"
      : "这个房间还有空位。若你是受邀玩家，请在首页的加入房间区域输入昵称并加入。";
  } else if (!currentRoomSeat()?.hero_code) {
    roomMessage.textContent = `你当前是玩家 ${state.room.viewer_player_id}，请从下方选择自己的武将。`;
  } else if (!state.room.can_start) {
    roomMessage.textContent = "你已经选好了武将，正在等待另一位玩家加入或完成选将。";
  } else {
    roomMessage.textContent = "双方都已就绪，可以开始这场联机测试对局。";
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
      <div class="seat-hero"><strong>当前武将：</strong>${seat.hero_name || "未选择"}</div>
      <div class="seat-note">${seat.occupied ? "已进入房间" : "等待朋友加入该席位"}</div>
    `;
    seatCards.append(card);
  });
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
      .map((seat) => `玩家 ${seat.player_id}：${seat.name || "空位"}${seat.hero_name ? ` · ${seat.hero_name}` : ""}`)
      .join(" / ");
    const card = document.createElement("article");
    card.className = "room-list-card";
    card.innerHTML = `
      <div class="room-list-head">
        <strong>房间 ${room.room_id}</strong>
        <span class="room-list-state ${roomStateClass(room)}">${roomStateLabel(room)}</span>
      </div>
      <div class="room-list-meta">席位 ${room.occupied_seat_count}/${room.seat_count} · ${room.status === "lobby" ? "等待玩家就绪" : "正在进行或已结束"}</div>
      <div class="room-list-seats">${seatSummary}</div>
      <div class="room-list-note">${remembered.token ? "这个浏览器之前进过该房间，可直接返回继续。" : "若要直接加入，请先在上方输入你的昵称。"} </div>
    `;
    const actions = document.createElement("div");
    actions.className = "room-list-actions";
    const primary = document.createElement("button");
    primary.className = room.can_join ? "primary" : "ghost";
    primary.textContent = remembered.token ? "返回房间" : (room.can_join ? "加入房间" : "查看房间");
    primary.addEventListener("click", () => {
      if (remembered.token) {
        openListedRoom(room.room_id);
        return;
      }
      if (room.can_join) {
        joinListedRoom(room.room_id);
        return;
      }
      openListedRoom(room.room_id);
    });
    actions.append(primary);
    if (!remembered.token && room.can_join) {
      const fillBtn = document.createElement("button");
      fillBtn.className = "ghost";
      fillBtn.textContent = "填入房间码";
      fillBtn.addEventListener("click", () => {
        state.roomForm.joinRoomCode = room.room_id;
        $("join-room-code").value = room.room_id;
        $("lobby-caption").textContent = `已填入房间 ${room.room_id}。输入昵称后即可加入。`;
      });
      actions.append(fillBtn);
    }
    card.append(actions);
    list.append(card);
  });
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
      .map((seat) => `玩家 ${seat.player_id}：${seat.name || "空位"}${seat.hero_name ? ` · ${seat.hero_name}` : ""}`)
      .join(" / ");
    const card = document.createElement("article");
    card.className = "room-list-card";
    card.innerHTML = `
      <div class="room-list-head">
        <strong>房间 ${room.room_id}</strong>
        <span class="room-list-state ${roomStateClass(room)}">${roomStateLabel(room)}</span>
      </div>
      <div class="room-list-meta">席位 ${room.occupied_seat_count}/${room.seat_count} · ${room.status === "lobby" ? "等待玩家就绪" : "正在进行或已结束"}</div>
      <div class="room-list-seats">${seatSummary}</div>
      <div class="room-list-note">${remembered.token ? "这个浏览器之前进入过该房间。若你要继续原来的席位，请点“继续原身份”；若你要作为另一位玩家进入，请输入新昵称后点“加入房间”。" : "若要直接加入，请先在上方输入你的昵称。"} </div>
    `;

    const actions = document.createElement("div");
    actions.className = "room-list-actions";

    const primary = document.createElement("button");
    primary.className = room.can_join ? "primary" : "ghost";
    primary.textContent = room.can_join ? "加入房间" : "查看房间";
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
      resumeBtn.textContent = "继续原身份";
      resumeBtn.addEventListener("click", () => resumeStoredSeat(room.room_id));
      actions.append(resumeBtn);
    }

    if (!remembered.token && room.can_join) {
      const fillBtn = document.createElement("button");
      fillBtn.className = "ghost";
      fillBtn.textContent = "填入房间码";
      fillBtn.addEventListener("click", () => {
        state.roomForm.joinRoomCode = room.room_id;
        $("join-room-code").value = room.room_id;
        $("lobby-caption").textContent = `已填入房间 ${room.room_id}。输入昵称后即可加入。`;
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
  if (!panel || !text) return;
  const identity = storedIdentityForCurrentRoom();
  const visible = Boolean(roomQueryId() && identity.token && !viewerPlayerId() && !state.playerToken);
  panel.classList.toggle("hidden", !visible);
  if (!visible) return;
  text.textContent = `检测到这个浏览器之前曾以“${identity.name || "未命名玩家"}”进入当前房间。你可以直接继续原来的席位，或者输入新昵称作为另一位玩家加入。`;
}

function renderRoomList() {
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
      .map((seat) => `\u73a9\u5bb6 ${seat.player_id}\uff1a${seat.name || "\u7a7a\u4f4d"}${seat.hero_name ? ` \u00b7 ${seat.hero_name}` : ""}`)
      .join(" / ");
    const card = document.createElement("article");
    card.className = "room-list-card";
    card.innerHTML = `
      <div class="room-list-head">
        <strong>\u623f\u95f4 ${room.room_id}</strong>
        <span class="room-list-state ${roomStateClass(room)}">${roomStateLabel(room)}</span>
      </div>
      <div class="room-list-meta">\u5e2d\u4f4d ${room.occupied_seat_count}/${room.seat_count} \u00b7 ${room.status === "lobby" ? "\u7b49\u5f85\u73a9\u5bb6\u5c31\u7eea" : "\u6b63\u5728\u8fdb\u884c\u6216\u5df2\u7ed3\u675f"}</div>
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
        $("join-room-code").value = room.room_id;
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
  if (!panel || !text) return;
  const identity = storedIdentityForCurrentRoom();
  const visible = Boolean(roomQueryId() && identity.token && !viewerPlayerId() && !state.playerToken);
  panel.classList.toggle("hidden", !visible);
  if (!visible) return;
  text.textContent = `\u68c0\u6d4b\u5230\u8fd9\u4e2a\u6d4f\u89c8\u5668\u4e4b\u524d\u66fe\u4ee5\u201c${identity.name || "\u672a\u547d\u540d\u73a9\u5bb6"}\u201d\u8fdb\u5165\u5f53\u524d\u623f\u95f4\u3002\u4f60\u53ef\u4ee5\u76f4\u63a5\u7ee7\u7eed\u539f\u6765\u7684\u5e2d\u4f4d\uff0c\u6216\u8005\u76f4\u63a5\u7528\u5f53\u524d\u6635\u79f0\u201c${effectiveProfileName()}\u201d\u4f5c\u4e3a\u53e6\u4e00\u4f4d\u73a9\u5bb6\u52a0\u5165\u3002`;
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

  if (!showLobby) {
    title.textContent = `\u52a0\u5165\u623f\u95f4 ${state.room.room_id}`;
    caption.textContent = `\u8fd9\u4e2a\u623f\u95f4\u4ecd\u5728\u7b49\u5f85\u73a9\u5bb6\u5360\u4f4d\u3002\u70b9\u51fb\u201c\u52a0\u5165\u623f\u95f4\u201d\u540e\uff0c\u5c31\u4f1a\u4ee5\u5f53\u524d\u6635\u79f0\u201c${effectiveProfileName()}\u201d\u8fdb\u5165\u623f\u95f4\u5927\u5385\u5f00\u59cb\u9009\u5c06\u3002`;
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
    : "\u53cc\u65b9\u73a9\u5bb6\u5728\u8fd9\u91cc\u5404\u81ea\u9009\u62e9\u81ea\u5df1\u7684\u6b66\u5c06\uff0c\u51c6\u5907\u5b8c\u6210\u540e\u5f00\u59cb\u5bf9\u5c40\u3002";

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
  startRoom.textContent = state.room.status === "finished" ? "\u91cd\u65b0\u5f00\u59cb\u9009\u5c06" : "\u5f00\u59cb\u5bf9\u5c40";

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
    item.innerHTML = "<strong>对局已结束</strong><p>所有行动已锁定，可返回选将重新开始。</p>";
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
    caption.textContent = "对局已结束，无法再进行连锁。";
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
  caption.textContent = `原动作：${sourceUnit?.name || "未知单位"} 的 ${chain.queued_action.display_name}`;
  skipBtn.classList.remove("hidden");

  const sourceItem = document.createElement("div");
  sourceItem.className = "queue-item";
  sourceItem.innerHTML = `
    <strong>${sourceUnit?.name || "未知单位"} · ${chain.queued_action.display_name}</strong>
    <p>速度 ${chain.queued_action.speed}。当前等待 ${currentReactor?.name || "响应方"} 选择连锁动作。</p>
  `;
  panel.append(sourceItem);

  if (currentOptions.length) {
    const optionsItem = document.createElement("div");
    optionsItem.className = "queue-item current-options";
    optionsItem.innerHTML = `
      <strong>${currentReactor?.name || "当前单位"} 可用连锁</strong>
      <p>${currentOptions.map((action) => `${action.action_name}（速度${action.chain_speed}）`).join(" / ")} / 不连锁</p>
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
  text.textContent = `玩家 ${state.battle.winner} 已获胜。战场上的行动与连锁都已锁定。你可以回到房间大厅，或者直接重新开始选将。`;
  if (rematch) {
    rematch.disabled = !Boolean(state.room?.can_rematch && state.room?.viewer_player_id !== null);
  }
  overlay.classList.remove("hidden");
}

function renderRoomActionButtons() {
  const surrenderBtn = $("surrender-battle");
  if (!surrenderBtn) return;
  const canSurrender = Boolean(
    hasBattle()
      && !isGameOver()
      && viewerPlayerId() !== null
      && state.screen === "battle",
  );
  surrenderBtn.classList.toggle("hidden", !canSurrender);
  surrenderBtn.disabled = !canSurrender;
}

function renderMessage() {
  const node = $("message");
  if (!state.battle) {
    node.textContent = hasRoom() ? "房间已建立，但对局还没开始。" : "尚未进入房间。";
    return;
  }
  if (isGameOver()) {
    node.textContent = `玩家 ${state.battle.winner} 已获胜。战场已锁定，可回到房间大厅查看本局房间。`;
    return;
  }
  if (!canInteract()) {
    node.textContent = `当前轮到玩家 ${inputPlayer()} 操作。你可以继续观察战场，等待对手行动完成。`;
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    node.textContent = `${unit?.name || "消失单位"} 需要先重新出现。请点击蓝色高亮的最近落点。`;
    return;
  }
  if (state.selectedActionCode === "mana_pull" && !state.stagedPayload?.targetUnitId) {
    node.textContent = "魔力牵引分两步：先选单位，再选落点。";
    return;
  }
  if (state.stagedPayload?.targetUnitId && state.selectedActionCode === "mana_pull") {
    node.textContent = `已选中 ${stagedTarget()?.name || "被牵引目标"}，请点击蓝色高亮落点。`;
    return;
  }
  if (isChainMode()) {
    const current = unitById(state.battle.pending_chain?.current_unit_id || "");
    const source = unitById(state.battle.pending_chain?.queued_action?.actor_id || "");
    const actionName = state.battle.pending_chain?.queued_action?.display_name || "原动作";
    node.textContent = `${current?.name || "当前单位"} 可以对 ${source?.name || "对方单位"} 的【${actionName}】进行连锁，点击其周围动作按钮或放弃连锁。`;
    return;
  }
  const action = actionByCode(state.selectedActionCode);
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
  renderResumePanel();
  renderRoomList();
  renderHeroCards();
  renderHeader();
  renderMessage();
  renderBattleEffects();
  renderBoard();
  renderBoardAlert();
  renderActionWheel();
  renderHoverCard();
  renderSelectedCard();
  renderUnitStrip();
  renderChainPanel();
  renderLogs();
  renderGameOverOverlay();
  renderRoomActionButtons();
  renderTargetCancelButton();
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
      const staleRoomId = roomQueryId();
      clearStoredIdentity(staleRoomId);
      state.playerToken = "";
      state.room = null;
      state.battle = null;
      state.roomForm.joinRoomCode = "";
      syncLocation("draft", "");
      try {
        const payload = await fetchJson("/api/heroes");
        state.heroes = payload.heroes;
        state.rooms = payload.rooms || [];
      } catch {
        state.rooms = [];
      }
      syncScreen({ preferBattle: false });
      render();
      $("lobby-caption").textContent = error.error || "加载房间状态失败。";
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
    $("lobby-caption").textContent = "这个房间没有可继续的旧身份，请直接使用当前昵称加入。";
    return;
  }
  state.playerToken = identity.token;
  syncLocation("draft", roomId);
  refreshState({ preserveScreen: false }).then(() => {
    if (!viewerPlayerId()) {
      clearStoredIdentity(roomId);
      state.playerToken = "";
      $("lobby-caption").textContent = "之前保存的房间身份已经失效，请直接使用当前昵称重新加入。";
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
  if (!window.confirm(`确定要删除房间 ${state.room.room_id} 吗？删除后双方都需要重新建房。`)) {
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
  if (!window.confirm(`确定要离开房间 ${leftRoomId} 吗？${seatLabel} 将返回大厅。`)) {
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
      ? `你已离开房间 ${leftRoomId}，该房间因已无玩家而被关闭。`
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
  if (!window.confirm("确定要投降并立刻结束这局对战吗？")) {
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

async function selectRoomHero(heroCode) {
  if (!hasRoom() || !state.playerToken) return;
  try {
    const payload = await fetchJson("/api/rooms/select-hero", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        hero_code: heroCode,
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
    $("lobby-caption").textContent = "邀请链接已复制，发给另一位玩家就能加入同一房间。";
  } catch {
    $("lobby-caption").textContent = `请手动复制这个链接：${state.room.invite_url}`;
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
    if (state.battle?.active_units?.length) {
      state.selectedUnitId = state.battle.active_units[0].unit_id;
    }
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
    state.hoveredActionCode = "";
    state.hoveredBoardCell = null;
    state.stagedPayload = null;
  }
  render();
}

function onBoardClick(x, y, occupant) {
  if (!canInteract()) return;
  const preview = currentPreview();
  const action = state.selectedActionCode ? actionByCode(state.selectedActionCode) : null;
  const key = positionKey({ x, y });
  let canUseCell = preview.cellKeys.has(key);
  let canUseUnit = occupant ? preview.targetIds.has(occupant.id) : false;

  if (action && !canUseCell && !canUseUnit) {
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
    clearActionSelection();
    render();
    return;
  }

  if (isChainMode()) {
    const action = actionByCode(state.selectedActionCode);
    if (!action || !actionNeedsTarget(action)) return;
    if (!canUseCell && !canUseUnit) return;
    const payload = {
      type: "chain_react",
      unit_id: state.selectedUnitId,
      action_code: action.code,
    };
    if (occupant && canUseUnit) {
      payload.target_unit_id = occupant.id;
    } else if (canUseCell) {
      payload.x = x;
      payload.y = y;
    }
    performAction(payload);
    return;
  }

  if (!action) {
    clearActionSelection();
    render();
    return;
  }

  if (!canUseCell && !canUseUnit) {
    clearActionSelection();
    state.selectedUnitId = occupant?.id || "";
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

  if (action.code === "attack") {
    performAction({
      type: "attack",
      unit_id: state.selectedUnitId,
      target_unit_id: occupant.id,
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

  if (action.code === "pierce") {
    const chosenCells = stagedPierceCells();
    if (!chosenCells.length) {
      state.stagedPayload = { pierceCells: [{ x, y }] };
      render();
      return;
    }
    if (sameCell(chosenCells[0], { x, y })) {
      state.stagedPayload = null;
      render();
      return;
    }
    performAction({
      type: "skill",
      unit_id: state.selectedUnitId,
      skill_code: action.code,
      x: chosenCells[0].x,
      y: chosenCells[0].y,
      second_x: x,
      second_y: y,
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
  $("leave-room").addEventListener("click", leaveRoom);
  $("delete-room").addEventListener("click", deleteRoom);
  $("start-room").addEventListener("click", startRoomBattle);
  $("copy-invite").addEventListener("click", copyInviteLink);
  $("copy-invite-top").addEventListener("click", copyInviteLink);
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

window.addEventListener("DOMContentLoaded", async () => {
  hydrateStaticLabels();
  initializeProfileState();
  syncIdentityFromUrl();
  bindEvents();
  await refreshState({ preserveScreen: false });
  pollHandle = window.setInterval(() => {
    if (!roomQueryId()) {
      const active = document.activeElement;
      const typing = active && ["INPUT", "TEXTAREA"].includes(active.tagName);
      if (typing) return;
      refreshState({ preserveScreen: false });
      return;
    }
    refreshState();
  }, 1500);
});
