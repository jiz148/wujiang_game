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
  authToken: "",
  authUser: null,
  authUsername: "",
  authPassword: "",
  authMessage: "",
  authBusy: false,
  strategyCampaigns: [],
  strategyCampaign: null,
  strategyBattleRoom: null,
  strategyName: "英灵城邦",
  strategySeed: "1",
  strategyPlayerCount: "2",
  strategyJoinCode: "",
  strategyMessage: "",
  strategyBusy: false,
  strategySelectedCityId: "",
  strategyActiveOfficeId: "",
  strategyCommandDrafts: {},
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
  rightRailCollapsed: false,
  strategyRouteIntelOpen: false,
  strategyDossierOpen: false,
  strategyDossierTab: "",
  floatingToasts: [],
  lastToastLogCount: 0,
  aiPreview: null,
  homeFlow: "",
  quickStartBusy: false,
  resumableTutorial: null,
  tutorialResumeError: "",
  onboarding: {beginner_heroes: [], recommended_rosters: [], hero_discovery: []},
  showFullRoster: false,
  heroSearchQuery: "",
  heroRoleFilter: "",
  heroDifficultyFilter: "",
  connectionLostAt: 0,
  reconnectedAt: 0,
  lastTurnTimeoutAt: 0,
  tutorialGuideCollapsed: false,
  tutorialHistoryOffset: 0,
  tutorialCompletionRecorded: false,
  lastCompletedMatch: null,
  recentMatches: [],
  recentMatchesBusy: false,
  recentMatchesError: "",
  progression: null,
  progressionBusy: false,
  progressionError: "",
  historicalMatchId: "",
  lastHistorySyncMatchId: "",
};

const ROOM_TOKEN_PREFIX = "wujiang-room-token:";
const ROOM_NAME_PREFIX = "wujiang-room-name:";
const PROFILE_NAME_KEY = "wujiang-profile-name";
const PROFILE_READY_KEY = "wujiang-profile-ready";
const AUTH_TOKEN_KEY = "wujiang-auth-token";
const ANALYTICS_SESSION_KEY = "wujiang-analytics-session";
const LAST_TUTORIAL_ROOM_KEY = "wujiang-last-tutorial-room";
const RECORDED_MATCH_ENDS_KEY = "wujiang-recorded-match-ends";
const LAST_COMPLETED_MATCH_KEY = "wujiang-last-completed-match";
let pollHandle = null;
let nextHomePollAt = 0;
let lastHomeRenderSignature = "";
let refreshInFlight = false;
let boardOverlayRenderHandle = 0;
let battleVfxCleanupHandle = 0;
let boardDragState = null;
let boardDragSuppressUntil = 0;
let tooltipHideHandle = 0;
let keyboardHelpReturnFocus = null;

const $ = (id) => document.getElementById(id);

async function fetchJson(url, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(state.authToken ? { Authorization: `Bearer ${state.authToken}` } : {}),
    ...(options.headers || {}),
  };
  const response = await fetch(url, {
    ...options,
    headers,
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

async function recordProductEvent(eventName, properties = {}) {
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

function recordStrategyEventOnce(campaign, suffix, eventName, properties = {}) {
  if (!campaign?.id) return;
  const key = `wujiang-strategy-analytics-${campaign.id}-${suffix}`;
  if (localStorage.getItem(key)) return;
  localStorage.setItem(key, "1");
  recordProductEvent(eventName, { campaign_id: String(campaign.id), ...properties });
}

function recordStrategyConclusionIfNeeded(campaign) {
  const conclusion = campaign?.world?.strategic_status?.conclusion;
  if (!conclusion?.state) return;
  recordStrategyEventOnce(campaign, "complete", "strategy_campaign_complete", {
    month: String(conclusion.concluded_month || campaign.world?.current_month || ""),
    reason: conclusion.reason || "unknown",
  });
}

function toggleSidebarPanel(panel) {
  if (panel !== "logs") return;
  state.rightRailCollapsed = !state.rightRailCollapsed;
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
    || right.ridden_by_unit_id === left.id
    || (left.allow_enemy_destination_overlap && left.player_id !== right.player_id)
    || (right.allow_enemy_destination_overlap && right.player_id !== left.player_id);
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
  return Boolean(state.authUser) && (state.profileModalOpen || !state.profileReady);
}

function userLoggedIn() {
  return Boolean(state.authUser);
}

function requireAuthForRoomEntry() {
  if (userLoggedIn()) return true;
  focusAuthGateForMode("武将对战房间");
  return false;
}

function focusAuthGateForMode(modeLabel = "游戏") {
  if (userLoggedIn()) return false;
  state.authMessage = `进入${modeLabel}前需要先登录或注册。`;
  renderAuthPanel();
  enqueueFloatingToast(state.authMessage);
  focusDraftTarget(".auth-card");
  const timer = window.setTimeout || (typeof setTimeout === "function" ? setTimeout : null);
  if (typeof timer === "function") timer(() => {
    const username = $("auth-username");
    if (username && typeof username.focus === "function") username.focus();
  }, 80);
  return true;
}

function openStrategyModeEntry() {
  state.homeFlow = "strategy";
  render();
  if (focusAuthGateForMode("英灵城邦战役")) return;
  focusDraftTarget("#strategy-panel");
}

function openDuelModeEntry() {
  state.homeFlow = "custom";
  render();
  if (focusAuthGateForMode("武将对战房间")) return;
  focusDraftTarget(".room-home-grid");
}

function openQuickStartEntry() {
  state.homeFlow = "quick";
  recordProductEvent("quick_start_click", {
    entry_state: userLoggedIn() ? "logged_in" : "anonymous",
  });
  render();
  if (focusAuthGateForMode("新手教学")) return;
  focusDraftTarget("#quick-start-panel");
}

function renderHomeFlow() {
  const flow = state.homeFlow;
  const authCard = document.querySelector(".auth-card");
  const quickPanel = $("quick-start-panel");
  const strategyPanel = $("strategy-panel");
  const customSections = document.querySelectorAll(".home-custom-section");
  const tutorialButton = $("start-tutorial");
  const quickAiButton = $("start-quick-ai");
  const resumeButton = $("resume-tutorial");
  const resumeNote = $("tutorial-resume-note");
  const canResume = Boolean(state.resumableTutorial);
  if (authCard) authCard.classList.toggle("hidden", !flow || userLoggedIn());
  if (quickPanel) quickPanel.classList.toggle("hidden", flow !== "quick");
  if (strategyPanel) strategyPanel.classList.toggle("hidden", flow !== "strategy");
  customSections.forEach((node) => node.classList.toggle("hidden", flow !== "custom"));
  if (tutorialButton) {
    tutorialButton.disabled = state.quickStartBusy || !userLoggedIn();
    tutorialButton.textContent = state.quickStartBusy
      ? "正在准备教学..."
      : (canResume ? "重新开始教学" : "进入新手教学");
    tutorialButton.classList.remove("primary");
    tutorialButton.classList.add("ghost");
  }
  if (quickAiButton) {
    quickAiButton.disabled = state.quickStartBusy || !userLoggedIn();
    quickAiButton.textContent = state.quickStartBusy ? "正在准备对战..." : "快速 AI 对战";
  }
  if (resumeButton) {
    resumeButton.classList.toggle("hidden", !canResume);
    resumeButton.disabled = state.quickStartBusy || !userLoggedIn();
    resumeButton.textContent = state.quickStartBusy ? "正在恢复教学..." : "继续未完成教学";
  }
  if (resumeNote) {
    if (canResume) {
      const stepTitle = state.resumableTutorial.step_title || "上次步骤";
      resumeNote.textContent = `发现未完成教学：${stepTitle}。你可以继续当前进度，也可以重新开始。`;
    } else if (state.tutorialResumeError) {
      resumeNote.textContent = state.tutorialResumeError;
    } else {
      resumeNote.textContent = "固定阵容和地图，依次练习选中、移动、普攻、技能、连锁响应和结束回合。";
    }
  }
}

function clearResumableTutorial() {
  state.resumableTutorial = null;
  state.tutorialResumeError = "";
  localStorage.removeItem(LAST_TUTORIAL_ROOM_KEY);
}

async function refreshResumableTutorial() {
  const roomId = String(localStorage.getItem(LAST_TUTORIAL_ROOM_KEY) || "").trim();
  if (!roomId) {
    state.resumableTutorial = null;
    state.tutorialResumeError = "";
    return;
  }
  const identity = loadStoredIdentity(roomId);
  if (!identity.token) {
    clearResumableTutorial();
    return;
  }
  try {
    const query = new URLSearchParams({room_id: roomId, player_token: identity.token});
    const payload = await fetchJson(`/api/rooms/state?${query.toString()}`);
    const room = payload.room || {};
    const tutorial = room.tutorial || null;
    const resumable = room.experience_kind === "tutorial"
      && Boolean(tutorial)
      && !tutorial.completed_at
      && !payload.battle?.winner
      && room.viewer_player_id !== null
      && room.viewer_player_id !== undefined;
    if (!resumable) {
      clearResumableTutorial();
      return;
    }
    state.resumableTutorial = {
      room_id: roomId,
      player_token: identity.token,
      step_id: tutorial.step_id || "",
      step_title: tutorial.step?.title || tutorial.step_title || "上次步骤",
    };
    state.tutorialResumeError = "";
  } catch (_error) {
    state.resumableTutorial = null;
    state.tutorialResumeError = "暂时无法检查上次教学进度；你仍可重新开始，稍后也可以再次检查。";
  }
}

function normalizeAuthUsername(username) {
  return String(username || "").trim().replace(/\s+/g, " ").slice(0, 32);
}

function initializeAuthState() {
  state.authToken = localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

function clearAuthSession(message = "") {
  state.authToken = "";
  state.authUser = null;
  state.authPassword = "";
  state.authMessage = message;
  state.recentMatches = [];
  state.recentMatchesError = "";
  state.progression = null;
  state.progressionError = "";
  clearStrategyState();
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

function saveAuthSession(sessionToken, user) {
  state.authToken = sessionToken || "";
  state.authUser = user || null;
  state.authPassword = "";
  if (state.authToken) {
    localStorage.setItem(AUTH_TOKEN_KEY, state.authToken);
  }
  if (user?.username) {
    saveProfileName(user.username);
  }
}

async function refreshAuthSession() {
  if (!state.authToken) return;
  try {
    const payload = await fetchJson(`/api/auth/me?session_token=${encodeURIComponent(state.authToken)}`);
    if (payload.user) {
      saveAuthSession(state.authToken, payload.user);
    } else {
      clearAuthSession();
    }
  } catch (error) {
    clearAuthSession(error.error || "登录状态已失效。");
  }
}

async function refreshRecentMatches({renderAfter = true} = {}) {
  if (!userLoggedIn()) {
    state.recentMatches = [];
    state.recentMatchesError = "";
    if (renderAfter) renderRecentMatches();
    return;
  }
  if (state.recentMatchesBusy) return;
  state.recentMatchesBusy = true;
  state.recentMatchesError = "";
  if (renderAfter) renderRecentMatches();
  try {
    const payload = await fetchJson("/api/matches/recent");
    state.recentMatches = payload.matches || [];
  } catch (error) {
    state.recentMatchesError = error.error || "读取最近战绩失败。";
  } finally {
    state.recentMatchesBusy = false;
    await refreshProgression({renderAfter: false});
    if (renderAfter) renderRecentMatches();
  }
}

async function refreshProgression({renderAfter = true} = {}) {
  if (!userLoggedIn()) {
    state.progression = null;
    state.progressionError = "";
    if (renderAfter) renderRecentMatches();
    return;
  }
  if (state.progressionBusy) return;
  state.progressionBusy = true;
  state.progressionError = "";
  if (renderAfter) renderRecentMatches();
  try {
    const payload = await fetchJson("/api/progression/overview");
    state.progression = payload.progression || null;
    trackAnalytics("progression_view", {
      source: state.screen === "battle" ? "postgame" : "home",
      empty_state: !Number(state.progression?.total_matches || 0),
    });
  } catch (error) {
    state.progressionError = error.error || "读取武将熟练度失败。";
  } finally {
    state.progressionBusy = false;
    if (renderAfter) {
      if (state.screen === "battle") renderPostgameSummary();
      else renderRecentMatches();
    }
  }
}

async function submitAuth(mode) {
  if (state.authBusy) return;
  const username = normalizeAuthUsername(state.authUsername);
  const password = state.authPassword;
  if (!username || !password) {
    state.authMessage = "请输入用户名和密码。";
    renderAuthPanel();
    return;
  }
  state.authBusy = true;
  state.authMessage = mode === "register" ? "正在注册..." : "正在登录...";
  renderAuthPanel();
  try {
    const payload = await fetchJson(`/api/auth/${mode}`, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    saveAuthSession(payload.session_token, payload.user);
    state.authUsername = "";
    state.authMessage = mode === "register" ? "注册成功，已登录。" : "登录成功。";
    await refreshRecentMatches({renderAfter: false});
    await refreshStrategyCampaigns({ renderAfter: false });
  } catch (error) {
    state.authMessage = error.error || "账号操作失败。";
  } finally {
    state.authBusy = false;
    render();
  }
}

async function logoutAuth() {
  if (state.authBusy) return;
  state.authBusy = true;
  renderAuthPanel();
  try {
    await fetchJson("/api/auth/logout", {
      method: "POST",
      body: JSON.stringify({ session_token: state.authToken }),
    });
  } catch (error) {
    state.authMessage = error.error || "退出登录时出现问题。";
  } finally {
    clearAuthSession("已退出登录。");
    state.authBusy = false;
    render();
  }
}

function clearStrategyState(message = "") {
  state.strategyCampaigns = [];
  state.strategyCampaign = null;
  state.strategyBattleRoom = null;
  state.strategyMessage = message;
}

function syncStrategyCampaignFromRoomPayload(payload = {}) {
  const campaign = payload.strategy_campaign;
  if (!campaign) return;
  state.strategyCampaign = campaign;
  const campaigns = Array.isArray(state.strategyCampaigns) ? state.strategyCampaigns.slice() : [];
  const index = campaigns.findIndex((item) => Number(item.id) === Number(campaign.id));
  if (index >= 0) {
    campaigns[index] = campaign;
  } else {
    campaigns.unshift(campaign);
  }
  state.strategyCampaigns = campaigns;
  if (payload.room?.status === "finished" || payload.battle?.winner) {
    state.strategyMessage = "真实战斗已结束，战役结算已同步。";
  }
}

async function refreshStrategyCampaigns({ renderAfter = true } = {}) {
  if (!userLoggedIn()) {
    clearStrategyState("请先登录账号。");
    if (renderAfter && !isStrategyControlActive()) renderStrategyPanel();
    return;
  }
  try {
    const payload = await fetchJson("/api/strategy/campaigns");
    state.strategyCampaigns = payload.campaigns || [];
    if (state.strategyCampaign) {
      state.strategyCampaign = state.strategyCampaigns.find((campaign) => campaign.id === state.strategyCampaign.id) || state.strategyCampaign;
    }
  } catch (error) {
    state.strategyMessage = error.error || "读取战役列表失败。";
  } finally {
    if (renderAfter && !isStrategyControlActive()) renderStrategyPanel();
  }
}

async function strategyPost(path, body) {
  if (state.strategyBusy) return null;
  if (!userLoggedIn()) {
    state.strategyMessage = "请先登录账号。";
    renderStrategyPanel();
    return null;
  }
  state.strategyBusy = true;
  try {
    return await fetchJson(path, {
      method: "POST",
      body: JSON.stringify(body),
    });
  } catch (error) {
    state.strategyMessage = error.error || "战略操作失败。";
    return null;
  } finally {
    state.strategyBusy = false;
  }
}

function focusStrategyWarRoom() {
  const run = () => {
    const target = (document.querySelector && document.querySelector(".strategy-war-room")) || $("strategy-panel");
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "start", inline: "nearest" });
    }
  };
  if (window.requestAnimationFrame) {
    window.requestAnimationFrame(run);
  } else {
    run();
  }
}

function focusDraftTarget(selector) {
  const target = document.querySelector ? document.querySelector(selector) : null;
  if (target && typeof target.scrollIntoView === "function") {
    target.scrollIntoView({ block: "start", inline: "nearest" });
  }
}

function focusStrategyCommandPanel() {
  focusDraftTarget(".strategy-command-panel");
}

function focusStrategyMapStage() {
  focusDraftTarget(".strategy-map-stage");
}

function focusStrategyDossier() {
  focusDraftTarget(".strategy-dossier");
}

async function createStrategyCampaign() {
  const name = String(state.strategyName || "英灵城邦").trim() || "英灵城邦";
  const seed = Number.parseInt(state.strategySeed || "1", 10) || 1;
  const payload = await strategyPost("/api/strategy/campaigns/create", {
    name,
    seed,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  recordProductEvent("strategy_campaign_create", {
    campaign_id: String(payload.campaign.id),
    scenario_id: payload.campaign.world?.campaign_contract?.id || "legacy_sandbox",
  });
  state.strategyMessage = "战役大厅已创建。可以分享加入码；房主锁定后，空席会由 AI 接管。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
  focusStrategyWarRoom();
}

async function joinStrategyCampaignByCode() {
  const joinCode = String(state.strategyJoinCode || "").trim().toUpperCase();
  if (!joinCode) {
    state.strategyMessage = "请输入战役加入码。";
    renderStrategyPanel();
    return;
  }
  const payload = await strategyPost("/api/strategy/campaigns/join", { join_code: joinCode });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyJoinCode = "";
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = "已加入战役大厅。等待房主锁定初始玩家。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
  focusStrategyWarRoom();
}

async function chooseStrategyHeroPath(heroCode, path, targetFactionId = "") {
  if (!state.strategyCampaign || !heroCode || !path) return;
  const payload = await strategyPost("/api/strategy/campaigns/choose-hero-path", {
    campaign_id: state.strategyCampaign.id,
    hero_code: heroCode,
    path,
    target_faction_id: targetFactionId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  const messages = {
    lord: "你已以主公身份统领初始势力。",
    found: "你已在所在城市举旗，建立新的势力。",
    roaming: "你已成为在野武将，可以选择建国或投靠主公。",
    join: "投靠请求已经送到目标主公案前，获准前保持在野。",
  };
  state.strategyMessage = messages[path] || "武将道路已更新。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
  focusStrategyWarRoom();
}

async function updateStrategyCampaignGuide(action) {
  if (!state.strategyCampaign) return false;
  const payload = await strategyPost("/api/strategy/campaigns/guide-action", {
    campaign_id: state.strategyCampaign.id,
    action,
  });
  if (!payload) {
    renderStrategyPanel();
    return false;
  }
  state.strategyCampaign = payload.campaign;
  if (action === "survey_border") {
    state.strategyMessage = "已完成引导目标：查看边境。";
  } else if (action === "skip") {
    state.strategyMessage = "已跳过前三个月情境引导；战役规则、资源和月份均未改变。";
  }
  render();
  return true;
}

async function lockStrategyCampaign(campaignId) {
  const payload = await strategyPost("/api/strategy/campaigns/lock", { campaign_id: campaignId });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  recordProductEvent("strategy_campaign_lock", { campaign_id: String(payload.campaign.id) });
  state.strategyMessage = strategyCanResume(payload.campaign)
    ? "初始玩家已锁定，空席由 AI 接管，战役可以继续。"
    : "初始玩家已锁定，等待所有真人初始玩家在线。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
  focusStrategyWarRoom();
}

async function rotateStrategyJoinCode(campaignId) {
  const payload = await strategyPost("/api/strategy/campaigns/rotate-join-code", { campaign_id: campaignId });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = `加入码已更新：${payload.campaign.join_code || "未生成"}`;
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

async function enterStrategyCampaign(campaignId) {
  const payload = await strategyPost("/api/strategy/campaigns/enter", { campaign_id: campaignId });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  recordProductEvent("strategy_campaign_enter", { campaign_id: String(payload.campaign.id) });
  state.strategyMessage = strategyCanResume(payload.campaign) ? "战役已就绪。" : "已进入战役，等待初始玩家到齐。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
  focusStrategyWarRoom();
}

async function leaveStrategyCampaign(campaignId) {
  const payload = await strategyPost("/api/strategy/campaigns/leave", { campaign_id: campaignId });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  if (state.strategyCampaign?.id === campaignId) {
    state.strategyCampaign.resume = payload.resume;
  }
  state.strategyMessage = "已离开战役在线状态。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

async function advanceStrategyMonth() {
  if (!state.strategyCampaign) {
    state.strategyMessage = "请先选择一个战役。";
    renderStrategyPanel();
    return;
  }
  const queuedBattles = (state.strategyCampaign.queued_actions || []).filter((action) => action.action_type === "city_attack");
  const payload = await strategyPost("/api/strategy/campaigns/advance-month", {
    campaign_id: state.strategyCampaign.id,
    issuer_office_id: state.strategyActiveOfficeId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  const status = payload.campaign?.world?.strategic_status || {};
  const reachedMonth = Number(payload.campaign?.world?.current_month || 0);
  queuedBattles.forEach((action, index) => {
    recordStrategyEventOnce(payload.campaign, `battle-${action.id || action.action_key || `${reachedMonth}-${index}`}`, "strategy_battle_trigger", {
      month: String(action.month || Math.max(1, reachedMonth - 1)),
      resolution_mode: action.payload?.resolution_mode || "quick",
    });
  });
  if ([3, 6, 9, 12].includes(reachedMonth)) {
    recordStrategyEventOnce(payload.campaign, `month-${reachedMonth}`, "strategy_campaign_milestone", {
      month: String(reachedMonth),
    });
  }
  recordStrategyConclusionIfNeeded(payload.campaign);
  state.strategyMessage = status.awaiting_conclusion_choice
    ? `第 ${payload.campaign.world.current_month} 月结算完成，战役已进入${status.conclusion?.result_label || "评议"}。`
    : `已推进到第 ${payload.campaign.world.current_month} 月。`;
  state.strategyBattleRoom = (payload.battle_rooms || []).slice(-1)[0] || state.strategyBattleRoom;
  if (state.strategyBattleRoom?.player_token) {
    saveStoredIdentity(state.strategyBattleRoom.room_id, state.strategyBattleRoom.player_token, effectiveProfileName());
  }
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

async function continueStrategySandbox() {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/continue-sandbox", {
    campaign_id: state.strategyCampaign.id,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  recordStrategyEventOnce(payload.campaign, "continue-sandbox", "strategy_campaign_continue_sandbox", {
    month: String(payload.campaign.world?.current_month || ""),
  });
  state.strategyMessage = "已保留战役评议结果，并转入自由沙盒。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

async function archiveStrategyCampaign() {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/archive", {
    campaign_id: state.strategyCampaign.id,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  recordStrategyEventOnce(payload.campaign, "archive", "strategy_campaign_archive", {
    month: String(payload.campaign.world?.current_month || ""),
  });
  state.strategyMessage = "战役已结束归档；结局与完整复盘已冻结保存。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

async function setStrategyCityPolicy(cityId, policy) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/set-city-policy", {
    campaign_id: state.strategyCampaign.id,
    city_id: cityId,
    policy,
    issuer_office_id: state.strategyActiveOfficeId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = "城市方针已更新。";
  render();
}

async function unlockStrategyTech(techId) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/unlock-tactic-tech", {
    campaign_id: state.strategyCampaign.id,
    tech_id: techId,
    issuer_office_id: state.strategyActiveOfficeId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = "战术科技已解锁。";
  render();
}

async function setStrategyDefenseHero(heroCode) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/set-defense-hero", {
    campaign_id: state.strategyCampaign.id,
    hero_code: heroCode || "",
    issuer_office_id: state.strategyActiveOfficeId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = heroCode ? "防守英灵已设置。" : "防守英灵已恢复自动配置。";
  render();
}

async function setStrategyBattleDefenseHero(battleId, heroCode) {
  if (!state.strategyCampaign || !battleId) return;
  const heroCodes = Array.isArray(heroCode) ? heroCode : (heroCode ? [heroCode] : []);
  const payload = await strategyPost("/api/strategy/campaigns/set-battle-defense-hero", {
    campaign_id: state.strategyCampaign.id,
    battle_id: battleId,
    hero_codes: heroCodes,
    issuer_office_id: state.strategyActiveOfficeId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = heroCodes.length ? "本场防守英灵已设置。" : "本场防守英灵已设为不投入。";
  render();
}

async function declareStrategyAttack(sourceCityId, targetCityId, resolutionMode, attackerHeroCodes = []) {
  if (!state.strategyCampaign || !sourceCityId || !targetCityId) return;
  const payload = await strategyPost("/api/strategy/campaigns/declare-attack", {
    campaign_id: state.strategyCampaign.id,
    source_city_id: sourceCityId,
    target_city_id: targetCityId,
    resolution_mode: resolutionMode || "quick",
    attacker_hero_codes: attackerHeroCodes,
    issuer_office_id: state.strategyActiveOfficeId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  recordProductEvent("strategy_battle_trigger", {
    campaign_id: String(payload.campaign.id),
    month: String(payload.campaign.world?.current_month || ""),
    resolution_mode: resolutionMode || "quick",
  });
  recordStrategyConclusionIfNeeded(payload.campaign);
  state.strategyBattleRoom = payload.battle_room || null;
  if (payload.battle_room?.player_token) {
    saveStoredIdentity(payload.battle_room.room_id, payload.battle_room.player_token, effectiveProfileName());
  }
  if (payload.battle_room) {
    state.strategyMessage = resolutionMode === "watch_ai"
      ? "已创建 AI 观战房间，可进入观看真实格子战。"
      : "已创建真实格子战房间，可进入战场手动处理。";
  } else {
    state.strategyMessage = "战斗已结算并写入战役事件。";
  }
  render();
}

async function queueStrategyAction(actionType, actionPayload) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/queue-action", {
    campaign_id: state.strategyCampaign.id,
    action_type: actionType,
    action_payload: {
      ...(actionPayload || {}),
      issuer_office_id: state.strategyActiveOfficeId,
    },
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  const submission = payload.submission || {};
  const points = submission.command_points || strategyFactionCommandPoints(payload.campaign);
  const resources = submission.resource_balance || {};
  const balance = `粮 ${resources.food ?? "?"} · 钱 ${resources.money ?? "?"} · 以太 ${resources.ether ?? "?"} · 兵 ${resources.troops ?? "?"}`;
  const affected = (submission.affected_months || []).map((month) => `第 ${month} 月`).join("、");
  state.strategyMessage = submission.replaced
    ? `已替换原计划；剩余军令 ${points.remaining}/${points.maximum}，当前资源 ${balance}，影响 ${affected || "本次月结"}。`
    : `已加入本月计划；剩余军令 ${points.remaining}/${points.maximum}，当前资源 ${balance}，影响 ${affected || "本次月结"}。`;
  if (submission.execution) {
    const executor = (state.strategyCampaign?.world?.offices || []).find((office) => office.id === submission.execution.executor_office_id);
    state.strategyMessage += ` 执行者：${strategyOfficeLabel(executor, state.strategyCampaign)}；成本 ${submission.execution.command_cost} 军令；预计第 ${submission.execution.expected_completion_month} 月回执。`;
  }
  await refreshStrategyCampaigns({ renderAfter: false });
  const queuedAction = (state.strategyCampaign?.queued_actions || []).slice(-1)[0];
  enqueueFloatingToast(queuedAction
    ? `${submission.replaced ? "计划已替换" : "军令已记录"}：${strategyQueuedActionLabel(state.strategyCampaign, queuedAction)}`
    : (submission.replaced ? "计划已替换。" : "军令已记录。"));
  render();
}

async function openStrategyBattleRoom(roomInfo = {}) {
  const roomId = String(roomInfo.room_id || roomInfo.battle_room_id || "").trim().toUpperCase();
  if (!roomId) {
    state.strategyMessage = "这场战斗还没有可进入的真实房间。";
    renderStrategyPanel();
    return;
  }
  const playerToken = String(roomInfo.player_token || loadStoredIdentity(roomId).token || "").trim();
  state.playerToken = playerToken;
  if (playerToken) {
    saveStoredIdentity(roomId, playerToken, effectiveProfileName());
  }
  state.roomForm.joinRoomCode = roomId;
  const joinInput = $("join-room-code");
  if (joinInput) joinInput.value = roomId;
  syncLocation("battle", roomId);
  await refreshState({ preserveScreen: false });
}

function returnToStrategyCampaign() {
  if (!state.strategyCampaign) return;
  state.strategyMessage = state.strategyMessage || "已返回战役。";
  setScreen("draft", { renderAfter: false });
  render();
  const panel = $("strategy-panel");
  if (panel && typeof panel.scrollIntoView === "function") {
    panel.scrollIntoView({ block: "start" });
  }
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
  if (screen !== "battle" && state.historicalMatchId) {
    state.historicalMatchId = "";
    state.room = null;
    state.battle = null;
    state.liveBattle = null;
    state.replayMode = false;
    state.replayStepIndex = 0;
    state.replayOmniscient = false;
    state.playerToken = "";
    state.screen = "draft";
    clearActionSelection();
    syncLocation("draft", "");
    refreshRecentMatches({renderAfter: false}).then(() => render());
    if (renderAfter) render();
    return;
  }
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
  const strategyButton = $("game-over-strategy");
  if (strategyButton) strategyButton.textContent = "\u8fd4\u56de\u6218\u5f79";
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
    return filterTutorialActions(reactions);
  }
  return filterTutorialActions((bundle.actions.actions || []).filter((action) => {
    if (!action.available) return false;
    if (action.kind === "move" || action.kind === "attack") return true;
    return action.timing === "active";
  }));
}

function tutorialState() {
  return state.room?.experience_kind === "tutorial" ? state.room?.tutorial || null : null;
}

function filterTutorialActions(actions) {
  const tutorial = tutorialState();
  if (!tutorial) return actions;
  const stepId = tutorial.step_id;
  let filtered = actions;
  if (stepId === "select_unit" || stepId === "end_turn") filtered = [];
  else if (stepId === "move") filtered = actions.filter((action) => action.code === "move");
  else if (stepId === "basic_attack") filtered = actions.filter((action) => action.kind === "attack");
  else if (stepId === "active_skill") {
    filtered = actions.filter((action) => action.code === "pierce").map((action) => ({
      ...action,
      preview: {
        ...(action.preview || {}),
        cells: [{x: 5, y: 4}, {x: 6, y: 4}],
        selection: {mode: "pattern_cells", patterns: [[{x: 5, y: 4}, {x: 6, y: 4}]], ordered: false},
      },
    }));
  }
  else if (stepId === "chain_response") filtered = actions.filter((action) => action.kind === "chain_skip" || action.timing === "reaction");
  if (stepId !== "move") return filtered;
  return filtered.map((action) => ({
    ...action,
    preview: {
      ...(action.preview || {}),
      cells: [{x: 4, y: 4}],
    },
  }));
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

function actionWheelLayer() {
  const stage = $("board-stage");
  if (!stage) return null;
  let layer = $("action-wheel");
  if (!layer) {
    layer = document.createElement("div");
    layer.id = "action-wheel";
    layer.className = "action-wheel";
  }
  if (layer.parentNode !== stage && typeof stage.appendChild === "function") {
    stage.appendChild(layer);
  }
  return layer;
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
  if (globalThis.WujiangBattleFeedback?.reducedMotion()) return 160;
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

function nodeRectRelativeToStage(node) {
  if (!node || typeof node.getBoundingClientRect !== "function") return null;
  const rect = node.getBoundingClientRect();
  const stageRect = $("board-stage")?.getBoundingClientRect?.();
  if (!rect || !stageRect) return null;
  const left = rect.left - stageRect.left;
  const top = rect.top - stageRect.top;
  return {
    left,
    top,
    width: rect.width,
    height: rect.height,
    right: left + rect.width,
    bottom: top + rect.height,
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

function unitBoundsRelativeToStage(unit) {
  const cells = unitOccupiedCells(unit);
  if (!cells.length) return null;
  const rects = cells.map((cell) => nodeRectRelativeToStage(boardCellNodeAt(cell.x, cell.y))).filter(Boolean);
  if (!rects.length) return null;
  return {
    left: Math.min(...rects.map((rect) => rect.left)),
    top: Math.min(...rects.map((rect) => rect.top)),
    right: Math.max(...rects.map((rect) => rect.right)),
    bottom: Math.max(...rects.map((rect) => rect.bottom)),
    width: Math.max(...rects.map((rect) => rect.right)) - Math.min(...rects.map((rect) => rect.left)),
    height: Math.max(...rects.map((rect) => rect.bottom)) - Math.min(...rects.map((rect) => rect.top)),
  };
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

function reviveUnitCellSelection(action) {
  return action?.preview?.selection?.mode === "revive_unit_cell" ? action.preview.selection : null;
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
  if (Number(selection.required_cells || 0) > 0 && (!Array.isArray(selection.patterns) || !selection.patterns.length)) {
    return [];
  }
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
  return normalizedMovePath(Array.isArray(state.stagedPayload?.path) ? state.stagedPayload.path : []);
}

function setStagedMovePath(path) {
  const normalized = normalizedMovePath(path);
  state.stagedPayload = normalized.length ? { path: normalized } : null;
}

function normalizedMovePath(path = []) {
  return path
    .filter((cell) => cell && cell.x != null && cell.y != null)
    .map((cell) => ({ x: Number(cell.x), y: Number(cell.y) }));
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

function stagedReviveUnitId(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !reviveUnitCellSelection(action)) return "";
  return String(state.stagedPayload?.reviveUnitId || "").trim();
}

function stagedReviveCell(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !reviveUnitCellSelection(action)) return null;
  const cell = state.stagedPayload?.cell;
  if (!cell || cell.x == null || cell.y == null) return null;
  return { x: Number(cell.x), y: Number(cell.y) };
}

function setStagedReviveUnitId(unitId) {
  const action = selectedAction();
  if (!reviveUnitCellSelection(action)) return;
  const next = String(unitId || "").trim();
  const current = next && next === stagedReviveUnitId(action) ? stagedReviveCell(action) : null;
  state.stagedPayload = next || current ? { ...(next ? { reviveUnitId: next } : {}), ...(current ? { cell: current } : {}) } : null;
}

function setStagedReviveCell(cell) {
  const reviveUnitId = stagedReviveUnitId();
  const next = cell && cell.x != null && cell.y != null ? { x: Number(cell.x), y: Number(cell.y) } : null;
  state.stagedPayload = reviveUnitId || next ? { ...(reviveUnitId ? { reviveUnitId } : {}), ...(next ? { cell: next } : {}) } : null;
}

function reviveCandidate(action, unitId = stagedReviveUnitId(action)) {
  const id = String(unitId || "").trim();
  return (reviveUnitCellSelection(action)?.candidates || []).find((entry) => String(entry.id || "") === id) || null;
}

function reviveSelectionCells(action) {
  const candidate = reviveCandidate(action);
  if (candidate) return candidate.cells || [];
  return action.preview?.cells || [];
}

function reviveUnitCellSelectionCanComplete(action) {
  const cell = stagedReviveCell(action);
  const unitId = stagedReviveUnitId(action);
  if (!reviveUnitCellSelection(action) || !unitId || !cell) return false;
  return positionsToSet(reviveSelectionCells(action)).has(positionKey(cell));
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
  return blockingOccupants.length > 0;
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
  for (let index = chosen.length - 1; index >= 0; index -= 1) {
    const anchor = chosen[index];
    if (sameCell(anchor, clickedCell)
      || unitFootprintCellsAt(selectedUnit(), anchor).some((cell) => sameCell(cell, clickedCell))) return index;
  }
  return -1;
}

function nextMovePathCells(action, chosen = stagedMovePath(action)) {
  const unit = selectedUnit();
  const head = movePathHead(action, chosen);
  const maxSteps = movePathMaxSteps(action);
  if (!unit?.position || !head || !state.battle || chosen.length >= maxSteps) return [];
  const next = [];
  for (let dx = -1; dx <= 1; dx += 1) {
    for (let dy = -1; dy <= 1; dy += 1) {
      if (dx === 0 && dy === 0) continue;
      const candidate = { x: head.x + dx, y: head.y + dy };
      if (!cellInBounds(candidate)) continue;
      if (cellBlockedForMover(unit, candidate) && !unit.ignore_units_while_moving) continue;
      next.push(candidate);
    }
  }
  return next;
}

function movePathCanComplete(action, chosen = stagedMovePath(action)) {
  const unit = selectedUnit();
  return Boolean(movePathSelection(action) && chosen.length && unit && !cellBlockedForMover(unit, chosen[chosen.length - 1]));
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
  const selection = patternSelection(action);
  const required = Number(selection?.required_cells || 0);
  if (required > 0 && (!Array.isArray(selection?.patterns) || !selection.patterns.length)) {
    if (chosen.length >= required) return [];
    const chosenKeys = positionsToSet(chosen);
    return (action.preview?.cells || []).filter((cell) => !chosenKeys.has(positionKey(cell)));
  }
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
  const selection = patternSelection(action);
  const required = Number(selection?.required_cells || 0);
  if (required > 0 && (!Array.isArray(selection?.patterns) || !selection.patterns.length)) {
    const legalKeys = positionsToSet(action.preview?.cells || []);
    return chosen.length === required && chosen.every((cell) => legalKeys.has(positionKey(cell)));
  }
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

  if (state.selectedActionCode === "descent_moment" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    return {
      cellKeys: positionsToSet(descentMomentDestinations(action, target)),
      targetIds: new Set(target ? [target.id] : []),
      secondaryCellKeys: positionsToSet(target ? unitOccupiedCells(target) : []),
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
    return {
      cellKeys: positionsToSet(activeCells),
      targetIds: new Set(),
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
  if (reviveUnitCellSelection(action)) {
    const selectedCell = stagedReviveCell(action);
    return {
      cellKeys: positionsToSet(reviveSelectionCells(action)),
      targetIds: new Set(),
      secondaryCellKeys: positionsToSet([...(action.preview?.secondary_cells || []), ...(selectedCell ? [selectedCell] : [])]),
      destinationCellKeys: new Set(),
    };
  }
  const useDirectTargetCells = action.kind === "attack"
    || (action.preview?.requires_target && ["ally", "enemy", "unit"].includes(action.target_mode));
  const previewCells = useDirectTargetCells
    ? (action.preview?.cells?.length ? action.preview.cells : previewCellsForTargetIds(filteredTargetIds))
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

function descentMomentDestinations(action, target) {
  if (!action || !target) return [];
  const mapping = action.preview?.destinations_by_target || {};
  const cells = mapping[target.id] || [];
  return Array.isArray(cells) ? cells : [];
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

function isBoardTargetSelectionActive() {
  if (!canInteract()) return false;
  if (isRespawnMode()) return true;
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
  if (reviveUnitCellSelection(action)) return reviveUnitCellSelectionCanComplete(action);
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
      : "登录后会默认使用账号名作为昵称;你也可以再修改一个更容易识别的显示名。";
  }
  if (createButton) createButton.disabled = !userLoggedIn() || !state.profileReady;
  if (joinButton) joinButton.disabled = !userLoggedIn() || !state.profileReady || !String(joinCode?.value || "").trim();
}

function connectionStatusLabel(status) {
  return ({online: "在线", unstable: "连接不稳", offline: "已掉线", ai: "AI 在线", open: "开放"})[status] || "未知";
}

function readyStateLabel(seat) {
  if (!seat?.occupied) return "未占用";
  if (seat.is_ai) return "自动准备";
  return seat.ready ? "已准备" : "未准备";
}

function renderAuthPanel() {
  const display = $("auth-display-name");
  const note = $("auth-display-note");
  const message = $("auth-message");
  const username = $("auth-username");
  const password = $("auth-password");
  const login = $("auth-login");
  const register = $("auth-register");
  const logout = $("auth-logout");
  if (!display || !note || !message || !username || !password || !login || !register || !logout) return;

  const loggedIn = Boolean(state.authUser);
  display.textContent = loggedIn ? state.authUser.username : "未登录";
  note.textContent = loggedIn
    ? "已登录。昵称用于游戏内显示；房间席位凭据只用于断线后恢复当前席位。"
    : "所有可玩模式都需要登录；昵称只用于游戏内显示，不能代替账号身份。";
  message.textContent = state.authMessage || "";
  username.value = state.authUsername;
  password.value = state.authPassword;
  username.disabled = state.authBusy || loggedIn;
  password.disabled = state.authBusy || loggedIn;
  login.disabled = state.authBusy || loggedIn;
  register.disabled = state.authBusy || loggedIn;
  logout.disabled = state.authBusy || !loggedIn;
  login.classList.toggle("hidden", loggedIn);
  register.classList.toggle("hidden", loggedIn);
  logout.classList.toggle("hidden", !loggedIn);
}

function recentMatchRosterText(match) {
  return globalThis.WujiangHomeUi?.rosterText(match) || "";
}

function renderRecentMatches() {
  globalThis.WujiangHomeUi?.renderRecentMatches({
    document,
    state,
    loggedIn: userLoggedIn(),
    onOpenReplay: openRecentReplay,
  });
}

async function openRecentReplay(matchId) {
  if (!matchId || !userLoggedIn()) return;
  try {
    const query = new URLSearchParams({match_id: matchId, step_index: "-1"});
    const payload = await fetchJson(`/api/matches/replay?${query.toString()}`);
    state.historicalMatchId = matchId;
    state.playerToken = "";
    applyRoomPayload(payload, {preserveScreen: false});
    state.historicalMatchId = matchId;
    state.replayMode = true;
    state.replayStepIndex = Number(payload.replay?.step_index || 0);
    state.replayOmniscient = true;
    state.battle = payload.battle || null;
    state.screen = "battle";
    syncLocation("battle", "");
    syncSelectedUnitAfterStateChange();
    render();
  } catch (error) {
    state.recentMatchesError = error.error || "历史回放加载失败。";
    renderRecentMatches();
  }
}

function strategyMember(campaign = state.strategyCampaign) {
  const userId = Number(state.authUser?.id || 0);
  return (campaign?.members || []).find((member) => Number(member.user_id) === userId) || null;
}

const STRATEGY_OFFICE_LABELS = {
  lord: "主公",
  grand_general: "大将军",
  general: "将军",
  governor: "城主",
};

const STRATEGY_DUTY_LABELS = {
  review_national_strategy: "审阅国家战略",
  review_office_vacancies: "检查职位空缺",
  review_subordinate_requests: "批阅下级请求",
  review_theater_security: "审阅战区安全",
  coordinate_generals: "协调属下将军",
  report_major_threats: "上报重大威胁",
  maintain_army_readiness: "维持军团战备",
  execute_military_orders: "执行军事命令",
  submit_battle_reports: "提交战斗报告",
  maintain_food_supply: "维持城市粮食",
  maintain_city_support: "维持城市民心",
  manage_local_defense: "管理地方防务",
};

const STRATEGY_OFFICE_STATUS_LABELS = {
  pending: "待处理",
  accepted: "已接受",
  completed: "已完成",
  rejected: "已拒绝",
  cancelled: "已撤销",
};

function strategyControlledOffices(campaign = state.strategyCampaign) {
  const userId = Number(state.authUser?.id || 0);
  const officeOrder = { lord: 0, grand_general: 1, general: 2, governor: 3 };
  return (campaign?.world?.offices || []).filter((office) => (
    office.controller_type === "player"
      && Number(office.controller_user_id || 0) === userId
      && office.status === "active"
  )).sort((first, second) => (
    (officeOrder[first.office_type] ?? 9) - (officeOrder[second.office_type] ?? 9)
      || strategyOfficeLabel(first, campaign).localeCompare(strategyOfficeLabel(second, campaign), "zh-CN")
  ));
}

function strategyControlledHero(campaign = state.strategyCampaign) {
  const userId = Number(state.authUser?.id || 0);
  return (campaign?.world?.strategic_hero_pool || []).find((hero) => (
    hero.controller_type === "player" && Number(hero.controller_user_id || 0) === userId
  )) || null;
}

function strategyActiveOffice(campaign = state.strategyCampaign) {
  const offices = strategyControlledOffices(campaign);
  let active = offices.find((office) => office.id === state.strategyActiveOfficeId);
  if (!active) {
    active = offices.find((office) => office.office_type === "lord") || offices[0] || null;
    state.strategyActiveOfficeId = active?.id || "";
  }
  return active;
}

function strategyOfficeLabel(office, campaign = state.strategyCampaign) {
  if (!office) return "未任职";
  const base = STRATEGY_OFFICE_LABELS[office.office_type] || office.office_type;
  if (office.office_type === "governor") {
    const cityId = (office.managed_entity_ids || [])[0];
    const city = (campaign?.world?.cities || []).find((item) => item.id === cityId);
    return city ? `${city.name}城主` : base;
  }
  const peers = (campaign?.world?.offices || []).filter((item) => (
    item.faction_id === office.faction_id && item.office_type === office.office_type && item.status === "active"
  ));
  return peers.length > 1 ? `${base} ${peers.findIndex((item) => item.id === office.id) + 1}` : base;
}

function strategyOfficeManagedCities(campaign, office) {
  const managed = new Set(office?.managed_entity_ids || []);
  return (campaign?.world?.cities || []).filter((city) => managed.has(city.id));
}

function strategyFaction(campaign = state.strategyCampaign) {
  const hero = strategyControlledHero(campaign);
  const office = strategyActiveOffice(campaign);
  const member = strategyMember(campaign);
  const factionId = hero?.faction_id || office?.faction_id || member?.faction_id;
  return (campaign?.world?.factions || []).find((faction) => faction.id === factionId) || null;
}

function strategyFactionCommandPoints(campaign = state.strategyCampaign, faction = strategyFaction(campaign)) {
  return campaign?.command_points_by_faction?.[faction?.id] || { maximum: 4, used: 0, remaining: 4 };
}

function strategyMonthlyBriefing(campaign = state.strategyCampaign, faction = strategyFaction(campaign)) {
  return campaign?.world?.monthly_briefings?.[faction?.id] || { entries: [] };
}

function strategyMonthlyCycle(campaign = state.strategyCampaign, faction = strategyFaction(campaign)) {
  return campaign?.world?.monthly_cycle?.[faction?.id] || {
    previous_month: null,
    must_handle: [],
    advance_forecast: { cities: [] },
    planned_actions: [],
  };
}

function strategyCampaignGuide(campaign = state.strategyCampaign, faction = strategyFaction(campaign)) {
  return campaign?.world?.campaign_tutorial?.[faction?.id] || null;
}

function strategyOfficeCoordination(campaign = state.strategyCampaign, faction = strategyFaction(campaign)) {
  return campaign?.world?.office_coordination?.[faction?.id] || null;
}

function strategyCommandCost(actionType, payload = {}) {
  if (["send_office_request", "request_registered_units", "approve_registered_unit_request", "assign_strategic_hero_duty"].includes(actionType)) return 0;
  if (actionType === "declare_attack" || actionType === "rebellion_battle") return 2;
  if (actionType === "peaceful_integration") return 2;
  if (actionType === "rebellion_action" && (payload.rebellion_action_id || payload.action_id) === "suppress") return 2;
  return 1;
}

function strategyCanAffordCommand(campaign, faction, actionType, payload = {}, actionKey = "") {
  let available = strategyFactionCommandPoints(campaign, faction).remaining;
  if (actionKey) {
    const existing = (campaign?.queued_actions || []).find((action) => (
      action.faction_id === faction?.id && action.action_type === actionType && action.action_key === actionKey
    ));
    if (existing) available += existing.command_cost || strategyCommandCost(existing.action_type, existing.payload || {});
  }
  return available >= strategyCommandCost(actionType, payload);
}

function strategyPendingStoryEvent(campaign, faction) {
  return (campaign?.world?.story_events || []).find((event) => event.faction_id === faction?.id && event.status === "pending") || null;
}

function strategyCommandDraft(campaign, city) {
  const key = `${campaign?.id || "campaign"}:${city?.id || "city"}`;
  if (!state.strategyCommandDrafts[key]) state.strategyCommandDrafts[key] = {};
  return state.strategyCommandDrafts[key];
}

function strategyAttackTargetsForCity(campaign, sourceCity, factionId) {
  const nodesById = new Map((campaign?.world?.nodes || []).map((node) => [node.id, node]));
  const citiesByNodeId = new Map((campaign?.world?.cities || []).map((city) => [city.node_id, city]));
  const sourceNode = nodesById.get(sourceCity?.node_id);
  if (!sourceNode || sourceCity?.owner_faction_id !== factionId) return [];
  return (sourceNode.connected_node_ids || [])
    .map((nodeId) => citiesByNodeId.get(nodeId))
    .filter((city) => city && city.owner_faction_id !== factionId);
}

function strategyCitiesAreAdjacent(campaign, firstCityId, secondCityId) {
  if (!firstCityId || !secondCityId) return false;
  if (firstCityId === secondCityId) return true;
  const cities = campaign?.world?.cities || [];
  const first = cities.find((city) => city.id === firstCityId);
  const second = cities.find((city) => city.id === secondCityId);
  const node = (campaign?.world?.nodes || []).find((item) => item.id === first?.node_id);
  return Boolean(second && (node?.connected_node_ids || []).includes(second.node_id));
}

function strategyFactionName(campaign, factionId) {
  const faction = (campaign?.world?.factions || []).find((item) => item.id === factionId);
  return faction?.name || factionId || "未归属";
}

function strategyFactionById(campaign, factionId) {
  return (campaign?.world?.factions || []).find((item) => item.id === factionId) || null;
}

function strategyIsNeutralCityState(campaign, factionId) {
  return strategyFactionById(campaign, factionId)?.faction_type === "neutral_city_state";
}

function strategyNeutralIncitementTargets(campaign, city, currentFactionId) {
  const node = (campaign?.world?.nodes || []).find((item) => item.id === city?.node_id);
  const citiesByNode = new Map((campaign?.world?.cities || []).map((item) => [item.node_id, item]));
  const factionIds = new Set(
    (node?.connected_node_ids || [])
      .map((nodeId) => citiesByNode.get(nodeId)?.owner_faction_id)
      .filter((factionId) => (
        factionId
        && factionId !== currentFactionId
        && !strategyIsNeutralCityState(campaign, factionId)
      ))
  );
  return (campaign?.world?.factions || []).filter((item) => factionIds.has(item.id));
}

function strategyMemberLabel(campaign, userId) {
  const member = (campaign?.members || []).find((item) => Number(item.user_id) === Number(userId));
  return member?.username || `用户 ${userId}`;
}

function strategyMemberIsAi(member) {
  return String(member?.role || "").toLowerCase() === "ai" || Number(member?.user_id || 0) < 0;
}

function strategyMemberRoleLabel(campaign, member) {
  if (strategyMemberIsAi(member)) return "AI 接管";
  return Number(member?.user_id) === Number(campaign?.owner_user_id) ? "房主" : "成员";
}

function strategyInitialMembers(campaign) {
  const members = (campaign?.members || []).filter((member) => member.is_initial_player !== false);
  const initialIds = campaign?.resume?.initial_user_ids || [];
  if (!initialIds.length) return members;
  const byUserId = new Map(members.map((member) => [Number(member.user_id), member]));
  const initialMembers = initialIds.map((userId) => byUserId.get(Number(userId)) || {
    user_id: userId,
    username: strategyMemberLabel(campaign, userId),
    faction_id: "",
    is_initial_player: true,
  });
  const includedIds = new Set(initialMembers.map((member) => Number(member.user_id)));
  members.forEach((member) => {
    if (strategyMemberIsAi(member) && !includedIds.has(Number(member.user_id))) initialMembers.push(member);
  });
  return initialMembers;
}

function strategyMissingInitialPlayerLabels(campaign) {
  return (campaign?.resume?.missing_initial_user_ids || []).map((userId) => strategyMemberLabel(campaign, userId));
}

function renderStrategyMembersPanel(current, campaign, isOwner) {
  const members = campaign?.members || [];
  if (!members.length) return;

  const title = document.createElement("h4");
  title.textContent = "成员与邀请";
  current.append(title);

  const panel = document.createElement("div");
  panel.className = "strategy-member-panel";
  appendTextLine(panel, "strategy-meta", `当前加入码：${campaign.join_code || "未生成"}`);

  const actions = document.createElement("div");
  actions.className = "strategy-campaign-actions";
  if (isOwner) {
    const rotate = document.createElement("button");
    rotate.type = "button";
    rotate.className = "ghost";
    rotate.textContent = "重新生成加入码";
    rotate.disabled = state.strategyBusy;
    rotate.addEventListener("click", () => rotateStrategyJoinCode(campaign.id));
    actions.append(rotate);
  } else {
    appendTextLine(actions, "strategy-meta", "只有战役房主可以重置加入码。");
  }
  panel.append(actions);

  const grid = document.createElement("div");
  grid.className = "strategy-member-grid";
  members.forEach((member) => {
    const card = document.createElement("article");
    card.className = "strategy-member-card";
    const strong = document.createElement("strong");
    strong.textContent = member.username || strategyMemberLabel(campaign, member.user_id);
    card.append(strong);
    appendTextLine(card, "strategy-meta", `势力：${strategyFactionName(campaign, member.faction_id)}`);
    appendTextLine(card, "strategy-meta", `角色：${strategyMemberRoleLabel(campaign, member)}`);
    appendTextLine(card, "strategy-meta", strategyMemberIsAi(member) ? "锁定时由 AI 操作" : (member.is_initial_player === false ? "后续成员" : "初始玩家"));
    grid.append(card);
  });
  panel.append(grid);
  current.append(panel);
}

function renderStrategyResumePanel(current, campaign) {
  const initialMembers = strategyInitialMembers(campaign);
  if (!initialMembers.length) return;

  const title = document.createElement("h4");
  title.textContent = "初始玩家在线状态";
  current.append(title);

  const resume = campaign.resume || {};
  const panel = document.createElement("div");
  panel.className = "strategy-resume-panel";
  if (campaign.status !== "active") {
    appendTextLine(panel, "strategy-meta", "战役锁定前，已加入成员会作为候选初始玩家显示；空席会在锁定后交给 AI。");
  } else if (resume.can_resume) {
    appendTextLine(panel, "strategy-meta", "所有真人初始玩家在线，AI 空席会自动操作。");
  } else {
    const missing = strategyMissingInitialPlayerLabels(campaign);
    appendTextLine(panel, "strategy-meta", `等待初始玩家：${missing.join("、") || "未知"}`);
  }

  const onlineIds = new Set((resume.online_initial_user_ids || []).map((userId) => Number(userId)));
  const missingIds = new Set((resume.missing_initial_user_ids || []).map((userId) => Number(userId)));
  const grid = document.createElement("div");
  grid.className = "strategy-resume-grid";
  initialMembers.forEach((member) => {
    const userId = Number(member.user_id);
    let status = "待锁定";
    let className = "strategy-resume-member is-pending";
    if (campaign.status === "active" && strategyMemberIsAi(member)) {
      status = "AI 托管";
      className = "strategy-resume-member is-online";
    } else if (campaign.status === "active" && onlineIds.has(userId)) {
      status = "在线";
      className = "strategy-resume-member is-online";
    } else if (campaign.status === "active" && missingIds.has(userId)) {
      status = "缺席";
      className = "strategy-resume-member is-missing";
    } else if (campaign.status === "active" && resume.can_resume) {
      status = "在线";
      className = "strategy-resume-member is-online";
    }
    const card = document.createElement("article");
    card.className = className;
    const strong = document.createElement("strong");
    strong.textContent = member.username || strategyMemberLabel(campaign, userId);
    card.append(strong);
    appendTextLine(card, "strategy-meta", `势力：${strategyFactionName(campaign, member.faction_id)}`);
    appendTextLine(card, "strategy-meta", `状态：${status}`);
    if (userId === Number(state.authUser?.id || 0)) {
      appendTextLine(card, "strategy-meta", "当前账号");
    }
    grid.append(card);
  });
  panel.append(grid);
  current.append(panel);
}

function strategyMapNodeId(node) {
  return node?.id || node?.node_id || "";
}

function strategyNodeName(campaign, nodeId) {
  const node = (campaign?.world?.nodes || []).find((item) => strategyMapNodeId(item) === nodeId);
  const city = (campaign?.world?.cities || []).find((item) => item.node_id === nodeId);
  return city?.name || node?.name || nodeId || "未知节点";
}

function strategyArmyStatusLabel(status) {
  return ({ garrisoned: "驻扎", deployed: "部署", marching: "行军", engaged: "交战", besieging: "围城", retreating: "撤退", disbanded: "已解散", destroyed: "已覆灭" })[status] || status;
}

function strategyArmyOrderLabel(order) {
  return ({ hold: "待命", march: "行军", intercept: "拦截", reinforce: "增援", retreat: "撤退", besiege: "围城" })[order] || order;
}

function strategyArmySupplyStatusLabel(status) {
  return ({ unassessed: "待首次月结", local: "本地", open: "畅通", strained: "吃紧", severed: "已切断", none: "无来源" })[status] || status;
}

function createStrategySvgElement(tagName) {
  if (document.createElementNS) {
    return document.createElementNS("http://www.w3.org/2000/svg", tagName);
  }
  return document.createElement(tagName);
}

function strategyCityById(campaign, cityId) {
  return (campaign?.world?.cities || []).find((city) => city.id === cityId) || null;
}

function strategyQueuedActionsForCity(campaign, cityId) {
  const id = String(cityId || "");
  return (campaign?.queued_actions || []).filter((action) => {
    const payload = action?.payload || {};
    if (String(payload.city_id || "") === id) return true;
    if (String(payload.source_city_id || "") === id) return true;
    return false;
  });
}

function strategyCityOrderLimit(campaign) {
  const parsed = Number.parseInt(campaign?.world?.city_monthly_order_limit, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 2;
}

function strategyCanResume(campaign) {
  if (!campaign) return false;
  const resume = campaign.resume || {};
  if (resume.can_resume) return true;
  const active = campaign.status === "active" || resume.campaign_status === "active";
  const missing = Array.isArray(resume.missing_initial_user_ids) ? resume.missing_initial_user_ids : null;
  const initial = Array.isArray(resume.initial_user_ids) ? resume.initial_user_ids : [];
  return Boolean(active && missing && missing.length === 0 && initial.length);
}

function strategyCanIssueOrders(campaign) {
  if (!strategyCanResume(campaign)) return false;
  return campaign?.world?.strategic_status?.can_advance_month !== false;
}

function strategyCityOrderLimitReached(campaign, cityId) {
  return strategyQueuedActionsForCity(campaign, cityId).length >= strategyCityOrderLimit(campaign);
}

function strategyCityMapPrompt(campaign, city, faction) {
  if (!city) return "";
  const ownCity = city.owner_faction_id === faction?.id;
  const labels = strategyCityStateLabels(city);
  const plans = strategyQueuedActionsForCity(campaign, city.id);
  if (ownCity) {
    const targets = strategyAttackTargetsForCity(campaign, city, faction?.id);
    if (plans.length) return `${city.name} 已有 ${plans.length}/${strategyCityOrderLimit(campaign)} 条本月军令，可在右侧查看或替换。`;
    if (labels.length) return `${city.name} 有警报：${labels.join(" / ")}。`;
    if (targets.length) return `${city.name} 可进攻 ${targets.map((target) => target.name).join("、")}。`;
    return `${city.name} 已选中，可以调整方针或查看驻军。`;
  }
  return `${city.name} 属于 ${strategyFactionName(campaign, city.owner_faction_id)}。请从己方相邻城市发起进攻。`;
}

function strategyDefaultSelectedCity(campaign, faction) {
  const cities = campaign?.world?.cities || [];
  return cities.find((city) => (
    city.owner_faction_id === faction?.id && strategyCityRebellionForce(city) > 0
  )) || cities.find((city) => (
    city.owner_faction_id === faction?.id && strategyAttackTargetsForCity(campaign, city, faction?.id).length
  )) || cities.find((city) => city.owner_faction_id === faction?.id) || cities[0] || null;
}

function strategySelectedCity(campaign, faction) {
  const selected = strategyCityById(campaign, state.strategySelectedCityId);
  if (selected) return selected;
  const fallback = strategyDefaultSelectedCity(campaign, faction);
  state.strategySelectedCityId = fallback?.id || "";
  return fallback;
}

function strategyMapNodePositions(nodes) {
  const positions = new Map();
  const source = Array.isArray(nodes) ? nodes : [];
  const numeric = source.filter((node) => Number.isFinite(Number(node.x)) && Number.isFinite(Number(node.y)));
  if (numeric.length) {
    const xs = numeric.map((node) => Number(node.x));
    const ys = numeric.map((node) => Number(node.y));
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const spanX = Math.max(1, maxX - minX);
    const spanY = Math.max(1, maxY - minY);
    source.forEach((node, index) => {
      const nodeId = strategyMapNodeId(node);
      if (!nodeId) return;
      if (Number.isFinite(Number(node.x)) && Number.isFinite(Number(node.y))) {
        positions.set(nodeId, {
          x: 12 + ((Number(node.x) - minX) / spanX) * 76,
          y: 14 + ((Number(node.y) - minY) / spanY) * 72,
        });
        return;
      }
      const angle = (Math.PI * 2 * index) / Math.max(1, source.length) - Math.PI / 2;
      positions.set(nodeId, { x: 50 + Math.cos(angle) * 34, y: 50 + Math.sin(angle) * 30 });
    });
    return positions;
  }
  source.forEach((node, index) => {
    const nodeId = strategyMapNodeId(node);
    if (!nodeId) return;
    const angle = (Math.PI * 2 * index) / Math.max(1, source.length) - Math.PI / 2;
    positions.set(nodeId, { x: 50 + Math.cos(angle) * 34, y: 50 + Math.sin(angle) * 30 });
  });
  return positions;
}

function strategyCityMapClass(city, campaign, faction, selectedCityId) {
  const classes = ["strategy-map-node"];
  if (city.owner_faction_id === faction?.id) classes.push("is-owned");
  else if (strategyIsNeutralCityState(campaign, city.owner_faction_id)) classes.push("is-city-state");
  else if (city.owner_faction_id) classes.push("is-enemy");
  else classes.push("is-neutral");
  if (city.id === selectedCityId) classes.push("is-selected");
  if (strategyCityRebellionForce(city) > 0) classes.push("has-rebellion");
  if (strategyAttackTargetsForCity(campaign, city, faction?.id).length) classes.push("has-attack");
  if (strategyQueuedActionsForCity(campaign, city.id).length) classes.push("has-plan");
  return classes.join(" ");
}

function renderStrategyMap(current, campaign, faction) {
  const nodes = campaign?.world?.nodes || [];
  const cities = campaign?.world?.cities || [];
  if (!nodes.length && !cities.length) return;

  const map = document.createElement("div");
  map.className = "strategy-map strategy-map-stage";
  const nodesById = new Map(nodes.map((node) => [strategyMapNodeId(node), node]));
  const citiesByNodeId = new Map(cities.map((city) => [city.node_id, city]));
  const positions = strategyMapNodePositions(nodes);
  const selectedCityId = strategySelectedCity(campaign, faction)?.id || "";
  const activeArmies = (campaign?.world?.armies || []).filter((army) => !["disbanded", "destroyed"].includes(army.status));
  const armiesByNodeId = new Map();
  activeArmies.forEach((army) => {
    const rows = armiesByNodeId.get(army.location_node_id) || [];
    rows.push(army);
    armiesByNodeId.set(army.location_node_id, rows);
  });
  const activeArmyRouteKeys = new Set();
  const armySupplyRouteKeys = new Set();
  const armySupplyRiskRouteKeys = new Set();
  activeArmies.filter((army) => army.status === "marching").forEach((army) => {
    const route = army.route_node_ids || [];
    for (let index = Number(army.route_progress_index || 0); index < route.length - 1; index += 1) {
      activeArmyRouteKeys.add([route[index], route[index + 1]].sort().join("::"));
    }
  });
  activeArmies.filter((army) => army.faction_id === faction?.id).forEach((army) => {
    const route = army.supply_line_node_ids || [];
    for (let index = 0; index < route.length - 1; index += 1) {
      const key = [route[index], route[index + 1]].sort().join("::");
      armySupplyRouteKeys.add(key);
      if (["strained", "severed", "none"].includes(army.supply_line_status)) armySupplyRiskRouteKeys.add(key);
    }
  });

  const header = document.createElement("div");
  header.className = "strategy-map-header";
  const title = document.createElement("h4");
  title.textContent = "战略地图";
  header.append(title);
  appendTextLine(header, "strategy-meta", "点击城市选择命令目标。红色警报表示叛乱或战斗压力。");
  const legend = document.createElement("div");
  legend.className = "strategy-map-legend";
  [
    ["己方", "is-owned"],
    ["敌方", "is-enemy"],
    ["中立城邦", "is-city-state"],
    ["选中", "is-selected"],
    ["警报", "has-rebellion"],
  ].forEach(([label, className]) => {
    const item = document.createElement("span");
    item.className = `strategy-map-legend-item ${className}`;
    item.textContent = label;
    legend.append(item);
  });
  header.append(legend);
  map.append(header);

  const canvas = document.createElement("div");
  canvas.className = "strategy-map-canvas strategy-map-stage-canvas";
  const routeLayer = createStrategySvgElement("svg");
  routeLayer.className = "strategy-map-route-layer";
  routeLayer.setAttribute("viewBox", "0 0 100 100");
  routeLayer.setAttribute("preserveAspectRatio", "none");
  canvas.append(routeLayer);

  const routeList = document.createElement("div");
  routeList.className = "strategy-map-route-list";
  const routeKeys = new Set();
  nodes.forEach((node) => {
    const sourceId = strategyMapNodeId(node);
    (node.connected_node_ids || []).forEach((targetId) => {
      if (!sourceId || !targetId) return;
      const key = [sourceId, targetId].sort().join("::");
      if (routeKeys.has(key)) return;
      routeKeys.add(key);
      const sourceCity = citiesByNodeId.get(sourceId);
      const targetNode = nodesById.get(targetId);
      const targetCity = citiesByNodeId.get(targetId);
      const sourceName = sourceCity?.name || node.name || sourceId;
      const targetName = targetCity?.name || targetNode?.name || targetId;
      const sourcePos = positions.get(sourceId);
      const targetPos = positions.get(targetId);
      if (sourcePos && targetPos) {
        const line = createStrategySvgElement("line");
        line.setAttribute("x1", String(sourcePos.x));
        line.setAttribute("y1", String(sourcePos.y));
        line.setAttribute("x2", String(targetPos.x));
        line.setAttribute("y2", String(targetPos.y));
        line.setAttribute("class", `strategy-map-route-line${armySupplyRouteKeys.has(key) ? " is-supply-route" : ""}${armySupplyRiskRouteKeys.has(key) ? " is-supply-risk" : ""}${activeArmyRouteKeys.has(key) ? " is-army-route" : ""}`);
        routeLayer.append(line);
      }
      const route = document.createElement("div");
      route.className = "strategy-map-route";
      const strong = document.createElement("strong");
      strong.textContent = `${sourceName} ↔ ${targetName}`;
      route.append(strong);
      appendTextLine(
        route,
        "strategy-meta",
        `路线：${strategyFactionName(campaign, sourceCity?.owner_faction_id)} / ${strategyFactionName(campaign, targetCity?.owner_faction_id)}`
      );
      routeList.append(route);
    });
  });
  if (!routeList.children.length) {
    appendTextLine(routeList, "strategy-meta", "暂无可见连接路线。");
  }

  cities.forEach((city) => {
    const node = nodesById.get(city.node_id);
    const position = positions.get(city.node_id) || { x: 50, y: 50 };
    const card = document.createElement("button");
    card.type = "button";
    card.className = strategyCityMapClass(city, campaign, faction, selectedCityId);
    card.style.left = `${position.x}%`;
    card.style.top = `${position.y}%`;
    card.dataset.cityId = city.id;
    card.dataset.cityName = city.name;
    card.disabled = state.strategyBusy;
    const queuedActions = strategyQueuedActionsForCity(campaign, city.id);
    card.addEventListener("click", () => {
      state.strategySelectedCityId = city.id;
      enqueueFloatingToast(strategyCityMapPrompt(campaign, city, faction));
      renderStrategyPanel();
      if (window.innerWidth <= 720) {
        focusStrategyCommandPanel();
      }
    });
    const strong = document.createElement("strong");
    strong.textContent = city.name;
    const factionLine = document.createElement("span");
    const cityFaction = strategyFactionById(campaign, city.owner_faction_id);
    factionLine.textContent = cityFaction?.faction_type === "neutral_city_state"
      ? `${cityFaction.name} · 城主 ${cityFaction.governor_name || "无名"}`
      : strategyFactionName(campaign, city.owner_faction_id);
    const statLine = document.createElement("span");
    statLine.textContent = `兵${city.resources?.troops || 0} / 防${city.defense || 0}`;
    card.append(strong, factionLine, statLine);
    const cityStateLabels = strategyCityStateLabels(city);
    if (cityStateLabels.length) {
      const warning = document.createElement("span");
      warning.className = "strategy-map-warning";
      warning.textContent = cityStateLabels[0];
      card.append(warning);
    }
    if (queuedActions.length) {
      const plan = document.createElement("span");
      plan.className = "strategy-map-plan";
      plan.textContent = `军令 x${queuedActions.length}`;
      card.append(plan);
    }
    (armiesByNodeId.get(city.node_id) || []).forEach((army) => {
      const badge = document.createElement("span");
      badge.className = `strategy-map-army${army.faction_id === faction?.id ? " is-owned" : ""}`;
      badge.textContent = army.status === "marching"
        ? `军队 · 行军 ${Number(army.route_progress_index || 0)}/${Math.max(1, (army.route_node_ids || []).length - 1)} · 补给${strategyArmySupplyStatusLabel(army.supply_line_status)}`
        : `军队 · ${strategyArmyStatusLabel(army.status)} · 补给${strategyArmySupplyStatusLabel(army.supply_line_status)}`;
      card.append(badge);
    });
    const adjacentCities = (node?.connected_node_ids || [])
      .map((nodeId) => citiesByNodeId.get(nodeId))
      .filter(Boolean);
    if (adjacentCities.length) {
      appendTextLine(card, "strategy-map-hidden-text", `相邻：${adjacentCities.map((item) => item.name).join("、")}`);
    }
    const targets = strategyAttackTargetsForCity(campaign, city, faction?.id);
    if (targets.length) {
      appendTextLine(card, "strategy-map-hidden-text", `可进攻：${targets.map((item) => item.name).join("、")}`);
    }
    canvas.append(card);
  });
  nodes.forEach((node) => {
    const nodeId = strategyMapNodeId(node);
    if (!nodeId || citiesByNodeId.has(nodeId)) return;
    const position = positions.get(nodeId) || { x: 50, y: 50 };
    const card = document.createElement("div");
    card.className = "strategy-map-node is-neutral";
    card.style.left = `${position.x}%`;
    card.style.top = `${position.y}%`;
    const strong = document.createElement("strong");
    strong.textContent = node.name || nodeId;
    card.append(strong);
    (armiesByNodeId.get(nodeId) || []).forEach((army) => {
      const badge = document.createElement("span");
      badge.className = `strategy-map-army${army.faction_id === faction?.id ? " is-owned" : ""}`;
      badge.textContent = `军队 · ${strategyArmyStatusLabel(army.status)} · 补给${strategyArmySupplyStatusLabel(army.supply_line_status)}`;
      card.append(badge);
    });
    appendTextLine(card, "strategy-map-hidden-text", `节点 ${nodeId} · ${node.type || "地形"}`);
    canvas.append(card);
  });
  const routeDrawer = document.createElement("details");
  routeDrawer.className = "strategy-map-routes-drawer";
  routeDrawer.open = Boolean(state.strategyRouteIntelOpen);
  const routeSummary = document.createElement("summary");
  routeSummary.textContent = `路线情报 · ${routeKeys.size || routeList.children.length} 条`;
  routeSummary.addEventListener("click", (event) => {
    event.preventDefault();
    state.strategyRouteIntelOpen = !state.strategyRouteIntelOpen;
    routeDrawer.open = state.strategyRouteIntelOpen;
  });
  routeDrawer.append(routeSummary, routeList);
  activeArmies.filter((army) => army.status === "marching").forEach((army) => {
    appendTextLine(
      routeList,
      "strategy-map-army-route-summary",
      `军队 ${army.id}：${(army.route_node_ids || []).map((nodeId) => strategyNodeName(campaign, nodeId)).join(" → ")} · 预计第 ${army.estimated_arrival_month} 月抵达`,
    );
  });
  activeArmies.filter((army) => army.faction_id === faction?.id).forEach((army) => {
    const source = strategyCityById(campaign, army.supply_source_city_id);
    appendTextLine(
      routeList,
      "strategy-map-army-supply-summary",
      `补给 ${army.id}：${strategyArmySupplyStatusLabel(army.supply_line_status)} · ${source?.name || "无来源"} · 距离 ${army.supply_distance ?? "—"} · 月需 ${strategyNumber(army.monthly_supply_need)}`,
    );
  });
  map.append(canvas, routeDrawer);
  current.append(map);
}

function createStrategyCommandSection(title, note = "") {
  const section = document.createElement("section");
  section.className = "strategy-command-section";
  const head = document.createElement("div");
  head.className = "strategy-command-section-head";
  const strong = document.createElement("strong");
  strong.textContent = title;
  head.append(strong);
  section.append(head);
  if (note) appendTextLine(section, "strategy-meta", note);
  return section;
}

function createStrategyField(labelText, control) {
  const label = document.createElement("label");
  const span = document.createElement("span");
  span.textContent = labelText;
  label.append(span, control);
  return label;
}

function filterStrategySelectOptions(select, query) {
  const needle = String(query || "").trim().toLowerCase();
  let firstVisibleValue = "";
  Array.from(select?.children || []).forEach((option) => {
    const haystack = `${option.textContent || ""} ${option.value || ""}`.toLowerCase();
    const visible = !needle || haystack.includes(needle);
    option.hidden = !visible;
    option.disabled = !visible;
    if (visible && !firstVisibleValue) firstVisibleValue = option.value;
  });
  const selectedOption = select?.children?.[select.selectedIndex] || null;
  if (firstVisibleValue && selectedOption?.hidden) {
    select.value = firstVisibleValue;
  }
  return Boolean(firstVisibleValue);
}

function strategyCommandDisabledReason(canResume, ownCity) {
  if (!canResume) return "等待所有真人初始玩家在线后才能下达军令。";
  if (!ownCity) return "这不是你的城市；请在地图上选择己方城市。";
  return "";
}

function strategyRegisteredUnitsLabel(campaign, inventory = {}) {
  const unitTypes = campaign?.world?.registered_unit_types || [];
  const rows = Object.entries(inventory || {})
    .filter(([, count]) => Number(count || 0) > 0)
    .map(([unitType, count]) => {
      const config = unitTypes.find((item) => item.id === unitType);
      return `${config?.name || unitType} ${count}`;
    });
  return rows.join(" · ") || "暂无";
}

function strategyUnlockedRegisteredUnitTypes(faction) {
  const unlocked = new Set(["infantry"]);
  (faction?.tactic_tech_tree || []).filter((tech) => tech.unlocked).forEach((tech) => {
    (tech.unit_unlocks || []).forEach((unitType) => unlocked.add(unitType));
  });
  return unlocked;
}

function createStrategyCityCommandCard(campaign, city, faction, canResume, office = strategyActiveOffice(campaign)) {
  const card = document.createElement("article");
  card.className = "strategy-city-card strategy-command-card strategy-city-command-card";
  if (!city) {
    appendTextLine(card, "strategy-meta", "地图上还没有可操作城市。");
    return card;
  }

  const title = document.createElement("strong");
  title.textContent = `${city.name} · ${city.policy}`;
  card.append(title);
  appendTextLine(
    card,
    "strategy-meta",
    `${strategyFactionName(campaign, city.owner_faction_id)} · 粮 ${city.resources?.food || 0} · 钱 ${city.resources?.money || 0} · 人 ${city.resources?.population || 0} · 以太 ${city.resources?.ether || 0} · 兵 ${city.resources?.troops || 0} · 城防 ${city.defense || 0}`
  );
  appendTextLine(
    card,
    "strategy-conversion",
    `兵种：${(city.troop_conversion || []).map((row) => `${row.unit_type} ${row.ratio}%`).join(" / ") || "暂无编制"}`
  );
  const cityStateLabels = strategyCityStateLabels(city);
  if (cityStateLabels.length) {
    appendTextLine(card, "strategy-meta", `状态：${cityStateLabels.join(" / ")}`);
  }
  const queuedActions = strategyQueuedActionsForCity(campaign, city.id);
  const draft = strategyCommandDraft(campaign, city);
  const orderLimit = strategyCityOrderLimit(campaign);
  const orderCount = queuedActions.length;
  const orderLimitReached = strategyCityOrderLimitReached(campaign, city.id);
  if (queuedActions.length) {
    const planBox = document.createElement("div");
    planBox.className = "strategy-command-plan";
    const planTitle = document.createElement("strong");
    planTitle.textContent = `本月已计划 ${orderCount}/${orderLimit} 条军令`;
    planBox.append(planTitle);
    queuedActions.slice(0, 3).forEach((action) => appendTextLine(planBox, "strategy-meta", strategyQueuedActionLabel(campaign, action)));
    card.append(planBox);
  } else {
    appendTextLine(card, "strategy-meta", `本城军令：0/${orderLimit}`);
  }

  const stack = document.createElement("div");
  stack.className = "strategy-command-stack";
  const ownCity = city.owner_faction_id === faction?.id;
  const cityFaction = strategyFactionById(campaign, city.owner_faction_id);
  const neutralCityState = cityFaction?.faction_type === "neutral_city_state";
  if (neutralCityState) card.classList.add("is-neutral-city-state");
  const disabledReason = strategyCommandDisabledReason(canResume, ownCity);
  const orderLimitReason = orderLimitReached ? `本城本月军令已满（${orderCount}/${orderLimit}）。` : "";
  const commandPoints = strategyFactionCommandPoints(campaign, faction);
  const noCommandReason = commandPoints.remaining <= 0 ? "本势力本月军令已用尽。" : "";

  const canGovern = !office || office.office_type === "governor";
  const canHandleRebellion = !office || ["lord", "governor"].includes(office.office_type);
  const canManageOccupation = !office || ["lord", "governor"].includes(office.office_type);
  const canRitual = !office || ["lord", "governor"].includes(office.office_type);
  const canAttack = !office || ["lord", "general"].includes(office.office_type);

  if (neutralCityState) {
    const politics = cityFaction.neutral_politics || {};
    const currentRelation = (politics.relationships || []).find((item) => item.faction_id === faction?.id);
    const neutral = createStrategyCommandSection(
      "中立城邦",
      "城主只经营与防守，不会主动扩张；其政治立场由本城处境实时变化。"
    );
    appendTextLine(neutral, "strategy-meta", `城主：${cityFaction.governor_name || "无名"} · 姿态：${politics.posture?.label || (cityFaction.incited_against_faction_id ? "已受教唆" : "中立守备")}`);
    if (politics.current_need) appendTextLine(neutral, "strategy-meta", `当前诉求：${politics.current_need.label} · ${politics.current_need.summary}`);
    if (politics.fear) appendTextLine(neutral, "strategy-meta", `恐惧来源：${politics.fear.label} · ${politics.fear.summary}`);
    if (politics.governor_position) appendTextLine(neutral, "strategy-meta", `城主立场：${politics.governor_position.label} · ${politics.governor_position.summary}`);
    if (currentRelation) {
      appendTextLine(neutral, "strategy-meta", `对我方关系：${currentRelation.score > 0 ? "+" : ""}${currentRelation.score}（${currentRelation.label}）· ${currentRelation.governor_view}`);
      appendTextLine(neutral, "strategy-meta", `我方影响力：${Number(currentRelation.influence || 0)}/100 · 当地支持：${Number(currentRelation.local_support || 0)}/100`);
    }
    if (faction) appendTextLine(neutral, "strategy-meta", `我方外交信誉：${Number(faction.diplomatic_reputation ?? 50)}/100${Number(faction.diplomatic_reputation ?? 50) < 30 ? " · 城主不再相信新的保护或停战承诺" : ""}`);
    const agreements = (politics.agreements || []).filter((item) => item.major_faction_id === faction?.id);
    if (agreements.length) appendTextLine(neutral, "strategy-meta", `协议记录：${agreements.map((item) => {
      const label = item.label || item.agreement_type;
      if (item.status === "active") return `${label}（剩余 ${item.remaining_months} 月）`;
      return `${label}（${item.end_reason_label || item.status}）`;
    }).join("、")}`);
    const diplomaticMemory = (politics.diplomatic_memory || []).filter((item) => item.major_faction_id === faction?.id).slice(-3).reverse();
    diplomaticMemory.forEach((item) => appendTextLine(neutral, "strategy-meta", `外交记忆 · 第 ${item.month} 月：${item.summary}`));
    const diplomacyOptions = currentRelation?.diplomacy_options || [];
    if (diplomacyOptions.length) {
      const diplomacySelect = document.createElement("select");
      diplomacyOptions.forEach((option) => {
        const item = document.createElement("option");
        item.value = option.id;
        item.textContent = `${option.name} · 预计${option.expected_response}`;
        diplomacySelect.append(item);
      });
      const diplomacyPreview = document.createElement("p");
      diplomacyPreview.className = "strategy-meta";
      const propose = document.createElement("button");
      propose.type = "button";
      propose.className = "primary";
      const syncDiplomacy = () => {
        const option = diplomacyOptions.find((item) => item.id === diplomacySelect.value) || diplomacyOptions[0];
        const costs = Object.entries(option?.resource_cost || {}).filter(([, value]) => Number(value) > 0).map(([key, value]) => `${{money: "钱", food: "粮", troops: "兵"}[key] || key} ${value}`);
        diplomacyPreview.textContent = `${option?.response_reason || ""} ${option?.direct_effect || ""}${costs.length ? ` · 接受时成本：${costs.join(" / ")}` : ""}`;
        propose.textContent = `${option?.name || "提出交涉"} · 1 军令`;
        propose.disabled = (
          state.strategyBusy
          || !canResume
          || office?.office_type !== "lord"
          || !option?.can_propose
          || !strategyCanAffordCommand(campaign, faction, "neutral_diplomacy")
        );
      };
      diplomacySelect.addEventListener("change", syncDiplomacy);
      propose.addEventListener("click", () => queueStrategyAction("neutral_diplomacy", {
        neutral_faction_id: cityFaction.id,
        diplomacy_action_id: diplomacySelect.value,
      }));
      neutral.append(createStrategyField("普通交涉", diplomacySelect), diplomacyPreview, propose);
      syncDiplomacy();
      if (office?.office_type !== "lord") appendTextLine(neutral, "strategy-command-lock", "只有主公可签署普通外交交涉。");
    }
    const integration = currentRelation?.peaceful_integration;
    if (integration) {
      const integrationRequirements = (integration.requirements || []).map((item) => `${item.met ? "✓" : "○"}${item.label} ${item.current}/${item.required}`).join(" · ");
      appendTextLine(neutral, "strategy-meta", `和平整合门槛：${integrationRequirements}`);
      const integrate = document.createElement("button");
      integrate.type = "button";
      integrate.className = "primary";
      integrate.textContent = "和平整合 · 100 钱 / 80 粮 / 2 军令";
      integrate.disabled = (
        state.strategyBusy
        || !canResume
        || office?.office_type !== "lord"
        || !integration.can_integrate
        || !strategyCanAffordCommand(campaign, faction, "peaceful_integration")
      );
      integrate.addEventListener("click", () => queueStrategyAction("peaceful_integration", {
        neutral_faction_id: cityFaction.id,
      }));
      neutral.append(integrate);
      if (!integration.can_integrate) appendTextLine(neutral, "strategy-command-lock", integration.blocked_reason || "尚未达到和平整合门槛。");
      if (office?.office_type !== "lord") appendTextLine(neutral, "strategy-command-lock", "只有主公可提出和平整合。");
    }
    if (cityFaction.incited_against_faction_id) {
      appendTextLine(neutral, "strategy-meta", `当前目标：${strategyFactionName(campaign, cityFaction.incited_against_faction_id)} · 教唆者：${strategyFactionName(campaign, cityFaction.incited_by_faction_id)}`);
    }
    const targets = strategyNeutralIncitementTargets(campaign, city, faction?.id);
    if (office?.office_type === "lord" && targets.length) {
      const targetSelect = document.createElement("select");
      targets.forEach((target) => {
        const option = document.createElement("option");
        option.value = target.id;
        option.textContent = target.name;
        targetSelect.append(option);
      });
      neutral.append(createStrategyField("教唆目标", targetSelect));
      const incite = document.createElement("button");
      incite.type = "button";
      incite.className = "primary";
      incite.textContent = "教唆出兵 · 60 金钱 · 1 军令";
      incite.disabled = (
        state.strategyBusy
        || !canResume
        || Number(faction?.resources?.money || 0) < 60
        || Number(campaign?.world?.current_month || 0) < Number(currentRelation?.incitement_cooldown_until_month || 0)
        || !strategyCanAffordCommand(campaign, faction, "incite_neutral_city_state")
      );
      incite.addEventListener("click", () => queueStrategyAction("incite_neutral_city_state", {
        neutral_faction_id: cityFaction.id,
        target_faction_id: targetSelect.value,
      }));
      neutral.append(incite);
      if (Number(faction?.resources?.money || 0) < 60) appendTextLine(neutral, "strategy-command-lock", "势力金钱不足 60。 ");
      if (Number(campaign?.world?.current_month || 0) < Number(currentRelation?.incitement_cooldown_until_month || 0)) appendTextLine(neutral, "strategy-command-lock", `教唆冷却至第 ${currentRelation.incitement_cooldown_until_month} 月。`);
    } else if (office?.office_type !== "lord") {
      appendTextLine(neutral, "strategy-command-lock", "只有主公可执行教唆。 ");
    } else {
      appendTextLine(neutral, "strategy-command-lock", "该城邦当前没有接壤的可教唆目标。 ");
    }
    stack.append(neutral);
  }

  const occupation = city.occupation_governance || {};
  if (ownCity && occupation.status && occupation.status !== "ended") {
    const occupationSection = createStrategyCommandSection(
      "占领治理",
      occupation.status === "pending" ? "武力夺城后的统治方式尚未决定；拖延会减半产出并提高叛乱风险。" : "占领政策会持续影响三次月结，之后进入常态治理。"
    );
    appendTextLine(occupationSection, "strategy-meta", `状态：${occupation.status === "pending" ? "政策待定" : occupation.status === "active" ? "政策执行中" : "已稳定"} · 前统治者：${strategyFactionName(campaign, occupation.previous_owner_faction_id)}`);
    appendTextLine(occupationSection, "strategy-meta", `当前政策：${occupation.policy_label || "待选择"} · 产出 ${Number(occupation.income_percent || 100)}% · 叛乱风险 ${Number(occupation.rebellion_modifier || 0) >= 0 ? "+" : ""}${Number(occupation.rebellion_modifier || 0)}${occupation.remaining_settlements != null ? ` · 剩余 ${occupation.remaining_settlements} 次月结` : ""}`);
    const occupationChoices = occupation.policy_choices || [];
    if (occupationChoices.length) {
      const occupationSelect = document.createElement("select");
      occupationChoices.forEach((choice) => {
        const option = document.createElement("option");
        option.value = choice.id;
        option.textContent = choice.name;
        occupationSelect.append(option);
      });
      const occupationPreview = document.createElement("p");
      occupationPreview.className = "strategy-meta";
      const chooseOccupation = document.createElement("button");
      chooseOccupation.type = "button";
      chooseOccupation.className = "primary";
      const syncOccupation = () => {
        const choice = occupationChoices.find((item) => item.id === occupationSelect.value) || occupationChoices[0];
        const costs = [choice?.money_cost ? `钱 ${choice.money_cost}` : "", choice?.food_cost ? `粮 ${choice.food_cost}` : "", choice?.minimum_garrison ? `守军至少 ${choice.minimum_garrison}` : ""].filter(Boolean);
        occupationPreview.textContent = `${choice?.summary || ""} · 产出 ${choice?.income_percent || 100}% · 叛乱风险 ${Number(choice?.rebellion_modifier || 0) >= 0 ? "+" : ""}${Number(choice?.rebellion_modifier || 0)}${costs.length ? ` · ${costs.join(" / ")}` : ""}${choice?.blocked_reason ? ` · ${choice.blocked_reason}` : ""}`;
        chooseOccupation.textContent = `选择${choice?.name || "占领政策"} · 1 军令`;
        chooseOccupation.disabled = state.strategyBusy || !canResume || !canManageOccupation || !choice?.can_choose || orderLimitReached || !strategyCanAffordCommand(campaign, faction, "choose_occupation_policy", {}, city.id);
      };
      occupationSelect.addEventListener("change", syncOccupation);
      chooseOccupation.addEventListener("click", () => queueStrategyAction("choose_occupation_policy", {
        city_id: city.id,
        policy_id: occupationSelect.value,
      }));
      occupationSection.append(createStrategyField("占领政策", occupationSelect), occupationPreview, chooseOccupation);
      syncOccupation();
    }
    if (!canManageOccupation) appendTextLine(occupationSection, "strategy-command-lock", "只有主公或本城城主可决定占领政策。");
    stack.append(occupationSection);
  }

  const funding = city.rebellion_funding_options?.[faction?.id];
  const fundingRelevant = !ownCity && funding && (occupation.status || strategyCityRebellionForce(city) > 0 || Number(funding.rebellion_risk || 0) >= 45);
  const lordPoliticalCrisisView = office?.office_type === "lord" && !neutralCityState && (
    (occupation.status && occupation.status !== "ended")
    || (ownCity && strategyCityRebellionForce(city) > 0)
    || fundingRelevant
  );
  if (fundingRelevant) {
    const fundingSection = createStrategyCommandSection("外部资助", "资助敌城反抗力量可能推动自治或倒戈，但会留下明确的世界记忆。");
    appendTextLine(fundingSection, "strategy-meta", `消耗 60 金钱 · 叛军 +${funding.rebel_troop_delta || 120} · 我方当地支持 +10 · 当前叛乱风险 ${funding.rebellion_risk || 0}`);
    const fund = document.createElement("button");
    fund.type = "button";
    fund.className = "ghost danger";
    fund.textContent = "资助叛乱 · 60 钱 / 1 军令";
    fund.disabled = state.strategyBusy || !canResume || office?.office_type !== "lord" || !funding.can_fund || orderLimitReached || !strategyCanAffordCommand(campaign, faction, "fund_rebellion", {}, city.id);
    fund.addEventListener("click", () => queueStrategyAction("fund_rebellion", { city_id: city.id }));
    fundingSection.append(fund);
    if (funding.blocked_reason) appendTextLine(fundingSection, "strategy-command-lock", funding.blocked_reason);
    if (office?.office_type !== "lord") appendTextLine(fundingSection, "strategy-command-lock", "只有主公可批准外部资助。");
    stack.append(fundingSection);
  }

  if (canGovern) {
  const governance = createStrategyCommandSection("治理", "调整城市本月方针。想稳住局势就选稳定，准备扩张就选征兵。");
  const select = document.createElement("select");
  const queuedPolicy = queuedActions.find((action) => action.action_type === "set_city_policy")?.payload?.policy;
  const desiredPolicy = draft.policy || queuedPolicy || city.policy;
  (campaign?.world?.policy_choices || []).forEach((policy) => {
    const option = document.createElement("option");
    option.value = policy;
    option.textContent = policy;
    option.selected = policy === desiredPolicy;
    select.append(option);
  });
  select.value = desiredPolicy;
  select.disabled = state.strategyBusy || !canResume || !ownCity;
  select.addEventListener("change", () => { draft.policy = select.value; });
  governance.append(createStrategyField("方针", select));

  const queuePolicy = document.createElement("button");
  queuePolicy.type = "button";
  queuePolicy.className = "primary";
  queuePolicy.textContent = "计划方针 · 1 军令";
  queuePolicy.disabled = state.strategyBusy || !canResume || !ownCity || orderLimitReached || !strategyCanAffordCommand(campaign, faction, "set_city_policy", {}, city.id);
  queuePolicy.addEventListener("click", () => queueStrategyAction("set_city_policy", {
    city_id: city.id,
    policy: select.value,
  }));
  governance.append(queuePolicy);
  if (disabledReason || orderLimitReason || noCommandReason) appendTextLine(governance, "strategy-command-lock", disabledReason || orderLimitReason || noCommandReason);
  stack.append(governance);
  }

  if (canRitual && ownCity && !lordPoliticalCrisisView) {
    const ritual = createStrategyCommandSection("召唤祭祀", "祭祀随机召唤一名未绑定武将，并永久记录本城祭祀场为保存位置。");
    const ritualLevel = Number(city.building_levels?.ritual_site || 0);
    const capacity = faction?.hero_ritual_capacity || { maximum: 0, used: 0, remaining: 0 };
    appendTextLine(ritual, "strategy-meta", `祭祀场 ${ritualLevel ? `${ritualLevel} 级` : "未建"} · 以太 ${city.resources?.ether || 0}/30 · 职位容量 ${capacity.used}/${capacity.maximum}`);
    const ritualButton = document.createElement("button");
    ritualButton.type = "button";
    ritualButton.className = "primary";
    ritualButton.textContent = "举行祭祀 · 30 以太 · 1 军令";
    ritualButton.disabled = state.strategyBusy || !canResume || orderLimitReached || ritualLevel < 1 || Number(city.resources?.ether || 0) < 30 || Number(capacity.remaining || 0) < 1 || !strategyCanAffordCommand(campaign, faction, "perform_hero_ritual", {}, city.id);
    ritualButton.addEventListener("click", () => queueStrategyAction("perform_hero_ritual", { city_id: city.id }));
    ritual.append(ritualButton);
    if (!ritualLevel) appendTextLine(ritual, "strategy-command-lock", "先由城主建造祭祀场。");
    else if (Number(capacity.remaining || 0) < 1) appendTextLine(ritual, "strategy-command-lock", "职位容量已满；研究职位科技、取得新城市或由主公解绑武将。");
    else if (disabledReason || orderLimitReason || noCommandReason) appendTextLine(ritual, "strategy-command-lock", disabledReason || orderLimitReason || noCommandReason);
    stack.append(ritual);
  }

  if (office?.office_type === "governor" && ownCity) {
    const defense = createStrategyCommandSection("增加兵力", "从本城人口中征集 90 兵力，并提高 1 点城防。");
    const levy = document.createElement("button");
    levy.type = "button";
    levy.className = "primary";
    levy.textContent = "增加本城兵力 · 1 军令";
    levy.disabled = state.strategyBusy || !canResume || orderLimitReached || Number(city.resources?.population || 0) < 80 || Number(city.resources?.food || 0) < 40 || Number(city.resources?.money || 0) < 25 || !strategyCanAffordCommand(campaign, faction, "increase_city_troops", {}, city.id);
    levy.addEventListener("click", () => queueStrategyAction("increase_city_troops", { city_id: city.id }));
    defense.append(levy);
    stack.append(defense);

    const registration = createStrategyCommandSection("注册士兵", "把城市兵力编成可直接进入格子战的确切单位；组成由本城训练建筑确定。");
    const registrationCount = document.createElement("select");
    [1, 2, 3].forEach((count) => {
      const option = document.createElement("option");
      option.value = String(count);
      option.textContent = `${count} 个单位`;
      registrationCount.append(option);
    });
    registrationCount.value = "1";
    const eligible = [];
    const unlockedUnitTypes = strategyUnlockedRegisteredUnitTypes(faction);
    if (unlockedUnitTypes.has("infantry") && Number(city.building_levels?.barracks || 0) > 0) eligible.push("步兵 100兵力/单位");
    if (unlockedUnitTypes.has("archer") && Number(city.building_levels?.archery_range || 0) > 0) eligible.push("弓兵 140兵力/单位");
    if (unlockedUnitTypes.has("cavalry") && Number(city.building_levels?.stables || 0) > 0) eligible.push("骑兵 180兵力/单位");
    appendTextLine(registration, "strategy-meta", `可用训练设施：${eligible.join(" · ") || "无"}`);
    appendTextLine(registration, "strategy-unit-ledger", `城内已注册：${strategyRegisteredUnitsLabel(campaign, city.registered_units)}`);
    const register = document.createElement("button");
    register.type = "button";
    register.className = "primary";
    register.textContent = "注册选定数量 · 1 军令";
    register.disabled = state.strategyBusy || !canResume || orderLimitReached || !eligible.length || Number(city.resources?.troops || 0) < 100 || !strategyCanAffordCommand(campaign, faction, "register_city_soldiers", {}, city.id);
    register.addEventListener("click", () => queueStrategyAction("register_city_soldiers", { city_id: city.id, unit_count: Number(registrationCount.value) }));
    registration.append(createStrategyField("注册批次", registrationCount), register);
    stack.append(registration);

    const building = createStrategyCommandSection("城市建设", "建筑可逐级升级；当前等级上限由主公研究的建筑科技决定。");
    const buildingSelect = document.createElement("select");
    (campaign?.world?.building_projects || []).filter((project) => Number(city.building_levels?.[project.id] || 0) < Number(city.building_limits?.[project.id] || 1)).forEach((project) => {
      const option = document.createElement("option");
      option.value = project.id;
      const nextLevel = Number(city.building_levels?.[project.id] || 0) + 1;
      option.textContent = `${project.name} ${nextLevel}级 · 钱 ${Number(project.money || 0) * nextLevel} / 粮 ${Number(project.food || 0) * nextLevel}`;
      buildingSelect.append(option);
    });
    buildingSelect.value = buildingSelect.children[0]?.value || "";
    const construct = document.createElement("button");
    construct.type = "button";
    construct.className = "ghost";
    construct.textContent = "建造 / 升级 · 1 军令";
    construct.disabled = state.strategyBusy || !canResume || orderLimitReached || !buildingSelect.children.length || !strategyCanAffordCommand(campaign, faction, "construct_city_building", {}, city.id);
    construct.addEventListener("click", () => queueStrategyAction("construct_city_building", { city_id: city.id, building_id: buildingSelect.value }));
    building.append(createStrategyField("建设项目", buildingSelect), construct);
    const buildingNames = Object.entries(city.building_levels || {}).map(([id, level]) => {
      const project = (campaign?.world?.building_projects || []).find((item) => item.id === id);
      return `${project?.name || id} ${level}级`;
    });
    if (buildingNames.length) appendTextLine(building, "strategy-meta", `已有设施：${buildingNames.join("、")}`);
    stack.append(building);
  }

  if (canHandleRebellion && (campaign?.world?.rebellion_action_choices || []).length) {
    const rebellion = createStrategyCommandSection(
      "叛乱",
      strategyCityRebellionForce(city) > 0 ? "城内已有叛军。处理民心可以减压，清剿会直接消耗兵力。" : "提前处理叛乱风险，避免正式叛军扩大。"
    );
    const rebellionSelect = document.createElement("select");
    (campaign.world.rebellion_action_choices || []).forEach((choice) => {
      const option = document.createElement("option");
      option.value = choice.id;
      option.textContent = choice.name || choice.id;
      rebellionSelect.append(option);
    });
    rebellionSelect.disabled = state.strategyBusy || !canResume || !ownCity;
    if (draft.rebellionActionId && Array.from(rebellionSelect.children).some((option) => option.value === draft.rebellionActionId)) {
      rebellionSelect.value = draft.rebellionActionId;
    }
    rebellionSelect.addEventListener("change", () => { draft.rebellionActionId = rebellionSelect.value; });
    rebellion.append(createStrategyField("叛乱处理", rebellionSelect));

    const queueRebellion = document.createElement("button");
    queueRebellion.type = "button";
    queueRebellion.className = "ghost";
    const updateRebellionCommand = () => {
      const cost = strategyCommandCost("rebellion_action", { rebellion_action_id: rebellionSelect.value });
      queueRebellion.textContent = `计划处理 · ${cost} 军令`;
      queueRebellion.disabled = state.strategyBusy || !canResume || !ownCity || orderLimitReached || !rebellionSelect.children.length || commandPoints.remaining < cost;
    };
    updateRebellionCommand();
    rebellionSelect.addEventListener("change", updateRebellionCommand);
    queueRebellion.addEventListener("click", () => queueStrategyAction("rebellion_action", {
      rebellion_action_id: rebellionSelect.value,
      city_id: city.id,
    }));
    rebellion.append(queueRebellion);

    const rebelForce = strategyCityRebellionForce(city);
    if (rebelForce > 0) {
      const rebelBattle = document.createElement("button");
      rebelBattle.type = "button";
      rebelBattle.className = "ghost";
      rebelBattle.textContent = "计划清剿 · 2 军令";
      rebelBattle.disabled = state.strategyBusy || !canResume || !ownCity || orderLimitReached || Number(city.resources?.troops || 0) < 50 || !strategyCanAffordCommand(campaign, faction, "rebellion_battle");
      rebelBattle.addEventListener("click", () => queueStrategyAction("rebellion_battle", {
        city_id: city.id,
        troops: Math.min(Number(city.resources?.troops || 0), Math.max(50, rebelForce)),
      }));
      rebellion.append(rebelBattle);
    }
    if (disabledReason || orderLimitReason || noCommandReason) appendTextLine(rebellion, "strategy-command-lock", disabledReason || orderLimitReason || noCommandReason);
    stack.append(rebellion);
  }

  const targets = strategyAttackTargetsForCity(campaign, city, faction?.id);
  if (canAttack && !lordPoliticalCrisisView && targets.length) {
    const attackSection = createStrategyCommandSection(
      office?.office_type === "lord" ? "主公亲征" : "进攻",
      "选择邻接目标和处理方式。主公亲征会自动由当前主公武将带队；快速用于沙盒结算，手动/AI 会生成真实格子战。"
    );
    const targetSelect = document.createElement("select");
    targets.forEach((target) => {
      const option = document.createElement("option");
      option.value = target.id;
      option.textContent = target.name;
      targetSelect.append(option);
    });
    if (draft.attackTargetId && Array.from(targetSelect.children).some((option) => option.value === draft.attackTargetId)) {
      targetSelect.value = draft.attackTargetId;
    }
    targetSelect.addEventListener("change", () => { draft.attackTargetId = targetSelect.value; });
    attackSection.append(createStrategyField("目标", targetSelect));

    const modeSelect = document.createElement("select");
    const modeNames = {
      manual: "手动",
      ai_auto: "AI 自动",
      watch_ai: "观看 AI",
      quick: "快速",
    };
    (campaign.world.battle_resolution_modes || ["quick"]).forEach((mode) => {
      const option = document.createElement("option");
      option.value = mode;
      option.textContent = modeNames[mode] || mode;
      modeSelect.append(option);
    });
    if (draft.attackMode && Array.from(modeSelect.children).some((option) => option.value === draft.attackMode)) {
      modeSelect.value = draft.attackMode;
    }
    modeSelect.addEventListener("change", () => { draft.attackMode = modeSelect.value; });
    attackSection.append(createStrategyField("处理", modeSelect));

    const heroSelect = document.createElement("select");
    const noHeroOption = document.createElement("option");
    noHeroOption.value = "";
    noHeroOption.textContent = "不投入";
    heroSelect.append(noHeroOption);
    strategyDeployableHeroes(faction).forEach((hero) => {
      const option = document.createElement("option");
      option.value = hero.code;
      option.textContent = hero.name || hero.code;
      heroSelect.append(option);
    });
    if (draft.attackHeroCodes?.length) heroSelect.value = draft.attackHeroCodes[0] || "";
    heroSelect.addEventListener("change", () => { draft.attackHeroCodes = heroSelect.value ? [heroSelect.value] : []; });
    const heroLabel = createStrategyField("英灵", heroSelect);
    attackSection.append(heroLabel);
    const heroMultiPicker = createStrategyHeroDeploymentPicker(faction, draft.attackHeroCodes || []);
    if (strategyHeroDeploymentLimit(faction) > 1) {
      heroSelect.disabled = true;
      heroSelect.style.display = "none";
      heroMultiPicker.setDisabled(state.strategyBusy || !canResume || !ownCity);
      heroLabel.append(heroMultiPicker.element);
      heroMultiPicker.element.addEventListener("change", () => { draft.attackHeroCodes = heroMultiPicker.selectedCodes(); });
    }
    const selectedAttackHeroes = () => (
      strategyHeroDeploymentLimit(faction) > 1
        ? heroMultiPicker.selectedCodes()
        : (heroSelect.value ? [heroSelect.value] : [])
    );

    const queueAttack = document.createElement("button");
    queueAttack.type = "button";
    queueAttack.className = "ghost";
    queueAttack.textContent = "计划进攻 · 2 军令";
    queueAttack.disabled = state.strategyBusy || !canResume || !ownCity || orderLimitReached || !strategyCanAffordCommand(campaign, faction, "declare_attack");
    queueAttack.addEventListener("click", () => queueStrategyAction("declare_attack", {
      source_city_id: city.id,
      target_city_id: targetSelect.value,
      resolution_mode: modeSelect.value,
      attacker_hero_codes: selectedAttackHeroes(),
    }));
    attackSection.append(queueAttack);
    if (disabledReason || orderLimitReason || noCommandReason) appendTextLine(attackSection, "strategy-command-lock", disabledReason || orderLimitReason || noCommandReason);
    stack.append(attackSection);
  }

  if (!stack.children.length || !ownCity) {
    appendTextLine(stack, "strategy-meta", ownCity ? "暂无可用军令。" : "该城市不属于你的势力；选择己方城市下达军令。");
  }
  card.append(stack);
  return card;
}

function strategyGuideLines(campaign, faction, selectedCity) {
  const lines = [];
  const status = campaign?.world?.strategic_status || {};
  if (status.campaign_state === "archived") {
    lines.push("战役已经结束归档；结局与完整复盘已冻结，可在战报卷宗中随时查看。");
    return lines;
  }
  if (status.awaiting_conclusion_choice) {
    lines.push(`战役已进入${status.conclusion?.result_label || "结算"}，等待房主决定结束或继续沙盒。`);
    return lines;
  }
  if (!strategyCanResume(campaign)) {
    const missing = strategyMissingInitialPlayerLabels(campaign);
    lines.push(`等待真人初始玩家在线：${missing.join("、") || "当前战役尚未满足恢复条件"}`);
    return lines;
  }
  const office = strategyActiveOffice(campaign);
  const owned = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === faction?.id);
  const managed = strategyOfficeManagedCities(campaign, office);
  const rebellion = office?.office_type === "governor" ? managed.find((city) => strategyCityRebellionForce(city) > 0) : null;
  if (rebellion) lines.push(`${rebellion.name} 有叛军，优先在城主府处理或清剿。`);
  const attackSource = office?.office_type === "general"
    ? managed.find((city) => strategyAttackTargetsForCity(campaign, city, faction?.id).length)
    : null;
  if (attackSource) {
    const targetNames = strategyAttackTargetsForCity(campaign, attackSource, faction?.id).map((city) => city.name).join("、");
    lines.push(`${attackSource.name} 可向 ${targetNames} 发起进攻。`);
  }
  const ritualCapacity = faction?.hero_ritual_capacity || { remaining: 0 };
  if (Number(ritualCapacity.remaining || 0) > 0 && office?.office_type === "lord") lines.push("职位仍有空位；可选择有祭祀场且以太充足的城市举行召唤祭祀。");
  if (office?.office_type === "governor") lines.push("先增加城市兵力，再通过兵营、马厩或靶场把兵力注册为确切单位。");
  if (office?.office_type === "grand_general") lines.push("检查各城已注册单位和直属将军库存，处理请兵申请或直接调拨。");
  if (office?.office_type === "general" && !Object.keys(office?.unit_inventory || {}).length) lines.push("军团没有确切单位；向直属大将军提交请兵申请。");
  if (selectedCity && ["general", "governor"].includes(office?.office_type)) lines.push(`当前职位操作范围：${selectedCity.name}。`);
  return lines.length ? lines : ["本月没有当前职位必须处理的紧急事项。"];
}

function renderStrategyBriefing(parent, campaign, faction) {
  const entries = strategyMonthlyBriefing(campaign, faction).entries || [];
  if (!entries.length) return;
  const labels = { threat: "威胁", opportunity: "机会", rival_intent: "敌情" };
  const grid = document.createElement("div");
  grid.className = "strategy-briefing-grid";
  entries.forEach((entry) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `strategy-briefing-entry ${entry.severity || "info"}`;
    row.disabled = !entry.city_id;
    const label = document.createElement("span");
    label.className = "strategy-briefing-label";
    label.textContent = labels[entry.kind] || "局势";
    const body = document.createElement("span");
    body.className = "strategy-briefing-body";
    const strong = document.createElement("strong");
    strong.textContent = entry.title || "未明局势";
    const detail = document.createElement("span");
    detail.textContent = entry.detail || "";
    body.append(strong, detail);
    row.append(label, body);
    if (entry.city_id) {
      row.addEventListener("click", () => {
        state.strategySelectedCityId = entry.city_id;
        renderStrategyPanel();
      });
    }
    grid.append(row);
  });
  parent.append(grid);
}

function renderStrategyAIGoals(parent, campaign, faction) {
  const goals = (campaign?.world?.ai_strategic_goals || []).filter((goal) => goal.faction_id !== faction?.id);
  if (!goals.length) return;
  const panel = document.createElement("section");
  panel.className = "strategy-ai-goals";
  const title = document.createElement("strong");
  title.textContent = "AI 战略动向";
  panel.append(title);
  appendTextLine(panel, "strategy-meta", "这些目标由公开的资源、地理和威胁决定，持续 2～3 个月；不代表 AI 获得额外资源或跳过合法条件。");
  goals.forEach((goal) => {
    const card = document.createElement("article");
    card.className = `strategy-ai-goal ${goal.status || "active"}`;
    const head = document.createElement("div");
    head.className = "strategy-ai-goal-head";
    const name = document.createElement("strong");
    name.textContent = `${goal.faction_name} · ${goal.title}`;
    const timing = document.createElement("span");
    timing.textContent = goal.status === "completed"
      ? "本月已完成"
      : `第 ${goal.start_month}～${goal.end_month} 月 · 剩余 ${goal.months_remaining} 月`;
    head.append(name, timing);
    card.append(head);
    appendTextLine(card, "strategy-meta", goal.rationale || "根据当前局势选择。 ");
    const progress = document.createElement("div");
    progress.className = "strategy-ai-goal-progress";
    progress.setAttribute("role", "progressbar");
    progress.setAttribute("aria-label", `${goal.faction_name}${goal.title}进度`);
    progress.setAttribute("aria-valuemin", "0");
    progress.setAttribute("aria-valuemax", "100");
    progress.setAttribute("aria-valuenow", String(goal.progress || 0));
    const fill = document.createElement("span");
    fill.style.width = `${Math.max(0, Math.min(100, Number(goal.progress || 0)))}%`;
    progress.append(fill);
    card.append(progress);
    appendTextLine(card, "strategy-meta", `进度 ${goal.progress || 0}% · ${goal.progress_label || "等待行动"}`);
    appendTextLine(card, "strategy-meta", `上次行动：${goal.last_action_summary || "尚无"}`);
    if (goal.change_reason) appendTextLine(card, "strategy-ai-goal-reason", `选择原因：${goal.change_reason}`);
    if (goal.target_city_id) {
      const locate = document.createElement("button");
      locate.type = "button";
      locate.className = "ghost compact";
      locate.textContent = `定位 ${goal.target_city_name || strategyCityName(campaign, goal.target_city_id)}`;
      locate.addEventListener("click", () => {
        state.strategySelectedCityId = goal.target_city_id;
        renderStrategyPanel();
        focusStrategyMapStage();
      });
      card.append(locate);
    }
    panel.append(card);
  });
  parent.append(panel);
}

function strategyRecommendedNextStep(campaign, faction, selectedCity, isOwner) {
  const status = campaign?.world?.strategic_status || {};
  if (status.campaign_state === "archived") {
    return {
      title: "战役已归档",
      detail: "本次战役不再接受军令或推进月份；请在战报卷宗查看完整复盘。",
      buttonText: "",
    };
  }
  if (status.awaiting_conclusion_choice) {
    return {
      title: status.conclusion?.result_label || "战役结算",
      detail: isOwner ? "查看完整复盘后，选择结束归档或保留结局继续沙盒。" : "等待房主决定结束归档或继续自由沙盒。",
      buttonText: "",
    };
  }
  if (!strategyCanResume(campaign)) {
    const missing = strategyMissingInitialPlayerLabels(campaign);
    return {
      title: "等待玩家",
      detail: `还不能推进：${missing.join("、") || "有初始玩家未在线"}`,
      buttonText: "",
    };
  }
  const office = strategyActiveOffice(campaign);
  const managedCityIds = new Set(strategyOfficeManagedCities(campaign, office).map((city) => city.id));
  const storyEvent = strategyPendingStoryEvent(campaign, faction);
  const canHandleStory = !office || (office.office_type === "governor" && managedCityIds.has(storyEvent?.city_id));
  if (storyEvent && canHandleStory) {
    return {
      title: "处理突发事件",
      detail: `${storyEvent.title}正在等待决定。不同选择会改变当前城市，并可能在以后产生后果。`,
      buttonText: `定位 ${strategyCityName(campaign, storyEvent.city_id)}`,
      cityId: storyEvent.city_id,
    };
  }
  const owned = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === faction?.id);
  const scopedCities = ["general", "governor"].includes(office?.office_type)
    ? owned.filter((city) => managedCityIds.has(city.id))
    : owned;
  const rebellion = office?.office_type === "governor" || !office
    ? scopedCities.find((city) => strategyCityRebellionForce(city) > 0)
    : null;
  if (rebellion) {
    return {
      title: "先稳住叛乱",
      detail: `${rebellion.name} 有 ${strategyCityRebellionForce(rebellion)} 名叛军。选择这座城后计划处理或清剿。`,
      buttonText: `定位 ${rebellion.name}`,
      cityId: rebellion.id,
    };
  }
  const attackSource = office?.office_type === "general" || !office
    ? scopedCities.find((city) => strategyAttackTargetsForCity(campaign, city, faction?.id).length)
    : null;
  if (attackSource) {
    const targets = strategyAttackTargetsForCity(campaign, attackSource, faction?.id);
    return {
      title: "可以扩张",
      detail: `${attackSource.name} 可进攻 ${targets.map((city) => city.name).join("、")}。`,
      buttonText: `选择 ${attackSource.name}`,
      cityId: attackSource.id,
    };
  }
  const ritualCity = owned.find((city) => Number(city.building_levels?.ritual_site || 0) > 0 && Number(city.resources?.ether || 0) >= 30);
  if (ritualCity && Number(faction?.hero_ritual_capacity?.remaining || 0) > 0 && (!office || office.office_type === "lord")) {
    return {
      title: "举行祭祀",
      detail: `${ritualCity.name}拥有足够以太和可用祭祀场，可随机召唤一名武将。`,
      buttonText: "",
    };
  }
  if (isOwner && (!office || office.office_type === "lord")) {
    return {
      title: "本月可结算",
      detail: selectedCity ? `检查 ${selectedCity.name} 的军令后，可以推进到下个月。` : "没有紧急事项，可以推进到下个月。",
      buttonText: "推进一月",
      advance: true,
    };
  }
  return {
    title: "等待房主结算",
    detail: "你的本月操作完成后，等待房主推进月份。",
    buttonText: "",
  };
}

function renderStrategyStoryEvent(parent, campaign, faction) {
  const event = strategyPendingStoryEvent(campaign, faction);
  if (!event) return;
  const office = strategyActiveOffice(campaign);
  const managedCityIds = new Set(strategyOfficeManagedCities(campaign, office).map((city) => city.id));
  if (office && (office.office_type !== "governor" || !managedCityIds.has(event.city_id))) return;
  const queued = (campaign?.queued_actions || []).find((action) => (
    action.faction_id === faction?.id && action.action_type === "resolve_story_event" && action.action_key === event.id
  ));
  const city = strategyCityById(campaign, event.city_id);
  const panel = document.createElement("section");
  panel.className = "strategy-story-event";
  const eyebrow = document.createElement("span");
  eyebrow.className = "strategy-story-eyebrow";
  eyebrow.textContent = `待决事件 · ${city?.name || "未知地点"}`;
  const title = document.createElement("strong");
  title.textContent = event.title || "突发事件";
  panel.append(eyebrow, title);
  appendTextLine(panel, "strategy-story-description", event.description || "");
  appendTextLine(panel, "strategy-story-deadline", "本月底未处理将自动采用放任结果。事件选择消耗 1 点势力军令。");
  if (queued) {
    const selected = (event.choices || []).find((choice) => choice.id === queued.payload?.choice_id);
    appendTextLine(panel, "strategy-story-planned", `已计划：${selected?.label || queued.payload?.choice_id || "未知选择"}（可以替换）`);
  }
  const choices = document.createElement("div");
  choices.className = "strategy-story-choices";
  (event.choices || []).forEach((choice) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = queued?.payload?.choice_id === choice.id ? "primary" : "ghost";
    const label = document.createElement("strong");
    label.textContent = choice.label || choice.id;
    const preview = document.createElement("span");
    preview.textContent = choice.preview || "";
    button.append(label, preview);
    button.disabled = state.strategyBusy || !strategyCanIssueOrders(campaign) || !choice.enabled || !strategyCanAffordCommand(
      campaign,
      faction,
      "resolve_story_event",
      { event_id: event.id, choice_id: choice.id },
      event.id
    );
    button.addEventListener("click", () => queueStrategyAction("resolve_story_event", {
      event_id: event.id,
      choice_id: choice.id,
    }));
    choices.append(button);
    if (!choice.enabled && choice.disabled_reason) appendTextLine(choices, "strategy-command-lock", choice.disabled_reason);
  });
  panel.append(choices);
  const consequences = (campaign?.world?.scheduled_consequences || []).filter((item) => item.faction_id === faction?.id);
  consequences.slice(0, 2).forEach((item) => appendTextLine(
    panel,
    "strategy-story-thread",
    `未完影响 · 第 ${item.due_month} 月：${item.description}`
  ));
  parent.append(panel);
}

function renderStrategyGuide(parent, campaign, faction, selectedCity, isOwner) {
  const guide = document.createElement("div");
  guide.className = "strategy-guide";
  renderStrategyCampaignTutorial(guide, campaign, faction, selectedCity);
  renderStrategyOfficeCoordination(guide, campaign, faction);
  const next = strategyRecommendedNextStep(campaign, faction, selectedCity, isOwner);
  const head = document.createElement("div");
  head.className = "strategy-guide-head";
  const title = document.createElement("strong");
  title.textContent = "本月军令";
  const nextTitle = document.createElement("span");
  nextTitle.textContent = `下一步：${next.title}`;
  head.append(title, nextTitle);
  guide.append(head);
  appendTextLine(guide, "strategy-guide-main", next.detail);
  renderStrategyStoryEvent(guide, campaign, faction);
  renderStrategyBriefing(guide, campaign, faction);
  renderStrategyAIGoals(guide, campaign, faction);
  const stepRow = document.createElement("div");
  stepRow.className = "strategy-step-row";
  ["看地图", "选城市", "下军令", "等房主结算"].forEach((label) => {
    const chip = document.createElement("span");
    chip.className = "strategy-step-chip";
    chip.textContent = label;
    stepRow.append(chip);
  });
  guide.append(stepRow);
  strategyGuideLines(campaign, faction, selectedCity).forEach((line) => appendTextLine(guide, "strategy-meta", line));
  const quickActions = document.createElement("div");
  quickActions.className = "strategy-guide-actions";
  const mapButton = document.createElement("button");
  mapButton.type = "button";
  mapButton.className = "ghost";
  mapButton.textContent = "查看地图";
  mapButton.addEventListener("click", focusStrategyMapStage);
  const commandButton = document.createElement("button");
  commandButton.type = "button";
  commandButton.className = "primary";
  commandButton.textContent = "打开军令";
  commandButton.disabled = !selectedCity;
  commandButton.addEventListener("click", focusStrategyCommandPanel);
  quickActions.append(mapButton, commandButton);
  guide.append(quickActions);
  if (next.buttonText) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.textContent = next.buttonText;
    button.disabled = state.strategyBusy || (next.advance && (!strategyCanIssueOrders(campaign) || !isOwner));
    button.addEventListener("click", () => {
      if (next.cityId) {
        state.strategySelectedCityId = next.cityId;
        renderStrategyPanel();
        return;
      }
      if (next.advance) advanceStrategyMonth();
    });
    guide.append(button);
  }
  parent.append(guide);
}

function renderStrategyOfficeCoordination(parent, campaign, faction) {
  const coordination = strategyOfficeCoordination(campaign, faction);
  if (!coordination) return;
  const panel = document.createElement("section");
  panel.className = "strategy-office-coordination";
  const title = document.createElement("strong");
  title.textContent = "本月关键决策";
  panel.append(title);
  const decisions = coordination.high_consequence_decisions || [];
  appendTextLine(panel, "strategy-meta", `优先处理 ${decisions.length} 项高后果决定；常规维护由持续方针或 AI 官职承担。`);
  decisions.forEach((decision, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = decision.planned ? "strategy-decision planned" : "strategy-decision ghost";
    const number = document.createElement("span");
    number.textContent = decision.planned ? "✓" : String(index + 1);
    const body = document.createElement("span");
    const strong = document.createElement("strong");
    strong.textContent = decision.title;
    const detail = document.createElement("span");
    detail.textContent = decision.planned ? `${decision.detail}（已安排）` : decision.detail;
    body.append(strong, detail);
    button.append(number, body);
    button.disabled = !decision.city_id;
    button.addEventListener("click", () => {
      state.strategySelectedCityId = decision.city_id;
      renderStrategyPanel();
      focusStrategyCommandPanel();
    });
    panel.append(button);
  });

  const routine = document.createElement("details");
  routine.className = "strategy-routine-maintenance";
  const summary = document.createElement("summary");
  summary.textContent = `常规维护 · ${(coordination.routine_maintenance || []).length} 座城市`;
  routine.append(summary);
  appendTextLine(routine, "strategy-meta", coordination.automation_rule || "默认方针持续生效。 ");
  (coordination.routine_maintenance || []).forEach((item) => {
    const executor = (campaign?.world?.offices || []).find((office) => office.id === item.executor_office_id);
    appendTextLine(
      routine,
      "strategy-meta",
      `${item.city_name} · ${item.policy} · ${item.mode === "ai_emergency" ? `${strategyOfficeLabel(executor, campaign)}在生存危机时自动干预` : "沿用默认方针"}`
    );
  });
  panel.append(routine);

  const feedback = coordination.order_feedback || [];
  if (feedback.length) {
    const feedbackTitle = document.createElement("strong");
    feedbackTitle.textContent = "命令与请求回执";
    panel.append(feedbackTitle);
    feedback.slice(-4).reverse().forEach((item) => {
      const issuer = (campaign?.world?.offices || []).find((office) => office.id === item.issuer_office_id);
      const executor = (campaign?.world?.offices || []).find((office) => office.id === item.executor_office_id);
      appendTextLine(
        panel,
        "strategy-order-feedback",
        `${strategyOfficeLabel(issuer, campaign)} → ${strategyOfficeLabel(executor, campaign)} · ${item.command_cost} 军令 · 预计第 ${item.expected_completion_month} 月 · ${STRATEGY_OFFICE_STATUS_LABELS[item.status] || "已计划"}：${item.result_summary}`
      );
    });
  }
  parent.append(panel);
}

function renderStrategyCampaignTutorial(parent, campaign, faction, selectedCity) {
  const tutorial = strategyCampaignGuide(campaign, faction);
  if (!tutorial?.enabled) return;
  if (tutorial.skipped) {
    appendTextLine(parent, "strategy-tutorial-compact", `前三个月引导已于第 ${tutorial.skipped_month || "?"} 月跳过；普通战役规则与资源不变。`);
    return;
  }
  if (tutorial.completed) {
    appendTextLine(parent, "strategy-tutorial-compact complete", `前三个月战役引导完成 · ${tutorial.completed_count}/${tutorial.total_count}`);
    return;
  }

  const panel = document.createElement("section");
  panel.className = "strategy-campaign-tutorial";
  const head = document.createElement("div");
  head.className = "strategy-campaign-tutorial-head";
  const title = document.createElement("strong");
  title.textContent = "前三个月战役引导";
  const progress = document.createElement("span");
  progress.textContent = `${tutorial.completed_count}/${tutorial.total_count} 已完成`;
  head.append(title, progress);
  panel.append(head);
  appendTextLine(
    panel,
    "strategy-meta",
    tutorial.guide_period_ended
      ? "前三个月已经结束；未完成目标仍可补做，也可以跳过引导。"
      : "情境目标只帮助你走完第一场战役，不会锁住月份或改变普通规则。"
  );

  (tutorial.steps || []).forEach((step) => {
    const row = document.createElement("div");
    row.className = `strategy-campaign-tutorial-step ${step.timing || "upcoming"}`;
    const marker = document.createElement("span");
    marker.className = "strategy-campaign-tutorial-marker";
    marker.textContent = step.completed ? "✓" : step.timing === "upcoming" ? String(step.month) : "○";
    const body = document.createElement("div");
    const stepTitle = document.createElement("strong");
    stepTitle.textContent = `${step.chapter} · ${step.title}`;
    body.append(stepTitle);
    appendTextLine(body, "strategy-meta", step.detail);
    row.append(marker, body);
    if (!step.completed && step.timing !== "upcoming") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      const labels = {
        map: "查看边境",
        city_command: "打开城市治理",
        story: "处理事件",
        organization: "查看祭祀与任命",
        conflict: "准备边境冲突",
      };
      button.textContent = labels[step.action_kind] || "前往处理";
      button.disabled = state.strategyBusy || !strategyCanIssueOrders(campaign) && step.action_kind !== "map";
      button.addEventListener("click", async () => {
        if (step.action_kind === "map") {
          const updated = await updateStrategyCampaignGuide("survey_border");
          if (updated) focusStrategyMapStage();
          return;
        }
        const office = strategyActiveOffice(campaign);
        const story = strategyPendingStoryEvent(campaign, faction);
        const owned = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === faction?.id);
        const border = owned.find((city) => strategyAttackTargetsForCity(campaign, city, faction?.id).length);
        const targetCityId = step.action_kind === "story" ? story?.city_id : step.action_kind === "conflict" ? border?.id : selectedCity?.id || owned[0]?.id;
        const canDirectlyHandle = (
          ["city_command", "story"].includes(step.action_kind) && office?.office_type === "governor"
        ) || (
          step.action_kind === "organization" && ["lord", "governor"].includes(office?.office_type)
        ) || step.action_kind === "conflict";
        if (!canDirectlyHandle && ["city_command", "story", "organization"].includes(step.action_kind)) {
          const receiver = strategyTutorialDelegateOffice(campaign, office, targetCityId);
          if (receiver) {
            const goalId = step.action_kind === "city_command" ? "set_policy" : step.action_kind === "story" ? "resolve_event" : "ritual_or_appoint";
            const objective = step.action_kind === "city_command"
              ? "安排城主检查粮食与民心并设置城市方针"
              : step.action_kind === "story"
                ? `处理${story?.title || "本地待决事件"}，避免月底放任`
                : "安排一次召唤祭祀或向主公申请任命武将";
            queueStrategyAction(office?.office_type === "lord" ? "issue_office_order" : "send_office_request", {
              receiver_office_id: receiver.id,
              objective: `[引导:${goalId}] ${objective}`,
              target_entity_id: targetCityId || "",
              priority: 3,
              deadline_month: campaign.world.current_month,
              office_order_type: "order",
            });
            return;
          }
        }
        if (targetCityId) state.strategySelectedCityId = targetCityId;
        renderStrategyPanel();
        focusStrategyCommandPanel();
      });
      row.append(button);
    }
    panel.append(row);
  });

  appendTextLine(panel, "strategy-tutorial-skip-note", tutorial.skip_explanation || "跳过不会改变战役规则。 ");
  const skip = document.createElement("button");
  skip.type = "button";
  skip.className = "ghost strategy-tutorial-skip";
  skip.textContent = "跳过情境引导";
  skip.disabled = state.strategyBusy;
  skip.addEventListener("click", () => updateStrategyCampaignGuide("skip"));
  panel.append(skip);
  parent.append(panel);
}

function strategyTutorialDelegateOffice(campaign, office, cityId = "") {
  const offices = campaign?.world?.offices || [];
  if (!office) return null;
  if (office.office_type === "lord") {
    return offices.find((candidate) => (
      candidate.parent_office_id === office.id
      && candidate.office_type === "governor"
      && (!cityId || (candidate.managed_entity_ids || []).includes(cityId))
    )) || null;
  }
  return offices.find((candidate) => candidate.id === office.parent_office_id) || null;
}

function renderStrategyWarStateBanner(parent, campaign, canResume, isOwner) {
  if (campaign?.status === "active" && canResume) return;
  const banner = document.createElement("div");
  banner.className = "strategy-war-state";
  const text = document.createElement("strong");
  if (campaign?.status !== "active") {
    text.textContent = isOwner ? "战役大厅尚未锁定" : "等待房主锁定初始玩家";
    banner.append(text);
    appendTextLine(
      banner,
      "strategy-meta",
      isOwner ? "锁定后未加入的初始势力会由 AI 操作，真人初始玩家需要在线才能继续。" : "锁定后才能进入正式战役，空席会交给 AI。"
    );
    if (isOwner) {
      const lock = document.createElement("button");
      lock.type = "button";
      lock.className = "primary";
      lock.textContent = "锁定并启用 AI";
      lock.disabled = state.strategyBusy;
      lock.addEventListener("click", () => lockStrategyCampaign(campaign.id));
      banner.append(lock);
    }
  } else {
    const missing = strategyMissingInitialPlayerLabels(campaign);
    text.textContent = "等待初始玩家回到战役";
    banner.append(text);
    appendTextLine(banner, "strategy-meta", `仍缺席：${missing.join("、") || "未知玩家"}`);
  }
  parent.append(banner);
}

function renderStrategyOfficeSwitcher(parent, campaign, activeOffice) {
  const offices = strategyControlledOffices(campaign);
  if (!offices.length) return;
  const bar = document.createElement("nav");
  bar.className = "strategy-office-switcher";
  bar.setAttribute("aria-label", "职位切换");
  offices.forEach((office) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = office.id === activeOffice?.id ? "active" : "ghost";
    button.textContent = strategyOfficeLabel(office, campaign);
    button.dataset.officeType = office.office_type;
    button.addEventListener("click", () => {
      state.strategyActiveOfficeId = office.id;
      const managedCity = strategyOfficeManagedCities(campaign, office)[0];
      if (managedCity) state.strategySelectedCityId = managedCity.id;
      renderStrategyPanel();
    });
    bar.append(button);
  });
  parent.append(bar);
}

function createStrategyOfficeDesk(campaign, office, canResume) {
  const desk = document.createElement("section");
  desk.className = "strategy-office-desk";
  const title = document.createElement("h4");
  title.textContent = `${strategyOfficeLabel(office, campaign)}案牍`;
  desk.append(title);
  const duties = (campaign?.world?.office_duties || []).filter((duty) => (
    duty.office_id === office?.id
      && duty.status === "pending"
      && Number(duty.due_month || campaign?.world?.current_month) === Number(campaign?.world?.current_month)
  ));
  const dutyList = document.createElement("div");
  dutyList.className = "strategy-office-duties";
  duties.slice(0, 4).forEach((duty) => {
    const row = document.createElement("div");
    row.className = `strategy-office-duty priority-${duty.priority || 1}`;
    row.textContent = STRATEGY_DUTY_LABELS[duty.duty_type] || "待办职责";
    dutyList.append(row);
  });
  if (!duties.length) appendTextLine(dutyList, "strategy-meta", "本月职责已清。");
  desk.append(dutyList);
  const orders = (campaign?.world?.office_orders || []).filter((order) => order.issuer_office_id === office?.id || order.receiver_office_id === office?.id);
  orders.slice(-3).reverse().forEach((order) => {
    appendTextLine(
      desk,
      "strategy-office-order",
      `${order.receiver_office_id === office?.id ? "收到" : "发出"} · ${order.objective} · ${STRATEGY_OFFICE_STATUS_LABELS[order.status] || order.status}${order.details?.result_summary ? ` · ${order.details.result_summary}` : ""}`
    );
  });
  const isRequest = ["general", "governor"].includes(office?.office_type);
  const receiverIds = isRequest ? [office?.parent_office_id] : (office?.subordinate_office_ids || []);
  const receivers = (campaign?.world?.offices || []).filter((item) => receiverIds.includes(item.id) && item.status === "active");
  if (receivers.length) {
    const controls = document.createElement("div");
    controls.className = "strategy-office-order-controls";
    const receiver = document.createElement("select");
    receivers.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = strategyOfficeLabel(item, campaign);
      receiver.append(option);
    });
    const orderKind = document.createElement("select");
    [
      ["order", "一般目标"],
      ["attack_city", "进攻城市"],
      ["defend_city", "防守城市"],
      ["set_policy", "设置城市方针"],
    ].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      orderKind.append(option);
    });
    const targetCity = document.createElement("select");
    (campaign?.world?.cities || []).forEach((city) => {
      const option = document.createElement("option");
      option.value = city.id;
      option.textContent = `${city.name} · ${strategyFactionName(campaign, city.owner_faction_id)}`;
      option.dataset.ownerFactionId = city.owner_faction_id;
      targetCity.append(option);
    });
    const cityPolicy = document.createElement("select");
    (campaign?.world?.policy_choices || []).forEach((policy) => {
      const option = document.createElement("option");
      option.value = policy;
      option.textContent = policy;
      cityPolicy.append(option);
    });
    orderKind.hidden = isRequest;
    targetCity.hidden = true;
    cityPolicy.hidden = true;
    const syncMilitaryOrder = () => {
      const military = !isRequest && ["attack_city", "defend_city"].includes(orderKind.value);
      const policyOrder = !isRequest && orderKind.value === "set_policy";
      targetCity.hidden = !(military || policyOrder);
      cityPolicy.hidden = !policyOrder;
      Array.from(targetCity.children).forEach((option) => {
        option.disabled = policyOrder && option.dataset.ownerFactionId !== office?.faction_id;
      });
      if (military && office?.office_type === "lord") {
        const grand = receivers.find((item) => item.office_type === "grand_general");
        if (grand) receiver.value = grand.id;
      }
      if (policyOrder && office?.office_type === "lord") {
        const governor = receivers.find((item) => item.id === receiver.value && item.office_type === "governor")
          || receivers.find((item) => item.office_type === "governor");
        if (governor) {
          receiver.value = governor.id;
          const governedCityId = governor.managed_entity_ids?.[0];
          if (governedCityId) targetCity.value = governedCityId;
        }
      }
    };
    orderKind.addEventListener("change", syncMilitaryOrder);
    receiver.addEventListener("change", syncMilitaryOrder);
    syncMilitaryOrder();
    const objective = document.createElement("input");
    objective.type = "text";
    objective.maxLength = 120;
    objective.placeholder = isRequest ? "向上级请求支援或批准" : "向直属下级下达目标";
    const issue = document.createElement("button");
    issue.type = "button";
    issue.className = "primary";
    issue.textContent = isRequest ? "提交请求" : "下达命令 · 1军令";
    issue.disabled = state.strategyBusy || !canResume;
    issue.addEventListener("click", () => {
      const military = !isRequest && ["attack_city", "defend_city"].includes(orderKind.value);
      const policyOrder = !isRequest && orderKind.value === "set_policy";
      const objectiveText = objective.value.trim() || (
        military
          ? `${orderKind.value === "attack_city" ? "进攻" : "防守"}${strategyCityName(campaign, targetCity.value)}`
          : policyOrder
            ? `将${strategyCityName(campaign, targetCity.value)}设为${cityPolicy.value}`
            : ""
      );
      if (!objectiveText) {
        objective.focus();
        return;
      }
      queueStrategyAction(isRequest ? "send_office_request" : "issue_office_order", {
        receiver_office_id: receiver.value,
        objective: objectiveText,
        office_order_type: isRequest ? "request" : orderKind.value,
        target_entity_id: military || policyOrder ? targetCity.value : "",
        city_policy: policyOrder ? cityPolicy.value : "",
        priority: 1,
      });
    });
    controls.append(receiver, orderKind, targetCity, cityPolicy, objective, issue);
    desk.append(controls);
  }
  return desk;
}

function createLordHeroBindingPanel(campaign, office, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-ritual-bindings";
  const title = document.createElement("h4");
  title.textContent = "祭祀绑定名册";
  panel.append(title);
  const faction = (campaign?.world?.factions || []).find((item) => item.id === office?.faction_id);
  const capacity = faction?.hero_ritual_capacity || { maximum: 0, used: 0, remaining: 0 };
  appendTextLine(panel, "strategy-meta", `职位承载 ${capacity.used}/${capacity.maximum} · 可继续召唤 ${capacity.remaining}`);
  const heroes = (campaign?.world?.strategic_hero_pool || []).filter((hero) => (
    hero.faction_id === office?.faction_id && hero.ritual_city_id && hero.office_id !== office?.id
  ));
  heroes.forEach((hero) => {
    const row = document.createElement("div");
    row.className = "strategy-hero-duty-row strategy-binding-row";
    const identity = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = hero.name || hero.code;
    identity.append(name);
    const heldOffice = (campaign?.world?.offices || []).find((item) => item.id === hero.office_id);
    appendTextLine(identity, "strategy-meta", `${strategyCityName(campaign, hero.ritual_city_id)}祭祀场 · ${heldOffice ? strategyOfficeLabel(heldOffice, campaign) : "待任命"}`);
    const unbind = document.createElement("button");
    unbind.type = "button";
    unbind.className = "danger";
    unbind.textContent = "解除绑定";
    unbind.disabled = state.strategyBusy || !canResume;
    unbind.addEventListener("click", () => queueStrategyAction("unbind_strategic_hero", { hero_code: hero.code }));
    row.append(identity, unbind);
    panel.append(row);
  });
  if (!heroes.length) appendTextLine(panel, "strategy-meta", "没有可由主公解除绑定的武将。");
  return panel;
}

function createStrategyHeroAppointmentPanel(campaign, office, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-hero-appointments";
  const title = document.createElement("h4");
  title.textContent = "任命武将";
  panel.append(title);
  const officeOrder = { grand_general: 0, general: 1, governor: 2 };
  const offices = (campaign?.world?.offices || []).filter((item) => (
    item.faction_id === office?.faction_id
      && item.office_type !== "lord"
      && item.status !== "disabled"
  )).sort((first, second) => (
    (officeOrder[first.office_type] ?? 9) - (officeOrder[second.office_type] ?? 9)
      || strategyOfficeLabel(first, campaign).localeCompare(strategyOfficeLabel(second, campaign), "zh-CN")
  ));
  const heroes = (campaign?.world?.strategic_hero_pool || []).filter((hero) => (
    hero.faction_id === office?.faction_id && hero.status === "serving" && !hero.office_id
  ));
  if (!offices.length || !heroes.length) {
    appendTextLine(panel, "strategy-meta", heroes.length ? "当前没有可任命职位。" : "先在祭祀场召唤武将，再进行任命。");
    return panel;
  }
  const controls = document.createElement("div");
  controls.className = "strategy-office-order-controls";
  const heroSelect = document.createElement("select");
  heroes.forEach((hero) => {
    const option = document.createElement("option");
    option.value = hero.code;
    option.textContent = `${hero.name} · ${hero.role || "武将"}`;
    heroSelect.append(option);
  });
  const officeSelect = document.createElement("select");
  offices.forEach((target) => {
    const option = document.createElement("option");
    option.value = target.id;
    const holder = target.holder_type === "hero" ? ` · 现任 ${strategyHeroName(campaign, target.holder_id)}` : " · 空缺";
    option.textContent = `${strategyOfficeLabel(target, campaign)}${holder}`;
    officeSelect.append(option);
  });
  const appoint = document.createElement("button");
  appoint.type = "button";
  appoint.className = "primary";
  appoint.textContent = "任命 · 1军令";
  appoint.disabled = state.strategyBusy || !canResume;
  appoint.addEventListener("click", () => queueStrategyAction("appoint_strategic_hero", {
    target_office_id: officeSelect.value,
    hero_code: heroSelect.value,
  }));
  controls.append(createStrategyField("武将", heroSelect), createStrategyField("任命职位", officeSelect), appoint);
  panel.append(controls);
  return panel;
}

function createRoleWorkspaceHeader(campaign, office, title, subtitle) {
  const header = document.createElement("header");
  header.className = `strategy-role-header role-${office?.office_type || "none"}`;
  const copy = document.createElement("div");
  appendTextLine(copy, "meta-label", strategyOfficeLabel(office, campaign));
  const heading = document.createElement("h3");
  heading.textContent = title;
  copy.append(heading);
  appendTextLine(copy, "strategy-meta", subtitle);
  const seal = document.createElement("strong");
  seal.className = "strategy-role-seal";
  seal.textContent = STRATEGY_OFFICE_LABELS[office?.office_type] || "职位";
  header.append(copy, seal);
  return header;
}

function createLordRitualPanel(campaign, office, faction, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-lord-ritual";
  const title = document.createElement("h4");
  title.textContent = "举行召唤祭祀";
  panel.append(title);
  const capacity = faction?.hero_ritual_capacity || { maximum: 0, used: 0, remaining: 0 };
  appendTextLine(panel, "strategy-meta", `当前职位承载 ${capacity.used}/${capacity.maximum}；每次祭祀消耗 30 城市以太。`);
  const cities = (campaign?.world?.cities || []).filter((city) => (
    city.owner_faction_id === faction?.id && Number(city.building_levels?.ritual_site || 0) > 0
  ));
  const citySelect = document.createElement("select");
  cities.forEach((city) => {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = `${city.name} · 祭祀场 ${city.building_levels?.ritual_site || 0}级 · 以太 ${city.resources?.ether || 0}`;
    citySelect.append(option);
  });
  citySelect.value = cities[0]?.id || "";
  const issue = document.createElement("button");
  issue.type = "button";
  issue.className = "primary";
  issue.textContent = "举行祭祀 · 1 军令";
  const update = () => {
    const city = strategyCityById(campaign, citySelect.value);
    issue.disabled = state.strategyBusy || !canResume || !citySelect.children.length || Number(capacity.remaining || 0) < 1 || Number(city?.resources?.ether || 0) < 30;
  };
  citySelect.addEventListener("change", update);
  issue.addEventListener("click", () => queueStrategyAction("perform_hero_ritual", { city_id: citySelect.value }));
  panel.append(createStrategyField("祭祀城市", citySelect), issue);
  update();
  return panel;
}

function createLordTechnologyPanel(campaign, faction, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-lord-tech";
  const title = document.createElement("h4");
  title.textContent = "国家科技树";
  panel.append(title);
  const branchLabels = { office: "职位", unit: "兵种", building: "建筑", military: "战术" };
  const available = (faction?.tactic_tech_tree || []).filter((tech) => !tech.unlocked);
  const select = document.createElement("select");
  available.forEach((tech) => {
    const option = document.createElement("option");
    option.value = tech.id;
    option.disabled = !tech.available;
    option.textContent = `${branchLabels[tech.branch] || "战术"} · ${tech.name}${tech.available ? "" : "（前置未满足）"}`;
    select.append(option);
  });
  select.value = available[0]?.id || "";
  const detail = document.createElement("p");
  detail.className = "strategy-meta strategy-tech-detail";
  const unlock = document.createElement("button");
  unlock.type = "button";
  unlock.className = "primary";
  unlock.textContent = "研究科技 · 1 军令";
  const update = () => {
    const tech = available.find((item) => item.id === select.value);
    detail.textContent = tech ? `${tech.description} · 钱 ${tech.money_cost} / 以太 ${tech.ether_cost}` : "科技树已全部完成。";
    unlock.disabled = state.strategyBusy || !canResume || !tech?.available || Number(faction?.resources?.money || 0) < Number(tech?.money_cost || 0) || Number(faction?.resources?.ether || 0) < Number(tech?.ether_cost || 0);
  };
  select.addEventListener("change", update);
  unlock.addEventListener("click", () => queueStrategyAction("unlock_tactic_tech", { tech_id: select.value }));
  panel.append(createStrategyField("研究项目", select), detail, unlock);
  update();
  return panel;
}

function createLordHeroDutyPanel(campaign, office, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-hero-duty-board";
  const title = document.createElement("h4");
  title.textContent = "武将任务总览";
  panel.append(title);
  const heroes = (campaign?.world?.strategic_hero_pool || []).filter((hero) => hero.faction_id === office?.faction_id && hero.status !== "roaming");
  const cities = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === office?.faction_id);
  const dutyLabels = {
    reserve: "待命",
    administration: "辅佐内政",
    training: "训练军队",
    garrison: "驻守城市",
    campaign: "随军出征",
  };
  heroes.forEach((hero) => {
    const row = document.createElement("div");
    row.className = "strategy-hero-duty-row";
    const identity = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = hero.name || hero.code;
    identity.append(name);
    const heldOffice = (campaign?.world?.offices || []).find((entry) => entry.id === hero.office_id);
    appendTextLine(identity, "strategy-meta", heldOffice ? strategyOfficeLabel(heldOffice, campaign) : "未任职");
    const duty = document.createElement("select");
    Object.entries(dutyLabels).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      duty.append(option);
    });
    duty.value = hero.assignment_type || "reserve";
    const target = document.createElement("select");
    cities.forEach((city) => {
      const option = document.createElement("option");
      option.value = city.id;
      option.textContent = city.name;
      target.append(option);
    });
    target.value = hero.assignment_target_id || cities[0]?.id || "";
    const syncTarget = () => { target.hidden = !["training", "garrison"].includes(duty.value); };
    duty.addEventListener("change", syncTarget);
    syncTarget();
    const assign = document.createElement("button");
    assign.type = "button";
    assign.className = "ghost";
    assign.textContent = "安排";
    assign.disabled = state.strategyBusy || !canResume;
    assign.addEventListener("click", () => queueStrategyAction("assign_strategic_hero_duty", {
      hero_code: hero.code,
      assignment_type: duty.value,
      target_id: target.hidden ? "" : target.value,
    }));
    row.append(identity, duty, target, assign);
    panel.append(row);
  });
  if (!heroes.length) appendTextLine(panel, "strategy-meta", "当前没有可安排的已仕官武将。");
  return panel;
}

function createGrandGeneralMilitaryPanel(campaign, office, faction, canResume, selectedCity) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-theater-command";
  const title = document.createElement("h4");
  title.textContent = "战区军务";
  panel.append(title);
  const generals = (campaign?.world?.offices || []).filter((entry) => (office?.subordinate_office_ids || []).includes(entry.id));
  const roster = document.createElement("div");
  roster.className = "strategy-general-roster";
  generals.forEach((general) => {
    const item = document.createElement("div");
    item.className = "strategy-general-roster-item";
    const holder = general.holder_type === "hero" ? strategyHeroName(campaign, general.holder_id) : "职位空缺";
    appendTextLine(item, "strategy-meta", strategyOfficeLabel(general, campaign));
    const strong = document.createElement("strong");
    strong.textContent = holder;
    item.append(strong);
    appendTextLine(item, "strategy-meta", `${(general.managed_entity_ids || []).filter((id) => strategyCityById(campaign, id)).map((id) => strategyCityName(campaign, id)).join("、") || "尚未分配驻地"}`);
    appendTextLine(item, "strategy-unit-ledger", `军团单位：${strategyRegisteredUnitsLabel(campaign, general.unit_inventory)}`);
    roster.append(item);
  });
  panel.append(roster);
  const cities = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === faction?.id);
  const citySelect = document.createElement("select");
  cities.forEach((city) => {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = `${city.name} · ${strategyRegisteredUnitsLabel(campaign, city.registered_units)}`;
    citySelect.append(option);
  });
  citySelect.value = cities[0]?.id || "";
  if (selectedCity && cities.some((city) => city.id === selectedCity.id)) citySelect.value = selectedCity.id;
  const generalSelect = document.createElement("select");
  generals.filter((general) => general.status === "active").forEach((general) => {
    const option = document.createElement("option");
    option.value = general.id;
    option.textContent = `${strategyOfficeLabel(general, campaign)} · ${strategyHeroName(campaign, general.holder_id)}`;
    generalSelect.append(option);
  });
  generalSelect.value = generalSelect.children[0]?.value || "";
  const unitSelect = document.createElement("select");
  const count = document.createElement("input");
  count.type = "number";
  count.min = "1";
  count.max = "12";
  count.value = "1";
  const transfer = document.createElement("button");
  transfer.type = "button";
  transfer.className = "primary";
  transfer.textContent = "调拨给直属将军 · 1 军令";
  const syncUnits = () => {
    unitSelect.innerHTML = "";
    const city = strategyCityById(campaign, citySelect.value);
    Object.entries(city?.registered_units || {}).filter(([, amount]) => Number(amount) > 0).forEach(([unitType, amount]) => {
      const option = document.createElement("option");
      option.value = unitType;
      option.textContent = `${strategyRegisteredUnitsLabel(campaign, { [unitType]: amount })} 可调`;
      unitSelect.append(option);
    });
    unitSelect.value = unitSelect.children[0]?.value || "";
    transfer.disabled = state.strategyBusy || !canResume || !unitSelect.children.length || !generalSelect.children.length;
  };
  citySelect.addEventListener("change", syncUnits);
  transfer.addEventListener("click", () => queueStrategyAction("transfer_registered_units", {
    city_id: citySelect.value,
    general_office_id: generalSelect.value,
    unit_type: unitSelect.value,
    count: Math.max(1, Number(count.value || 1)),
  }));
  panel.append(
    createStrategyField("调出城市", citySelect),
    createStrategyField("接收将军", generalSelect),
    createStrategyField("确切兵种", unitSelect),
    createStrategyField("数量", count),
    transfer,
  );
  syncUnits();

  const requests = (campaign?.world?.office_orders || []).filter((order) => (
    order.order_type === "unit_request" && order.receiver_office_id === office?.id && order.status === "pending"
  ));
  if (requests.length) {
    const requestTitle = document.createElement("h4");
    requestTitle.textContent = "待批调兵申请";
    panel.append(requestTitle);
    requests.forEach((request) => {
      const row = document.createElement("div");
      row.className = "strategy-unit-request";
      const general = (campaign?.world?.offices || []).find((item) => item.id === request.issuer_office_id);
      appendTextLine(row, "strategy-meta", `${strategyOfficeLabel(general, campaign)} · ${request.objective} · ${strategyCityName(campaign, request.details?.city_id || request.target_entity_id)}`);
      const approve = document.createElement("button");
      approve.type = "button";
      approve.className = "primary";
      approve.textContent = "批准调拨";
      approve.disabled = state.strategyBusy || !canResume;
      approve.addEventListener("click", () => queueStrategyAction("approve_registered_unit_request", { request_id: request.id }));
      row.append(approve);
      panel.append(row);
    });
  }
  return panel;
}

function createGeneralLogisticsPanel(campaign, office, faction, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-general-logistics";
  const title = document.createElement("h4");
  title.textContent = "军团编制与请兵";
  panel.append(title);
  appendTextLine(panel, "strategy-unit-ledger strategy-unit-ledger-prominent", `当前军团：${strategyRegisteredUnitsLabel(campaign, office?.unit_inventory)}`);
  const cities = (campaign?.world?.cities || []).filter((city) => (
    city.owner_faction_id === faction?.id && Object.values(city.registered_units || {}).some((amount) => Number(amount) > 0)
  ));
  if (!cities.length) {
    appendTextLine(panel, "strategy-meta", "己方城市暂无已注册单位；请城主先注册士兵。");
    return panel;
  }
  const citySelect = document.createElement("select");
  cities.forEach((city) => {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = `${city.name} · ${strategyRegisteredUnitsLabel(campaign, city.registered_units)}`;
    citySelect.append(option);
  });
  citySelect.value = cities[0]?.id || "";
  const unitSelect = document.createElement("select");
  const count = document.createElement("input");
  count.type = "number";
  count.min = "1";
  count.max = "12";
  count.value = "1";
  const request = document.createElement("button");
  request.type = "button";
  request.className = "primary";
  request.textContent = "请示直属大将军";
  const sync = () => {
    unitSelect.innerHTML = "";
    const city = strategyCityById(campaign, citySelect.value);
    Object.entries(city?.registered_units || {}).filter(([, amount]) => Number(amount) > 0).forEach(([unitType, amount]) => {
      const option = document.createElement("option");
      option.value = unitType;
      option.textContent = `${strategyRegisteredUnitsLabel(campaign, { [unitType]: amount })} 可申请`;
      unitSelect.append(option);
    });
    unitSelect.value = unitSelect.children[0]?.value || "";
    request.disabled = state.strategyBusy || !canResume || !unitSelect.children.length;
  };
  citySelect.addEventListener("change", sync);
  request.addEventListener("click", () => queueStrategyAction("request_registered_units", {
    city_id: citySelect.value,
    unit_type: unitSelect.value,
    count: Math.max(1, Number(count.value || 1)),
  }));
  panel.append(
    createStrategyField("兵源城市", citySelect),
    createStrategyField("兵种", unitSelect),
    createStrategyField("数量", count),
    request,
  );
  sync();
  return panel;
}

function createGeneralArmyPanel(campaign, office, faction, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-army-form";
  const title = document.createElement("h4");
  title.textContent = "持久军队";
  panel.append(title);
  const armies = (campaign?.world?.armies || []).filter((army) => (
    army.commander_office_id === office?.id && !["disbanded", "destroyed"].includes(army.status)
  ));
  const army = armies[0];
  if (army) {
    appendTextLine(panel, "strategy-unit-ledger strategy-unit-ledger-prominent", `现役 ${army.id} · ${strategyRegisteredUnitsLabel(campaign, army.unit_inventory)}`);
    appendTextLine(panel, "strategy-meta", `兵员 ${strategyNumber(army.manpower)} · 粮草 ${strategyNumber(army.supply)}/${strategyNumber(army.supply_capacity)} · 士气 ${strategyNumber(army.morale)}`);
    appendTextLine(panel, "strategy-meta", `状态 ${strategyArmyStatusLabel(army.status)} · 命令 ${strategyArmyOrderLabel(army.current_order)} · 当前位置 ${strategyNodeName(campaign, army.location_node_id)}`);
    const supplySource = strategyCityById(campaign, army.supply_source_city_id);
    appendTextLine(panel, "strategy-army-supply", `补给线 ${strategyArmySupplyStatusLabel(army.supply_line_status)} · 来源 ${supplySource?.name || "无"} · 距离 ${army.supply_distance ?? "—"} · 月需 ${strategyNumber(army.monthly_supply_need)}`);
    appendTextLine(panel, "strategy-meta", `上月接收 ${strategyNumber(army.last_supply_received)} / 消耗 ${strategyNumber(army.last_supply_consumed)}${Number(army.starvation_months || 0) ? ` · 已连续断粮 ${strategyNumber(army.starvation_months)} 月` : ""}`);
    if ((army.supply_line_node_ids || []).length) {
      appendTextLine(panel, "strategy-army-supply-route", `补给路径：${army.supply_line_node_ids.map((nodeId) => strategyNodeName(campaign, nodeId)).join(" → ")}`);
    }
    if ((army.route_node_ids || []).length) {
      appendTextLine(panel, "strategy-army-route", `路线：${army.route_node_ids.map((nodeId) => strategyNodeName(campaign, nodeId)).join(" → ")}`);
      appendTextLine(panel, "strategy-meta", `进度 ${Number(army.route_progress_index || 0)}/${Math.max(0, army.route_node_ids.length - 1)} · 预计第 ${army.estimated_arrival_month} 月抵达`);
    }
  } else {
    appendTextLine(panel, "strategy-meta", "尚未编成现役军队。单位与粮草会从将军库存和驻城真实转入。");
  }

  const inventory = office?.unit_inventory || {};
  const unitInputs = {};
  let defaultsAssigned = false;
  Object.entries(inventory).filter(([, amount]) => Number(amount) > 0).forEach(([unitType, amount]) => {
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = String(amount);
    input.value = defaultsAssigned ? "0" : "1";
    defaultsAssigned = true;
    unitInputs[unitType] = input;
    panel.append(createStrategyField(`${strategyRegisteredUnitsLabel(campaign, { [unitType]: amount })} 转入`, input));
  });
  const cities = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === faction?.id);
  const citySelect = document.createElement("select");
  cities.forEach((city) => {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = `${city.name} · 可装粮 ${strategyNumber(city.resources?.food)}`;
    citySelect.append(option);
  });
  if (army?.home_city_id && cities.some((city) => city.id === army.home_city_id)) citySelect.value = army.home_city_id;
  const supply = document.createElement("input");
  supply.type = "number";
  supply.min = "50";
  supply.value = "50";
  const form = document.createElement("button");
  form.type = "button";
  form.className = "primary";
  form.textContent = army ? "补充军队 · 1 军令" : "编成军队 · 1 军令";
  form.disabled = state.strategyBusy || !canResume || !cities.length || !Object.keys(unitInputs).length || Boolean(army && army.status !== "garrisoned");
  form.addEventListener("click", () => queueStrategyAction("form_army", {
    city_id: citySelect.value,
    unit_inventory: Object.fromEntries(Object.entries(unitInputs).map(([unitType, input]) => [unitType, Math.max(0, Number(input.value || 0))])),
    supply: Math.max(0, Number(supply.value || 0)),
  }));
  panel.append(createStrategyField("编军城市", citySelect), createStrategyField("装载粮草", supply), form);

  if (army) {
    const currentCity = (campaign?.world?.cities || []).find((city) => (
      city.node_id === army.location_node_id && city.owner_faction_id === faction?.id
    ));
    const loadSupply = document.createElement("input");
    loadSupply.type = "number";
    const maximumLoad = Math.max(0, Math.min(
      Number(currentCity?.resources?.food || 0),
      Number(army.supply_capacity || 0) - Number(army.supply || 0),
    ));
    loadSupply.min = maximumLoad > 0 ? "1" : "0";
    loadSupply.max = String(maximumLoad);
    loadSupply.value = maximumLoad > 0 ? String(Math.min(50, maximumLoad)) : "0";
    const load = document.createElement("button");
    load.type = "button";
    load.className = "primary";
    load.textContent = "驻城装粮 · 1 军令";
    load.disabled = state.strategyBusy || !canResume || army.status !== "garrisoned" || Number(loadSupply.max || 0) <= 0;
    load.addEventListener("click", () => queueStrategyAction("load_army_supply", {
      army_id: army.id,
      supply: Math.max(1, Number(loadSupply.value || 1)),
    }));
    panel.append(createStrategyField(`携行补给（可装 ${loadSupply.max}）`, loadSupply), load);
    const destination = document.createElement("select");
    (campaign?.world?.nodes || []).filter((node) => strategyMapNodeId(node) !== army.location_node_id).forEach((node) => {
      const option = document.createElement("option");
      option.value = strategyMapNodeId(node);
      option.textContent = strategyNodeName(campaign, option.value);
      destination.append(option);
    });
    if (army.destination_node_id && army.destination_node_id !== army.location_node_id) destination.value = army.destination_node_id;
    const march = document.createElement("button");
    march.type = "button";
    march.className = "primary";
    march.textContent = army.status === "marching" ? "改道 · 1 军令" : "下达行军 · 1 军令";
    march.disabled = state.strategyBusy || !canResume || !destination.children.length || !["garrisoned", "deployed", "marching"].includes(army.status);
    march.addEventListener("click", () => queueStrategyAction("set_army_movement", {
      army_id: army.id,
      movement_order: "march",
      destination_node_id: destination.value,
    }));
    panel.append(createStrategyField("行军目的地", destination), march);
    if (army.status === "marching") {
      const halt = document.createElement("button");
      halt.type = "button";
      halt.className = "ghost";
      halt.textContent = "停止行军 · 1 军令";
      halt.disabled = state.strategyBusy || !canResume;
      halt.addEventListener("click", () => queueStrategyAction("set_army_movement", {
        army_id: army.id,
        movement_order: "hold",
      }));
      panel.append(halt);
    }
    const disband = document.createElement("button");
    disband.type = "button";
    disband.className = "ghost";
    disband.textContent = "解散并归库 · 1 军令";
    disband.disabled = state.strategyBusy || !canResume || army.status !== "garrisoned";
    disband.addEventListener("click", () => queueStrategyAction("disband_army", { army_id: army.id }));
    panel.append(disband);
  }
  return panel;
}

function renderLordWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  command.append(createRoleWorkspaceHeader(campaign, office, "主公中枢", "统筹职位容量、国家科技、祭祀绑定和武将任务。"));
  const occupation = selectedCity?.occupation_governance || {};
  const funding = selectedCity?.rebellion_funding_options?.[faction?.id];
  const occupationCrisis = Boolean(occupation.status && occupation.status !== "ended");
  const ownRebellion = selectedCity?.owner_faction_id === faction?.id && strategyCityRebellionForce(selectedCity) > 0;
  const externalFundingTarget = selectedCity?.owner_faction_id !== faction?.id && Boolean(funding) && (
    occupationCrisis || strategyCityRebellionForce(selectedCity) > 0 || Number(funding.rebellion_risk || 0) >= 45
  );
  if (
    strategyIsNeutralCityState(campaign, selectedCity?.owner_faction_id)
    || occupationCrisis
    || ownRebellion
    || externalFundingTarget
  ) {
    const cityCard = createStrategyCityCommandCard(campaign, selectedCity, faction, canResume, office);
    if (!strategyIsNeutralCityState(campaign, selectedCity?.owner_faction_id)) {
      cityCard.classList.add("is-political-crisis");
    }
    command.append(cityCard);
  }
  command.append(createStrategyOfficeDesk(campaign, office, canResume));
  command.append(createLordTechnologyPanel(campaign, faction, canResume));
  command.append(createLordRitualPanel(campaign, office, faction, canResume));
  command.append(createLordHeroBindingPanel(campaign, office, canResume));
  command.append(createStrategyHeroAppointmentPanel(campaign, office, canResume));
  command.append(createLordHeroDutyPanel(campaign, office, canResume));
}

function renderGrandGeneralWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  command.append(createRoleWorkspaceHeader(campaign, office, "战区统帅部", "管理直属将军，把城市已注册单位调入具体军团。"));
  if (strategyIsNeutralCityState(campaign, selectedCity?.owner_faction_id)) {
    command.append(createStrategyCityCommandCard(campaign, selectedCity, faction, canResume, office));
  }
  command.append(createStrategyOfficeDesk(campaign, office, canResume));
  command.append(createGrandGeneralMilitaryPanel(campaign, office, faction, canResume, selectedCity));
}

function renderGeneralWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  command.append(createRoleWorkspaceHeader(campaign, office, "军团行营", "持有确切作战单位；缺兵时必须向直属大将军请示。"));
  command.append(createStrategyOfficeDesk(campaign, office, canResume));
  command.append(createGeneralArmyPanel(campaign, office, faction, canResume));
  command.append(createGeneralLogisticsPanel(campaign, office, faction, canResume));
  const managed = strategyOfficeManagedCities(campaign, office);
  const source = managed.find((city) => city.id === selectedCity?.id) || managed[0] || selectedCity;
  command.append(createStrategyCityCommandCard(campaign, source, faction, canResume, office));
}

function renderGovernorWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  command.append(createRoleWorkspaceHeader(campaign, office, "城主府", "管理所辖城市的兵力增长、士兵注册、建筑、叛乱与祭祀。"));
  command.append(createStrategyOfficeDesk(campaign, office, canResume));
  const managedCity = strategyOfficeManagedCities(campaign, office)[0] || selectedCity;
  command.append(createStrategyCityCommandCard(campaign, managedCity, faction, canResume, office));
}

function createStrategyHeroPathPanel(campaign) {
  const currentHero = strategyControlledHero(campaign);
  const isLobby = campaign?.status === "lobby";
  const pool = campaign?.world?.strategic_hero_pool || [];
  const availableHeroes = pool.filter((hero) => (
    hero.status === "roaming" || hero.code === currentHero?.code
  ));
  const panel = document.createElement("section");
  panel.className = "strategy-hero-path-panel";
  const head = document.createElement("div");
  head.className = "strategy-hero-path-head";
  const titleBox = document.createElement("div");
  appendTextLine(titleBox, "meta-label", isLobby ? "出身抉择" : "在野行止");
  const title = document.createElement("h3");
  title.textContent = currentHero ? strategyHeroName(campaign, currentHero.code) : "选择武将";
  titleBox.append(title);
  const seal = document.createElement("strong");
  seal.className = `strategy-hero-status-seal ${currentHero?.status || "roaming"}`;
  seal.textContent = currentHero?.status === "serving" ? "仕官" : "在野";
  head.append(titleBox, seal);
  panel.append(head);

  const form = document.createElement("div");
  form.className = "strategy-hero-path-form";
  const heroSelect = document.createElement("select");
  availableHeroes.forEach((hero) => {
    const option = document.createElement("option");
    option.value = hero.code;
    const city = (campaign?.world?.cities || []).find((item) => item.id === hero.city_id);
    option.textContent = `${hero.name} · ${hero.role || "武将"} · ${city?.name || "行踪不明"}`;
    option.selected = hero.code === currentHero?.code;
    heroSelect.append(option);
  });
  heroSelect.disabled = state.strategyBusy || !isLobby;

  const pathSelect = document.createElement("select");
  const pathOptions = isLobby
    ? [
      ["lord", "成为主公"],
      ["roaming", "以在野身份入世"],
      ["found", "在所在城举旗建国"],
      ["join", "请求投靠其他主公"],
    ]
    : [
      ["found", "在所在城举旗建国"],
      ["join", "请求投靠其他主公"],
    ];
  pathOptions.forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    pathSelect.append(option);
  });
  if (isLobby && currentHero?.status === "roaming") pathSelect.value = "roaming";

  const targetSelect = document.createElement("select");
  (campaign?.world?.factions || []).forEach((faction) => {
    if (faction.id === currentHero?.faction_id) return;
    const option = document.createElement("option");
    option.value = faction.id;
    option.textContent = `${faction.name} · 主城 ${strategyCityName(campaign, faction.capital_city_id)}`;
    targetSelect.append(option);
  });
  const targetField = createStrategyField("投靠对象", targetSelect);
  targetField.className = "strategy-hero-target-field";

  const detail = document.createElement("p");
  detail.className = "strategy-hero-path-detail";
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "primary strategy-hero-path-submit";
  const updatePathState = () => {
    const selectedHero = pool.find((hero) => hero.code === heroSelect.value) || currentHero;
    const cityName = strategyCityName(campaign, selectedHero?.city_id);
    const details = {
      lord: "接掌分配给你的初始势力，可亲征，也可向大将军下达攻防军令。",
      roaming: "不隶属任何势力，不可调动城市；之后可举旗建国或递交投靠请求。",
      found: `在${cityName || "所在城"}举旗并夺取该城，成为新势力主公。`,
      join: "向所选主公递交投靠请求；对方录用后才正式成为其麾下武将。",
    };
    targetField.hidden = pathSelect.value !== "join";
    detail.textContent = details[pathSelect.value] || "";
    submit.textContent = pathSelect.value === "join" ? "递交投靠书" : pathSelect.value === "found" ? "举旗建国" : "确认武将道路";
    submit.disabled = state.strategyBusy || !heroSelect.value || (pathSelect.value === "join" && !targetSelect.value);
  };
  heroSelect.addEventListener("change", updatePathState);
  pathSelect.addEventListener("change", updatePathState);
  targetSelect.addEventListener("change", updatePathState);
  submit.addEventListener("click", () => chooseStrategyHeroPath(heroSelect.value, pathSelect.value, targetSelect.value));
  form.append(
    createStrategyField("你所操作的武将", heroSelect),
    createStrategyField("道路", pathSelect),
    targetField,
    detail,
    submit,
  );
  panel.append(form);
  updatePathState();
  return panel;
}

function renderStrategyRoamingWorkspace(current, campaign, hero) {
  const location = (campaign?.world?.cities || []).find((city) => city.id === hero?.city_id);
  const warRoom = document.createElement("section");
  warRoom.className = "strategy-war-room strategy-office-workspace HeroWorkspace strategy-roaming-workspace";
  const hud = document.createElement("div");
  hud.className = "strategy-war-hud";
  [
    ["战役", campaign.name],
    ["月份", `第 ${campaign.world.current_month} 月`],
    ["武将", strategyHeroName(campaign, hero?.code)],
    ["身份", "在野"],
    ["所在", location?.name || "行踪不明"],
  ].forEach(([label, value]) => {
    const item = document.createElement("div");
    item.className = "strategy-hud-item";
    appendTextLine(item, "meta-label", label);
    const strong = document.createElement("strong");
    strong.textContent = value;
    item.append(strong);
    hud.append(item);
  });
  warRoom.append(hud);
  const main = document.createElement("div");
  main.className = "strategy-war-main";
  const stage = document.createElement("div");
  stage.className = "strategy-war-stage";
  renderStrategyMap(stage, campaign, null);
  const command = document.createElement("aside");
  command.className = "strategy-command-panel strategy-roaming-command";
  command.append(createStrategyHeroPathPanel(campaign));
  main.append(stage, command);
  warRoom.append(main);
  current.append(warRoom);
}

function renderStrategyWarRoom(current, campaign, faction, canResume, isOwner) {
  const office = strategyActiveOffice(campaign);
  const managedCities = strategyOfficeManagedCities(campaign, office);
  let selectedCity = strategySelectedCity(campaign, faction);
  if (["general", "governor"].includes(office?.office_type) && !managedCities.some((city) => city.id === selectedCity?.id)) {
    selectedCity = managedCities[0] || null;
    state.strategySelectedCityId = selectedCity?.id || "";
  }
  const warRoom = document.createElement("section");
  const workspaceName = campaign?.world?.office_system?.office_types?.find((item) => item.id === office?.office_type)?.workspace;
  warRoom.className = `strategy-war-room strategy-office-workspace ${workspaceName || "LegacyWorkspace"}`;

  const hud = document.createElement("div");
  hud.className = "strategy-war-hud";
  const commandPoints = strategyFactionCommandPoints(campaign, faction);
  [
    ["战役", campaign.name],
    ["月份", `第 ${campaign.world.current_month} 月`],
    ["势力", faction?.name || "未绑定"],
    ["武将", office?.holder_id ? strategyHeroName(campaign, office.holder_id) : "在野"],
    ["军令", `${commandPoints.remaining}/${commandPoints.maximum} 可用`],
    ["资源", faction ? `粮 ${faction.resources.food} · 钱 ${faction.resources.money} · 以太 ${faction.resources.ether} · 兵 ${faction.resources.troops}` : "未知"],
  ].forEach(([label, value]) => {
    const item = document.createElement("div");
    item.className = "strategy-hud-item";
    appendTextLine(item, "meta-label", label);
    const strong = document.createElement("strong");
    strong.textContent = value;
    item.append(strong);
    hud.append(item);
  });
  warRoom.append(hud);
  renderStrategyOfficeSwitcher(warRoom, campaign, office);
  renderStrategyWarStateBanner(warRoom, campaign, canResume, isOwner);

  const tabs = document.createElement("div");
  tabs.className = "strategy-war-tabs";
  [
    ["地图", focusStrategyMapStage, "primary"],
    ["军令", focusStrategyCommandPanel, selectedCity ? "primary" : "ghost"],
    ["卷宗", focusStrategyDossier, "ghost"],
  ].forEach(([label, handler, className]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    if (label === "军令" && !selectedCity) button.disabled = true;
    button.addEventListener("click", handler);
    tabs.append(button);
  });
  warRoom.append(tabs);

  const main = document.createElement("div");
  main.className = "strategy-war-main";
  const stage = document.createElement("div");
  stage.className = "strategy-war-stage";
  renderStrategyGuide(stage, campaign, faction, selectedCity, isOwner);
  renderStrategyMonthlyCycle(stage, campaign, faction);
  renderStrategyMap(stage, campaign, faction);

  const command = document.createElement("aside");
  command.className = "strategy-command-panel";
  const commandTitle = document.createElement("h4");
  commandTitle.textContent = office ? strategyOfficeLabel(office, campaign) : "城市军令";
  command.append(commandTitle);
  const workspaceRenderers = {
    lord: renderLordWorkspace,
    grand_general: renderGrandGeneralWorkspace,
    general: renderGeneralWorkspace,
    governor: renderGovernorWorkspace,
  };
  (workspaceRenderers[office?.office_type] || renderGovernorWorkspace)(command, campaign, office, selectedCity, faction, canResume);
  main.append(stage, command);
  warRoom.append(main);
  current.append(warRoom);
}

function renderStrategyMonthlyCycle(current, campaign, faction) {
  const cycle = strategyMonthlyCycle(campaign, faction);
  const section = document.createElement("section");
  section.className = "strategy-monthly-cycle strategy-campaign-card";
  const title = document.createElement("h4");
  title.textContent = "月度决策";
  section.append(title);

  const previousTitle = document.createElement("strong");
  previousTitle.textContent = "上月发生了什么";
  section.append(previousTitle);
  const previous = cycle.previous_month;
  if (!previous) {
    appendTextLine(section, "strategy-meta", "战役首月，尚无上月结算记录。");
  } else {
    const changes = previous.city_changes || [];
    const events = previous.important_events || [];
    if (!changes.length && !events.length) appendTextLine(section, "strategy-meta", `第 ${previous.month} 月没有与你势力直接相关的重大变化。`);
    changes.slice(0, 4).forEach((change) => {
      const delta = change.resource_delta || {};
      const owner = change.owner_changed ? `，归属由${strategyFactionName(campaign, change.owner_before)}变为${strategyFactionName(campaign, change.owner_after)}` : "";
      appendTextLine(
        section,
        "strategy-meta",
        `${change.city_name}：粮 ${delta.food >= 0 ? "+" : ""}${delta.food || 0}、钱 ${delta.money >= 0 ? "+" : ""}${delta.money || 0}、兵 ${delta.troops >= 0 ? "+" : ""}${delta.troops || 0}、民心 ${change.support_delta >= 0 ? "+" : ""}${change.support_delta || 0}${owner}`
      );
    });
    events.slice(0, 3).forEach((event) => appendTextLine(section, "strategy-meta", event.message));
  }

  const mustTitle = document.createElement("strong");
  mustTitle.textContent = "本月必须处理什么";
  section.append(mustTitle);
  const mustHandle = cycle.must_handle || [];
  if (!mustHandle.length) appendTextLine(section, "strategy-meta", "没有迫在眉睫的危机，可以围绕战役目标主动规划。");
  mustHandle.slice(0, 3).forEach((item) => appendTextLine(section, "strategy-meta", `• ${item}`));

  const forecastTitle = document.createElement("strong");
  forecastTitle.textContent = "推进后预计发生什么";
  section.append(forecastTitle);
  const forecast = cycle.advance_forecast || {};
  (forecast.cities || []).forEach((city) => {
    const delta = city.resource_delta || {};
    appendTextLine(
      section,
      "strategy-meta",
      `${city.city_name}（${city.policy}）：粮 ${delta.food >= 0 ? "+" : ""}${delta.food || 0}（维护 ${city.food_upkeep || 0}）、钱 ${delta.money >= 0 ? "+" : ""}${delta.money || 0}、以太 ${delta.ether >= 0 ? "+" : ""}${delta.ether || 0}、兵 ${delta.troops >= 0 ? "+" : ""}${delta.troops || 0}；民心 ${city.support_delta >= 0 ? "+" : ""}${city.support_delta || 0}，叛乱 ${city.rebellion_risk || 0}（${city.rebellion_stage || "安全"}）`
    );
  });
  const planned = cycle.planned_actions || [];
  if (planned.length) {
    appendTextLine(section, "strategy-meta", `行动队列：${planned.length} 项；均在城市月结前执行。`);
    planned.slice(0, 4).forEach((action) => {
      appendTextLine(section, "strategy-meta", `• ${strategyQueuedActionLabel(campaign, action)} → 第 ${(action.affected_months || []).join(" / ")} 月`);
    });
  } else {
    appendTextLine(section, "strategy-meta", "行动队列为空；将按当前方针直接月结。");
  }
  appendTextLine(section, "strategy-meta", forecast.disclaimer || "战争和未知事件结果不会提前泄露。");
  current.append(section);
}

function collapseStrategyDossier(current) {
  if (!current || !current.children || !current.children.length) return;
  const children = Array.from(current.children);
  const hasWarRoom = children.some((child) => String(child.className || "").includes("strategy-war-room"));
  if (!hasWarRoom) return;
  const dossierChildren = children.filter((child) => {
    const className = String(child.className || "");
    return (
      !className.includes("strategy-war-room") &&
      !className.includes("strategy-status-strip") &&
      !className.includes("strategy-campaign-actions") &&
      !className.includes("strategy-hero-path-panel") &&
      className !== "strategy-dossier"
    );
  });
  if (!dossierChildren.length) return;
  const details = document.createElement("details");
  details.className = "strategy-dossier";
  details.open = Boolean(state.strategyDossierOpen);
  const summary = document.createElement("summary");
  summary.textContent = "战报卷宗";
  summary.addEventListener("click", (event) => {
    event.preventDefault();
    state.strategyDossierOpen = !state.strategyDossierOpen;
    details.open = state.strategyDossierOpen;
  });
  details.append(summary);
  const groups = buildStrategyDossierGroups(dossierChildren);
  const activeTab = groups.some((group) => group.id === state.strategyDossierTab)
    ? state.strategyDossierTab
    : groups[0]?.id || "";
  state.strategyDossierTab = activeTab;
  if (groups.length > 1) {
    const tabs = document.createElement("div");
    tabs.className = "strategy-dossier-tabs";
    groups.forEach((group) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = group.id === activeTab ? "primary" : "ghost";
      button.textContent = group.label;
      button.addEventListener("click", () => {
        state.strategyDossierTab = group.id;
        renderStrategyPanel();
        focusStrategyDossier();
      });
      tabs.append(button);
    });
    details.append(tabs);
  }
  groups.forEach((group) => {
    const page = document.createElement("div");
    page.className = "strategy-dossier-page";
    page.dataset.dossierTab = group.id;
    page.hidden = group.id !== activeTab;
    group.nodes.forEach((child) => page.append(child));
    details.append(page);
  });
  current.append(details);
}

function buildStrategyDossierGroups(nodes) {
  const groups = [];
  const byId = new Map();
  let current = null;
  const ensureGroup = (id, label) => {
    current = byId.get(id);
    if (!current) {
      current = { id, label, nodes: [] };
      byId.set(id, current);
      groups.push(current);
    }
    return current;
  };
  nodes.forEach((node) => {
    if (node.tagName === "H4") {
      const group = strategyDossierGroupForTitle(node.textContent || "");
      ensureGroup(group.id, group.label).nodes.push(node);
      return;
    }
    if (!current) ensureGroup("overview", "概况");
    current.nodes.push(node);
  });
  return groups.filter((group) => group.nodes.length);
}

function strategyDossierGroupForTitle(title) {
  if (title.includes("成员") || title.includes("邀请") || title.includes("初始玩家") || title.includes("在线状态")) {
    return { id: "members", label: "成员" };
  }
  if (title.includes("行动队列") || title.includes("军令")) return { id: "orders", label: "军令" };
  if (title.includes("目标") || title.includes("流亡") || title.includes("胜利")) return { id: "objectives", label: "目标" };
  if (title.includes("战斗") || title.includes("战役")) return { id: "battles", label: "战斗" };
  if (title.includes("英灵") || title.includes("武将")) return { id: "heroes", label: "英灵" };
  if (title.includes("科技") || title.includes("战术")) return { id: "tech", label: "科技" };
  if (title.includes("事件") || title.includes("日志")) return { id: "events", label: "事件" };
  return { id: "other", label: "其他" };
}

function appendTextLine(parent, className, text) {
  const node = document.createElement("div");
  node.className = className;
  node.textContent = text;
  parent.append(node);
  return node;
}

function currentStrategyBattleRoomForBattle(battle) {
  const latest = state.strategyBattleRoom || {};
  const battleRoomId = String(battle?.battle_room_id || "").trim().toUpperCase();
  const latestRoomId = String(latest.room_id || "").trim().toUpperCase();
  if (battleRoomId && latestRoomId === battleRoomId) {
    return latest;
  }
  return {
    room_id: battleRoomId,
    invite_path: battle?.battle_room_invite_path || "",
    player_token: "",
  };
}

function strategyRosterManifestSummary(manifest = []) {
  if (!Array.isArray(manifest) || !manifest.length) return "";
  return manifest
    .filter((row) => Number(row?.grid_units || 0) > 0)
    .map((row) => `${row.unit_type || "单位"}×${row.grid_units}`)
    .join(" / ");
}

function strategyCityName(campaign, cityId) {
  const city = (campaign?.world?.cities || []).find((item) => item.id === cityId);
  return city?.name || cityId || "未知城市";
}

function strategyCityStateLabels(city = {}) {
  const labels = (city.event_states || []).map((state) => {
    const parts = String(state || "").split(":");
    if (parts[0] === "rebellion_risk" && parts.length >= 3) {
      return `叛乱风险 ${parts[1]} ${parts[2]}`;
    }
    if (parts[0] === "rebellion_force" && parts.length >= 2) {
      return `叛军 ${parts[1]}`;
    }
    if (parts[0] === "rebellion_crisis") {
      const riskIndex = parts.indexOf("risk");
      return riskIndex >= 0 && parts[riskIndex + 1] ? `叛乱危机 ${parts[riskIndex + 1]}` : "叛乱危机";
    }
    if (parts[0] === "rebellion_action" && parts.length >= 2) {
      return `本月处理 ${parts[1]}`;
    }
    return "";
  }).filter(Boolean);
  const occupation = city.occupation_governance || city.occupation || {};
  if (occupation.status === "pending") labels.push("占领政策待定");
  if (occupation.status === "active") labels.push(`占领政策 ${occupation.policy_label || occupation.policy_id || "执行中"}`);
  return labels;
}

function strategyCityRebellionForce(city = {}) {
  for (const state of city.event_states || []) {
    const parts = String(state || "").split(":");
    if (parts[0] !== "rebellion_force" || !parts[1]) continue;
    const force = Number(parts[1]);
    return Number.isFinite(force) ? Math.max(0, Math.floor(force)) : 0;
  }
  return 0;
}

function strategyExileActionName(campaign, actionId) {
  const action = (campaign?.world?.exile_action_choices || []).find((item) => item.id === actionId);
  return action?.name || actionId || "未知流亡行动";
}

function strategyRebellionActionName(campaign, actionId) {
  const action = (campaign?.world?.rebellion_action_choices || []).find((item) => item.id === actionId);
  return action?.name || actionId || "未知叛乱处理";
}

function strategyHeroName(campaign, heroCode) {
  const hero = (campaign?.world?.strategic_hero_pool || []).find((item) => item.code === heroCode);
  return hero?.name || heroCode || "未知英灵";
}

function strategyDeployableHeroes(faction) {
  return (faction?.strategic_heroes || []).filter((hero) => hero.status === "serving");
}

function strategyHeroDeploymentLimit(faction) {
  const value = Number(faction?.strategic_hero_deployment_limit || 1);
  return Math.max(0, Math.floor(Number.isFinite(value) ? value : 1));
}

function createStrategyHeroDeploymentPicker(faction, selectedCodes = []) {
  const heroes = strategyDeployableHeroes(faction);
  const limit = strategyHeroDeploymentLimit(faction);
  const selected = new Set((Array.isArray(selectedCodes) ? selectedCodes : []).map((code) => String(code || "")));

  if (limit <= 1) {
    const select = document.createElement("select");
    const noHeroOption = document.createElement("option");
    noHeroOption.value = "";
    noHeroOption.textContent = "不投入";
    select.append(noHeroOption);
    heroes.forEach((hero) => {
      const option = document.createElement("option");
      option.value = hero.code;
      option.textContent = hero.name || hero.code;
      option.selected = selected.has(hero.code);
      select.append(option);
    });
    return {
      element: select,
      selectedCodes: () => (select.value ? [select.value] : []),
      setDisabled: (disabled) => { select.disabled = disabled; },
    };
  }

  const wrapper = document.createElement("div");
  wrapper.className = "strategy-hero-picker";
  const inputs = [];
  heroes.forEach((hero) => {
    const item = document.createElement("label");
    item.className = "strategy-hero-choice";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = hero.code;
    input.checked = selected.has(hero.code);
    input.addEventListener("change", () => {
      const checked = inputs.filter((node) => node.checked);
      if (checked.length > limit) input.checked = false;
    });
    const span = document.createElement("span");
    span.textContent = hero.name || hero.code;
    item.append(input, span);
    wrapper.append(item);
    inputs.push(input);
  });
  return {
    element: wrapper,
    selectedCodes: () => inputs.filter((input) => input.checked).map((input) => input.value).slice(0, limit),
    setDisabled: (disabled) => { inputs.forEach((input) => { input.disabled = disabled; }); },
  };
}

function strategyQueuedActionLabel(campaign, action = {}) {
  const payload = action.payload || {};
  if (action.action_type === "set_city_policy") {
    return `${strategyCityName(campaign, payload.city_id)}：方针计划为 ${payload.policy || "未知"}`;
  }
  if (action.action_type === "neutral_diplomacy") {
    const neutral = strategyFactionById(campaign, payload.neutral_faction_id);
    const relation = neutral?.neutral_politics?.relationships?.find((item) => item.faction_id === action.faction_id);
    const option = relation?.diplomacy_options?.find((item) => item.id === payload.diplomacy_action_id);
    return `中立交涉：${neutral?.name || payload.neutral_faction_id || "未知城邦"} · ${option?.name || payload.diplomacy_action_id || "未知行动"}`;
  }
  if (action.action_type === "peaceful_integration") {
    const neutral = (campaign?.world?.factions || []).find((item) => item.id === payload.neutral_faction_id);
    return `和平整合：${neutral?.name || payload.neutral_faction_id || "未知城邦"}`;
  }
  if (action.action_type === "resolve_story_event") {
    const event = (campaign?.world?.story_events || []).find((item) => item.id === payload.event_id);
    const choice = (event?.choices || []).find((item) => item.id === payload.choice_id);
    return `事件抉择：${event?.title || payload.event_id || "未知事件"} · ${choice?.label || payload.choice_id || "未知选项"}`;
  }
  if (action.action_type === "unlock_tactic_tech") {
    const faction = (campaign?.world?.factions || []).find((item) => item.id === action.faction_id);
    const tech = (faction?.tactic_tech_tree || []).find((item) => item.id === payload.tech_id);
    return `解锁战术科技：${tech?.name || payload.tech_id || "未知"}`;
  }
  if (action.action_type === "declare_attack") {
    const modeNames = {
      manual: "手动",
      ai_auto: "AI 自动",
      watch_ai: "观看 AI",
      quick: "快速",
    };
    const heroCodes = Array.isArray(payload.attacker_hero_codes) ? payload.attacker_hero_codes : [];
    const heroes = heroCodes.length ? ` · 英灵 ${heroCodes.map((code) => strategyHeroName(campaign, code)).join(", ")}` : "";
    return `${strategyCityName(campaign, payload.source_city_id)} → ${strategyCityName(campaign, payload.target_city_id)}：${modeNames[payload.resolution_mode] || payload.resolution_mode || "快速"}进攻${heroes}`;
  }
  if (action.action_type === "exile_action") {
    const target = payload.target_city_id ? `：${strategyCityName(campaign, payload.target_city_id)}` : "";
    return `流亡行动：${strategyExileActionName(campaign, payload.exile_action_id || payload.action_id)}${target}`;
  }
  if (action.action_type === "rebellion_action") {
    return `${strategyCityName(campaign, payload.city_id || payload.target_city_id)}：叛乱处理 ${strategyRebellionActionName(campaign, payload.rebellion_action_id || payload.action_id)}`;
  }
  if (action.action_type === "rebellion_battle") {
    const troops = payload.troops ? ` · 投入 ${payload.troops}` : "";
    return `${strategyCityName(campaign, payload.city_id || payload.target_city_id)}：清剿叛军${troops}`;
  }
  if (action.action_type === "choose_occupation_policy") {
    const city = (campaign?.world?.cities || []).find((item) => item.id === payload.city_id);
    const choice = (city?.occupation_governance?.policy_choices || []).find((item) => item.id === payload.policy_id);
    return `${strategyCityName(campaign, payload.city_id)}：占领政策 ${choice?.name || payload.policy_id}`;
  }
  if (action.action_type === "fund_rebellion") {
    return `${strategyCityName(campaign, payload.city_id)}：外部资助叛乱`;
  }
  if (action.action_type === "perform_hero_ritual") {
    return `${strategyCityName(campaign, payload.city_id)}：举行召唤祭祀`;
  }
  if (action.action_type === "unbind_strategic_hero") {
    return `解除祭祀绑定：${strategyHeroName(campaign, payload.hero_code)}`;
  }
  if (action.action_type === "appoint_strategic_hero") {
    const target = (campaign?.world?.offices || []).find((office) => office.id === payload.target_office_id);
    return `任命${strategyHeroName(campaign, payload.hero_code)}为${strategyOfficeLabel(target, campaign)}`;
  }
  if (action.action_type === "assign_strategic_hero_duty") {
    const labels = { reserve: "待命", administration: "辅佐内政", training: "训练军队", garrison: "驻守城市", campaign: "随军出征" };
    const target = payload.target_id ? ` · ${strategyCityName(campaign, payload.target_id)}` : "";
    return `安排${strategyHeroName(campaign, payload.hero_code)}：${labels[payload.assignment_type] || payload.assignment_type}${target}`;
  }
  if (action.action_type === "increase_city_troops") {
    return `${strategyCityName(campaign, payload.city_id)}：增加本城兵力`;
  }
  if (action.action_type === "register_city_soldiers") {
    return `${strategyCityName(campaign, payload.city_id)}：注册 ${payload.unit_count || 1} 个士兵单位`;
  }
  if (action.action_type === "transfer_registered_units") {
    const general = (campaign?.world?.offices || []).find((item) => item.id === payload.general_office_id);
    return `${strategyCityName(campaign, payload.city_id)}：向${strategyOfficeLabel(general, campaign)}调拨 ${payload.count || 1} 个单位`;
  }
  if (action.action_type === "request_registered_units") {
    return `请兵：${strategyCityName(campaign, payload.city_id)} · ${payload.count || 1} 个单位`;
  }
  if (action.action_type === "approve_registered_unit_request") {
    return `批准调兵申请：${payload.request_id || "未知申请"}`;
  }
  if (action.action_type === "construct_city_building") {
    const project = (campaign?.world?.building_projects || []).find((item) => item.id === payload.building_id);
    return `${strategyCityName(campaign, payload.city_id)}：兴建${project?.name || payload.building_id}`;
  }
  if (action.action_type === "issue_office_order" || action.action_type === "send_office_request") {
    const receiver = (campaign?.world?.offices || []).find((office) => office.id === payload.receiver_office_id);
    const kind = action.action_type === "send_office_request" ? "职位请求" : "职位命令";
    return `${kind}：${strategyOfficeLabel(receiver, campaign)} · ${payload.objective || "未填写目标"}`;
  }
  return action.action_type || "未知行动";
}

function renderStrategyActionQueue(current, campaign) {
  const title = document.createElement("h4");
  title.textContent = "本月行动队列";
  current.append(title);

  const panel = document.createElement("div");
  panel.className = "strategy-event-list";
  const actions = campaign?.queued_actions || [];
  if (!actions.length) {
    appendTextLine(panel, "strategy-meta", "暂无已提交的本月行动。");
  } else {
    actions.forEach((action) => {
      const card = document.createElement("article");
      card.className = "strategy-campaign-card";
      const strong = document.createElement("strong");
      strong.textContent = strategyQueuedActionLabel(campaign, action);
      card.append(strong);
      appendTextLine(card, "strategy-meta", `消耗 ${action.command_cost || strategyCommandCost(action.action_type, action.payload || {})} 点军令`);
      appendTextLine(
        card,
        "strategy-meta",
        `${action.username || strategyMemberLabel(campaign, action.user_id)} · ${strategyFactionName(campaign, action.faction_id)} · 第 ${action.month} 月`
      );
      panel.append(card);
    });
  }
  current.append(panel);
}

function appendStrategyRetrospective(card, campaign, retrospective) {
  if (!retrospective?.version) return;
  const heading = document.createElement("h4");
  heading.className = "strategy-retrospective-title";
  heading.textContent = "完整战役复盘";
  card.append(heading);

  const summary = retrospective.summary || {};
  appendTextLine(
    card,
    "strategy-meta strategy-retrospective-summary",
    `共 ${summary.resolved_battles || 0} 场城市战（${summary.grid_battles || 0} 场真实格子战）· ${summary.cities_changed_hands || 0} 次城市易主 · ${summary.story_choices || 0} 次事件抉择`
  );

  const sections = [
    {
      title: "势力结局",
      rows: retrospective.faction_outcomes || [],
      line: (row) => `${row.outcome_label} · ${row.faction_name} · 第 ${row.rank || "-"} 名 / ${row.total_score || 0} 分。${row.summary || ""}`,
    },
    {
      title: "关键月份",
      rows: retrospective.key_months || [],
      line: (row) => `第 ${row.month} 月 · ${(row.events || [row.headline]).join("；")}`,
    },
    {
      title: "城市变化",
      rows: retrospective.city_changes || [],
      line: (row) => `第 ${row.month} 月 · ${row.city_name}：${row.owner_before_name} → ${row.owner_after_name}`,
    },
    {
      title: "战斗记录",
      rows: retrospective.battles || [],
      line: (row) => `第 ${row.month} 月 · ${row.source_city_name} → ${row.target_city_name} · ${row.grid_battle ? "真实格子战" : "快速结算"} · ${row.winner_faction_name}获胜`,
    },
    {
      title: "角色经历",
      rows: retrospective.hero_experiences || [],
      line: (row) => `${strategyHeroName(campaign, row.hero_code)} · ${row.office_label || "未任职"} · 参战 ${row.battle_appearances || 0} / 胜 ${row.battle_wins || 0} · ${row.faction_name || "无所属"}`,
    },
  ];
  sections.forEach((section, index) => {
    const details = document.createElement("details");
    details.className = "strategy-retrospective-section";
    if (index < 2) details.open = true;
    const summaryNode = document.createElement("summary");
    summaryNode.textContent = `${section.title}（${section.rows.length}）`;
    details.append(summaryNode);
    if (!section.rows.length) {
      appendTextLine(details, "strategy-meta", "本次战役没有相关记录。 ");
    } else {
      section.rows.slice(0, 12).forEach((row) => appendTextLine(details, "strategy-meta", section.line(row)));
    }
    card.append(details);
  });
}

function renderStrategyObjectivePanel(current, campaign) {
  const status = campaign?.world?.strategic_status || {};
  const contract = status.campaign_contract || {};
  const conditions = Array.isArray(status.victory_conditions) ? status.victory_conditions : [];
  const exiledFactions = Array.isArray(status.exiled_factions) ? status.exiled_factions : [];
  if (!conditions.length && !exiledFactions.length) return;

  const title = document.createElement("h4");
  title.textContent = "战略目标与流亡";
  current.append(title);

  const panel = document.createElement("div");
  panel.className = "strategy-event-list";
  if (contract.id) {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = contract.name || "限时战役";
    card.append(name);
    const duration = Array.isArray(contract.expected_duration_minutes) ? contract.expected_duration_minutes.join("～") : "60～90";
    const monthText = status.campaign_state === "sandbox"
      ? `第 ${campaign.world.current_month} 月 · 已转入自由沙盒`
      : `第 ${campaign.world.current_month}/${contract.month_limit} 月 · 剩余 ${status.months_remaining} 月`;
    appendTextLine(card, "strategy-meta", monthText);
    appendTextLine(card, "strategy-meta", `${contract.city_count} 城 · ${contract.major_faction_count} 个主要势力 · ${contract.neutral_city_state_count} 个中立城邦 · 预计 ${duration} 分钟`);
      appendTextLine(card, "strategy-meta", "已开放：统一、消灭主要敌对势力、十二月评议、中立政治档案与基础外交；正式军队、世界主线和圣物祭坛仍在后续 Phase。");
    panel.append(card);
  }
  conditions.forEach((condition) => {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = condition.name || condition.id || "未知目标";
    card.append(name);
    const stateLabel = !condition.implemented ? "未开放" : condition.achieved ? "已达成" : "未达成";
    const winnerName = condition.winner_faction_id ? strategyFactionName(campaign, condition.winner_faction_id) : "";
    appendTextLine(card, "strategy-meta", winnerName ? `${stateLabel} · ${winnerName}` : stateLabel);
    if (condition.description) appendTextLine(card, "strategy-meta", condition.description);
    panel.append(card);
  });
  if (exiledFactions.length) {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = "流亡势力";
    card.append(name);
    appendTextLine(card, "strategy-meta", exiledFactions.map((faction) => faction.name || faction.id).join("、"));
    panel.append(card);
  } else {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = "流亡势力";
    card.append(name);
    appendTextLine(card, "strategy-meta", "暂无");
    panel.append(card);
  }
  const conclusion = status.conclusion || {};
  if (conclusion.state) {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = conclusion.result_label || "战役结算";
    card.append(name);
    const conclusionStateLabels = { settled: "等待房主选择", sandbox: "已继续沙盒", archived: "已结束归档" };
    appendTextLine(card, "strategy-meta", `结算月份：第 ${conclusion.concluded_month} 月 · ${conclusionStateLabels[conclusion.state] || conclusion.state}`);
    const rankings = Array.isArray(conclusion.rankings) ? conclusion.rankings : [];
    rankings.forEach((row) => {
      appendTextLine(
        card,
        "strategy-meta",
        `第 ${row.rank} 名 ${row.faction_name || strategyFactionName(campaign, row.faction_id)}：${row.total_score} 分（城市 ${row.city_score} / 民心 ${row.support_score} / 存续 ${row.survival_score} / 战斗 ${row.battle_score} / 城邦影响 ${row.influence_score || 0} / 主线 ${row.mainline_score}）`
      );
    });
    appendStrategyRetrospective(card, campaign, campaign.world?.campaign_retrospective || conclusion.retrospective);
    if (status.awaiting_conclusion_choice && Number(campaign.owner_user_id) === Number(state.authUser?.id || 0)) {
      const actions = document.createElement("div");
      actions.className = "strategy-campaign-actions";
      const continueButton = document.createElement("button");
      continueButton.type = "button";
      continueButton.className = "primary";
      continueButton.textContent = "保留结算并继续沙盒";
      continueButton.disabled = state.strategyBusy || !strategyCanResume(campaign);
      continueButton.addEventListener("click", continueStrategySandbox);
      actions.append(continueButton);
      const archiveButton = document.createElement("button");
      archiveButton.type = "button";
      archiveButton.className = "ghost danger";
      archiveButton.textContent = "结束并归档战役";
      archiveButton.disabled = state.strategyBusy || !strategyCanResume(campaign);
      archiveButton.addEventListener("click", archiveStrategyCampaign);
      actions.append(archiveButton);
      card.append(actions);
    }
    panel.append(card);
  }
  const faction = strategyFaction(campaign);
  const isExiled = Boolean(faction?.id && (status.exiled_faction_ids || []).includes(faction.id));
  const canResume = strategyCanIssueOrders(campaign);
  if (isExiled) {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = "你的流亡行动";
    card.append(name);
    appendTextLine(card, "strategy-meta", "无城势力可以求援、募兵、潜伏联络，并在条件足够时重建据点。");
    const actions = document.createElement("div");
    actions.className = "strategy-campaign-actions";

    const actionLabel = document.createElement("label");
    const actionText = document.createElement("span");
    actionText.textContent = "行动";
    const actionSelect = document.createElement("select");
    (campaign?.world?.exile_action_choices || []).forEach((choice) => {
      const option = document.createElement("option");
      option.value = choice.id;
      option.textContent = choice.name || choice.id;
      option.dataset.requiresTargetCity = choice.requires_target_city ? "1" : "";
      actionSelect.append(option);
    });
    if (actionSelect.children.length && !actionSelect.value) actionSelect.value = actionSelect.children[0].value;
    actionLabel.append(actionText, actionSelect);
    actions.append(actionLabel);

    const targetLabel = document.createElement("label");
    const targetText = document.createElement("span");
    targetText.textContent = "目标城市";
    const targetSelect = document.createElement("select");
    (campaign?.world?.cities || [])
      .filter((city) => city.owner_faction_id !== faction.id)
      .forEach((city) => {
        const option = document.createElement("option");
        option.value = city.id;
        option.textContent = city.name;
        targetSelect.append(option);
      });
    if (targetSelect.children.length && !targetSelect.value) targetSelect.value = targetSelect.children[0].value;
    targetLabel.append(targetText, targetSelect);
    actions.append(targetLabel);

    const updateTargetState = () => {
      const selectedOption = actionSelect.children[actionSelect.selectedIndex] || null;
      const requiresTarget = selectedOption?.dataset?.requiresTargetCity === "1";
      targetSelect.disabled = state.strategyBusy || !canResume || !requiresTarget;
    };
    actionSelect.addEventListener("change", updateTargetState);
    updateTargetState();

    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.textContent = "加入月度计划 · 1 军令";
    button.disabled = state.strategyBusy || !canResume || !actionSelect.children.length || !strategyCanAffordCommand(campaign, faction, "exile_action");
    button.addEventListener("click", () => {
      const selectedOption = actionSelect.children[actionSelect.selectedIndex] || null;
      const requiresTarget = selectedOption?.dataset?.requiresTargetCity === "1";
      const payload = { exile_action_id: actionSelect.value };
      if (requiresTarget) payload.target_city_id = targetSelect.value;
      queueStrategyAction("exile_action", payload);
    });
    actions.append(button);
    card.append(actions);
    panel.append(card);
  }
  current.append(panel);
}

function renderStrategyHeroPanel(current, campaign, faction, office = strategyActiveOffice(campaign)) {
  const heroes = Array.isArray(faction?.strategic_heroes)
    ? faction.strategic_heroes
    : (campaign?.world?.strategic_hero_pool || []).filter((hero) => hero.home_faction_id === faction?.id);
  if (!heroes.length) return;
  const canSetDefense = !office || office.office_type === "grand_general";

  const title = document.createElement("h4");
  title.textContent = "本势力武将";
  current.append(title);

  const panel = document.createElement("div");
  panel.className = "strategy-tech-grid";
  heroes.slice(0, 8).forEach((hero) => {
    const card = document.createElement("article");
    card.className = "strategy-tech-card";
    const name = document.createElement("strong");
    name.textContent = hero.name || hero.code;
    card.append(name);
    appendTextLine(card, "strategy-meta", `${hero.role || "未知职业"} · ${hero.attribute || "未知属性"} · Lv ${hero.level || 1}`);
    if (hero.status === "sleeping") {
      appendTextLine(card, "strategy-meta", `状态：沉睡中 · 第 ${hero.sleeping_until_month || "?"} 月恢复`);
    } else {
      appendTextLine(card, "strategy-meta", `状态：${hero.status === "serving" ? "仕官中" : "在野"}`);
    }
    if (hero.office_id) {
      const heldOffice = (campaign?.world?.offices || []).find((item) => item.id === hero.office_id);
      appendTextLine(card, "strategy-meta", `职位：${strategyOfficeLabel(heldOffice, campaign)}`);
    }
    if (hero.ritual_city_id) {
      appendTextLine(card, "strategy-meta", `祭祀绑定：${strategyCityName(campaign, hero.ritual_city_id)}`);
    }
    if (hero.defender_assigned) {
      appendTextLine(card, "strategy-meta", "防守：默认出战");
    }
    const actions = document.createElement("div");
    actions.className = "strategy-tech-actions";
    if (canSetDefense && hero.status === "serving") {
      const defense = document.createElement("button");
      defense.type = "button";
      defense.className = hero.defender_assigned ? "ghost" : "primary";
      defense.textContent = hero.defender_assigned ? "防守中" : "设为防守";
      defense.disabled = state.strategyBusy || !strategyCanIssueOrders(campaign) || hero.defender_assigned;
      defense.addEventListener("click", () => setStrategyDefenseHero(hero.code));
      actions.append(defense);
    }
    if (actions.children.length) card.append(actions);
    panel.append(card);
  });
  if (heroes.length > 8) {
    appendTextLine(panel, "strategy-meta", `另有 ${heroes.length - 8} 名本势力英灵暂未展开。`);
  }
  current.append(panel);
}

function strategySideLabel(side) {
  if (side === "attacker") return "攻方";
  if (side === "defender") return "守方";
  return side || "未知";
}

function strategyNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function strategyBattleResultLines(battle = {}) {
  const result = battle.battle_result || {};
  if (!result || typeof result !== "object" || !Object.keys(result).length) return [];

  const lost = result.lost_troops_by_side || {};
  const remaining = result.remaining_troops_by_side || {};
  const initialGrid = result.initial_grid_units_by_side || {};
  const survivingGrid = result.surviving_grid_units_by_side || {};
  const lines = [
    `结果：${strategySideLabel(result.winner_side)}胜利 · ${result.city_captured ? "攻城成功" : "守城成功"}`,
    `损失：攻方 ${strategyNumber(lost.attacker)} · 守方 ${strategyNumber(lost.defender)}`,
    `剩余兵力：攻方 ${strategyNumber(remaining.attacker)} · 守方 ${strategyNumber(remaining.defender)}`,
  ];
  if (
    Object.prototype.hasOwnProperty.call(survivingGrid, "attacker") ||
    Object.prototype.hasOwnProperty.call(survivingGrid, "defender") ||
    Object.prototype.hasOwnProperty.call(initialGrid, "attacker") ||
    Object.prototype.hasOwnProperty.call(initialGrid, "defender")
  ) {
    const attackerInitial = Object.prototype.hasOwnProperty.call(initialGrid, "attacker") ? strategyNumber(initialGrid.attacker) : "?";
    const defenderInitial = Object.prototype.hasOwnProperty.call(initialGrid, "defender") ? strategyNumber(initialGrid.defender) : "?";
    lines.push(
      `存活单位：攻方 ${strategyNumber(survivingGrid.attacker)}/${attackerInitial} · 守方 ${strategyNumber(survivingGrid.defender)}/${defenderInitial}`
    );
  }
  const strategicHeroes = result.strategic_heroes_by_side || {};
  ["attacker", "defender"].forEach((side) => {
    const row = strategicHeroes[side] || {};
    const committed = Array.isArray(row.committed) ? row.committed : [];
    const surviving = Array.isArray(row.surviving) ? row.surviving : [];
    const sleeping = Array.isArray(row.sleeping) ? row.sleeping : [];
    if (!committed.length && !surviving.length && !sleeping.length) return;
    const fragments = [];
    if (committed.length) fragments.push(`参战 ${committed.join(", ")}`);
    if (surviving.length) fragments.push(`存活 ${surviving.join(", ")}`);
    if (sleeping.length) fragments.push(`沉睡 ${sleeping.join(", ")}`);
    lines.push(`英灵：${strategySideLabel(side)} ${fragments.join(" · ")}`);
  });
  if (result.battle_log_summary) {
    lines.push(`摘要：${result.battle_log_summary}`);
  }
  return lines;
}

function renderStrategyPanel() {
  const panel = $("strategy-panel");
  if (!panel) return;
  const caption = $("strategy-caption");
  const nameInput = $("strategy-name");
  const seedInput = $("strategy-seed");
  const playerCountInput = $("strategy-player-count");
  const joinCodeInput = $("strategy-join-code");
  const createButton = $("strategy-create");
  const joinButton = $("strategy-join");
  const refreshButton = $("strategy-refresh");
  const advanceButton = $("strategy-advance-month");
  const message = $("strategy-message");
  const list = $("strategy-campaign-list");
  const current = $("strategy-current");
  const roomHome = $("room-home");
  const loggedIn = userLoggedIn();
  const selected = state.strategyCampaign;
  const canResume = strategyCanResume(selected);
  const canIssueOrders = strategyCanIssueOrders(selected);
  const selectedIsOwner = Boolean(selected && Number(selected.owner_user_id) === Number(state.authUser?.id || 0));
  panel.classList.toggle("is-war-room", Boolean(selected));
  if (roomHome) roomHome.classList.toggle("strategy-war-layout", Boolean(selected));

  if (caption) {
    caption.textContent = loggedIn
      ? "创建或恢复战役后，可以管理城市方针、解锁战术科技并推进月度结算。"
      : "请先登录账号，战略战役会绑定到账号存档。";
  }
  if (nameInput) {
    nameInput.value = state.strategyName;
    nameInput.disabled = state.strategyBusy || !loggedIn;
  }
  if (seedInput) {
    seedInput.value = state.strategySeed;
    seedInput.disabled = state.strategyBusy || !loggedIn;
  }
  if (playerCountInput) {
    playerCountInput.value = "2";
    playerCountInput.disabled = true;
  }
  if (joinCodeInput) {
    joinCodeInput.value = state.strategyJoinCode;
    joinCodeInput.disabled = state.strategyBusy || !loggedIn;
  }
  if (createButton) createButton.disabled = state.strategyBusy || !loggedIn;
  if (joinButton) joinButton.disabled = state.strategyBusy || !loggedIn || !String(state.strategyJoinCode || "").trim();
  if (refreshButton) refreshButton.disabled = state.strategyBusy || !loggedIn;
  if (advanceButton) {
    const office = strategyActiveOffice(selected);
    advanceButton.disabled = state.strategyBusy || !loggedIn || !selected || !canIssueOrders || !selectedIsOwner || Boolean(office && office.office_type !== "lord");
  }
  if (message) message.textContent = state.strategyMessage || "";
  if (!list || !current) return;

  list.innerHTML = "";
  current.innerHTML = "";
  if (!loggedIn) {
    appendTextLine(list, "strategy-meta", "登录后会显示你参与过的战役。");
    return;
  }
  if (!state.strategyCampaigns.length) {
    appendTextLine(list, "strategy-meta", "当前账号还没有战略战役。");
  } else {
    state.strategyCampaigns.forEach((campaign) => {
      const card = document.createElement("article");
      card.className = "strategy-campaign-card";
      const campaignStatus = campaign.status === "active" ? "已锁定" : "大厅开放";
      appendTextLine(
        card,
        "strategy-meta",
        `第 ${campaign.world.current_month}${campaign.world.strategic_status?.month_limit ? `/${campaign.world.strategic_status.month_limit}` : ""} 月 · ${campaign.world.cities.length} 城 · ${campaign.members.length}/${campaign.world.factions.length} 初始玩家 · ${campaignStatus}`
      );
      appendTextLine(card, "strategy-meta", `加入码：${campaign.join_code || "未生成"}`);
      const title = document.createElement("strong");
      title.textContent = campaign.name;
      card.prepend(title);
      const resume = campaign.resume || {};
      if (campaign.status !== "active") {
        appendTextLine(card, "strategy-meta", "房主锁定后，未加入的初始势力会由 AI 接管。");
      } else {
        const missingNames = strategyMissingInitialPlayerLabels(campaign);
        appendTextLine(
          card,
          "strategy-meta",
          resume.can_resume ? "所有真人初始玩家在线，AI 空席会自动操作。" : `等待初始玩家：${missingNames.join("、") || "未知"}`
        );
      }
      const actions = document.createElement("div");
      actions.className = "strategy-campaign-actions";
      const enter = document.createElement("button");
      enter.className = "primary";
      enter.type = "button";
      enter.textContent = campaign.status === "active" ? "继续战役" : "进入大厅";
      enter.disabled = state.strategyBusy;
      enter.addEventListener("click", () => enterStrategyCampaign(campaign.id));
      actions.append(enter);
      const leave = document.createElement("button");
      leave.className = "ghost";
      leave.type = "button";
      leave.textContent = "离线";
      leave.disabled = state.strategyBusy;
      leave.addEventListener("click", () => leaveStrategyCampaign(campaign.id));
      actions.append(leave);
      if (campaign.status !== "active" && Number(campaign.owner_user_id) === Number(state.authUser?.id || 0)) {
        const lock = document.createElement("button");
        lock.className = "ghost";
        lock.type = "button";
        lock.textContent = "锁定并启用 AI";
        lock.disabled = state.strategyBusy;
        lock.addEventListener("click", () => lockStrategyCampaign(campaign.id));
        actions.append(lock);
      }
      card.append(actions);
      list.append(card);
    });
  }

  if (!selected) {
    appendTextLine(current, "strategy-meta", "选择一个战役后会显示城市、科技和事件。");
    return;
  }

  const controlledHero = strategyControlledHero(selected);
  const faction = strategyFaction(selected);
  const activeOffice = strategyActiveOffice(selected);
  const isOwner = selectedIsOwner;
  if (controlledHero?.status === "roaming") {
    renderStrategyRoamingWorkspace(current, selected, controlledHero);
  } else {
    if (selected.status === "lobby") current.append(createStrategyHeroPathPanel(selected));
    renderStrategyWarRoom(current, selected, faction || selected.world.factions[0], canIssueOrders, isOwner);
  }

  const campaignStatus = document.createElement("div");
  campaignStatus.className = "strategy-status-strip";
  appendTextLine(campaignStatus, "strategy-meta", `加入码：${selected.join_code || "未生成"}`);
  appendTextLine(campaignStatus, "strategy-meta", selected.status === "active" ? "初始玩家已锁定" : "战役大厅开放");
  appendTextLine(
    campaignStatus,
    "strategy-meta",
    isOwner
      ? (activeOffice?.office_type === "lord" ? "房主可推进" : "房主账号 · 需由主公职位推进")
      : "仅房主的主公职位可推进"
  );
  current.append(campaignStatus);

  renderStrategyMembersPanel(current, selected, isOwner);
  renderStrategyResumePanel(current, selected);
  renderStrategyActionQueue(current, selected);
  renderStrategyObjectivePanel(current, selected);
  if (faction && (!activeOffice || ["lord", "grand_general"].includes(activeOffice.office_type))) {
    renderStrategyHeroPanel(current, selected, faction, activeOffice);
  }

  if (selected.status !== "active" && isOwner) {
    const lobbyActions = document.createElement("div");
    lobbyActions.className = "strategy-campaign-actions";
    const lock = document.createElement("button");
    lock.className = "primary";
    lock.type = "button";
    lock.textContent = "锁定并启用 AI";
    lock.disabled = state.strategyBusy;
    lock.addEventListener("click", () => lockStrategyCampaign(selected.id));
    lobbyActions.append(lock);
    appendTextLine(lobbyActions, "strategy-meta", "锁定后加入码只允许已有真人初始玩家恢复；未加入势力会由 AI 操作。");
    current.append(lobbyActions);
  }

  const battleRecords = (selected.world.pending_battles || []).slice(-6).reverse();
  if (battleRecords.length && (!activeOffice || activeOffice.office_type === "general")) {
    const battlesTitle = document.createElement("h4");
    battlesTitle.textContent = "战斗记录";
    current.append(battlesTitle);
    const battles = document.createElement("div");
    battles.className = "strategy-event-list";
    battleRecords.forEach((battle) => {
      const card = document.createElement("article");
      card.className = "strategy-campaign-card";
      const title = document.createElement("strong");
      title.textContent = `${strategyCityName(selected, battle.source_city_id)} → ${strategyCityName(selected, battle.target_city_id)}`;
      card.append(title);
      const statusNames = { pending: "待处理", resolved: "已结算" };
      appendTextLine(card, "strategy-meta", `处理方式：${battle.resolution_mode || "quick"} · 状态：${statusNames[battle.status] || battle.status}`);
      strategyBattleResultLines(battle).forEach((line) => appendTextLine(card, "strategy-meta", line));
      if (battle.battle_room_id) {
        const roomInfo = currentStrategyBattleRoomForBattle(battle);
        appendTextLine(card, "strategy-meta", `真实战斗房间：${battle.battle_room_id}`);
        if (battle.battle_room_invite_path) {
          appendTextLine(card, "strategy-meta", `入口：${battle.battle_room_invite_path}`);
        }
        const attackerSummary = strategyRosterManifestSummary(roomInfo.attacker_roster_manifest);
        const defenderSummary = strategyRosterManifestSummary(roomInfo.defender_roster_manifest);
        if (attackerSummary) {
          appendTextLine(card, "strategy-meta", `攻方单位：${attackerSummary}`);
        }
        if (defenderSummary) {
          appendTextLine(card, "strategy-meta", `守方单位：${defenderSummary}`);
        }
        const actions = document.createElement("div");
        actions.className = "strategy-campaign-actions";
        const open = document.createElement("button");
        open.type = "button";
        open.className = "primary";
        open.textContent = battle.status === "resolved" ? "查看真实战斗" : battle.resolution_mode === "watch_ai" ? "观看 AI 战斗" : "进入真实战斗";
        open.disabled = state.strategyBusy;
        open.addEventListener("click", () => openStrategyBattleRoom(roomInfo));
        actions.append(open);
        card.append(actions);
      } else if (battle.status === "pending") {
        appendTextLine(card, "strategy-meta", "等待创建真实格子战房间。");
        if (battle.defender_faction_id === faction?.id) {
          const actions = document.createElement("div");
          actions.className = "strategy-campaign-actions";
          const heroLabel = document.createElement("label");
          const heroSpan = document.createElement("span");
          heroSpan.textContent = "本场防守";
          const heroSelect = document.createElement("select");
          const noHeroOption = document.createElement("option");
          noHeroOption.value = "";
          noHeroOption.textContent = "不投入";
          heroSelect.append(noHeroOption);
          strategyDeployableHeroes(faction).forEach((hero) => {
            const option = document.createElement("option");
            option.value = hero.code;
            option.textContent = hero.name || hero.code;
            heroSelect.append(option);
          });
          const currentDefenderHeroes = Array.isArray(battle.defender_hero_codes) ? battle.defender_hero_codes : [];
          const currentDefenderHero = currentDefenderHeroes[0] || "";
          heroSelect.value = currentDefenderHero;
          heroSelect.disabled = state.strategyBusy || !canResume;
          const heroMultiPicker = createStrategyHeroDeploymentPicker(faction, currentDefenderHeroes);
          if (strategyHeroDeploymentLimit(faction) > 1) {
            heroSelect.disabled = true;
            heroSelect.style.display = "none";
            heroMultiPicker.setDisabled(state.strategyBusy || !canResume);
          }
          heroLabel.append(heroSpan, heroSelect);
          if (strategyHeroDeploymentLimit(faction) > 1) heroLabel.append(heroMultiPicker.element);
          actions.append(heroLabel);
          const defend = document.createElement("button");
          defend.type = "button";
          defend.className = "ghost";
          defend.textContent = "设置本场防守";
          defend.disabled = state.strategyBusy || !canResume;
          defend.addEventListener("click", () => setStrategyBattleDefenseHero(
            battle.id || battle.battle_id,
            strategyHeroDeploymentLimit(faction) > 1 ? heroMultiPicker.selectedCodes() : heroSelect.value
          ));
          actions.append(defend);
          card.append(actions);
        }
      } else if (!strategyBattleResultLines(battle).length && Array.isArray(battle.report) && battle.report.length) {
        appendTextLine(card, "strategy-meta", `战报：${battle.report[battle.report.length - 1]}`);
      }
      battles.append(card);
    });
    current.append(battles);
  }

  if (!activeOffice || activeOffice.office_type === "lord") {
  const techTitle = document.createElement("h4");
  techTitle.textContent = "国家科技树";
  current.append(techTitle);
  const techs = document.createElement("div");
  techs.className = "strategy-tech-grid";
  (faction?.tactic_tech_tree || []).forEach((tech) => {
    const card = document.createElement("article");
    card.className = `strategy-tech-card tech-branch-${tech.branch || "military"}`;
    const title = document.createElement("strong");
    title.textContent = tech.name;
    card.append(title);
    appendTextLine(card, "meta-label", ({ office: "职位分支", unit: "兵种分支", building: "建筑分支", military: "战术分支" })[tech.branch] || "战术分支");
    appendTextLine(card, "strategy-meta", tech.description);
    appendTextLine(card, "strategy-meta", `费用：钱 ${tech.money_cost} · 以太 ${tech.ether_cost}`);
    const actions = document.createElement("div");
    actions.className = "strategy-tech-actions";
    const queueTech = document.createElement("button");
    queueTech.type = "button";
    queueTech.className = tech.unlocked ? "ghost" : "primary";
    queueTech.textContent = tech.unlocked ? "已解锁" : "加入月度计划 · 1 军令";
    queueTech.disabled = state.strategyBusy || !canResume || tech.unlocked || !tech.available || !strategyCanAffordCommand(selected, faction, "unlock_tactic_tech");
    queueTech.addEventListener("click", () => queueStrategyAction("unlock_tactic_tech", { tech_id: tech.id }));
    actions.append(queueTech);
    card.append(actions);
    techs.append(card);
  });
  current.append(techs);
  }

  const eventsTitle = document.createElement("h4");
  eventsTitle.textContent = "事件";
  current.append(eventsTitle);
  const events = document.createElement("div");
  events.className = "strategy-event-list";
  (selected.world.event_log || []).slice(-8).reverse().forEach((event) => {
    appendTextLine(events, "strategy-event", `第 ${event.month} 月 · ${event.message}`);
  });
  current.append(events);
  collapseStrategyDossier(current);
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

function roomStateClass(room) {
  if (!room) return "";
  if (room.status === "battle") return "is-battle";
  if (room.is_full) return "is-full";
  return "";
}

function shouldShowLobbyPanel() {
  return hasRoom() && (viewerPlayerId() !== null || state.room?.is_full || hasBattle());
}

function isRoomConfigControlActive() {
  const active = typeof document !== "undefined" ? document.activeElement : null;
  if (!active || !hasRoom() || state.room?.status !== "lobby") return false;
  if (
    active.id === "room-seat-count-input"
    || active.id === "random-roster-size-input"
    || active.id === "hero-search"
    || active.id === "hero-role-filter"
    || active.id === "hero-difficulty-filter"
  ) return true;
  const data = active.dataset || {};
  return Boolean(data.seatTeam || data.seatController || data.seatQuota);
}

function isStrategyControlActive() {
  const active = typeof document !== "undefined" ? document.activeElement : null;
  if (!active || typeof active.closest !== "function") return false;
  if (!active.closest("#strategy-panel")) return false;
  const tagName = String(active.tagName || "").toUpperCase();
  return tagName === "SELECT" || tagName === "INPUT" || tagName === "TEXTAREA";
}

function applyRoomPayload(payload, { preserveScreen = false } = {}) {
  const hadBattle = Boolean(state.liveBattle || state.battle);
  const previousScreen = state.screen;
  const previousBoardKey = state.battle ? `${state.battle.board.width}x${state.battle.board.height}` : "";
  const previousRoomId = state.room?.room_id || "";
  const previousBattle = state.liveBattle;
  state.heroes = payload.heroes || [];
  if (payload.rooms) {
    state.rooms = payload.rooms;
  }
  state.room = payload.room || null;
  state.historicalMatchId = state.room?.historical ? String(state.room.match_id || state.historicalMatchId || "") : "";
  const finishedMatchId = !state.room?.historical && state.room?.status === "finished" ? String(state.room.match_id || "") : "";
  if (finishedMatchId && finishedMatchId !== state.lastHistorySyncMatchId) {
    state.lastHistorySyncMatchId = finishedMatchId;
    refreshRecentMatches({renderAfter: false}).then(() => render());
  }
  if (state.connectionLostAt) {
    state.reconnectedAt = Date.now();
    state.connectionLostAt = 0;
    enqueueFloatingToast("连接已恢复，已按原席位同步当前房间和战场。", "connection-restored");
  }
  const timeoutAt = Number(state.room?.turn_timer?.last_timeout?.occurred_at || 0);
  if (timeoutAt > state.lastTurnTimeoutAt) {
    if (state.lastTurnTimeoutAt > 0) {
      const timeout = state.room.turn_timer.last_timeout;
      enqueueFloatingToast(`${timeout.player_name || `席位 ${timeout.seat_id}`} 操作超时，系统已继续推进对局。`, `timeout-${timeoutAt}`);
    }
    state.lastTurnTimeoutAt = timeoutAt;
  }
  syncStrategyCampaignFromRoomPayload(payload);
  state.liveBattle = payload.battle || null;
  if (!state.room || state.room.room_id !== previousRoomId) {
    state.lastToastLogCount = 0;
    state.floatingToasts = [];
    state.aiPreview = null;
  }
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
  const previousVisualEventId = maxVisualEventId(previousBattle?.visual_events || []);
  const feedbackEvents = previousBattle && previousRoomId === state.room?.room_id
    ? visualEvents().filter((event) => Number(event?.id || 0) > previousVisualEventId)
    : [];
  globalThis.WujiangBattleFeedback?.consume({
    previousBattle: previousRoomId === state.room?.room_id ? previousBattle : null,
    battle: state.liveBattle,
    events: feedbackEvents,
    viewerTeamId: viewerTeamId(),
    replayMode: isReplayMode(),
  });
  syncFloatingToasts(previousBattle, state.liveBattle);
  syncAiPreview(previousBattle, state.liveBattle);
  trackQuickAiMatchEnd();
}

function loadRecordedMatchEnds() {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECORDED_MATCH_ENDS_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.map((item) => String(item || "")).filter(Boolean) : [];
  } catch (_error) {
    return [];
  }
}

function trackQuickAiMatchEnd() {
  const roomId = String(state.room?.room_id || "");
  if (!roomId || state.room?.experience_kind !== "quick_ai" || !state.battle?.winner || viewerTeamId() === null) return;
  const recorded = loadRecordedMatchEnds();
  const completedAt = Date.now();
  const completedMatch = {room_id: roomId, completed_at: completedAt, mode: "quick_ai"};
  state.lastCompletedMatch = completedMatch;
  localStorage.setItem(LAST_COMPLETED_MATCH_KEY, JSON.stringify(completedMatch));
  if (recorded.includes(roomId)) return;
  recorded.push(roomId);
  localStorage.setItem(RECORDED_MATCH_ENDS_KEY, JSON.stringify(recorded.slice(-40)));
  const createdAtMs = Number(state.room?.created_at || 0) * 1000;
  recordProductEvent("match_end", {
    match_id: roomId,
    mode: "quick_ai",
    result: Number(state.battle.winner) === Number(viewerTeamId()) ? "win" : "loss",
    duration_ms: createdAtMs > 0 ? Math.max(0, completedAt - createdAtMs) : 0,
  });
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
  $("nav-draft").textContent = state.historicalMatchId ? "返回战绩" : "房间大厅";
  $("nav-battle").classList.toggle("hidden", !(state.screen === "draft" && canResume));
  $("copy-invite-top").classList.toggle("hidden", !state.room?.invite_url);
  $("edit-profile").classList.toggle("hidden", state.screen === "battle");
  $("nav-battle").textContent = isGameOver() ? "查看终局" : "返回战场";
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

  if (action?.code === "descent_moment" && !state.stagedPayload?.targetUnitId) {
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>降临时刻</strong>
      <span>先点击带有抹杀计数点的对方单位，再点击其周围合法落点。</span>
    `;
    return;
  }

  if (action?.code === "descent_moment" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>降临时刻</strong>
      <span>已选中 ${target?.name || "目标"}，请点击其周围蓝色高亮落点。</span>
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

  if (action && reviveUnitCellSelection(action)) {
    const selectedId = stagedReviveUnitId(action);
    const selectedCell = stagedReviveCell(action);
    const candidates = reviveUnitCellSelection(action).candidates || [];
    const buttons = candidates.map((entry) => `
      <button type="button" class="board-alert-choice ${selectedId === String(entry.id) ? "is-selected" : ""}" data-revive-unit-id="${entry.id}">${entry.name}</button>
    `).join("");
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>${selectedId ? `已选择复活单位${selectedCell ? "和落点" : "，现在点击周围高亮格作为落点"}。` : "先选择一个已被破坏的单位，再点击周围高亮格作为落点。"}</span>
      <div class="board-alert-actions">${buttons}</div>
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
  const aiPreviewCells = Array.isArray(state.aiPreview?.cells)
    ? state.aiPreview.cells.slice(0, Math.max(0, Number(state.aiPreview.visibleCount || 0)))
    : [];
  const aiPreviewKeys = positionsToSet(aiPreviewCells);
  const aiCurrentKey = aiPreviewCells.length ? positionKey(aiPreviewCells[aiPreviewCells.length - 1]) : "";

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
      if (aiPreviewKeys.has(key)) cell.classList.add("is-ai-preview");
      if (aiCurrentKey && aiCurrentKey === key) cell.classList.add("is-ai-current");
      if (cellEffects.length) cell.classList.add("has-field-effect");
      const cellLabels = [`第 ${y + 1} 行，第 ${x + 1} 列`];
      cellLabels.push(occupant ? `${occupant.name}，队伍 ${occupant.player_id}` : "空格");
      if (preview.cellKeys.has(key)) cellLabels.push("可选范围");
      if (occupant && preview.targetIds.has(occupant.id)) cellLabels.push("可选目标");
      if (unitOccupiedCells(selected).some((cellPosition) => cellPosition.x === x && cellPosition.y === y)) cellLabels.push("当前选择");
      if (cellEffects.length) cellLabels.push(`战场状态：${cellEffects.map((effect) => effect.name).join("、")}`);
      cell.setAttribute?.("aria-label", cellLabels.join("；"));
      if (typeof cell.setAttribute !== "function") cell.ariaLabel = cellLabels.join("；");

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

function renderActionPanel() {
  const panel = $("action-panel");
  if (!panel) return;
  panel.innerHTML = "";
  const note = document.createElement("div");
  note.className = "queue-item action-panel-note";
  note.innerHTML = "<strong>行动提示</strong><p>真正的行动按钮会围绕战场中的当前选中单位显示；这里保留提示、可操作单位与连锁信息。</p>";
  panel.append(note);
  return;
  const actions = displayActions();
  if (!actions.length) {
    const empty = document.createElement("div");
    empty.className = "queue-item";
    empty.innerHTML = `<strong>${isReplayMode() ? "回放中" : "暂无可用动作"}</strong><p>${isReplayMode() ? "回放状态下不可直接操作战场。" : "当前单位没有可执行的动作，或还没有轮到你操作。"}</p>`;
    panel.append(empty);
    return;
  }

  actions.forEach((action) => {
    const btn = document.createElement("button");
    const isSelected = state.selectedActionCode === action.code;
    const disabled = !canInteract() && action.kind !== "chain_skip";
    btn.className = `action-list-item ${isSelected ? "is-selected" : ""} ${disabled ? "is-disabled" : ""}`;
    btn.disabled = disabled;
    btn.innerHTML = `
      <div class="action-title">
        <span>${actionLabel(action)}</span>
        <span>${actionTimingLabel(action)}</span>
      </div>
      <div class="action-meta">${actionTierLabel(action)} · ${actionManaLabel(action) || "不费魔"} · ${actionLimitLabel(action)}</div>
      <div class="action-desc">${action.description || "无额外说明。"}</div>
    `;
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
    btn.addEventListener("click", () => {
      onActionClick(action);
    });
    panel.append(btn);
  });
}

function estimatedSummaryDamage(attackPower, defense) {
  const attack = Number(attackPower || 0);
  const guard = Number(defense || 0);
  if (attack > guard) return 1;
  return 1 / (2 ** Math.max(guard - attack + 1, 1));
}

function previewAffectedUnits(action) {
  if (!action) return [];
  const ids = new Set(action.preview?.target_unit_ids || []);
  (action.preview?.cells || []).forEach((cell) => {
    unitsAtCell(Number(cell.x), Number(cell.y)).forEach((unit) => ids.add(unit.id));
  });
  return [...ids].map((id) => unitById(id)).filter(Boolean);
}

function renderActionForecast() {
  const panel = $("action-forecast");
  if (!panel) return;
  const action = selectedAction();
  if (!action) {
    panel.className = "action-forecast is-empty";
    panel.textContent = tutorialState()?.step_id === "select_unit"
      ? "先点击火葬者；选择行动后，这里会显示消耗、目标、预计效果和最终站位。"
      : "选择一个行动后，这里会显示消耗、合法目标、预计效果和最终站位。";
    return;
  }
  panel.className = "action-forecast";
  const actor = selectedUnit();
  const targets = previewAffectedUnits(action);
  const targetNames = targets.length ? targets.map((unit) => unit.name).join("、") : "选择目标后确认";
  const mana = actionManaLabel(action) || "不消耗魔力";
  let effect = action.description || "按行动说明结算";
  if (action.kind === "attack" && actor && targets.length) {
    const estimates = targets.map((target) => `${target.name} 约 ${trimNumber(estimatedSummaryDamage(actor.stats.attack, target.stats.defense))} 血`);
    effect = `${estimates.join("；")}（护盾、连锁、免疫和多格命中会改变实际结果）`;
  } else if (action.kind === "move") {
    effect = "移动本身不造成伤害；路径上的进入/穿过效果仍会正常触发。";
  }
  const path = stagedMovePath(action);
  const destination = path[path.length - 1];
  const finalPosition = action.kind === "move"
    ? (destination ? `(${destination.x + 1}, ${destination.y + 1})` : "选择路径后显示")
    : (actor ? `保持在 (${actor.x + 1}, ${actor.y + 1})` : "不改变站位");
  panel.innerHTML = `
    <strong>${actionLabel(action)}</strong>
    <div class="action-forecast-row"><span>资源消耗</span><span>${mana} · ${actionLimitLabel(action)}</span></div>
    <div class="action-forecast-row"><span>合法目标</span><span>${targetNames}</span></div>
    <div class="action-forecast-row"><span>预计效果</span><span>${effect}</span></div>
    <div class="action-forecast-row"><span>最终站位</span><span>${finalPosition}</span></div>
    <div class="action-forecast-row"><span>影响单位</span><span>${targets.length ? `${targets.length} 个：${targetNames}` : "随当前高亮范围更新"}</span></div>
  `;
}

function renderActionWheel() {
  const layer = actionWheelLayer();
  if (!layer) return;
  layer.innerHTML = "";
  const unit = selectedUnit();
  const actions = displayActions();
  if (!unit || !actions.length) return;
  const action = selectedAction();
  if (action && actionNeedsTarget(action)) return;
  const stageRect = $("board-stage")?.getBoundingClientRect?.();
  const bounds = unitBoundsRelativeToStage(unit);
  if (!stageRect || !bounds) return;

  const buttonWidth = document.body.classList.contains("battle-mode") ? 74 : 84;
  const buttonHeight = document.body.classList.contains("battle-mode") ? 40 : 46;
  const gap = 10;
  const stagePadding = 12;
  const actionCount = actions.length;
  const columns = actionCount <= 3 ? 1 : actionCount <= 8 ? 2 : 3;
  const rows = Math.ceil(actionCount / columns);
  const clusterWidth = columns * buttonWidth + Math.max(0, columns - 1) * gap;
  const clusterHeight = rows * buttonHeight + Math.max(0, rows - 1) * gap;
  const centerX = bounds.left + bounds.width / 2;
  const centerY = bounds.top + bounds.height / 2;
  const placements = [
    {
      left: bounds.right + 16,
      top: centerY - clusterHeight / 2,
      score: stageRect.width - bounds.right,
      required: clusterWidth + 16,
    },
    {
      left: bounds.left - 16 - clusterWidth,
      top: centerY - clusterHeight / 2,
      score: bounds.left,
      required: clusterWidth + 16,
    },
    {
      left: centerX - clusterWidth / 2,
      top: bounds.bottom + 16,
      score: stageRect.height - bounds.bottom,
      required: clusterHeight + 16,
    },
    {
      left: centerX - clusterWidth / 2,
      top: bounds.top - 16 - clusterHeight,
      score: bounds.top,
      required: clusterHeight + 16,
    },
  ];
  const chosen = placements.find((placement) => placement.score >= placement.required)
    || placements.sort((a, b) => b.score - a.score)[0];
  const maxLeft = Math.max(stagePadding, stageRect.width - clusterWidth - stagePadding);
  const maxTop = Math.max(stagePadding, stageRect.height - clusterHeight - stagePadding);
  const anchorLeft = Math.max(stagePadding, Math.min(maxLeft, chosen.left));
  const anchorTop = Math.max(stagePadding, Math.min(maxTop, chosen.top));

  actions.forEach((action, index) => {
    const btn = document.createElement("button");
    const isSelected = state.selectedActionCode === action.code;
    const disabled = !canInteract() && action.kind !== "chain_skip";
    const column = index % columns;
    const row = Math.floor(index / columns);
    btn.className = `action-btn ${isSelected ? "is-selected" : ""}`;
    if (disabled) btn.classList.add("is-disabled");
    btn.disabled = disabled;
    btn.style.left = `${anchorLeft + column * (buttonWidth + gap)}px`;
    btn.style.top = `${anchorTop + row * (buttonHeight + gap)}px`;
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
    btn.addEventListener("click", () => {
      onActionClick(action);
    });
    layer.append(btn);
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

function tooltipNode() {
  return $("control-tooltip");
}

function hideTooltip() {
  const node = tooltipNode();
  if (!node) return;
  node.classList.add("hidden");
  node.textContent = "";
}

function showTooltip(text, pointer) {
  const node = tooltipNode();
  if (!node || !text || !pointer) return;
  node.textContent = text;
  node.classList.remove("hidden");
  const x = Math.min(window.innerWidth - 16, pointer.x + 12);
  const y = Math.min(window.innerHeight - 16, pointer.y + 12);
  node.style.left = `${x}px`;
  node.style.top = `${y}px`;
}

function pruneFloatingToasts() {
  const now = Date.now();
  state.floatingToasts = state.floatingToasts.filter((toast) => Number(toast.expiresAt || 0) > now);
}

function renderFloatingToasts() {
  const stack = $("floating-toast-stack");
  if (!stack) return;
  pruneFloatingToasts();
  stack.innerHTML = "";
  state.floatingToasts.forEach((toast) => {
    const item = document.createElement("div");
    item.className = "floating-toast";
    item.textContent = toast.text;
    stack.append(item);
  });
  stack.classList.toggle("hidden", state.floatingToasts.length === 0);
}

function enqueueFloatingToast(text) {
  const message = String(text || "").trim();
  if (!message) return;
  pruneFloatingToasts();
  state.floatingToasts.push({
    id: `${Date.now()}-${Math.random()}`,
    text: message,
    expiresAt: Date.now() + 5000,
  });
  renderFloatingToasts();
  const timer = window.setTimeout || (typeof setTimeout === "function" ? setTimeout : null);
  if (typeof timer !== "function") return;
  timer(() => {
    renderFloatingToasts();
  }, 5100);
}

function syncFloatingToasts(previousBattle, nextBattle) {
  if (!nextBattle) {
    state.lastToastLogCount = 0;
    state.floatingToasts = [];
    return;
  }
  if (!previousBattle) {
    state.lastToastLogCount = Array.isArray(nextBattle.logs) ? nextBattle.logs.length : 0;
    return;
  }
  const previousCount = previousBattle?.logs?.length || 0;
  const nextLogs = Array.isArray(nextBattle.logs) ? nextBattle.logs : [];
  const startIndex = Math.max(previousCount, state.lastToastLogCount);
  nextLogs.slice(startIndex).forEach((line) => {
    enqueueFloatingToast(line);
  });
  state.lastToastLogCount = nextLogs.length;
}

function simulationPendingAction() {
  return state.room?.simulation?.pending_action || null;
}

function clearAiPreview() {
  state.aiPreview = null;
}

function actionPreviewCells(meta, battleState) {
  const fromPath = normalizedPatternCells(Array.isArray(meta?.path) ? meta.path : []);
  if (fromPath.length) return fromPath;
  const fromCells = normalizedPatternCells(Array.isArray(meta?.cells) ? meta.cells : []);
  if (fromCells.length) return fromCells;
  const targetIds = Array.isArray(meta?.target_unit_ids) ? meta.target_unit_ids : [];
  return targetIds
    .map((id) => (battleState?.units || []).find((unit) => unit.id === id))
    .filter((unit) => unit?.position)
    .flatMap((unit) => unitOccupiedCells(unit));
}

function playAiPreview(meta, battleState) {
  const previewCells = actionPreviewCells(meta, battleState);
  const messages = Array.isArray(meta?.log_lines) ? meta.log_lines.filter(Boolean) : [];
  if (!messages.length && meta?.actor_name && meta?.display_name) {
    messages.push(`${meta.actor_name} 使用了【${meta.display_name}】`);
  }
  messages.forEach((line) => enqueueFloatingToast(line));
  if (!previewCells.length) {
    state.aiPreview = null;
    return;
  }
  const previewId = Number(meta?.id || Date.now());
  state.aiPreview = { id: previewId, cells: previewCells, visibleCount: 0 };
  render();
  window.setTimeout(() => {
    if (!state.aiPreview || state.aiPreview.id !== previewId) return;
    state.aiPreview = { ...state.aiPreview, visibleCount: 1 };
    render();
    previewCells.forEach((_, index) => {
      window.setTimeout(() => {
        if (!state.aiPreview || state.aiPreview.id !== previewId) return;
        state.aiPreview = { ...state.aiPreview, visibleCount: Math.min(previewCells.length, index + 1) };
        render();
      }, index * 600);
    });
    window.setTimeout(() => {
      if (!state.aiPreview || state.aiPreview.id !== previewId) return;
      clearAiPreview();
    }, Math.max(1800, previewCells.length * 600 + 1300));
  }, 1300);
}

function syncAiPreview(previousBattle, nextBattle) {
  const meta = simulationPendingAction();
  if (!meta || !meta.actor_is_ai) {
    state.aiPreview = null;
    return;
  }
  const battleState = nextBattle || previousBattle || state.liveBattle || state.battle;
  const previewCells = actionPreviewCells(meta, battleState);
  state.aiPreview = {
    id: Number(meta?.id || 0),
    cells: previewCells,
    visibleCount: Math.max(0, Math.min(previewCells.length, Number(meta?.visible_count || 0))),
  };
}

function renderSidebarPanels() {
  const rightRail = $("battle-right-rail");
  const toggle = $("toggle-right-rail");
  if (rightRail) {
    rightRail.classList.toggle("is-collapsed", state.rightRailCollapsed);
  }
  if (toggle) {
    toggle.setAttribute("aria-expanded", state.rightRailCollapsed ? "false" : "true");
    toggle.textContent = state.rightRailCollapsed ? "展开" : "收起";
  }
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

function formatPostgameValue(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return Number.isInteger(number) ? String(number) : String(Math.round(number * 1000) / 1000);
}

function renderPostgameSummary() {
  const panel = $("postgame-summary");
  const summary = state.room?.postgame || {available: false};
  if (!panel) return;
  panel.classList.toggle("hidden", !summary.available);
  if (!summary.available) return;
  $("postgame-reason").textContent = `${summary.winner_team_name}获胜：${summary.reason_text}`;
  $("postgame-meta").textContent = `${Number(summary.completed_turns || 0)} 个武将回合 · ${Number(summary.duration_seconds || 0)} 秒`;
  $("postgame-formula").textContent = `综合贡献：${summary.mvp_formula}`;

  const teamStats = $("postgame-team-stats");
  teamStats.innerHTML = "";
  (summary.team_stats || []).forEach((team) => {
    const card = document.createElement("article");
    card.className = `postgame-team-card ${Number(team.team_id) === Number(summary.winner_team_id) ? "is-winner" : ""}`;
    const title = document.createElement("strong");
    title.textContent = `${team.team_name}${Number(team.team_id) === Number(summary.winner_team_id) ? " · 胜方" : ""}`;
    const line = document.createElement("span");
    line.textContent = `伤害 ${formatPostgameValue(team.damage_dealt)} · 治疗 ${formatPostgameValue(team.healing_done)} · 承伤 ${formatPostgameValue(team.damage_taken)} · 击破 ${team.kills || 0} · 破盾 ${team.shields_broken || 0} · 连锁 ${team.chain_reactions || 0}`;
    card.append(title, line);
    teamStats.append(card);
  });

  const mvpPanel = $("postgame-mvp");
  mvpPanel.innerHTML = "";
  if (summary.mvp) {
    const title = document.createElement("strong");
    title.textContent = `本局 MVP · ${summary.mvp.name}（综合贡献 ${formatPostgameValue(summary.mvp.contribution_score)}）`;
    const detail = document.createElement("span");
    detail.textContent = summary.mvp.explanation || "按本局实际贡献计算。";
    mvpPanel.append(title, detail);
  } else {
    mvpPanel.textContent = "本局没有足够数据生成 MVP。";
  }

  const nextGoal = $("postgame-next-goal");
  if (nextGoal) {
    nextGoal.innerHTML = "";
    const title = document.createElement("strong");
    title.textContent = "下一修炼目标";
    const detail = document.createElement("span");
    detail.textContent = state.progressionError
      || state.progression?.next_goal?.message
      || (state.progressionBusy ? "正在更新本局后的熟练度..." : "返回首页可查看完整武将熟练度。 ");
    nextGoal.append(title, detail);
  }

  const heroBody = $("postgame-hero-stats");
  heroBody.innerHTML = "";
  (summary.hero_stats || []).forEach((hero) => {
    const row = document.createElement("tr");
    const values = [
      `${hero.name}${hero.owner_name ? ` · ${hero.owner_name}` : ""}`,
      formatPostgameValue(hero.damage_dealt),
      formatPostgameValue(hero.healing_done),
      formatPostgameValue(hero.damage_taken),
      String(hero.kills || 0),
      String(hero.shields_broken || 0),
      String(hero.chain_reactions || 0),
    ];
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    heroBody.append(row);
  });

  const keyTurns = $("postgame-key-turns");
  keyTurns.innerHTML = "";
  (summary.key_turns || []).forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "postgame-key-turn";
    const title = document.createElement("strong");
    title.textContent = `第 ${item.turn_index} 个武将回合 · ${item.title}`;
    const detail = document.createElement("span");
    detail.textContent = `${item.detail}${item.replay_step_index != null ? ` · 回放步骤 ${item.replay_step_index}` : ""}`;
    button.append(title, detail);
    button.disabled = item.replay_step_index == null || !state.room?.replay?.available;
    button.addEventListener("click", () => loadReplayStep(Number(item.replay_step_index)));
    keyTurns.append(button);
  });
  if (!(summary.key_turns || []).length) {
    const empty = document.createElement("span");
    empty.textContent = "本局没有可标记的关键回合。";
    keyTurns.append(empty);
  }
}

function renderGameOverOverlay() {
  const overlay = $("game-over-overlay");
  const title = $("game-over-title");
  const text = $("game-over-text");
  const rematch = $("game-over-rematch");
  const strategy = $("game-over-strategy");
  if (!state.battle || !isGameOver() || state.screen !== "battle" || isReplayMode()) {
    overlay.classList.add("hidden");
    return;
  }
  renderPostgameSummary();
  title.textContent = "游戏结束";
  const tutorial = tutorialState();
  if (tutorial) {
    title.textContent = state.battle.winner === 1 ? "教学完成" : "这次没有获胜";
    text.textContent = state.battle.winner === 1
      ? "你已经完成固定新手战斗。可以再次练习，或返回首页选择其他模式。"
      : "可以从自由战斗检查点继续，不需要重新完成前面的教学步骤。";
  } else text.textContent = state.strategyCampaign
    ? `玩家 ${state.battle.winner} 已获胜。战场上的行动与连锁都已锁定，战役结算已同步。`
    : `玩家 ${state.battle.winner} 已获胜。战场上的行动与连锁都已锁定。你可以回到房间大厅,或者直接重新开始选将。`;
  if (strategy) {
    const hasStrategyCampaign = Boolean(state.strategyCampaign);
    strategy.classList.toggle("hidden", !hasStrategyCampaign);
    strategy.disabled = !hasStrategyCampaign;
  }
  if (rematch) {
    rematch.disabled = !Boolean(state.room?.can_rematch && state.room?.viewer_player_id !== null);
    if (tutorial) {
      rematch.disabled = state.battle.winner !== 1 && !tutorial.can_retry_checkpoint;
      rematch.textContent = state.battle.winner === 1 ? "再次开始教学" : "从检查点重试";
    } else if (state.room?.experience_kind === "quick_ai") {
      rematch.textContent = "同阵容再来一局";
    } else {
      rematch.textContent = state.room?.viewer_is_host ? "同配置再来一局" : "等待房主再开一局";
    }
  }
  overlay.classList.remove("hidden");
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
  const preserveRoomConfig = isRoomConfigControlActive();
  const preserveStrategyControl = isStrategyControlActive();
  renderScreens();
  renderNavigation();
  renderProfilePanel();
  renderAuthPanel();
  renderHomeFlow();
  renderRecentMatches();
  if (!preserveStrategyControl) renderStrategyPanel();
  renderProfileModal();
  if (!preserveRoomConfig) renderRoomPanels();
  applyRandomRoomPanelState();
  renderResumePanel();
  renderRoomListActive();
  renderHeroCards();
  renderHeader();
  renderConnectionAndTurnState();
  renderBoardZoomControls();
  renderMessage();
  renderBattleEffects();
  renderBoard();
  renderBoardOverlays();
  renderHoverCard();
  renderSidebarPanels();
  renderSelectedCard();
  renderActionPanel();
  renderActionForecast();
  renderUnitStrip();
  renderChainPanel();
  renderLogs();
  renderFloatingToasts();
  renderGameOverOverlay();
  renderReplayToolbar();
  renderTutorialGuide();
  renderRoomActionButtons();
  renderTargetCancelButton();
  renderTargetCompleteButton();
  const tutorialStepId = tutorialState()?.step_id;
  $("end-turn").disabled = !canInteract() || isChainMode() || isRespawnMode()
    || Boolean(tutorialStepId && !["end_turn", "win_objective"].includes(tutorialStepId));
  $("skip-chain").disabled = !canInteract() || !isChainMode();
}

async function refreshState({ preserveScreen = true } = {}) {
  if (state.historicalMatchId) return;
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    syncIdentityFromUrl();
    const roomId = roomQueryId();
    if (!roomId) {
      const payload = await fetchJson("/api/heroes");
      state.heroes = payload.heroes;
      state.rooms = payload.rooms || [];
      state.onboarding = payload.onboarding || state.onboarding;
      state.room = null;
      state.battle = null;
      state.liveBattle = null;
      state.replayMode = false;
      state.replayStepIndex = 0;
      state.replayOmniscient = false;
      state.playerToken = "";
      await refreshResumableTutorial();
      if (userLoggedIn()) {
        await refreshStrategyCampaigns({ renderAfter: false });
      } else {
        clearStrategyState();
      }
      syncScreen({ preferBattle: false });
      const homeRenderSignature = JSON.stringify({
        rooms: (state.rooms || []).map((room) => [room.room_id, room.status, room.player_count, room.is_full]),
        campaigns: (state.strategyCampaigns || []).map((campaign) => [
          campaign.id,
          campaign.updated_at,
          campaign.status,
          campaign.world?.current_month,
          campaign.resume?.can_resume,
          campaign.resume?.online_initial_user_ids,
        ]),
        authenticatedUserId: state.authUser?.id || 0,
        selectedCampaignId: state.strategyCampaign?.id || 0,
        resumableTutorial: state.resumableTutorial
          ? [state.resumableTutorial.room_id, state.resumableTutorial.step_id]
          : null,
        tutorialResumeError: state.tutorialResumeError,
      });
      if (homeRenderSignature === lastHomeRenderSignature) return;
      lastHomeRenderSignature = homeRenderSignature;
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
      if (!state.connectionLostAt) state.connectionLostAt = Date.now();
      render();
      $("message").textContent = error.error || "连接中断，正在保留当前房间身份等待重新同步。";
    }
  } finally {
    refreshInFlight = false;
  }
}

async function createRoom() {
  if (!requireAuthForRoomEntry()) return;
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
  if (!requireAuthForRoomEntry()) return;
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
    $("room-message").textContent = error.error || "同配置再战准备失败。";
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
        const summary = seat.hero_summary || seat.hero_name || (room.mode === "random" && seat.occupied ? "\u5f00\u5c40\u540e\u968f\u673a\u5206\u914d" : "");
        return `\u5e2d\u4f4d ${seat.player_id}\uff1a${seat.team_name || ""} \u00b7 ${seat.name || controllerTypeLabel(seat)}${summary ? ` \u00b7 ${summary}` : ""}`;
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

async function startTutorialBattle() {
  if (state.quickStartBusy) return;
  if (!requireAuthForRoomEntry()) return;
  if (!state.profileReady) {
    openProfileModal();
    render();
    return;
  }
  state.quickStartBusy = true;
  render();
  try {
    const payload = await fetchJson("/api/rooms/tutorial-start", {
      method: "POST",
      body: JSON.stringify({player_name: effectiveProfileName()}),
    });
    state.playerToken = payload.player_token;
    saveStoredIdentity(payload.room.room_id, payload.player_token, payload.room.viewer_name || effectiveProfileName());
    localStorage.setItem(LAST_TUTORIAL_ROOM_KEY, payload.room.room_id);
    state.resumableTutorial = null;
    state.tutorialResumeError = "";
    syncLocation("battle", payload.room.room_id);
    applyRoomPayload(payload, {preserveScreen: false});
    await recordProductEvent("tutorial_start", {tutorial_id: "first_battle"});
    await recordProductEvent("match_start", {match_id: payload.room.room_id, mode: "tutorial"});
    render();
  } catch (error) {
    enqueueFloatingToast(error.error || "新手教学创建失败，请重试。");
  } finally {
    state.quickStartBusy = false;
    render();
  }
}

async function startQuickAiBattle({rematch = false} = {}) {
  if (state.quickStartBusy) return;
  if (!requireAuthForRoomEntry()) return;
  if (!state.profileReady) {
    openProfileModal();
    render();
    return;
  }
  let previousMatch = state.lastCompletedMatch;
  if (!previousMatch) {
    try {
      previousMatch = JSON.parse(localStorage.getItem(LAST_COMPLETED_MATCH_KEY) || "null");
    } catch (_error) {
      previousMatch = null;
    }
  }
  state.quickStartBusy = true;
  render();
  try {
    const payload = await fetchJson("/api/rooms/quick-ai-start", {
      method: "POST",
      body: JSON.stringify({player_name: effectiveProfileName()}),
    });
    state.playerToken = payload.player_token;
    saveStoredIdentity(payload.room.room_id, payload.player_token, payload.room.viewer_name || effectiveProfileName());
    syncLocation("battle", payload.room.room_id);
    applyRoomPayload(payload, {preserveScreen: false});
    await recordProductEvent("quick_ai_start", {
      match_id: payload.room.room_id,
      roster_code: payload.quick_ai?.player_roster_code || "steady_front",
      opponent_code: payload.quick_ai?.opponent_roster_code || "ranged_pressure",
    });
    await recordProductEvent("match_start", {match_id: payload.room.room_id, mode: "quick_ai"});
    if (rematch && previousMatch?.room_id) {
      await recordProductEvent("rematch_start", {
        match_id: previousMatch.room_id,
        mode: "quick_ai",
        duration_ms: Math.max(0, Date.now() - Number(previousMatch.completed_at || Date.now())),
      });
    }
    render();
  } catch (error) {
    enqueueFloatingToast(error.error || "快速 AI 对战创建失败，请重试。");
  } finally {
    state.quickStartBusy = false;
    render();
  }
}

async function resumeTutorialBattle() {
  if (state.quickStartBusy || !state.resumableTutorial) return;
  if (!requireAuthForRoomEntry()) return;
  const remembered = {...state.resumableTutorial};
  state.quickStartBusy = true;
  render();
  try {
    const query = new URLSearchParams({
      room_id: remembered.room_id,
      player_token: remembered.player_token,
    });
    const payload = await fetchJson(`/api/rooms/state?${query.toString()}`);
    const tutorial = payload.room?.tutorial;
    if (payload.room?.experience_kind !== "tutorial" || !tutorial || tutorial.completed_at || payload.battle?.winner) {
      clearResumableTutorial();
      throw {error: "这场教学已经结束，请重新开始教学。"};
    }
    if (payload.room.viewer_player_id === null || payload.room.viewer_player_id === undefined) {
      clearResumableTutorial();
      throw {error: "上次教学的席位凭据已失效，请重新开始教学。"};
    }
    state.playerToken = remembered.player_token;
    syncLocation("battle", remembered.room_id);
    applyRoomPayload(payload, {preserveScreen: false});
    await recordProductEvent("tutorial_step", {
      tutorial_id: "first_battle",
      step_id: tutorial.step_id,
      status: "resumed",
    });
    render();
  } catch (error) {
    state.tutorialResumeError = error.error || "恢复教学失败；你可以重试或重新开始。";
    enqueueFloatingToast(state.tutorialResumeError);
  } finally {
    state.quickStartBusy = false;
    render();
  }
}

async function completeTutorialUnitSelection(unitId) {
  if (tutorialState()?.step_id !== "select_unit" || !unitId) return;
  try {
    const previousStep = tutorialState()?.step_id;
    const payload = await fetchJson("/api/rooms/tutorial-select-unit", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room?.room_id,
        player_token: state.playerToken,
        unit_id: unitId,
      }),
    });
    applyRoomPayload(payload, {preserveScreen: true});
    await recordProductEvent("tutorial_step", {tutorial_id: "first_battle", step_id: previousStep, status: "completed"});
    await recordProductEvent("tutorial_step", {tutorial_id: "first_battle", step_id: tutorialState()?.step_id, status: "started"});
    render();
  } catch (error) {
    $("message").textContent = error.error || "请点击你控制的火葬者。";
  }
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
  const query = state.historicalMatchId
    ? new URLSearchParams({match_id: state.historicalMatchId, step_index: String(Math.max(0, Number(stepIndex) || 0))})
    : new URLSearchParams({room_id: state.room.room_id, step_index: String(Math.max(0, Number(stepIndex) || 0))});
  if (!state.historicalMatchId && state.playerToken) query.set("player_token", state.playerToken);
  if (!state.historicalMatchId && omniscient) query.set("omniscient", "1");
  try {
    const endpoint = state.historicalMatchId ? "/api/matches/replay" : "/api/rooms/replay";
    const payload = await fetchJson(`${endpoint}?${query.toString()}`);
    state.replayMode = true;
    state.replayStepIndex = Number(payload.replay?.step_index || 0);
    state.replayOmniscient = Boolean(payload.replay?.omniscient);
    state.battle = payload.battle || null;
    syncSelectedUnitAfterStateChange();
    render();
  } catch (error) {
    $("message").textContent = error.error || "\u52a0\u8f7d\u56de\u653e\u6b65\u6570\u5931\u8d25\u3002";
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
    $("message").textContent = error.error || "\u63a7\u5236 AI \u6a21\u62df\u5931\u8d25\u3002";
  }
}

async function performAction(payload) {
  const previousTutorial = tutorialState();
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
    recordProductEvent("action_succeeded", {
      match_id: state.room?.room_id || "",
      mode: tutorialState() ? "tutorial" : state.room?.mode || "room",
      action_type: payload.type || "unknown",
    });
    const currentTutorial = tutorialState();
    if (previousTutorial && currentTutorial && previousTutorial.step_id !== currentTutorial.step_id) {
      recordProductEvent("tutorial_step", {tutorial_id: "first_battle", step_id: previousTutorial.step_id, status: "completed"});
      recordProductEvent("tutorial_step", {tutorial_id: "first_battle", step_id: currentTutorial.step_id, status: "started"});
      if (!previousTutorial.first_effective_action_at && currentTutorial.first_effective_action_at) {
        recordProductEvent("first_effective_action", {
          tutorial_id: "first_battle",
          action_type: payload.type,
          duration_ms: Math.max(0, Math.round((currentTutorial.first_effective_action_at - currentTutorial.started_at) * 1000)),
        });
      }
    }
    clearActionSelection();
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("message").textContent = error.error || "执行失败。";
    recordProductEvent("invalid_action", {
      match_id: state.room?.room_id || "",
      mode: tutorialState() ? "tutorial" : state.room?.mode || "room",
      action_type: payload.type || "unknown",
      reason: error.error || "rejected",
    });
  }
}

async function toggleRoomReady() {
  const seat = currentRoomSeat();
  if (!hasRoom() || !state.playerToken || !seat?.is_human || state.room.status !== "lobby") return;
  try {
    const payload = await fetchJson("/api/rooms/set-ready", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        ready: !seat.ready,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    $("room-message").textContent = error.error || "准备状态更新失败。";
  }
}

const TUTORIAL_STEP_LABELS = {
  select_unit: "选中你的武将",
  move: "向敌人靠近",
  basic_attack: "进行普通攻击",
  active_skill: "使用主动技能",
  end_turn: "结束当前回合",
  chain_response: "完成一次连锁响应",
  win_objective: "独立赢下教学战",
};

function renderTutorialGuide() {
  const guide = $("tutorial-guide");
  if (!guide) return;
  const tutorial = tutorialState();
  guide.classList.toggle("hidden", !tutorial);
  if (!tutorial) return;
  const ids = Object.keys(TUTORIAL_STEP_LABELS);
  const currentIndex = Math.max(0, ids.indexOf(tutorial.step_id));
  const reviewIndex = Math.max(0, currentIndex - state.tutorialHistoryOffset);
  const reviewId = ids[reviewIndex];
  const reviewing = state.tutorialHistoryOffset > 0;
  $("tutorial-step-count").textContent = `步骤 ${currentIndex + 1}/${ids.length}`;
  $("tutorial-objective").textContent = tutorial.completed_at ? "教学完成" : "目标：击败艾莉";
  $("tutorial-title").textContent = reviewing ? `回顾：${TUTORIAL_STEP_LABELS[reviewId]}` : tutorial.step.title;
  $("tutorial-instruction").textContent = reviewing
    ? "这是已经完成的步骤说明；战局不会回滚。点击当前步骤可回到正在进行的目标。"
    : tutorial.step.instruction;
  guide.classList.toggle("is-collapsed", state.tutorialGuideCollapsed);
  $("tutorial-back-note").disabled = currentIndex === 0;
  $("tutorial-back-note").textContent = reviewing ? "当前步骤" : "上一步说明";
  $("tutorial-retry").disabled = tutorial.step_id !== "win_objective" || !tutorial.can_retry_checkpoint;
  $("tutorial-skip-note").textContent = state.tutorialGuideCollapsed ? "展开说明" : "跳过说明";
  if (tutorial.completed_at && !state.tutorialCompletionRecorded) {
    state.tutorialCompletionRecorded = true;
    const durationMs = Math.max(0, Math.round((tutorial.completed_at - tutorial.started_at) * 1000));
    recordProductEvent("tutorial_complete", {tutorial_id: "first_battle", duration_ms: durationMs});
    recordProductEvent("match_end", {match_id: state.room.room_id, mode: "tutorial", result: "win", duration_ms: durationMs});
  }
}

async function retryTutorialStep() {
  const tutorial = tutorialState();
  if (!tutorial) return;
  clearActionSelection();
  if (!tutorial.can_retry_checkpoint) {
    $("message").textContent = "本步战局仍然有效，请按金色提示重新操作。";
    render();
    return;
  }
  try {
    const payload = await fetchJson("/api/rooms/tutorial-retry", {
      method: "POST",
      body: JSON.stringify({room_id: state.room.room_id, player_token: state.playerToken}),
    });
    applyRoomPayload(payload, {preserveScreen: false});
    recordProductEvent("tutorial_step", {tutorial_id: "first_battle", step_id: tutorial.step_id, status: "retried"});
    render();
  } catch (error) {
    $("message").textContent = error.error || "无法恢复教学检查点。";
  }
}

function exitTutorial() {
  const tutorial = tutorialState();
  if (!tutorial) return;
  recordProductEvent("tutorial_exit", {
    tutorial_id: "first_battle",
    step_id: tutorial.step_id,
    reason: "player_exit",
    duration_ms: Math.max(0, Math.round((Date.now() / 1000 - tutorial.started_at) * 1000)),
  });
  state.room = null;
  state.battle = null;
  state.liveBattle = null;
  state.playerToken = "";
  state.screen = "draft";
  state.homeFlow = "quick";
  syncLocation("draft", "");
  refreshState({preserveScreen: false});
}

function restartFromGameOver() {
  const tutorial = tutorialState();
  if (!tutorial) {
    if (state.room?.experience_kind === "quick_ai") {
      startQuickAiBattle({rematch: true});
      return;
    }
    restartRoomDraft();
    return;
  }
  if (state.battle?.winner === 1) startTutorialBattle();
  else retryTutorialStep();
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
  const key = positionKey({ x, y });
  const previewRestrictsCells = preview.cellKeys.size > 0;
  if (previewRestrictsCells && !preview.cellKeys.has(key)) {
    return "";
  }
  if (occupant && preview.targetIds.has(occupant.id) && unitIsSelectableTarget(occupant)) {
    return occupant.id;
  }
  return unitsAtCell(x, y)
    .filter((unit) => preview.targetIds.has(unit.id) && unitIsSelectableTarget(unit))
    .map((unit) => unit.id)[0] || "";
}

function explainInvalidBoardChoice(action, occupant = null) {
  let reason = "该格不属于当前行动的合法范围，请选择高亮格。";
  if (action?.kind === "move") reason = "该格无法作为移动路径的下一步：可能超出剩余速度、被单位阻挡或会让完整身形越界。";
  else if (action?.kind === "attack") reason = occupant
    ? "该单位不在当前攻击范围或合法直线上。"
    : "这里没有可攻击的单位，请点击高亮目标。";
  else if (action?.kind === "skill") reason = occupant
    ? "该单位不是此技能当前可选择的合法目标。"
    : "该格不符合技能的范围、方向或形状要求。";
  $("message").textContent = reason;
  enqueueFloatingToast(reason);
}

function onBoardClick(x, y, occupant) {
  if (!canInteract()) {
    clearActionSelection();
    state.selectedUnitId = occupant?.id || "";
    if (occupant) state.sidebarExpanded = "info";
    render();
    return;
  }
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
      || reviveUnitCellSelection(action)
      || (isChainMode() && action.code === "backstep_shot")
    ),
  );

  if (action && !canUseCell && !canUseUnit && !usesStructuredSelection) {
    const rawCellKeys = positionsToSet(action.preview?.cells || []);
    const rawTargetIds = targetIdsToSet(action.preview?.target_unit_ids || []);
    canUseCell = rawCellKeys.has(key);
    canUseUnit = occupant ? rawTargetIds.has(occupant.id) : false;
  }

  if (
    action
    && occupant
    && preview.cellKeys.size
    && preview.targetIds.size
    && action.preview?.requires_target
    && action.target_mode !== "cell"
  ) {
    canUseUnit = canUseUnit && preview.cellKeys.has(key);
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
    if (occupant) completeTutorialUnitSelection(occupant.id);
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
    const nextAnchor = movePathAnchorForClickedCell(action, clickedCell, chosenPath);
    if (nextAnchor) {
      setStagedMovePath([...chosenPath, nextAnchor]);
      render();
      return;
    }
    const existingIndex = movePathIndexForClickedCell(action, clickedCell, chosenPath);
    if (existingIndex >= 0) {
      setStagedMovePath(chosenPath.slice(0, existingIndex));
      render();
      return;
    }
    explainInvalidBoardChoice(action, occupant);
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
    if (!(occupant && canUseUnit)) {
      explainInvalidBoardChoice(action, occupant);
      return;
    }
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

  if (reviveUnitCellSelection(action)) {
    if (!stagedReviveUnitId(action)) return;
    if (!positionsToSet(reviveSelectionCells(action)).has(key)) return;
    const selectedCell = stagedReviveCell(action);
    setStagedReviveCell(selectedCell && sameCell(selectedCell, { x, y }) ? null : { x, y });
    render();
    return;
  }

  if (isChainMode()) {
    if (!actionNeedsTarget(action)) return;
    if (!canUseCell && !canUseUnit) {
      explainInvalidBoardChoice(action, occupant);
      return;
    }
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
    explainInvalidBoardChoice(action, occupant);
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
    if (!targetUnitId) {
      explainInvalidBoardChoice(action, occupant);
      return;
    }
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

  if (action.code === "descent_moment") {
    if (!state.stagedPayload?.targetUnitId) {
      if (!(occupant && canUseUnit)) return;
      state.stagedPayload = { targetUnitId: occupant.id };
      render();
      return;
    }
    if (!canUseCell) {
      explainInvalidBoardChoice(action, occupant);
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

function keyboardHelpIsOpen() {
  return Boolean($("keyboard-help") && !$("keyboard-help").classList.contains("hidden"));
}

function openKeyboardHelp() {
  const panel = $("keyboard-help");
  if (!panel) return;
  keyboardHelpReturnFocus = document.activeElement;
  panel.classList.remove("hidden");
  $("close-keyboard-help")?.focus();
}

function closeKeyboardHelp() {
  const panel = $("keyboard-help");
  if (!panel) return;
  panel.classList.add("hidden");
  keyboardHelpReturnFocus?.focus?.();
  keyboardHelpReturnFocus = null;
}

function eventComesFromTextControl(event) {
  const target = event.target;
  if (!target || typeof target.closest !== "function") return false;
  return Boolean(target.closest("input, select, textarea, [contenteditable='true']"));
}

function clickEnabledControl(id) {
  const control = $(id);
  if (!control || control.disabled || control.classList.contains("hidden")) return false;
  control.click();
  return true;
}

function handleBattleKeyboard(event) {
  if (eventComesFromTextControl(event)) return;
  if (event.key === "?" || (event.key === "/" && event.shiftKey)) {
    event.preventDefault();
    if (keyboardHelpIsOpen()) closeKeyboardHelp();
    else openKeyboardHelp();
    return;
  }
  if (keyboardHelpIsOpen()) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeKeyboardHelp();
    } else if (event.key === "Tab") {
      event.preventDefault();
      $("close-keyboard-help")?.focus();
    }
    return;
  }
  if (state.screen !== "battle") return;
  const key = String(event.key || "").toLowerCase();
  let handled = false;
  if (event.key === "Escape") handled = clickEnabledControl("cancel-targeting");
  else if (event.key === "Enter") handled = clickEnabledControl("complete-targeting");
  else if (key === "e") handled = clickEnabledControl("end-turn");
  else if (event.key === "[") handled = clickEnabledControl("replay-step-back");
  else if (event.key === "]") handled = clickEnabledControl("replay-step-forward");
  else if (event.code === "Space" || event.key === " ") handled = clickEnabledControl("replay-pause");
  if (handled) event.preventDefault();
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
  const authUsername = $("auth-username");
  if (authUsername) {
    authUsername.addEventListener("input", (event) => {
      state.authUsername = normalizeAuthUsername(event.target.value);
      event.target.value = state.authUsername;
      renderAuthPanel();
    });
  }
  const authPassword = $("auth-password");
  if (authPassword) {
    authPassword.addEventListener("input", (event) => {
      state.authPassword = event.target.value;
    });
    authPassword.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        submitAuth("login");
      }
    });
  }
  const authLogin = $("auth-login");
  if (authLogin) authLogin.addEventListener("click", () => submitAuth("login"));
  const authRegister = $("auth-register");
  if (authRegister) authRegister.addEventListener("click", () => submitAuth("register"));
  const authLogout = $("auth-logout");
  if (authLogout) authLogout.addEventListener("click", logoutAuth);
  const strategyName = $("strategy-name");
  if (strategyName) {
    strategyName.addEventListener("input", (event) => {
      state.strategyName = String(event.target.value || "").slice(0, 40);
    });
  }
  const strategySeed = $("strategy-seed");
  if (strategySeed) {
    strategySeed.addEventListener("input", (event) => {
      state.strategySeed = String(event.target.value || "").replace(/[^\d-]/g, "").slice(0, 12) || "1";
      event.target.value = state.strategySeed;
    });
  }
  const strategyPlayerCount = $("strategy-player-count");
  if (strategyPlayerCount) {
    strategyPlayerCount.addEventListener("input", (event) => {
      const raw = String(event.target.value || "").replace(/[^\d]/g, "").slice(0, 1) || "2";
      const value = Math.max(1, Math.min(6, Number.parseInt(raw, 10) || 2));
      state.strategyPlayerCount = String(value);
      event.target.value = state.strategyPlayerCount;
    });
  }
  const strategyJoinCode = $("strategy-join-code");
  if (strategyJoinCode) {
    strategyJoinCode.addEventListener("input", (event) => {
      state.strategyJoinCode = String(event.target.value || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
      event.target.value = state.strategyJoinCode;
      renderStrategyPanel();
    });
    strategyJoinCode.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        joinStrategyCampaignByCode();
      }
    });
  }
  const strategyCreate = $("strategy-create");
  if (strategyCreate) strategyCreate.addEventListener("click", createStrategyCampaign);
  const strategyJoin = $("strategy-join");
  if (strategyJoin) strategyJoin.addEventListener("click", joinStrategyCampaignByCode);
  const strategyRefresh = $("strategy-refresh");
  if (strategyRefresh) strategyRefresh.addEventListener("click", () => refreshStrategyCampaigns());
  const strategyAdvance = $("strategy-advance-month");
  if (strategyAdvance) strategyAdvance.addEventListener("click", advanceStrategyMonth);
  const focusStrategyMode = $("focus-strategy-mode");
  if (focusStrategyMode) focusStrategyMode.addEventListener("click", openStrategyModeEntry);
  const focusDuelMode = $("focus-duel-mode");
  if (focusDuelMode) focusDuelMode.addEventListener("click", openDuelModeEntry);
  const quickStartEntry = $("quick-start-entry");
  if (quickStartEntry) quickStartEntry.addEventListener("click", openQuickStartEntry);
  const startTutorial = $("start-tutorial");
  if (startTutorial) startTutorial.addEventListener("click", startTutorialBattle);
  const startQuickAi = $("start-quick-ai");
  if (startQuickAi) startQuickAi.addEventListener("click", () => startQuickAiBattle());
  const refreshRecent = $("refresh-recent-matches");
  if (refreshRecent) refreshRecent.addEventListener("click", () => refreshRecentMatches());
  $("toggle-battle-sound")?.addEventListener("click", () => globalThis.WujiangBattleFeedback?.toggle("sound"));
  $("toggle-colorblind-mode")?.addEventListener("click", () => globalThis.WujiangBattleFeedback?.toggle("colorblind"));
  $("toggle-reduced-motion")?.addEventListener("click", () => {
    globalThis.WujiangBattleFeedback?.toggle("motion");
    clearBattleVfx();
  });
  $("open-keyboard-help")?.addEventListener("click", openKeyboardHelp);
  $("close-keyboard-help")?.addEventListener("click", closeKeyboardHelp);
  $("keyboard-help")?.addEventListener("click", (event) => {
    if (event.target === $("keyboard-help")) closeKeyboardHelp();
  });
  document.addEventListener("keydown", handleBattleKeyboard);
  const resumeTutorial = $("resume-tutorial");
  if (resumeTutorial) resumeTutorial.addEventListener("click", resumeTutorialBattle);
  const tutorialBack = $("tutorial-back-note");
  if (tutorialBack) tutorialBack.addEventListener("click", () => {
    state.tutorialHistoryOffset = state.tutorialHistoryOffset > 0 ? 0 : 1;
    renderTutorialGuide();
  });
  const tutorialRetry = $("tutorial-retry");
  if (tutorialRetry) tutorialRetry.addEventListener("click", retryTutorialStep);
  const tutorialSkip = $("tutorial-skip-note");
  if (tutorialSkip) tutorialSkip.addEventListener("click", () => {
    state.tutorialGuideCollapsed = !state.tutorialGuideCollapsed;
    const tutorial = tutorialState();
    if (tutorial) recordProductEvent("tutorial_step", {tutorial_id: "first_battle", step_id: tutorial.step_id, status: "skipped"});
    renderTutorialGuide();
  });
  const tutorialExit = $("tutorial-exit");
  if (tutorialExit) tutorialExit.addEventListener("click", exitTutorial);
  const toggleFullRoster = $("toggle-full-roster");
  if (toggleFullRoster) toggleFullRoster.addEventListener("click", () => {
    state.showFullRoster = !state.showFullRoster;
    renderHeroCards();
  });
  const heroSearch = $("hero-search");
  if (heroSearch) heroSearch.addEventListener("input", (event) => {
    state.heroSearchQuery = String(event.target.value || "");
    renderHeroCards();
  });
  const heroRoleFilter = $("hero-role-filter");
  if (heroRoleFilter) heroRoleFilter.addEventListener("change", (event) => {
    state.heroRoleFilter = String(event.target.value || "");
    renderHeroCards();
  });
  const heroDifficultyFilter = $("hero-difficulty-filter");
  if (heroDifficultyFilter) heroDifficultyFilter.addEventListener("change", (event) => {
    state.heroDifficultyFilter = String(event.target.value || "");
    renderHeroCards();
  });
  const clearHeroFilters = $("clear-hero-filters");
  if (clearHeroFilters) clearHeroFilters.addEventListener("click", () => {
    state.heroSearchQuery = "";
    state.heroRoleFilter = "";
    state.heroDifficultyFilter = "";
    renderHeroCards();
    $("hero-search")?.focus();
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
  $("toggle-ready").addEventListener("click", toggleRoomReady);
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
    if (state.historicalMatchId) setScreen("draft");
    else leaveReplayMode();
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
  $("board-stage").addEventListener("wheel", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest("input, select, textarea, label")) return;
    if (typeof event.preventDefault === "function") event.preventDefault();
    const delta = Number(event.deltaY || 0);
    if (Math.abs(delta) < 0.5) return;
    const step = delta < 0 ? 0.12 : -0.12;
    setBoardZoom((state.boardZoom || 1) + step, {
      clientX: event.clientX,
      clientY: event.clientY,
    });
  }, { passive: false });
  $("board-stage").addEventListener("pointerdown", (event) => {
    const stage = $("board-stage");
    const board = $("board");
    const target = event.target instanceof Element ? event.target : null;
    if (event.button !== 0) return;
    if (!target || target.closest("input, select, textarea, label, .board-alert")) return;
    if (isBoardTargetSelectionActive()) return;
    const boardCell = target.closest(".cell");
    const clickedBoardCell = Boolean(board && boardCell && board.contains(boardCell));
    if (!clickedBoardCell && target.closest("button")) return;
    boardDragState = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: stage.scrollLeft,
      scrollTop: stage.scrollTop,
      dragging: false,
    };
    if (typeof stage.setPointerCapture === "function") {
      try {
        stage.setPointerCapture(event.pointerId);
      } catch (error) {
        // Ignore browsers that reject capture for synthetic or unsupported pointers.
      }
    }
  });
  $("board-stage").addEventListener("pointermove", (event) => {
    if (!boardDragState || boardDragState.pointerId !== event.pointerId) return;
    const dx = event.clientX - boardDragState.startX;
    const dy = event.clientY - boardDragState.startY;
    if (!boardDragState.dragging && Math.hypot(dx, dy) < 6) return;
    boardDragState.dragging = true;
    boardDragSuppressUntil = Date.now() + 160;
    $("board-stage").classList.add("is-dragging");
    $("board-stage").scrollLeft = boardDragState.scrollLeft - dx;
    $("board-stage").scrollTop = boardDragState.scrollTop - dy;
    scheduleBoardOverlayRender();
  });
  const endBoardDrag = (event) => {
    if (!boardDragState || (event && boardDragState.pointerId !== event.pointerId)) return;
    const stage = $("board-stage");
    if (boardDragState.dragging) {
      boardDragSuppressUntil = Date.now() + 160;
    }
    if (typeof stage.releasePointerCapture === "function") {
      try {
        stage.releasePointerCapture(boardDragState.pointerId);
      } catch (error) {
        // Ignore browsers that reject release for uncaptured pointers.
      }
    }
    stage.classList.remove("is-dragging");
    boardDragState = null;
  };
  $("board-stage").addEventListener("pointerup", endBoardDrag);
  $("board-stage").addEventListener("pointercancel", endBoardDrag);
  window.addEventListener("resize", () => {
    scheduleBoardOverlayRender();
  });
  $("room-battle").addEventListener("click", () => setScreen("battle"));
  $("nav-draft").addEventListener("click", () => setScreen("draft"));
  $("nav-battle").addEventListener("click", () => setScreen("battle"));
  $("game-over-strategy").addEventListener("click", returnToStrategyCampaign);
  $("game-over-back").addEventListener("click", () => setScreen("draft"));
  $("game-over-rematch").addEventListener("click", restartFromGameOver);
  $("surrender-battle").addEventListener("click", surrenderBattle);
  $("end-turn").addEventListener("click", () => {
    if (!canInteract()) return;
    performAction({ type: "end_turn" });
  });
  $("skip-chain").addEventListener("click", () => {
    if (!canInteract()) return;
    performAction({ type: "chain_skip" });
  });
  $("toggle-right-rail")?.addEventListener("click", () => {
    toggleSidebarPanel("logs");
    render();
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
    const reviveButton = target?.closest("[data-revive-unit-id]");
    if (reviveButton) {
      const action = selectedAction();
      if (!action || !reviveUnitCellSelection(action)) return;
      setStagedReviveUnitId(reviveButton.dataset.reviveUnitId);
      render();
    }
  });
  document.addEventListener("pointerover", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-tooltip]") : null;
    if (!target) return;
    showTooltip(target.getAttribute("data-tooltip"), { x: event.clientX, y: event.clientY });
  });
  document.addEventListener("pointermove", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-tooltip]") : null;
    if (!target) return;
    showTooltip(target.getAttribute("data-tooltip"), { x: event.clientX, y: event.clientY });
  });
  document.addEventListener("pointerout", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-tooltip]") : null;
    if (!target) return;
    if (tooltipHideHandle) window.clearTimeout(tooltipHideHandle);
    tooltipHideHandle = window.setTimeout(() => {
      hideTooltip();
    }, 40);
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
    if (action.kind === "attack" && patternSelection(action)) {
      const payload = {
        type: "attack",
        unit_id: state.selectedUnitId,
        cells: stagedPatternCells(action),
        ...(action.attack_payload || {}),
      };
      if (choicePatternSelection(action)) payload.choice_code = stagedPatternChoiceCode(action);
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
    } else if (reviveUnitCellSelection(action)) {
      const cell = stagedReviveCell(action);
      payload.revive_unit_id = stagedReviveUnitId(action);
      payload.x = cell.x;
      payload.y = cell.y;
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
  $("board").addEventListener("click", (event) => {
    if (Date.now() < boardDragSuppressUntil) return;
    const target = event.target instanceof Element ? event.target : null;
    const cell = target?.closest(".cell");
    if (!cell || !$("board").contains(cell)) return;
    const x = Number(cell.dataset.x);
    const y = Number(cell.dataset.y);
    onBoardClick(x, y, activeOccupantAt(x, y));
  });
  $("board").addEventListener("keydown", (event) => {
    const cell = event.target?.closest?.(".cell");
    if (!cell) return;
    const direction = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    }[event.key];
    if (!direction) return;
    const nextX = Number(cell.dataset.x) + direction[0];
    const nextY = Number(cell.dataset.y) + direction[1];
    const next = $("board").querySelector(`.cell[data-x="${nextX}"][data-y="${nextY}"]`);
    if (!next) return;
    event.preventDefault();
    next.focus();
  });
  window.addEventListener("hashchange", () => {
    syncScreen({ preferBattle: Boolean(state.battle) });
    render();
  });
}

// Override legacy definitions above so the random-room lobby flow uses a single clean state path.
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
  setBoardZoom((state.boardZoom || 1) + Number(delta || 0));
}

function setBoardZoom(nextZoom, anchor = null) {
  const stage = $("board-stage");
  const board = $("board");
  const stageRect = stage?.getBoundingClientRect?.() || null;
  const boardRect = board?.getBoundingClientRect?.() || null;
  const previousZoom = clampBoardZoom(state.boardZoom);
  const targetZoom = clampBoardZoom(nextZoom);
  if (Math.abs(targetZoom - previousZoom) < 0.001) return;
  let anchorRatioX = null;
  let anchorRatioY = null;
  let anchorClientX = 0;
  let anchorClientY = 0;
  if (anchor && stageRect && boardRect && boardRect.width > 0 && boardRect.height > 0) {
    anchorClientX = Number(anchor.clientX || 0);
    anchorClientY = Number(anchor.clientY || 0);
    anchorRatioX = Math.max(0, Math.min(1, (anchorClientX - boardRect.left) / boardRect.width));
    anchorRatioY = Math.max(0, Math.min(1, (anchorClientY - boardRect.top) / boardRect.height));
  }
  state.boardZoom = targetZoom;
  renderBoardZoomControls();
  renderBoard();
  if (
    anchorRatioX != null
    && anchorRatioY != null
    && stage
    && board
    && stageRect
    && typeof stage.scrollLeft === "number"
    && typeof stage.scrollTop === "number"
  ) {
    const nextBoardRect = board.getBoundingClientRect?.();
    if (nextBoardRect?.width > 0 && nextBoardRect?.height > 0) {
      const desiredLeft = nextBoardRect.left + (nextBoardRect.width * anchorRatioX);
      const desiredTop = nextBoardRect.top + (nextBoardRect.height * anchorRatioY);
      stage.scrollLeft += desiredLeft - anchorClientX;
      stage.scrollTop += desiredTop - anchorClientY;
    }
  }
  scheduleBoardOverlayRender();
}

function resetBoardZoom() {
  setBoardZoom(1);
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

function heroDiscoveryByCode() {
  return new Map((state.onboarding?.hero_discovery || []).map((item) => [item.code, item]));
}

function normalizedHeroSearch(value) {
  return String(value || "").trim().toLocaleLowerCase("zh-CN");
}

function heroMatchesFilters(hero, discovery, beginner) {
  if (state.heroRoleFilter && String(discovery?.role || hero.role || "") !== state.heroRoleFilter) return false;
  if (state.heroDifficultyFilter && String(discovery?.difficulty || "") !== state.heroDifficultyFilter) return false;
  const query = normalizedHeroSearch(state.heroSearchQuery);
  if (!query) return true;
  const haystack = [
    hero.name,
    hero.code,
    hero.role,
    hero.attribute,
    hero.race,
    hero.raw_skill_text,
    hero.raw_trait_text,
    discovery?.position,
    discovery?.difficulty,
    beginner?.mechanics,
    beginner?.summary,
  ].map((value) => normalizedHeroSearch(value)).join(" ");
  return haystack.includes(query);
}

function rosterExactlyMatches(seat, heroCodes) {
  if (!seat || !Array.isArray(heroCodes)) return false;
  const expected = new Map();
  heroCodes.forEach((code) => expected.set(code, (expected.get(code) || 0) + 1));
  const selected = Object.entries(seat.hero_counts || {}).filter(([, count]) => Number(count) > 0);
  if (selected.length !== expected.size) return false;
  return selected.every(([code, count]) => Number(count) === Number(expected.get(code) || 0));
}

function renderTeamReadiness(discoveryByCode) {
  const panel = $("team-readiness");
  if (!panel) return;
  const visible = Boolean(hasRoom() && state.room?.status === "lobby");
  panel.classList.toggle("hidden", !visible);
  if (!visible) return;
  panel.replaceChildren();
  panel.classList.toggle("is-ready", Boolean(state.room.can_start));
  panel.classList.toggle("is-blocked", !state.room.can_start);

  const title = document.createElement("strong");
  title.textContent = state.room.can_start
    ? "全部真人已确认，房主可以开局"
    : (state.room.configuration_ready ? "阵容合法，等待真人确认准备" : "阵容尚未满足开局规则");
  panel.append(title);

  const teams = new Map();
  (state.room.seats || []).forEach((seat) => {
    const current = teams.get(Number(seat.team_id)) || {name: seat.team_name, heroes: 0, occupied: 0};
    current.heroes += seatHeroTotalCount(seat);
    if (seat.occupied) current.occupied += 1;
    teams.set(Number(seat.team_id), current);
  });
  const teamLine = document.createElement("span");
  teamLine.textContent = [...teams.values()]
    .map((team) => `${team.name}：${team.heroes} 名武将 / ${team.occupied} 个已占席位`)
    .join("；");
  panel.append(teamLine);

  const ruleLine = document.createElement("span");
  ruleLine.textContent = state.room.can_start
    ? "硬性规则与开局确认均已通过；阵容结构建议不会阻止开局。"
    : (state.room.start_blocker || "请继续补齐席位和双方阵容。");
  panel.append(ruleLine);

  const editingSeat = editableRoomSeat();
  const selectedCodes = Object.entries(editingSeat?.hero_counts || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([code]) => code);
  if (selectedCodes.length >= 2) {
    const roles = new Set(selectedCodes.map((code) => discoveryByCode.get(code)?.role || state.heroes.find((hero) => hero.code === code)?.role));
    const hasFront = ["勇者", "骑士", "剑士", "狂战"].some((role) => roles.has(role));
    const hasSupport = ["贤者", "术士"].some((role) => roles.has(role));
    const advice = document.createElement("span");
    advice.textContent = hasFront && hasSupport
      ? `结构建议：${seatIdentityLabel(editingSeat)} 已同时包含前排与辅助定位。`
      : `结构建议：${seatIdentityLabel(editingSeat)} ${hasFront ? "已有前排，可考虑加入贤者或术士" : "可考虑加入勇者、骑士、剑士或狂战前排"}。`;
    panel.append(advice);
  }
}

function renderConnectionAndTurnState() {
  const lobbyPanel = $("room-connection-summary");
  const battlePanel = $("battle-connection-summary");
  const timerPanel = $("battle-turn-timer");
  const panels = [lobbyPanel, battlePanel].filter(Boolean);
  panels.forEach((panel) => { panel.innerHTML = ""; });
  if (!state.room) {
    if (lobbyPanel) lobbyPanel.classList.add("hidden");
    if (battlePanel) battlePanel.classList.add("hidden");
    if (timerPanel) timerPanel.classList.add("hidden");
    return;
  }
  const humanSeats = (state.room.seats || []).filter((seat) => seat.is_human);
  panels.forEach((panel) => {
    panel.classList.toggle("hidden", humanSeats.length === 0);
    humanSeats.forEach((seat) => {
      const item = document.createElement("span");
      const status = String(seat.connection_status || "offline");
      item.className = `connection-seat is-${status}`;
      item.textContent = `${seat.name || `席位 ${seat.player_id}`}：${connectionStatusLabel(status)} · ${readyStateLabel(seat)}`;
      panel.append(item);
    });
    if (state.connectionLostAt) {
      const warning = document.createElement("strong");
      warning.textContent = "当前浏览器连接中断，正在保留原席位并自动重连。";
      panel.append(warning);
    }
  });
  if (!timerPanel) return;
  const timer = state.room.turn_timer || {};
  const remaining = Number(timer.remaining_seconds);
  const visible = Boolean(state.battle && timer.enabled && Number.isFinite(remaining));
  timerPanel.classList.toggle("hidden", !visible);
  timerPanel.classList.toggle("is-urgent", visible && remaining <= 30 && remaining > 10);
  timerPanel.classList.toggle("is-critical", visible && remaining <= 10);
  if (!visible) {
    timerPanel.textContent = state.room?.experience_kind === "tutorial" ? "教学模式不启用强制倒计时。" : "";
    return;
  }
  const promptLabel = ({turn: "回合操作", chain: "连锁响应", respawn: "复活落点"})[timer.prompt_kind] || "当前操作";
  const ownerLabel = Number(timer.prompt_seat_id) === Number(viewerPlayerId()) ? "你的" : `席位 ${timer.prompt_seat_id} 的`;
  timerPanel.textContent = `${ownerLabel}${promptLabel}：${Math.max(0, remaining)} 秒`;
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

  const beginnerByCode = new Map((state.onboarding?.beginner_heroes || []).map((item) => [item.code, item]));
  const discoveryByCode = heroDiscoveryByCode();
  const selectedCodes = new Set(Object.keys(editingSeat?.hero_counts || {}));
  const searchInput = $("hero-search");
  const roleFilter = $("hero-role-filter");
  const difficultyFilter = $("hero-difficulty-filter");
  if (searchInput && searchInput.value !== state.heroSearchQuery) searchInput.value = state.heroSearchQuery;
  if (roleFilter) {
    const roles = [...new Set(state.heroes.map((hero) => String(hero.role || "未分类")))].sort((a, b) => a.localeCompare(b, "zh-CN"));
    roleFilter.innerHTML = "";
    const allRoles = document.createElement("option");
    allRoles.value = "";
    allRoles.textContent = "全部定位";
    roleFilter.append(allRoles);
    roles.forEach((role) => {
      const option = document.createElement("option");
      option.value = role;
      option.textContent = role;
      roleFilter.append(option);
    });
    roleFilter.value = state.heroRoleFilter;
  }
  if (difficultyFilter) difficultyFilter.value = state.heroDifficultyFilter;
  const filtersActive = Boolean(
    normalizedHeroSearch(state.heroSearchQuery)
    || state.heroRoleFilter
    || state.heroDifficultyFilter
  );
  const discoveryPool = (state.showFullRoster || randomMode || filtersActive)
    ? state.heroes
    : state.heroes.filter((hero) => beginnerByCode.has(hero.code) || selectedCodes.has(hero.code));
  const visibleHeroes = discoveryPool.filter((hero) => heroMatchesFilters(hero, discoveryByCode.get(hero.code), beginnerByCode.get(hero.code)));
  const toggle = $("toggle-full-roster");
  if (toggle) toggle.textContent = state.showFullRoster ? "只看新手武将" : `查看完整武将库（${state.heroes.length}）`;
  const filterResult = $("hero-filter-result");
  if (filterResult) {
    filterResult.textContent = filtersActive
      ? `在完整武将库中找到 ${visibleHeroes.length} / ${state.heroes.length} 名武将。`
      : `当前显示 ${visibleHeroes.length} 名${state.showFullRoster ? "公开" : "新手优先"}武将。`;
  }
  const filterEmpty = $("hero-filter-empty");
  if (filterEmpty) filterEmpty.classList.toggle("hidden", visibleHeroes.length > 0);
  renderTeamReadiness(discoveryByCode);

  visibleHeroes.forEach((hero) => {
    const beginner = beginnerByCode.get(hero.code);
    const discovery = discoveryByCode.get(hero.code) || {position: hero.role, difficulty: "未评级", difficulty_source: "estimated"};
    const difficultySource = discovery.difficulty_source === "estimated"
      ? `<span class="hero-difficulty-estimated">估算难度 · 可在后续人工评级中调整</span>`
      : `<span class="hero-difficulty-estimated">人工评级</span>`;
    const discoverySummary = beginner
      ? `<div class="beginner-summary"><strong>${discovery.position} · ${discovery.difficulty}</strong>${difficultySource}<span>${beginner.summary}</span><span>主要机制：${beginner.mechanics}</span></div>`
      : `<div class="beginner-summary"><strong>${discovery.position} · ${discovery.difficulty}</strong>${difficultySource}</div>`;
    const homeCard = document.createElement("article");
    homeCard.className = "hero-card";
    homeCard.innerHTML = `
      <h3>${hero.name}</h3>
      <div class="meta">${hero.role} / ${hero.attribute} / ${hero.race} / 等级 ${hero.level}</div>
      <div class="meta">攻 ${hero.stats.attack} · 守 ${hero.stats.defense} · 速 ${hero.stats.speed} · 范 ${hero.stats.attack_range} · 魔 ${hero.stats.mana}</div>
      ${discoverySummary}
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
      ${beginner
        ? `<div class="beginner-summary"><strong>${discovery.position} · ${discovery.difficulty}</strong>${difficultySource}<span>${beginner.summary}</span><span>推荐队友：${beginner.teammates.map((code) => state.heroes.find((item) => item.code === code)?.name || code).join("、")}</span></div>`
        : discoverySummary}
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

  const recommended = $("recommended-rosters");
  if (recommended) {
    recommended.innerHTML = "";
    (state.onboarding?.recommended_rosters || []).forEach((roster) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "recommended-roster ghost";
      const names = roster.hero_codes.map((code) => state.heroes.find((hero) => hero.code === code)?.name || code);
      const applied = rosterExactlyMatches(editingSeat, roster.hero_codes);
      button.classList.toggle("is-applied", applied);
      button.setAttribute("aria-pressed", applied ? "true" : "false");
      button.innerHTML = `<strong>${roster.name}${applied ? " · 已应用" : ""}</strong><span>${names.join(" + ")}</span><small>${roster.summary}</small>`;
      button.disabled = !canSelect;
      button.addEventListener("click", () => applyRecommendedRoster(roster.code, editingSeat?.player_id));
      recommended.append(button);
    });
  }
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
  const toggleReady = $("toggle-ready");
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
    if (toggleReady) toggleReady.classList.add("hidden");
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
    if (toggleReady) toggleReady.classList.add("hidden");
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
  if (toggleReady) {
    const canShowReady = Boolean(viewerSeat?.is_human && state.room.status === "lobby");
    toggleReady.textContent = viewerSeat?.ready ? "取消准备" : "确认准备";
    toggleReady.className = viewerSeat?.ready ? "primary" : "ghost";
    toggleReady.classList.toggle("hidden", !canShowReady);
    toggleReady.disabled = !viewerSeat?.ready && !state.room.configuration_ready;
  }
  const canShowStart = state.room.status === "finished"
    ? state.room.viewer_player_id !== null
    : Boolean(state.room.viewer_is_host && state.room.status === "lobby");
  startRoom.classList.toggle("hidden", !canShowStart);
  startRoom.disabled = state.room.status === "lobby" ? !state.room.can_start : !state.room.can_rematch;
  startRoom.textContent = state.room.status === "finished"
    ? (state.room.viewer_is_host ? "同配置再来一局" : "等待房主再开一局")
    : (isRandomRoomMode() ? "开始随机对局" : "开始对局");

  const roomMessage = $("room-message");
  if (hasBattle()) {
    if (state.room.viewer_player_id === null && !isGameOver()) {
      roomMessage.textContent = canReclaimSeatByName()
        ? `房间 ${state.room.room_id} 的对局正在进行中。当前昵称与旧席位匹配，点击“恢复席位”后可继续操作。`
        : `房间 ${state.room.room_id} 的对局正在进行中。你当前是观战身份；如果你是原玩家，请先把昵称改回原来的名字再恢复席位。`;
    } else {
      roomMessage.textContent = isGameOver()
        ? `房间 ${state.room.room_id} 的本局对战已经结束。你可以查看终局；房主可保留当前配置发起下一局，双方重新确认准备。`
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
      ${seat.occupied ? `<div class="seat-note ready-state ${seat.ready ? "is-ready" : ""}">${readyStateLabel(seat)} · ${connectionStatusLabel(seat.connection_status)}</div>` : ""}
      ${controls.length ? `<div class="seat-controls">${controls.join("")}</div>` : ""}
    `;
    const teamSelect = card.querySelector(`[data-seat-team="${seat.player_id}"]`);
    if (teamSelect) {
      teamSelect.addEventListener("change", (event) => {
        if (typeof event.target.blur === "function") event.target.blur();
        setRoomSeatTeam(seat.player_id, event.target.value);
      });
    }
    const controllerSelect = card.querySelector(`[data-seat-controller="${seat.player_id}"]`);
    if (controllerSelect) {
      controllerSelect.addEventListener("change", (event) => {
        if (typeof event.target.blur === "function") event.target.blur();
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

async function applyRecommendedRoster(rosterCode, seatId = null) {
  if (!hasRoom() || !state.playerToken) return;
  try {
    const payload = await fetchJson("/api/rooms/apply-recommended-roster", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        roster_code: rosterCode,
        seat_id: seatId != null ? Number(seatId) : undefined,
      }),
    });
    applyRoomPayload(payload, {preserveScreen: true});
    render();
  } catch (error) {
    $("room-message").textContent = error.error || "应用推荐阵容失败。";
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
  if (tutorialState()?.step_id === "select_unit") {
    state.selectedUnitId = "";
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
  if (state.selectedActionCode === "descent_moment" && !state.stagedPayload?.targetUnitId) {
    node.textContent = "降临时刻分两步：先选择带有抹杀计数点的对方单位，再选择周围落点。";
    return;
  }
  if (state.stagedPayload?.targetUnitId && state.selectedActionCode === "descent_moment") {
    node.textContent = `已选中 ${stagedTarget()?.name || "降临目标"}，请点击蓝色高亮落点。`;
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
  globalThis.WujiangReplayUi?.renderToolbar({
    document,
    state,
    replay: replayMeta(),
    simulation: simulationMeta(),
    replayMode: isReplayMode(),
  });
}

function ensureDynamicUiScaffolding() {
  const heroHeadCopy = document.querySelector(".room-hero-head p");
  if (heroHeadCopy) {
    heroHeadCopy.textContent = "\u6bcf\u4f4d\u73a9\u5bb6\u53ef\u4ee5\u4e3a\u81ea\u5df1\u9009\u62e9\u591a\u4e2a\u6b66\u5c06\u3002\u4f7f\u7528\u4e0b\u65b9\u5361\u7247\u7684 +1 / -1 \u6765\u914d\u7f6e\u81ea\u5df1\u7684\u9635\u5bb9\u3002";
  }
  const boardHead = document.querySelector(".board-wrap .section-head");
  const legend = boardHead?.querySelector(".legend");
  if (!boardHead) return;
  if (!$("board-zoom-controls")) {
    const controls = document.createElement("div");
    controls.id = "board-zoom-controls";
    controls.className = "zoom-controls hidden";
    controls.innerHTML = `
      <button id="board-zoom-out" type="button" class="ghost" data-tooltip="缩小战场">-</button>
      <button id="board-zoom-reset" type="button" class="ghost" data-tooltip="重置战场缩放">1:1</button>
      <button id="board-zoom-in" type="button" class="ghost" data-tooltip="放大战场">+</button>
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
  }
  const footer = document.querySelector(".board-footer");
  const endTurnButton = $("end-turn");
  if (footer && endTurnButton && !$("replay-toolbar")) {
    const toolbar = document.createElement("div");
    toolbar.id = "replay-toolbar";
    toolbar.className = "replay-toolbar hidden";
    toolbar.innerHTML = `
      <button id="replay-step-back" class="ghost" type="button" data-tooltip="回到上一步">&lt;&lt;</button>
      <button id="replay-pause" class="ghost" type="button" data-tooltip="暂停或继续 AI 对局">II</button>
      <button id="replay-live" class="ghost" type="button" data-tooltip="回到实时战局">LIVE</button>
      <button id="replay-step-forward" class="ghost" type="button" data-tooltip="前进一步">&gt;&gt;</button>
      <label class="replay-speed-control" for="replay-speed">
        <span>\u901f\u5ea6</span>
        <select id="replay-speed">
          <option value="0.5">0.5x</option>
          <option value="1" selected>1x</option>
          <option value="2">2x</option>
          <option value="4">4x</option>
        </select>
      </label>
      <label class="replay-omniscient-toggle">
        <input id="replay-omniscient" type="checkbox" />
        <span>\u5168\u77e5</span>
      </label>
      <input id="replay-timeline" class="replay-timeline" type="range" min="0" max="0" step="1" value="0" />
      <span id="replay-status" class="replay-status">\u5b9e\u65f6</span>
    `;
    footer.insertBefore(toolbar, endTurnButton);
  }
  if (!$("control-tooltip")) {
    const tooltip = document.createElement("div");
    tooltip.id = "control-tooltip";
    tooltip.className = "control-tooltip hidden";
    document.body.append(tooltip);
  }
  if (!$("floating-toast-stack")) {
    const stack = document.createElement("div");
    stack.id = "floating-toast-stack";
    stack.className = "floating-toast-stack hidden";
    document.body.append(stack);
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  hydrateStaticLabels();
  initializeAuthState();
  initializeProfileState();
  await refreshAuthSession();
  recordProductEvent("home_view", {
    entry_state: state.authUser ? "logged_in" : "anonymous",
  });
  syncIdentityFromUrl();
  ensureDynamicUiScaffolding();
  globalThis.WujiangBattleFeedback?.initialize();
  bindEvents();
  await refreshRecentMatches({renderAfter: false});
  await refreshState({ preserveScreen: false });
  pollHandle = window.setInterval(() => {
    if (!roomQueryId()) {
      const now = Date.now();
      if (now < nextHomePollAt) return;
      nextHomePollAt = now + 5000;
      refreshState({ preserveScreen: false });
      return;
    }
    refreshState();
  }, 400);
});
