// Top-level render scheduling: decides which screen renderers run.
import { ensureSelectedUnit, renderBattleConsole, renderBoardZoomControls, renderConnectionAndTurnState, renderHeader, renderMessage, renderReplayToolbar, renderRoomPanels } from '../core/events.js';
import { canInteract, fetchJson, isChainMode, isGameOver, isRespawnMode, roomQueryId } from '../core/net.js';
import { applyScreen } from '../core/router.js';
import { state, ui } from '../core/state.js';
import { setScreen, syncModalIsolation, syncScreen } from '../core/ui.js';
import { renderProfilePanel, userLoggedIn } from '../platform/auth.js';
import { renderGate } from '../platform/gate.js';
import { refreshResumableTutorial, renderHomeFlow } from '../platform/home.js';
import { renderMenu } from '../platform/menu.js';
import { clearStrategyState, refreshStrategyCampaigns } from '../strategic/api.js';
import { renderRecentMatches } from '../strategic/ui-base.js';
import { isRoomConfigControlActive, isStrategyControlActive, renderProfileModal, renderStrategyPanel } from '../strategic/workbench.js';
import { renderActionForecast, renderActionPanel, renderBattleEffects, renderBoard, renderBoardOverlays, renderChainPanel, renderGameOverOverlay, renderHoverCard, renderLogs, renderRoomActionButtons, renderScreens, renderSelectedCard, renderSidebarPanels, renderTargetCancelButton, renderTargetCompleteButton, renderUnitStrip } from '../tactical/battle-ui.js';
import { renderHeroDetail, renderHeroPicker, renderRoomSetupDialog } from '../tactical/room-lobby.js';
import { renderTopbar } from './topbar.js';
import { applyRoomPayload, renderResumePanel, renderRoomListActive, renderTutorialGuide } from '../tactical/room-api.js';
import { clearActionSelection, ensureDraftSelection, syncIdentityFromUrl } from '../tactical/session.js';
import { tutorialState } from '../tactical/vfx.js';
import { $ } from './dom.js';

export function render() {
  // 硬门禁：后端对所有开局接口都要求账号，未登录时渲染主内容毫无意义，
  // 只会让玩家浏览一圈后在点击时撞上 401。
  if (!userLoggedIn()) {
    state.screen = "gate";
    applyScreen();
    renderGate();
    return;
  }
  if (state.screen === "gate") state.screen = "menu";
  if (state.screen === "menu") {
    applyScreen();
    renderMenu();
    renderProfileModal();
    syncModalIsolation();
    return;
  }

  if (isGameOver()) clearActionSelection();
  document.body.classList.toggle("battle-mode", state.screen === "battle");
  ensureDraftSelection();
  ensureSelectedUnit();
  const preserveRoomConfig = isRoomConfigControlActive();
  const preserveStrategyControl = isStrategyControlActive();
  renderScreens();
  renderTopbar();
  renderProfilePanel();
  renderHomeFlow();
  renderRecentMatches();
  if (!preserveStrategyControl) renderStrategyPanel();
  renderProfileModal();
  if (!preserveRoomConfig) renderRoomPanels();
  renderRoomSetupDialog();
  renderHeroPicker();
  renderHeroDetail();
  syncModalIsolation();
  renderResumePanel();
  renderRoomListActive();
  renderHeader();
  renderConnectionAndTurnState();
  renderBattleConsole();
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

function isTransportError(error) {
  if (!error || typeof error !== "object") return false;
  const name = String(error.name || "");
  const message = String(error.message || error.error || "");
  return name === "TypeError" || name === "AbortError"
    || /Failed to fetch|NetworkError|Load failed|aborted|请求超时/i.test(message);
}

function lobbySyncSignature() {
  const room = state.room;
  if (!room) return "";
  return JSON.stringify({
    status: room.status,
    mode: room.mode,
    hero_limit: room.hero_limit,
    board_width: room.board_width,
    board_height: room.board_height,
    turn_timeout_seconds: room.turn_timeout_seconds,
    seat_count: room.seat_count,
    can_start: room.can_start,
    start_blocker: room.start_blocker,
    viewer_player_id: room.viewer_player_id,
    roomError: state.roomError,
    seats: (room.seats || []).map((seat) => [
      seat.player_id,
      seat.name,
      seat.ready,
      seat.is_ai,
      seat.controller_type,
      seat.team_id,
      seat.hero_counts,
      seat.hero_total_count,
      seat.connection_status,
      seat.random_quota,
    ]),
  });
}

function markPollFailure() {
  ui.pollBackoffMs = Math.min(8000, ui.pollBackoffMs ? ui.pollBackoffMs * 2 : 1500);
  ui.nextRoomPollAt = Date.now() + ui.pollBackoffMs;
}

export async function refreshState({ preserveScreen = true } = {}) {
  if (state.historicalMatchId) return;
  if (ui.refreshInFlight) return;
  ui.refreshInFlight = true;
  try {
    syncIdentityFromUrl();
    const roomId = roomQueryId();
    if (!roomId) {
      ui.lastLobbyRenderSignature = "";
      ui.pollBackoffMs = 0;
      const payload = await fetchJson("/api/heroes");
      state.homeLoadError = "";
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
          campaign.resume?.ready_user_ids,
          campaign.resume?.drafting_user_ids,
          campaign.resume?.proxy_ai_user_ids,
        ]),
        authenticatedUserId: state.authUser?.id || 0,
        selectedCampaignId: state.strategyCampaign?.id || 0,
        resumableTutorial: state.resumableTutorial
          ? [state.resumableTutorial.room_id, state.resumableTutorial.step_id]
          : null,
        tutorialResumeError: state.tutorialResumeError,
      });
      if (homeRenderSignature === ui.lastHomeRenderSignature) return;
      ui.lastHomeRenderSignature = homeRenderSignature;
      render();
      return;
    }
    const query = new URLSearchParams({ room_id: roomId });
    if (state.playerToken) {
      query.set("player_token", state.playerToken);
    }
    const payload = await fetchJson(`/api/rooms/state?${query.toString()}`, { timeoutMs: 8000 });
    const wasLost = Boolean(state.connectionLostAt);
    applyRoomPayload(payload, { preserveScreen });
    if (payload.battle_recovery?.status === "recovered") state.strategyBattleRecovery = null;
    ui.pollBackoffMs = 0;
    // 大厅里别人没动的时候，整页重画只会把输入焦点和滚动位置打掉。
    // 对局里棋盘会变，所以有战场就每次都画。
    if (!state.liveBattle && !wasLost) {
      const signature = lobbySyncSignature();
      if (signature && signature === ui.lastLobbyRenderSignature) return;
      ui.lastLobbyRenderSignature = signature;
    }
    render();
  } catch (error) {
    // fetchJson 的业务失败抛的是后端 JSON。浏览器自己的断网、超时是 TypeError
    // 或 AbortError——那是网络，不是程序缺陷，不能当成启动崩溃横幅抛上去。
    if (error instanceof Error && !isTransportError(error)) throw error;
    const fault = isTransportError(error) ? { error: "连接中断，正在保留当前房间身份等待重新同步。" } : error;
    if (fault.state) {
      applyRoomPayload(fault.state, { preserveScreen });
      render();
    } else if (!roomQueryId()) {
      state.homeLoadError = fault.error || "加载英雄列表失败。";
      $("lobby-caption").textContent = state.homeLoadError;
    } else {
      if (fault.battle_recovery?.can_restart_from_prebattle) {
        state.strategyBattleRecovery = fault.battle_recovery;
        state.strategyMessage = fault.error || "战略战斗检查点不可恢复，可从战前快照安全重开。";
        setScreen("draft", { renderAfter: false });
        render();
        return;
      }
      if (!state.connectionLostAt) state.connectionLostAt = Date.now();
      markPollFailure();
      render();
    }
  } finally {
    ui.refreshInFlight = false;
  }
}
