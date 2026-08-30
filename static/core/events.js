// DOM event wiring for every screen.
import { activeBundles, activeOccupantAt, allUnits, canInteract, currentRespawnPrompt, fetchJson, hasBattle, hasRoom, inputPlayer, isChainMode, isGameOver, isReplayMode, isRespawnMode, recordProductEvent, replayMeta, roomQueryId, simulationMeta, stagedBackstepRetreatCell, stagedBackstepTargetId, syncLocation, toggleSidebarPanel, unitById, viewerPlayerId, visibleUnitAt } from '../core/net.js';
import { refreshState, render } from '../core/render.js';
import { state, ui } from '../core/state.js';
import { setScreen, syncScreen } from '../core/ui.js';
import { closeProfileModal, confirmProfile, effectiveProfileName, normalizeProfileName, openProfileModal, renderProfilePanel } from '../platform/auth.js';
import { refreshRecentMatches } from '../platform/home.js';
import { advanceStrategyMonth, exitStrategyCampaignView, joinStrategyCampaignByCode, openStrategyCampaignCreator, refreshStrategyCampaigns, returnToStrategyCampaign } from '../strategic/api.js';
import { connectionStatusLabel, readyStateLabel } from '../strategic/ui-base.js';
import { renderRecoveryButton, renderStrategyPanel } from '../strategic/workbench.js';
import { chainQueuedActionPrompt, hideTooltip, renderHoverCard, roomStateLabel, scheduleBoardOverlayRender, showTooltip } from '../tactical/battle-ui.js';
import { closeKeyboardHelp, focusMainContent, handleBattleKeyboard, onBoardClick, openKeyboardHelp } from '../tactical/board-input.js';
import { canEditRoomSetup, canManageSeatRoster, closeHeroDetail, closeHeroPicker, closeRoomSetup, confirmRoomSetup, isSeatLocked, openHeroDetail, openHeroPicker, openRoomSetup, renderHeroPicker, renderRoomSetupDialog, roomHeroLimit, seatHeroEntries, updateRoomSetupDraft } from '../tactical/room-lobby.js';
import { applyRoomPayload, autoConfigureRoom, canReclaimSeatByName, controlSimulation, copyInviteLink, createRoom, deleteRoom, exitTutorial, isRandomRoomMode, joinRoom, leaveReplayMode, leaveRoom, loadReplayStep, performAction, renderTutorialGuide, restartFromGameOver, resumeStoredSeat, resumeTutorialBattle, retryTutorialStep, roomModeMeta, selectRoomHero, setRoomSeatController, setRoomSeatTeam, setSeatRandomQuota, shouldShowLobbyPanel, startRoomBattle, startTutorialBattle, surrenderBattle, toggleRoomReady } from '../tactical/room-api.js';
import { clearActionSelection, loadStoredIdentity } from '../tactical/session.js';
import { bodyDirectionSelection, canCompleteTargetSelection, choicePatternSelection, controllerTypeLabel, currentRoomSeat, hasCancelableTargetSelection, isBoardTargetSelectionActive, movePathSelection, multiUnitSelection, patternSelection, randomRoomRosterSize, reviveUnitCellSelection, roomSummaries, sanitizeRandomRosterSizeInput, seatHeroSummary, setStagedAttackVariant, setStagedBodyDirection, setStagedPatternChoice, setStagedReviveUnitId, setStagedStatName, stagedAttackActionPayload, stagedBodyCells, stagedBodyDirection, stagedMovePath, stagedMultiTargetIds, stagedPatternCells, stagedPatternChoiceCode, stagedReviveCell, stagedReviveUnitId, stagedStatCells, stagedStatName, statCellSelection } from '../tactical/targeting.js';
import { clearBattleVfx, selectedAction, tutorialState } from '../tactical/vfx.js';
import { createMenu } from './components.js';
import { $ } from './dom.js';

export function bindEvents() {
  $("profile-name-input").addEventListener("input", (event) => {
    state.profileDraftName = normalizeProfileName(event.target.value);
  });
  $("profile-name-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      confirmProfile();
    }
  });
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
  const strategyJoinHostFaction = $("strategy-join-host-faction");
  if (strategyJoinHostFaction) {
    strategyJoinHostFaction.addEventListener("change", (event) => {
      state.strategyJoinHostFaction = Boolean(event.target.checked);
    });
  }
  const strategyNewCampaign = $("strategy-new-campaign");
  if (strategyNewCampaign) strategyNewCampaign.addEventListener("click", openStrategyCampaignCreator);
  const strategyJoin = $("strategy-join");
  if (strategyJoin) strategyJoin.addEventListener("click", joinStrategyCampaignByCode);
  const strategyExitCampaign = $("strategy-exit-campaign");
  if (strategyExitCampaign) strategyExitCampaign.addEventListener("click", exitStrategyCampaignView);
  const strategyRefresh = $("strategy-refresh");
  if (strategyRefresh) strategyRefresh.addEventListener("click", () => refreshStrategyCampaigns());
  const strategyBrowserRefresh = $("strategy-browser-refresh");
  if (strategyBrowserRefresh) strategyBrowserRefresh.addEventListener("click", () => refreshStrategyCampaigns());
  const strategyAdvance = $("strategy-advance-month");
  if (strategyAdvance) strategyAdvance.addEventListener("click", advanceStrategyMonth);
  const startTutorial = $("start-tutorial");
  if (startTutorial) startTutorial.addEventListener("click", startTutorialBattle);
  const refreshRecent = $("refresh-recent-matches");
  if (refreshRecent) refreshRecent.addEventListener("click", () => refreshRecentMatches());
  $("toggle-battle-sound")?.addEventListener("click", () => globalThis.WujiangBattleFeedback?.toggle("sound"));
  $("toggle-colorblind-mode")?.addEventListener("click", () => globalThis.WujiangBattleFeedback?.toggle("colorblind"));
  $("toggle-combat-feed")?.addEventListener("click", () => globalThis.WujiangBattleFeedback?.toggle("combatFeed"));
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
  bindRoomLobbyDialogs();
  $("join-room-code").addEventListener("input", (event) => {
    event.target.value = event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
    state.roomForm.joinRoomCode = event.target.value;
    renderProfilePanel();
  });
  $("profile-save").addEventListener("click", confirmProfile);
  $("profile-cancel").addEventListener("click", closeProfileModal);
  $("create-room").addEventListener("click", createRoom);
  $("join-room").addEventListener("click", () => joinRoom());
  $("resume-room").addEventListener("click", () => resumeStoredSeat());
  $("recover-room").addEventListener("click", () => resumeStoredSeat());
  $("leave-room").addEventListener("click", leaveRoom);
  $("delete-room").addEventListener("click", deleteRoom);
  $("start-room").addEventListener("click", startRoomBattle);
  $("toggle-ready").addEventListener("click", toggleRoomReady);
  $("copy-invite").addEventListener("click", copyInviteLink);
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
  $("board-stage").addEventListener("wheel", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest("input, select, textarea, label")) return;
    const overlay = $("game-over-overlay");
    if (overlay && !overlay.classList.contains("hidden")) {
      const card = overlay.querySelector(".game-over-card");
      if (card && typeof event.preventDefault === "function") event.preventDefault();
      if (card) card.scrollTop += Number(event.deltaY || 0);
      return;
    }
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
    if (!target || target.closest("input, select, textarea, label, .board-alert, .board-hint, .battle-surrender, .game-over-overlay")) return;
    const boardCell = target.closest(".cell");
    const clickedPiece = target.closest(".piece");
    if (clickedPiece) return;
    if (boardCell && board?.contains(boardCell) && visibleUnitAt(Number(boardCell.dataset.x), Number(boardCell.dataset.y))) return;
    if (isBoardTargetSelectionActive() && boardCell) return;
    const clickedBoardCell = Boolean(board && boardCell && board.contains(boardCell));
    if (!clickedBoardCell && target.closest("button")) return;
    ui.boardDragState = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      lastX: event.clientX,
      lastY: event.clientY,
      panX: Number(state.boardPanX || 0),
      panY: Number(state.boardPanY || 0),
      dragging: false,
      raf: 0,
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
    const drag = ui.boardDragState;
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag.lastX = event.clientX;
    drag.lastY = event.clientY;
    if (drag.raf) return;
    const flush = () => {
      drag.raf = 0;
      const dx = Number(drag.lastX || drag.startX) - drag.startX;
      const dy = Number(drag.lastY || drag.startY) - drag.startY;
      if (!drag.dragging && Math.hypot(dx, dy) < 6) return;
      drag.dragging = true;
      ui.boardDragSuppressUntil = Date.now() + 160;
      $("board-stage")?.classList.add("is-dragging");
      const next = clampBoardPan(drag.panX + dx, drag.panY + dy);
      state.boardPanX = next.x;
      state.boardPanY = next.y;
      applyBoardCamera();
      scheduleBoardOverlayRender();
    };
    if (typeof window.requestAnimationFrame === "function") {
      drag.raf = window.requestAnimationFrame(flush);
      return;
    }
    flush();
  });
  const endBoardDrag = (event) => {
    const drag = ui.boardDragState;
    if (!drag || (event && drag.pointerId !== event.pointerId)) return;
    if (drag.raf && typeof window.cancelAnimationFrame === "function") {
      window.cancelAnimationFrame(drag.raf);
      drag.raf = 0;
    }
    if (drag.dragging) {
      const dx = Number(drag.lastX || drag.startX) - drag.startX;
      const dy = Number(drag.lastY || drag.startY) - drag.startY;
      const next = clampBoardPan(drag.panX + dx, drag.panY + dy);
      state.boardPanX = next.x;
      state.boardPanY = next.y;
      applyBoardCamera();
      ui.boardDragSuppressUntil = Date.now() + 160;
    }
    const stage = $("board-stage");
    if (typeof stage.releasePointerCapture === "function") {
      try {
        stage.releasePointerCapture(drag.pointerId);
      } catch (error) {
        // Ignore browsers that reject release for uncaptured pointers.
      }
    }
    stage.classList.remove("is-dragging");
    ui.boardDragState = null;
  };
  $("board-stage").addEventListener("pointerup", endBoardDrag);
  $("board-stage").addEventListener("pointercancel", endBoardDrag);
  window.addEventListener("resize", () => {
    scheduleBoardOverlayRender();
  });
  $("room-battle").addEventListener("click", () => setScreen("battle"));
  $("return-room-lobby")?.addEventListener("click", () => setScreen("draft"));
  $("toggle-battle-console")?.addEventListener("click", () => {
    state.battleConsoleCollapsed = !state.battleConsoleCollapsed;
    renderBattleConsole();
  });
  $("game-over-strategy").addEventListener("click", returnToStrategyCampaign);
  $("game-over-back").addEventListener("click", () => setScreen("draft"));
  $("game-over-rematch").addEventListener("click", restartFromGameOver);
  $("game-over-details-toggle")?.addEventListener("click", () => {
    state.gameOverShowDetails = !state.gameOverShowDetails;
    render();
  });
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
  document.querySelectorAll("[data-battle-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.getAttribute("data-battle-tab") || "info";
      if (!state.rightRailCollapsed && state.battleDockTab === tab) {
        state.rightRailCollapsed = true;
      } else {
        state.battleDockTab = tab;
        state.rightRailCollapsed = false;
      }
      render();
    });
  });
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const attackVariantButton = target?.closest("[data-attack-variant]");
    if (attackVariantButton) {
      const action = selectedAction();
      if (!action || action.kind !== "attack") return;
      setStagedAttackVariant(attackVariantButton.getAttribute("data-attack-variant") || "");
      render();
      return;
    }
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
    if (ui.tooltipHideHandle) window.clearTimeout(ui.tooltipHideHandle);
    ui.tooltipHideHandle = window.setTimeout(() => {
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
      performAction({
        type: "attack",
        unit_id: state.selectedUnitId,
        cells: stagedPatternCells(action),
        ...stagedAttackActionPayload(action),
      });
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
    if (Date.now() < ui.boardDragSuppressUntil) return;
    const target = event.target instanceof Element ? event.target : null;
    const cell = target?.closest(".cell");
    if (!cell || !$("board").contains(cell)) return;
    const x = Number(cell.dataset.x);
    const y = Number(cell.dataset.y);
    onBoardClick(x, y, activeOccupantAt(x, y));
  });
  $("board-world")?.addEventListener("click", (event) => {
    if (Date.now() < ui.boardDragSuppressUntil) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest(".cell")) return;
    const piece = target?.closest(".board-piece");
    if (!piece || !$("board-pieces")?.contains(piece)) return;
    const x = Number(piece.dataset.x);
    const y = Number(piece.dataset.y);
    const occupant = unitById(piece.dataset.unitId || "") || activeOccupantAt(x, y);
    onBoardClick(x, y, occupant);
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

export function fallbackRoomModes() {
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

export function boardBasePixels(boardState = state.battle?.board) {
  if (!boardState) return 880;
  const maxDim = Math.max(Number(boardState.width || 0), Number(boardState.height || 0), 1);
  if (maxDim <= 8) return 1080;
  if (maxDim <= 10) return Math.max(920, maxDim * 96);
  return Math.max(980, maxDim * 84);
}

export function clampBoardZoom(value) {
  return Math.max(0.45, Math.min(1.85, Math.round(Number(value || 1) * 100) / 100));
}

function adjustBoardZoom(delta) {
  setBoardZoom((state.boardZoom || 1) + Number(delta || 0));
}

function clampBoardPan(x, y) {
  const stage = $("board-stage");
  const board = $("board");
  const rawX = Number(x || 0);
  const rawY = Number(y || 0);
  if (!stage || !board) return { x: rawX, y: rawY };
  const stageWidth = Number(stage.clientWidth || 0);
  const stageHeight = Number(stage.clientHeight || 0);
  const boardWidth = Number(board.offsetWidth || 0);
  const boardHeight = Number(board.offsetHeight || 0);
  if (stageWidth < 1 || stageHeight < 1 || boardWidth < 1 || boardHeight < 1) {
    return { x: rawX, y: rawY };
  }
  const zoom = clampBoardZoom(state.boardZoom);
  const visualWidth = boardWidth * zoom;
  const visualHeight = boardHeight * zoom;
  const pad = 64;
  const limitX = Math.max(pad, (visualWidth + stageWidth) / 2 - pad);
  const limitY = Math.max(pad, (visualHeight + stageHeight) / 2 - pad);
  return {
    x: Math.max(-limitX, Math.min(limitX, rawX)),
    y: Math.max(-limitY, Math.min(limitY, rawY)),
  };
}

export function applyBoardCamera() {
  const world = $("board-world") || $("board");
  if (!world) return;
  const pan = clampBoardPan(state.boardPanX, state.boardPanY);
  state.boardPanX = pan.x;
  state.boardPanY = pan.y;
  const zoom = clampBoardZoom(state.boardZoom);
  world.style.transform = `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${zoom})`;
}

function setBoardZoom(nextZoom, anchor = null) {
  const board = $("board");
  const boardRect = board?.getBoundingClientRect?.() || null;
  const previousZoom = clampBoardZoom(state.boardZoom);
  const targetZoom = clampBoardZoom(nextZoom);
  if (Math.abs(targetZoom - previousZoom) < 0.001) return;
  let anchorRatioX = null;
  let anchorRatioY = null;
  let anchorClientX = 0;
  let anchorClientY = 0;
  if (anchor && boardRect && boardRect.width > 0 && boardRect.height > 0) {
    anchorClientX = Number(anchor.clientX || 0);
    anchorClientY = Number(anchor.clientY || 0);
    anchorRatioX = Math.max(0, Math.min(1, (anchorClientX - boardRect.left) / boardRect.width));
    anchorRatioY = Math.max(0, Math.min(1, (anchorClientY - boardRect.top) / boardRect.height));
  }
  state.boardZoom = targetZoom;
  renderBoardZoomControls();
  applyBoardCamera();
  if (anchorRatioX != null && anchorRatioY != null && board) {
    const nextBoardRect = board.getBoundingClientRect?.();
    if (nextBoardRect?.width > 0 && nextBoardRect?.height > 0) {
      const desiredLeft = nextBoardRect.left + (nextBoardRect.width * anchorRatioX);
      const desiredTop = nextBoardRect.top + (nextBoardRect.height * anchorRatioY);
      const next = clampBoardPan(
        Number(state.boardPanX || 0) - (desiredLeft - anchorClientX),
        Number(state.boardPanY || 0) - (desiredTop - anchorClientY),
      );
      state.boardPanX = next.x;
      state.boardPanY = next.y;
      applyBoardCamera();
    }
  }
  scheduleBoardOverlayRender();
}

function resetBoardZoom() {
  state.boardPanX = 0;
  state.boardPanY = 0;
  applyBoardCamera();
  if (Math.abs(clampBoardZoom(state.boardZoom) - 1) < 0.001) {
    scheduleBoardOverlayRender();
    return;
  }
  setBoardZoom(1);
}

export function renderBattleConsole() {
  const bar = $("battle-console");
  const toggle = $("toggle-battle-console");
  if (!bar || !toggle) return;
  const collapsed = Boolean(state.battleConsoleCollapsed);
  bar.classList.toggle("is-collapsed", collapsed);
  toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  toggle.textContent = collapsed ? "展开控制" : "收起控制";
  toggle.title = collapsed ? "展开回放与缩放控制" : "收起回放与缩放控制";
}

export function renderBoardZoomControls() {
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

export function renderConnectionAndTurnState() {
  // 大厅里每个席位卡自己会写在线与准备状态，不再另立一块汇总；这里只剩战场那份。
  const battlePanel = $("battle-connection-summary");
  const timerPanel = $("battle-turn-timer");
  const panels = [battlePanel].filter(Boolean);
  panels.forEach((panel) => { panel.innerHTML = ""; });
  if (!state.room) {
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

// 三个弹窗的开关都在这里接线。它们共用同一套关闭手势：点遮罩、按 Esc、或者
// 点各自的关闭按钮；最上面那层先关，不会一次把三层都收掉。
function bindRoomLobbyDialogs() {
  $("open-room-setup")?.addEventListener("click", openRoomSetup);
  $("room-setup-cancel")?.addEventListener("click", closeRoomSetup);
  $("room-setup-confirm")?.addEventListener("click", confirmRoomSetup);
  $("room-mode-select")?.addEventListener("change", (event) => {
    updateRoomSetupDraft("mode", event.target.value);
    renderRoomSetupDialog();
  });
  $("room-seat-count-input")?.addEventListener("input", (event) => {
    updateRoomSetupDraft("seatCount", event.target.value);
  });
  $("random-roster-size-input")?.addEventListener("input", (event) => {
    const normalized = sanitizeRandomRosterSizeInput(event.target.value);
    event.target.value = normalized;
    updateRoomSetupDraft("randomRosterSize", normalized);
  });
  $("room-hero-limit-enabled")?.addEventListener("change", (event) => {
    if (!state.roomSetupDraft) return;
    state.roomSetupDraft.heroLimitEnabled = Boolean(event.target.checked);
    if (state.roomSetupDraft.heroLimitEnabled && !Number(state.roomSetupDraft.heroLimit)) {
      state.roomSetupDraft.heroLimit = "5";
    }
    renderRoomSetupDialog();
  });
  $("room-hero-limit-input")?.addEventListener("input", (event) => {
    const raw = Number.parseInt(event.target.value, 10);
    const normalized = Number.isFinite(raw) ? String(Math.max(1, Math.min(20, raw))) : "1";
    event.target.value = normalized;
    updateRoomSetupDraft("heroLimit", normalized);
  });
  $("room-turn-timeout-select")?.addEventListener("change", (event) => {
    updateRoomSetupDraft("turnTimeout", event.target.value);
  });
  $("room-board-width-input")?.addEventListener("input", (event) => {
    updateRoomSetupDraft("boardWidth", event.target.value);
  });
  $("room-board-height-input")?.addEventListener("input", (event) => {
    updateRoomSetupDraft("boardHeight", event.target.value);
  });
  $("auto-configure-room")?.addEventListener("click", autoConfigureRoom);

  $("hero-picker-close")?.addEventListener("click", closeHeroPicker);
  $("hero-search")?.addEventListener("input", (event) => {
    state.heroSearchQuery = String(event.target.value || "");
    renderHeroPicker();
  });
  $("hero-sort")?.addEventListener("change", (event) => {
    state.heroSortKey = String(event.target.value || "name");
    renderHeroPicker();
  });
  $("hero-sort-order")?.addEventListener("click", () => {
    state.heroSortDesc = !state.heroSortDesc;
    renderHeroPicker();
  });
  $("hero-detail-close")?.addEventListener("click", closeHeroDetail);

  const backdrops = [
    ["room-setup-dialog", closeRoomSetup],
    ["hero-picker", closeHeroPicker],
    ["hero-detail", closeHeroDetail],
  ];
  backdrops.forEach(([id, close]) => {
    $(id)?.addEventListener("click", (event) => {
      if (event.target === $(id)) close();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const open = backdrops.filter(([id]) => !$(id)?.classList.contains("hidden"));
    if (!open.length) return;
    event.stopPropagation();
    open[open.length - 1][1]();
  }, true);
}

function createSeatSelect(seat, label, datasetKey, options, value, onChange) {
  const field = document.createElement("label");
  field.className = "seat-control";
  const caption = document.createElement("span");
  caption.textContent = label;
  const select = document.createElement("select");
  select.className = "select";
  select.dataset[datasetKey] = String(seat.player_id);
  options.forEach(([optionValue, optionLabel]) => {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionLabel;
    if (optionValue === String(value)) option.selected = true;
    select.append(option);
  });
  select.addEventListener("change", (event) => {
    if (typeof event.target.blur === "function") event.target.blur();
    onChange(event.target.value);
  });
  field.append(caption, select);
  field.selectEl = select;
  return field;
}

/**
 * 阵容标签。
 *
 * 席位上只写名字：这一层要回答的是"这个席位带了谁"，不是"他们各自有多强"。
 * 点名字看详情，点叉去掉一个，同一个武将选了多个就在名字后缀上数量。
 */
function createSeatHeroTag(seat, entry, { showRemove = false, removeDisabled = false } = {}) {
  const tag = document.createElement("span");
  tag.className = "hero-tag";
  tag.dataset.heroCode = entry.code;
  const open = document.createElement("button");
  open.type = "button";
  open.className = "hero-tag__name";
  open.textContent = entry.count > 1 ? `${entry.name} x${entry.count}` : entry.name;
  open.addEventListener("click", () => openHeroDetail(entry.code));
  tag.append(open);
  if (!showRemove) return tag;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "hero-tag__remove";
  remove.textContent = "x";
  remove.disabled = removeDisabled;
  remove.setAttribute("aria-label", `移除 ${entry.name}`);
  remove.addEventListener("click", () => {
    if (remove.disabled) return;
    selectRoomHero(entry.code, -1, seat.player_id);
  });
  tag.append(remove);
  return tag;
}

function seatBadgeKind(label) {
  if (label === "红队") return "red";
  if (label === "蓝队") return "blue";
  if (label === "真人") return "human";
  if (label === "AI") return "ai";
  if (label === "开放") return "open";
  if (label === "房主") return "host";
  return "";
}

function createSeatBadge(label) {
  const badge = document.createElement("span");
  const kind = seatBadgeKind(label);
  badge.className = kind ? `seat-badge seat-badge--${kind}` : "seat-badge";
  badge.textContent = label;
  return badge;
}

function createSeatCard(seat) {
  const card = document.createElement("article");
  const isViewer = seat.player_id === state.room.viewer_player_id;
  const locked = isSeatLocked(seat);
  const manageRoster = canManageSeatRoster(seat);
  const entries = seatHeroEntries(seat);
  const showReady = Boolean(seat.ready || seat.is_ai);
  card.className = `seat-card${isViewer ? " is-viewer" : ""}${seat.occupied ? "" : " is-empty"}${showReady ? " is-ready" : ""}`;
  card.dataset.seatId = String(seat.player_id);

  const title = document.createElement("div");
  title.className = "seat-block seat-block--title";
  const head = document.createElement("div");
  head.className = "seat-head";
  const name = document.createElement("strong");
  name.className = "seat-name";
  name.textContent = `席位 ${seat.player_id}`;
  const badges = document.createElement("div");
  badges.className = "seat-badges";
  [seat.team_name, controllerTypeLabel(seat), seat.is_host ? "房主" : ""]
    .filter(Boolean)
    .forEach((label) => badges.append(createSeatBadge(label)));
  head.append(name, badges);
  const player = document.createElement("div");
  player.className = "seat-player";
  player.textContent = seat.name || "空位";
  title.append(head, player);
  card.append(title);

  const roster = document.createElement("div");
  roster.className = "seat-block seat-block--roster seat-roster";
  if (isRandomRoomMode()) {
    const quota = document.createElement("span");
    quota.className = "seat-roster__note";
    quota.textContent = `随机 ${Number(seat.random_quota || 0)} 人`;
    roster.append(quota);
  } else {
    entries.forEach((entry) => roster.append(createSeatHeroTag(seat, entry, {
      showRemove: manageRoster,
      removeDisabled: locked,
    })));
    if (manageRoster) {
      const add = document.createElement("button");
      add.type = "button";
      add.className = "seat-hero-add";
      add.dataset.addHero = String(seat.player_id);
      add.textContent = "+ 添加武将";
      add.disabled = locked;
      add.addEventListener("click", () => {
        if (add.disabled) return;
        openHeroPicker(seat.player_id);
      });
      roster.append(add);
    } else if (!entries.length) {
      const empty = document.createElement("span");
      empty.className = "seat-roster__note";
      empty.textContent = "未选择";
      roster.append(empty);
    }
  }
  card.append(roster);

  const showConfig = Boolean(state.room.viewer_is_host && state.room.status === "lobby");
  if (showConfig || seat.occupied) {
    const config = document.createElement("div");
    config.className = "seat-block seat-block--config";
    if (showConfig) {
      const controls = document.createElement("div");
      controls.className = "seat-controls";
      const teamField = createSeatSelect(
        seat,
        "队伍",
        "seatTeam",
        [["1", "红队"], ["2", "蓝队"]],
        String(seat.team_id),
        (value) => setRoomSeatTeam(seat.player_id, value),
      );
      teamField.selectEl.disabled = locked;
      controls.append(teamField);
      const controllerField = createSeatSelect(
        seat,
        "状态",
        "seatController",
        seat.is_human ? [["human", "真人"]] : [["open", "开放"], ["ai", "AI"]],
        seat.is_human ? "human" : String(seat.controller_type || "open"),
        (value) => setRoomSeatController(seat.player_id, value),
      );
      // 真人已经坐下了，房主不能把他改成 AI 或开放位；准备之后整张卡都锁住。
      controllerField.selectEl.disabled = Boolean(seat.is_human || locked);
      controls.append(controllerField);
      if (isRandomRoomMode()) {
        const quotaField = document.createElement("label");
        quotaField.className = "seat-control";
        const quotaLabel = document.createElement("span");
        quotaLabel.textContent = "随机配额";
        const quotaInput = document.createElement("input");
        quotaInput.className = "input";
        quotaInput.type = "number";
        quotaInput.min = "0";
        quotaInput.step = "1";
        quotaInput.value = String(Number(seat.random_quota || 0));
        quotaInput.dataset.seatQuota = String(seat.player_id);
        quotaInput.disabled = locked;
        const commitQuota = () => setSeatRandomQuota(seat.player_id, quotaInput.value);
        quotaInput.addEventListener("change", commitQuota);
        quotaInput.addEventListener("blur", commitQuota);
        quotaInput.addEventListener("keydown", (event) => {
          if (event.key === "Enter") commitQuota();
        });
        quotaField.append(quotaLabel, quotaInput);
        controls.append(quotaField);
      }
      config.append(controls);
    }
    if (seat.occupied) {
      const ready = Boolean(seat.ready || seat.is_ai);
      const status = document.createElement("div");
      status.className = `seat-status${ready ? " is-ready" : " is-waiting"}`;
      const mark = document.createElement("span");
      mark.className = "seat-status__mark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = ready ? "✓" : "×";
      const detail = document.createElement("span");
      detail.textContent = `${readyStateLabel(seat)} · ${connectionStatusLabel(seat.connection_status)}`;
      status.append(mark, detail);
      config.append(status);
    }
    card.append(config);
  }
  return card;
}

export function renderRoomPanels() {
  const showLobby = shouldShowLobbyPanel();
  const roomId = roomQueryId();
  $("room-home").classList.toggle("hidden", showLobby);
  $("room-lobby").classList.toggle("hidden", !showLobby);
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
  const viewerSeat = currentRoomSeat();
  renderRecoveryButton();

  if (!hasRoom()) {
    // 不在这里写页头。没有房间时玩家可能正在战役、教学或战绩里，页头归
    // renderHomeFlow 按当前流程决定；房间模块只在真的进了房间之后才接管它。
    leaveRoomBtn.classList.add("hidden");
    deleteRoomBtn.classList.add("hidden");
    copyInvite.classList.add("hidden");
    roomBattle.classList.add("hidden");
    if (toggleReady) toggleReady.classList.add("hidden");
    startRoom.classList.add("hidden");
    joinRoomButton.disabled = !state.profileReady || !String($("join-room-code").value || "").trim();
    renderRoomOverflowMenu();
    return;
  }

  if (!showLobby) {
    title.textContent = `加入房间 ${state.room.room_id}`;
    if (caption) {
      caption.textContent = state.roomError || "";
      caption.classList.toggle("is-error", Boolean(state.roomError));
    }
    leaveRoomBtn.classList.remove("hidden");
    leaveRoomBtn.disabled = false;
    deleteRoomBtn.classList.toggle("hidden", !state.room.viewer_is_host);
    deleteRoomBtn.disabled = !state.room.viewer_is_host;
    copyInvite.classList.remove("hidden");
    roomBattle.classList.toggle("hidden", !hasBattle());
    roomBattle.classList.add("primary", "room-battle-btn");
    roomBattle.classList.remove("ghost");
    if (toggleReady) toggleReady.classList.add("hidden");
    startRoom.classList.add("hidden");
    joinRoomButton.disabled = !state.profileReady || !String($("join-room-code").value || "").trim();
    renderRoomOverflowMenu();
    return;
  }

  title.textContent = `房间 ${state.room.room_id}`;
  // 这一行平时空着。建房页此前挂着一整段随状态改写的解说，那些话看一遍就够，
  // 却要一直占着位置；现在只有操作失败时才会有字。
  if (caption) {
    caption.textContent = state.roomError || "";
    caption.classList.toggle("is-error", Boolean(state.roomError));
  }

  $("room-code-label").textContent = state.room.room_id;
  $("room-status-label").textContent = state.room.status === "lobby"
    ? "等待双方就绪"
    : (isGameOver() ? "对局结束" : "对局进行中");
  $("viewer-seat-label").textContent = state.room.viewer_player_id
    ? `席位 ${state.room.viewer_player_id}`
    : "观战";
  $("viewer-seat-note").textContent = state.room.viewer_name
    ? `${state.room.viewer_name} · ${(viewerSeat?.team_name || "未分队")}${state.room.viewer_is_host ? " · 房主" : ""}`
    : "";
  $("invite-path-label").textContent = state.room.invite_url || state.room.invite_path;

  const modeMeta = roomModeMeta();
  $("room-mode-label").textContent = modeMeta.name;
  $("room-seat-count-label").textContent = `${state.room.seat_count} / ${state.room.seat_count_max}`;
  $("room-random-size-fact").classList.toggle("hidden", !isRandomRoomMode());
  $("room-random-size-label").textContent = String(randomRoomRosterSize());
  const heroLimit = roomHeroLimit();
  const heroLimitFact = $("room-hero-limit-fact");
  if (heroLimitFact) heroLimitFact.classList.toggle("hidden", !heroLimit);
  const heroLimitLabel = $("room-hero-limit-label");
  if (heroLimitLabel) heroLimitLabel.textContent = String(heroLimit);
  const timeoutLabel = $("room-turn-timeout-label");
  if (timeoutLabel) {
    const seconds = Number(state.room.turn_timeout_seconds ?? 0);
    timeoutLabel.textContent = seconds > 0 ? `${seconds} 秒` : "无限";
  }
  const boardSizeLabel = $("room-board-size-label");
  if (boardSizeLabel) {
    boardSizeLabel.textContent = `${Number(state.room.board_width || 10)}×${Number(state.room.board_height || 10)}`;
  }
  const openSetup = $("open-room-setup");
  if (openSetup) openSetup.classList.toggle("hidden", !canEditRoomSetup());
  const autoConfigure = $("auto-configure-room");
  if (autoConfigure) autoConfigure.classList.toggle("hidden", !canEditRoomSetup());

  leaveRoomBtn.classList.remove("hidden");
  leaveRoomBtn.disabled = false;
  deleteRoomBtn.classList.toggle("hidden", !state.room.viewer_is_host);
  deleteRoomBtn.disabled = !state.room.viewer_is_host;
  copyInvite.classList.toggle("hidden", !state.room.invite_url);
  roomBattle.classList.toggle("hidden", !hasBattle());
  roomBattle.disabled = !hasBattle();
  roomBattle.classList.add("primary", "room-battle-btn");
  roomBattle.classList.remove("ghost");
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
  // 开不了局的原因挂在按钮上。它只有在你想开局时才有意义，不值得为它常设一段文字。
  startRoom.title = startRoom.disabled ? String(state.room.start_blocker || "") : "";
  renderRoomOverflowMenu();

  const seatCards = $("seat-cards");
  seatCards.replaceChildren();
  (state.room.seats || []).forEach((seat) => seatCards.append(createSeatCard(seat)));
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

export function ensureSelectedUnit() {
  const action = selectedAction();
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

export function renderMessage() {
}

// 顶栏只放"你是谁"，不放操作说明——后者归当前屏幕的正文。
function setTopbarContext(text) {
  const node = $("topbar-context");
  if (!node) return;
  node.textContent = text;
  node.classList.toggle("hidden", !text);
}

export function renderHeader() {
  const pill = $("turn-pill");
  const caption = $("board-caption");
  // 没有房间时这颗药丸此前恒显示"尚未进入房间"——在主菜单、战役、战绩各屏
  // 都是一句没有信息量的常驻噪声。没内容就不占位。
  if (!hasRoom()) {
    pill.textContent = "";
    pill.classList.add("hidden");
    setTopbarContext("");
    caption.textContent = "\u8bf7\u5148\u521b\u5efa\u623f\u95f4\u6216\u52a0\u5165\u623f\u95f4\u3002";
    return;
  }
  pill.classList.remove("hidden");
  setTopbarContext(state.room.viewer_player_id ? `\u73a9\u5bb6 ${state.room.viewer_player_id}` : "\u89c2\u6218");
  if (!state.battle) {
    pill.textContent = `\u623f\u95f4 ${state.room.room_id} \u00b7 ${state.room.status === "lobby" ? "\u5927\u5385\u4e2d" : "\u7b49\u5f85\u5f00\u5c40"}`;
    caption.textContent = isRandomRoomMode()
      ? "\u5bf9\u5c40\u5c1a\u672a\u5f00\u59cb\uff0c\u968f\u673a\u9009\u4eba\u6a21\u5f0f\u4e0b\u65e0\u9700\u624b\u52a8\u9009\u5c06\u3002"
      : "\u5bf9\u5c40\u5c1a\u672a\u5f00\u59cb\uff0c\u8bf7\u5148\u5728\u623f\u95f4\u5927\u5385\u5b8c\u6210\u9009\u5c06\u3002";
    return;
  }
  const nextTurnName = state.battle.next_turn_unit_name || "";
  const nextTurnPlayerId = state.battle.next_turn_player_id;
  const nextTurnSummary = nextTurnName && nextTurnPlayerId
    ? `\u4e0b\u56de\u5408\uff1a\u73a9\u5bb6 ${nextTurnPlayerId} \u7684 ${nextTurnName}\u3002`
    : "\u4e0b\u56de\u5408\u5f85\u5b9a\u3002";
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

export function renderReplayToolbar() {
  globalThis.WujiangReplayUi?.renderToolbar({
    document,
    state,
    replay: replayMeta(),
    simulation: simulationMeta(),
    replayMode: isReplayMode(),
  });
}

// 房间头部原本并排七个按钮，其中"开始对局"和"离开房间"分量天差地别却长得一样。
// 只留下此刻要推进对局的那几个，其余收进"更多"。这些按钮的 id 和监听都不动，
// 下拉只是换了个容身之处，renderRoomActionButtons 那套 .hidden 开关照旧生效。
const ROOM_OVERFLOW_IDS = ["copy-invite", "recover-room", "leave-room", "delete-room"];

function buildRoomOverflowMenu() {
  const host = document.querySelector(".draft-head-actions");
  if (!host || $("room-overflow")) return;
  const children = ROOM_OVERFLOW_IDS.map((id) => $(id)).filter(Boolean);
  if (!children.length) return;
  const menu = createMenu({ label: "更多", children });
  // 显式赋值而不是走 createMenu 的 id 选项：verify_frontend_dom_ids 认的是
  // `.id = "..."`，让它能把这个运行时建出来的节点算进已知 ID。
  menu.id = "room-overflow";
  host.append(menu);
}

// 收进去的按钮全隐藏时，"更多"就没有内容可展开了。
function renderRoomOverflowMenu() {
  const menu = $("room-overflow");
  if (!menu) return;
  const empty = ROOM_OVERFLOW_IDS.every((id) => $(id)?.classList.contains("hidden") ?? true);
  menu.classList.toggle("is-empty", empty);
  if (empty) menu.closeMenu();
}

export function ensureDynamicUiScaffolding() {
  buildRoomOverflowMenu();
  if (!$("control-tooltip")) {
    const tooltip = document.createElement("div");
    tooltip.id = "control-tooltip";
    tooltip.className = "control-tooltip hidden";
    document.body.append(tooltip);
  }
}

document.querySelector(".skip-link")?.addEventListener("click", focusMainContent);
window.addEventListener("hashchange", () => {
  if (window.location.hash === "#main-content") focusMainContent();
});
