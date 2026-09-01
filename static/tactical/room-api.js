// Room lifecycle calls: create, join, seats, ready and actions.
import { confirmDialog } from '../core/dialog.js';
import { $ } from '../core/dom.js';
import { fallbackRoomModes } from '../core/events.js';
import { canInteract, fetchJson, hasBattle, hasRoom, isAiTakeover, isChainMode, isGameOver, isReplayMode, recordProductEvent, replayMeta, roomQueryId, syncLocation, unitsAtCell, viewerPlayerId, viewerTeamId } from '../core/net.js';
import { refreshState, render } from '../core/render.js';
import { LAST_COMPLETED_MATCH_KEY, LAST_TUTORIAL_ROOM_KEY, RECORDED_MATCH_ENDS_KEY, state } from '../core/state.js';
import { setScreen, syncScreen } from '../core/ui.js';
import { effectiveProfileName, openProfileModal, renderProfilePanel, requireAuthForRoomEntry } from '../platform/auth.js';
import { clearResumableTutorial, refreshRecentMatches } from '../platform/home.js';
import { roomStateLabel, syncAiPreview } from '../tactical/battle-ui.js';
import { clearActionSelection, clearStoredIdentity, loadStoredIdentity, resetRoomSession, saveStoredIdentity, syncSelectedUnitAfterStateChange } from '../tactical/session.js';
import { actionNeedsTarget, controllerTypeLabel, currentPreview, currentRoomSeat, randomRoomRosterSize, roomSummaries, unitIsSelectableTarget } from '../tactical/targeting.js';
import { maxVisualEventId, positionKey, syncBattleVfxState, tutorialState, visualEvents } from '../tactical/vfx.js';
import { adoptBattleLaunchFromRoom, currentBattleLaunch, rememberBattleLaunch } from '../bridge/battle-launch.js';
import { loadRecordedMatchEnds, syncStrategyCampaignFromRoomPayload } from '../bridge/campaign-battle.js';

function normalizePlayerNameForSeatMatch(name) {
  const cleaned = String(name || "").trim().split(/\s+/).filter(Boolean).join(" ");
  return (cleaned || "\u672a\u547d\u540d\u73a9\u5bb6").slice(0, 20);
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

export function availableRoomModes() {
  return state.room?.available_modes?.length ? state.room.available_modes : fallbackRoomModes();
}

/**
 * 大厅上唯一的一行文字位，平时空着。
 *
 * 建房页原本挂着一整段随房间状态改写的解说；那些话玩家看一遍就够，却要一直占
 * 着屏幕。现在这行只在操作真的失败时才写字——它是报错的地方，不是介绍的地方。
 */
export function reportRoomError(message) {
  state.roomError = String(message || "");
  render();
}

export function storedIdentityForCurrentRoom() {
  return loadStoredIdentity(roomQueryId());
}

export function canReclaimSeatByName() {
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

function roomStateClass(room) {
  if (!room) return "";
  if (room.status === "battle") return "is-battle";
  if (room.is_full) return "is-full";
  return "";
}

export function shouldShowLobbyPanel() {
  if (!hasRoom() || !currentBattleLaunch().allowLobby) return false;
  return viewerPlayerId() !== null || state.room?.is_full || hasBattle();
}

export function applyRoomPayload(payload, { preserveScreen = false } = {}) {
  const hadBattle = Boolean(state.liveBattle || state.battle);
  const previousScreen = state.screen;
  const previousBoardKey = state.battle ? `${state.battle.board.width}x${state.battle.board.height}` : "";
  const previousRoomId = state.room?.room_id || "";
  const previousBattle = state.liveBattle;
  // 拿到了新的房间状态，说明上一条失败已经翻篇了。
  state.roomError = "";
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
  }
  state.lastTurnTimeoutAt = Math.max(
    state.lastTurnTimeoutAt,
    Number(state.room?.turn_timer?.last_timeout?.occurred_at || 0),
  );
  syncStrategyCampaignFromRoomPayload(payload);
  if (payload.strategy_campaign || payload.battle_recovery || state.room?.experience_kind === "strategy_campaign") {
    const launch = rememberBattleLaunch({
      ...(state.room?.launch_context || {}),
      source: "campaign",
      campaign_id: payload.strategy_campaign?.id,
    });
    if (state.room) state.room.launch_context = {
      source: launch.source,
      return_flow: launch.returnFlow,
      allow_lobby: launch.allowLobby,
      allow_rematch: launch.allowRematch,
      allow_roster_edit: launch.allowRosterEdit,
      campaign_id: launch.campaignId,
      battle_id: launch.battleId,
    };
    state.homeFlow = "campaign";
  } else {
    adoptBattleLaunchFromRoom(state.room);
  }
  state.liveBattle = payload.battle || null;
  if (!state.room || state.room.room_id !== previousRoomId) {
    state.aiPreview = null;
    state.gameOverDismissed = false;
  }
  if (!state.room || state.room.room_id !== previousRoomId || !state.room.replay?.available) {
    state.replayMode = false;
    state.replayStepIndex = 0;
    state.replayOmniscient = false;
  }
  if (!state.replayMode || !state.liveBattle) {
    state.battle = state.liveBattle;
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
    state.boardPanX = 0;
    state.boardPanY = 0;
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
    matchKey: `${state.room?.room_id || ""}:${state.room?.match_id || ""}`,
  });
  syncAiPreview(previousBattle, state.liveBattle);
  trackQuickAiMatchEnd();
}

export function roomModeMeta(modeCode = state.room?.mode) {
  return availableRoomModes().find((mode) => mode.code === modeCode) || fallbackRoomModes()[0];
}

export function isRandomRoomMode() {
  return state.room?.mode === "random";
}

export async function createRoom() {
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

export async function joinRoom(roomIdOverride = "") {
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

export function resumeStoredSeat(roomId = roomQueryId()) {
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
    reportRoomError(error.error || "同配置再战准备失败。");
  }
}

export async function deleteRoom() {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  const confirmed = await confirmDialog({
    title: "删除房间",
    body: `房间 ${state.room.room_id} 将被关闭，双方都需要重新建房。`,
    confirmLabel: "删除房间",
    tone: "danger",
  });
  if (!confirmed) return;
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

export async function leaveRoom() {
  if (!hasRoom()) return;
  const leftRoomId = state.room.room_id;
  const inBattle = Boolean(hasBattle() && !isGameOver() && state.room.viewer_player_id);
  const confirmed = await confirmDialog({
    title: "离开房间",
    body: inBattle
      ? "对局进行中离开将判负，并返回房间列表。"
      : "将退出当前房间并返回房间列表。房间仍然保留，可以再回来。",
    confirmLabel: "离开房间",
    tone: inBattle ? "danger" : "default",
  });
  if (!confirmed) return;
  let caption = `你已离开房间 ${leftRoomId}。`;
  try {
    if (state.playerToken && state.room.viewer_player_id !== null) {
      const payload = await fetchJson("/api/rooms/leave", {
        method: "POST",
        body: JSON.stringify({
          room_id: leftRoomId,
          player_token: state.playerToken,
        }),
      });
      caption = payload.room_deleted
        ? `你已离开房间 ${leftRoomId},该房间因已无玩家而被关闭。`
        : `你已离开房间 ${leftRoomId}。`;
      resetRoomSession({ rooms: payload.rooms || [], roomId: leftRoomId });
    } else {
      resetRoomSession({ roomId: leftRoomId });
    }
  } catch (error) {
    resetRoomSession({ roomId: leftRoomId });
    caption = error.error || caption;
  }
  await refreshState({ preserveScreen: false });
  $("lobby-caption").textContent = caption;
}

export async function setAiStyles({ heroAiStyle, armyAiStyle } = {}) {
  if (!hasRoom() || !hasBattle() || !state.playerToken || isGameOver() || isReplayMode()) return;
  try {
    const payload = await fetchJson("/api/rooms/ai-style", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        hero_ai_style: heroAiStyle,
        army_ai_style: armyAiStyle,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
  }
}

export async function setAiTakeover(enabled) {
  if (!hasRoom() || !hasBattle() || !state.playerToken || isGameOver() || isReplayMode()) return;
  try {
    const payload = await fetchJson("/api/rooms/ai-takeover", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        enabled: Boolean(enabled),
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    if (enabled) clearActionSelection();
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
  }
}

export async function toggleAiTakeover() {
  if (!hasRoom() || !hasBattle() || !state.playerToken || isGameOver() || isReplayMode()) return;
  if (state.room?.experience_kind === "tutorial") return;
  if (isAiTakeover()) {
    await setAiTakeover(false);
    return;
  }
  const confirmed = await confirmDialog({
    title: "让 AI 接管",
    body: "确认后，AI 会代替你完成本局剩余操作，包括当前回合的武将行动和军队指令。你可以随时点「停止接管」收回控制。",
    confirmLabel: "开始接管",
  });
  if (!confirmed) return;
  await setAiTakeover(true);
}

export async function surrenderBattle() {
  if (!hasRoom() || !hasBattle() || !state.playerToken || isGameOver()) return;
  const confirmed = await confirmDialog({
    title: "投降",
    body: "这局对战会立刻结束，判你落败。此操作不可撤销。",
    confirmLabel: "投降",
    tone: "danger",
  });
  if (!confirmed) return;
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
  }
}

export async function setRoomMode(modeCode) {
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
    reportRoomError(error.error || "切换房间模式失败。");
  }
}

export async function setRandomRosterSize(rosterSize) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  const normalized = Math.max(1, Number.parseInt(rosterSize, 10) || 1);
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
    reportRoomError(error.error || "设置随机模式人数失败。");
  }
}

export async function setRoomSeatCount(seatCount) {
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
    reportRoomError(error.error || "调整席位数失败。");
  }
}

export async function setRoomTurnTimeout(seconds) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  const allowed = new Set([0, 30, 60, 120]);
  const normalized = allowed.has(Number.parseInt(seconds, 10)) ? Number.parseInt(seconds, 10) : 0;
  if (normalized === Number(state.room?.turn_timeout_seconds ?? 0)) return;
  try {
    const payload = await fetchJson("/api/rooms/set-turn-timeout", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        turn_timeout_seconds: normalized,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    reportRoomError(error.error || "设置回合时限失败。");
  }
}

export async function setRoomBoardSize(width, height) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  const clamp = (value) => Math.max(6, Math.min(100, Number.parseInt(value, 10) || 10));
  const nextWidth = clamp(width);
  const nextHeight = clamp(height);
  if (nextWidth === Number(state.room?.board_width || 10) && nextHeight === Number(state.room?.board_height || 10)) {
    return;
  }
  try {
    const payload = await fetchJson("/api/rooms/set-board-size", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        board_width: nextWidth,
        board_height: nextHeight,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    reportRoomError(error.error || "设置战场大小失败。");
  }
}

export async function autoConfigureRoom(options = {}) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host || state.room?.status !== "lobby") return;
  const method = options.method === "points" ? "points" : "count";
  try {
    const payload = await fetchJson("/api/rooms/auto-configure", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        method,
        count: Number.parseInt(options.count, 10) || 3,
        points: Number.parseInt(options.points, 10) || 15,
        allow_duplicates: Boolean(options.allowDuplicates),
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    reportRoomError(error.error || "自动配置失败。");
  }
}

export async function setRoomHeroLimit(heroLimit) {
  if (!hasRoom() || !state.playerToken || !state.room?.viewer_is_host) return;
  const normalized = Math.max(0, Math.min(20, Number.parseInt(heroLimit, 10) || 0));
  if (normalized === Number(state.room?.hero_limit || 0)) return;
  try {
    const payload = await fetchJson("/api/rooms/set-hero-limit", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        hero_limit: normalized,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    reportRoomError(error.error || "设置武将数量上限失败。");
  }
}

export async function setRoomSeatTeam(seatId, teamId) {
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
    reportRoomError(error.error || "调整席位队伍失败。");
  }
}

export async function setRoomSeatController(seatId, controllerType) {
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
    reportRoomError(error.error || "调整席位状态失败。");
  }
}

export async function setSeatArmyComposition(seatId, armyCounts) {
  if (!hasRoom() || !state.playerToken) return;
  try {
    const payload = await fetchJson("/api/rooms/set-army-composition", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        seat_id: seatId != null ? Number(seatId) : undefined,
        army_counts: armyCounts,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    reportRoomError(error.error || "调整士兵数量失败。");
  }
}

export async function setArmyOrder(order, direction, teamId = null, kind = null, stride = null, ammo = null) {
  if (!hasRoom() || !state.playerToken) return;
  try {
    const payload = await fetchJson("/api/rooms/set-army-order", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        order,
        direction,
        team_id: teamId != null ? Number(teamId) : undefined,
        kind: kind || undefined,
        stride: stride || undefined,
        ammo: ammo || undefined,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    if (error.state) {
      applyRoomPayload(error.state, { preserveScreen: true });
      render();
    }
    reportRoomError(error.error || "设置军队指令失败。");
  }
}

export async function setSeatRandomQuota(seatId, quota) {
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
    reportRoomError(error.error || "调整随机配额失败。");
  }
}

export function renderRoomListActive() {
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

export async function startTutorialBattle() {
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
    // 页头说明位是这一屏唯一常驻的文字位置，失败信息落在那里；下一次
    // /api/heroes 拉取成功会自行清掉它。
    state.homeLoadError = error.error || "新手教学创建失败，请重试。";
  } finally {
    state.quickStartBusy = false;
    render();
  }
}

export async function startQuickAiBattle({rematch = false} = {}) {
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
    state.homeLoadError = error.error || "快速 AI 对战创建失败，请重试。";
  } finally {
    state.quickStartBusy = false;
    render();
  }
}

export async function resumeTutorialBattle() {
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
  } finally {
    state.quickStartBusy = false;
    render();
  }
}

export async function completeTutorialUnitSelection(unitId) {
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
  } catch {
  }
}

export function renderResumePanel() {
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

export async function selectRoomHero(heroCode, delta = 1, seatId = null) {
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
    reportRoomError(error.error || "选将失败。");
  }
}

export async function startRoomBattle() {
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
    reportRoomError(error.error || "开始对局失败。");
  }
}

export async function copyInviteLink() {
  if (!state.room?.invite_url) return;
  try {
    await navigator.clipboard.writeText(state.room.invite_url);
    $("lobby-caption").textContent = "邀请链接已复制,发给另一位玩家就能加入同一房间。";
  } catch {
    $("lobby-caption").textContent = `请手动复制这个链接:${state.room.invite_url}`;
  }
}

export function leaveReplayMode({ renderAfter = true } = {}) {
  state.replayMode = false;
  state.replayStepIndex = replayMeta().last_step_index || 0;
  state.battle = state.liveBattle;
  syncSelectedUnitAfterStateChange();
  if (renderAfter) render();
}

export async function loadReplayStep(stepIndex, { omniscient = state.replayOmniscient } = {}) {
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
    const incoming = payload.battle || null;
    const incomingHasPieces = (incoming?.units || []).some((unit) => unit?.position)
      || (incoming?.destroyed_units || []).some((unit) => unit?.position || unit?.last_position);
    if (
      incoming?.winner
      && !incomingHasPieces
      && (state.battle?.units || []).some((unit) => unit?.position)
    ) {
      incoming.units = state.battle.units;
    }
    state.battle = incoming;
    syncSelectedUnitAfterStateChange();
    render();
  } catch {
  }
}

export async function controlSimulation(action, speed = null) {
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
  }
}

export async function performAction(payload) {
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
    recordProductEvent("invalid_action", {
      match_id: state.room?.room_id || "",
      mode: tutorialState() ? "tutorial" : state.room?.mode || "room",
      action_type: payload.type || "unknown",
      reason: error.error || "rejected",
    });
  }
}

export async function toggleRoomReady() {
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
    reportRoomError(error.error || "准备状态更新失败。");
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

export function renderTutorialGuide() {
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

export async function retryTutorialStep() {
  const tutorial = tutorialState();
  if (!tutorial) return;
  clearActionSelection();
  if (!tutorial.can_retry_checkpoint) {
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
  } catch {
  }
}

export function exitTutorial() {
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
  state.homeFlow = "tutorial";
  syncLocation("draft", "");
  refreshState({preserveScreen: false});
}

export function restartFromGameOver() {
  if (!currentBattleLaunch().allowRematch) return;
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

export function onActionClick(action) {
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

export function attackTargetIdAtCell(action, x, y, occupant) {
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

export function explainInvalidBoardChoice(_action = null, _occupant = null) {
}
