// Battle-room session bookkeeping and polling.
import { $ } from '../core/dom.js';
import { ensureSelectedUnit } from '../core/events.js';
import { activeBundles, allUnits, currentRespawnPrompt, isChainMode, isGameOver, isRespawnMode, roomNameKey, roomQueryId, roomTokenKey, syncLocation, unitById } from '../core/net.js';
import { state } from '../core/state.js';
import { syncScreen } from '../core/ui.js';

export function loadStoredIdentity(roomId) {
  if (!roomId) return { token: "", name: "" };
  const tokenKey = roomTokenKey(roomId);
  const nameKey = roomNameKey(roomId);
  return {
    token: localStorage.getItem(tokenKey) || sessionStorage.getItem(tokenKey) || "",
    name: localStorage.getItem(nameKey) || sessionStorage.getItem(nameKey) || "",
  };
}

export function clearStoredIdentity(roomId) {
  if (!roomId) return;
  const tokenKey = roomTokenKey(roomId);
  const nameKey = roomNameKey(roomId);
  sessionStorage.removeItem(tokenKey);
  sessionStorage.removeItem(nameKey);
  localStorage.removeItem(tokenKey);
  localStorage.removeItem(nameKey);
}

/**
 * 停止查看当前房间，但不放弃席位。
 *
 * 房间大厅的显示只看"手上有没有房间"，不看玩家在哪个流程里。所以只要地址栏
 * 还挂着 ?room=，从主菜单选战役也会被房间大厅盖掉——看上去就像点战役跳进了
 * 遭遇战。这里把房间从视图里摘出去；服务端的房间、以及本地存的席位身份都还在，
 * 通过房间列表或邀请链接可以回去。
 */
export function leaveRoomView() {
  state.playerToken = "";
  state.room = null;
  state.battle = null;
  state.liveBattle = null;
  state.replayMode = false;
  state.replayStepIndex = 0;
  state.replayOmniscient = false;
  state.selectedUnitId = "";
  state.inspectedUnitId = "";
  clearActionSelection();
  syncLocation(state.screen, "");
}

export function resetRoomSession({ rooms = state.rooms, roomId = roomQueryId() } = {}) {
  clearStoredIdentity(roomId);
  state.playerToken = "";
  state.room = null;
  state.battle = null;
  state.liveBattle = null;
  state.replayMode = false;
  state.replayStepIndex = 0;
  state.replayOmniscient = false;
  state.roomError = "";
  state.selectedUnitId = "";
  state.inspectedUnitId = "";
  state.roomForm.joinRoomCode = "";
  state.rooms = rooms || [];
  clearActionSelection();
  syncLocation("draft", "");
  syncScreen({ preferBattle: false });
}

export function saveStoredIdentity(roomId, token, name) {
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

export function syncIdentityFromUrl() {
  const roomId = roomQueryId();
  if (!roomId || state.playerToken) return;
  const identity = loadStoredIdentity(roomId);
  if (identity.token) {
    state.playerToken = identity.token;
  }
}

export function ensureDraftSelection() {
  return;
}

export function syncSelectedUnitAfterStateChange() {
  if (!state.battle) {
    state.selectedUnitId = "";
    state.inspectedUnitId = "";
    return;
  }
  if (state.inspectedUnitId && !unitById(state.inspectedUnitId)) {
    state.inspectedUnitId = "";
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

export function clearActionSelection() {
  state.selectedActionCode = "";
  state.selectedActionSnapshot = null;
  state.hoveredActionCode = "";
  state.hoveredUnitId = "";
  state.hoverPointer = null;
  state.hoveredBoardCell = null;
  state.stagedPayload = null;
}

// 这里原本在启动时把二十来个按钮和段落的文案重写一遍，而每一条 index.html
// 里都已经写过。结果是改文案要改两处，且 JS 那份总会覆盖标记——新加的按钮
// 文案会莫名其妙地被改回旧词。文案归标记所有，这里只保留标记表达不了的部分。
export function hydrateStaticLabels() {
  document.title = "\u6b66\u5c06";
}
