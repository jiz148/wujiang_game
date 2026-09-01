// Battle screen rendering: board, units, action panel and log.
import { $ } from '../core/dom.js';
import { applyBoardCamera, boardBasePixels, clampBoardZoom } from '../core/events.js';
import { activeBundles, allUnits, backstepFollowUpTargetIds, boardPieceZIndex, boardUnits, bundleFor, canInteract, currentRespawnPrompt, hasBattle, hasRoom, hoveredUnit, inspectBoardUnit, inspectedUnit, isAiTakeover, isChainMode, isGameOver, isReplayMode, isRespawnMode, selectedUnit, stagedBackstepRetreatCell, stagedTarget, unitById, unitFootprintBounds, unitHasLargeFootprint, unitOccupiedCells, unitsAtCell, viewerPlayerId, viewerTeamId } from '../core/net.js';
import { render } from '../core/render.js';
import { applyScreen } from '../core/router.js';
import { state, ui } from '../core/state.js';
import { effectiveProfileName } from '../platform/auth.js';
import { currentBattleLaunch, isCampaignBattleLaunch } from '../bridge/battle-launch.js';
import { isRandomRoomMode, loadReplayStep, onActionClick, roomModeMeta, setArmyOrder, shouldShowLobbyPanel } from '../tactical/room-api.js';
import { clearActionSelection } from '../tactical/session.js';
import { actionLabel, actionLimitLabel, actionManaLabel, actionNeedsTarget, actionTierLabel, actionTimingLabel, actionTitle, bodyDirectionSelection, canCompleteTargetSelection, choicePatternSelection, currentPreview, fieldEffectMarker, fieldEffectsByCell, hasCancelableTargetSelection, movePathSelection, multiUnitSelection, normalizedPatternCells, patternSelection, patternSelectionCanComplete, randomRoomFallbackSummary, randomRoomRosterSize, reviveUnitCellSelection, stagedAttackVariantCode, stagedBodyCells, stagedBodyDirection, stagedMovePath, stagedMultiTargetIds, stagedPatternCells, stagedPatternChoiceCode, stagedReviveCell, stagedReviveUnitId, stagedStatCells, stagedStatName, statCellRequired, statCellSelection, unitIsSelectableTarget } from '../tactical/targeting.js';
import { actionByCode, actionWheelLayer, displayActions, fieldEffectDuration, fieldEffects, flushPendingArmyVfx, hoveredAction, hpRatio, manaDisplayClass, manaPipsMarkup, positionKey, positionsToSet, renderBattleVfx, selectedAction, trimNumber, tutorialState, unitBoundsRelativeToStage, unitStatusSummary } from '../tactical/vfx.js';

export function renderScreens() {
  applyScreen();
}

function hideBoardHint() {
  const node = $("board-alert");
  if (!node) return;
  node.className = "board-alert board-hint hidden";
  node.innerHTML = "";
}

function showBoardHint(title, body, actionsHtml = "") {
  const node = $("board-alert");
  if (!node) return;
  node.className = "board-alert board-hint is-active";
  node.innerHTML = `
    <button type="button" class="board-hint__icon" aria-label="${title}">!</button>
    <div class="board-hint__pop" role="tooltip">
      <strong>${title}</strong>
      <span>${body}</span>
    </div>
    ${actionsHtml ? `<div class="board-hint__actions board-alert-actions">${actionsHtml}</div>` : ""}
  `;
}

function attackVariantButtons(action) {
  const variants = action?.attackVariants || [];
  if (!variants.length) return "";
  const selected = stagedAttackVariantCode(action);
  return [
    `<button type="button" class="board-alert-choice ${selected ? "" : "is-selected"}" data-attack-variant="">普攻</button>`,
    ...variants.map((entry) => `
      <button type="button" class="board-alert-choice ${selected === String(entry.code) ? "is-selected" : ""}" data-attack-variant="${entry.code}">${entry.name}</button>
    `),
  ].join("");
}

function renderBoardAlert() {
  if (!state.battle || isGameOver() || state.screen !== "battle") {
    hideBoardHint();
    return;
  }

  const action = selectedAction();

  if (isChainMode() && !action) {
    hideBoardHint();
    return;
  }

  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    showBoardHint("重新出现", `${unit?.name || "该单位"} 即将重新出现。请点击蓝色高亮的最近可用格子。`);
    return;
  }

  if (action && movePathSelection(action)) {
    const chosenCells = stagedMovePath(action);
    showBoardHint(
      actionTitle(action),
      `${chosenCells.length ? `已选择 ${chosenCells.length} 格移动路径。` : "请逐格点出这次移动的路径。"} 绿色高亮表示单位最后会占据的格子；多格单位可以点击绿色占据区域来选择落点。可以提前点击“完成选择”，也可以点击已选格子回退路径。`,
    );
    return;
  }

  if (action?.code === "mana_pull" && !state.stagedPayload?.targetUnitId) {
    showBoardHint("魔力牵引", "先点击被牵引的单位,再点击 1 到 3 格直线落点。");
    return;
  }

  if (action?.code === "mana_pull" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    showBoardHint("魔力牵引", `已选中 ${target?.name || "目标"},请点击其 1 到 3 格的直线落点。`);
    return;
  }

  if (action?.code === "descent_moment" && !state.stagedPayload?.targetUnitId) {
    showBoardHint("降临时刻", "先点击带有抹杀计数点的对方单位，再点击其周围合法落点。");
    return;
  }

  if (action?.code === "descent_moment" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    showBoardHint("降临时刻", `已选中 ${target?.name || "目标"}，请点击其周围蓝色高亮落点。`);
    return;
  }

  if (action?.code === "backstep_shot" && isChainMode()) {
    const retreatCell = stagedBackstepRetreatCell(action);
    const source = unitById(state.battle?.pending_chain?.queued_action?.actor_id || "");
    const targetIds = retreatCell ? backstepFollowUpTargetIds(action, retreatCell) : [];
    const canCounter = targetIds.some((id) => unitIsSelectableTarget(unitById(id)));
    if (!retreatCell) {
      showBoardHint(actionTitle(action), `先点击一个直线 2 格的撤步落点。撤步完成后，你可以选择只反击 ${source?.name || "原连锁来源"}，也可以直接点“完成选择”放弃反击。`);
      return;
    }
    showBoardHint(
      actionTitle(action),
      `${canCounter ? `撤步落点已确定。现在先决定是否反击：点击 ${source?.name || "原连锁来源"} 就会立刻反击；点击“完成选择”则表示不反击。` : "撤步落点已确定，但撤步后已无法攻击原连锁来源。请直接点“完成选择”结算。"} 再点一次已选落点可回到第一步。`,
    );
    return;
  }

  if (action && multiUnitSelection(action)) {
    const chosenIds = stagedMultiTargetIds(action);
    showBoardHint(
      actionTitle(action),
      `${chosenIds.length ? `已选择 ${chosenIds.length} 个目标。` : "请点击高亮单位来选择目标。"} 选好后可以点击“完成选择”，再次点击同一目标可取消。`,
    );
    return;
  }

  if (action && statCellSelection(action)) {
    const chosenCells = stagedStatCells(action);
    const required = statCellRequired(action);
    const statName = stagedStatName(action);
    const statButtons = (statCellSelection(action).stats || []).map((entry) => `
      <button type="button" class="board-alert-choice ${statName === entry.code ? "is-selected" : ""}" data-stat-choice="${entry.code}">${entry.label}</button>
    `).join("");
    showBoardHint(actionTitle(action), `先选择要吸取的能力值，再选择 ${required} 个新增占格。当前已选 ${chosenCells.length} 个新增格。`, statButtons);
    return;
  }

  if (action && bodyDirectionSelection(action)) {
    const chosenCells = stagedBodyCells(action);
    const direction = stagedBodyDirection(action);
    const directionButtons = (bodyDirectionSelection(action).directions || []).map((entry) => {
      const selected = direction && Number(entry.dx) === direction.dx && Number(entry.dy) === direction.dy;
      return `<button type="button" class="board-alert-choice ${selected ? "is-selected" : ""}" data-direction-dx="${entry.dx}" data-direction-dy="${entry.dy}">${entry.label}</button>`;
    }).join("");
    showBoardHint(actionTitle(action), `点击岩神身体格选择要发射的部分，然后选择方向。当前已选 ${chosenCells.length} 格；再次点击已选身体格可取消。`, directionButtons);
    return;
  }

  if (action && reviveUnitCellSelection(action)) {
    const selectedId = stagedReviveUnitId(action);
    const selectedCell = stagedReviveCell(action);
    const candidates = reviveUnitCellSelection(action).candidates || [];
    const buttons = candidates.map((entry) => `
      <button type="button" class="board-alert-choice ${selectedId === String(entry.id) ? "is-selected" : ""}" data-revive-unit-id="${entry.id}">${entry.name}</button>
    `).join("");
    showBoardHint(
      actionTitle(action),
      selectedId ? `已选择复活单位${selectedCell ? "和落点" : "，现在点击周围高亮格作为落点"}。` : "先选择一个已被破坏的单位，再点击周围高亮格作为落点。",
      buttons,
    );
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
    if (action.kind === "attack") {
      const variantName = (action.attackVariants || []).find((entry) => String(entry.code) === stagedAttackVariantCode(action))?.name || "普攻";
      const actionButtons = `${attackVariantButtons(action)}${choiceButtons}`;
      if (!choiceCode) {
        showBoardHint(
          actionTitle(action),
          `${action.attackVariants?.length ? `当前是${variantName}。` : ""}先声明这次普攻的前方方向，再点击该方向外侧高亮出来的可攻击目标。`.trim(),
          actionButtons,
        );
        return;
      }
      showBoardHint(
        actionTitle(action),
        `${action.attackVariants?.length ? `当前是${variantName}。` : ""}已声明方向“${choiceLabel}”。现在点击该方向外侧高亮出来的目标格或目标单位即可普攻；若想换方向，直接重新点方向按钮。`.trim(),
        actionButtons,
      );
      return;
    }
    if (!choiceCode) {
      showBoardHint(actionTitle(action), "先选择这次的 n，再逐格点击要覆盖的区域。", choiceButtons);
      return;
    }
    if (!chosenCells.length) {
      showBoardHint(actionTitle(action), `已选择 ${choiceCode}。现在请逐格点击这个 n 对应的合法区域；若贴着边界导致剩余格子本应落在棋盘外，可以直接点“完成选择”。`, choiceButtons);
      return;
    }
    showBoardHint(
      actionTitle(action),
      `已选择 ${choiceCode}，并选中 ${chosenCells.length} 格。${canComplete ? "当前已经可以点击“完成选择”结算；若还想扩大到同一合法区域，可继续点蓝色高亮格子。" : "请继续点击蓝色高亮的剩余格子。"} 点击已选格子可撤回该格。`,
      choiceButtons,
    );
    return;
  }

  if (action && patternSelection(action)) {
    const chosenCells = stagedPatternCells(action);
    const canComplete = patternSelectionCanComplete(action, chosenCells);
    if (!chosenCells.length) {
      showBoardHint(actionTitle(action), "请依次点击要覆盖的格子；若贴着边界导致剩余格子本应落在棋盘外，可以直接点“完成选择”，也可以随时取消。");
      return;
    }
    showBoardHint(
      actionTitle(action),
      `已选 ${chosenCells.length} 格。${canComplete ? "当前已经可以点击“完成选择”结算；若还想扩大到同一合法区域，可继续点蓝色高亮格子。" : "请继续点击蓝色高亮的剩余格子。"} 点击已选格子可撤回该格。`,
    );
    return;
  }

  if (action && actionNeedsTarget(action)) {
    showBoardHint(actionTitle(action), "请点击棋盘上蓝色高亮的可选目标或范围。");
    return;
  }

  hideBoardHint();
}

export function renderBattleEffects() {
  const node = $("battle-effects");
  if (!node) return;
  node.innerHTML = "";
  if (!state.battle || state.screen !== "battle") {
    node.classList.add("hidden");
    return;
  }
  const effects = fieldEffects();
  if (!effects.length) {
    node.classList.add("hidden");
    return;
  }
  node.classList.remove("hidden");
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

function soldierKindOf(unit) {
  const code = String(unit?.hero_code || "");
  if (code.startsWith("strategy_")) return code.slice("strategy_".length);
  if (!unit?.is_army_soldier) return "";
  const byName = {
    "普通步兵": "infantry",
    "弓兵": "archer",
    "骑兵": "cavalry",
    "守备兵": "garrison",
    "山地兵": "mountain_soldier",
    "以太侦察兵": "ether_scout",
    "城墙工兵": "wall_engineer",
    "雪鬼": "snow_ghost",
    "箭塔": "arrow_tower",
    "火炮": "cannon",
  };
  return byName[unit.name] || "infantry";
}

function soldierIconSvg(kind) {
  const icons = {
    infantry: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.4 2.8 4v4.3c0 3.3 2.2 5.8 5.2 6.5 3-.7 5.2-3.2 5.2-6.5V4L8 1.4z"/></svg>',
    archer: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.1 1.7c4.1 2.2 6.2 4.8 6.2 6.3s-2.1 4.1-6.2 6.3l.5-3.1c2.1-1.1 3.3-2.2 3.3-3.2S5.7 5.9 3.6 4.8z"/><path d="M3.2 8h10.2"/><path d="M11.1 5.7 14.2 8l-3.1 2.3"/></svg>',
    cavalry: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.8 11.8c.4-2.6 1.6-4.5 3.4-5.3L6 3.2h2.4l.6 2.1c.9-.1 1.8.2 2.5.8 1 .8 1.7 2.1 2 3.7l.7 2H3.8zm-.6.8h10v1.4h-10z"/></svg>',
    garrison: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 13V6.2h2V4.6h2V6.2h2V4.6h2V6.2h2V13H3zm2.2-2.2h5.6V8.4H5.2z"/></svg>',
    mountain_soldier: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.4 12.8 6.2 5l2 4.2 1.6-2.8 3.8 6.4H2.4z"/></svg>',
    ether_scout: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2C4.2 3.2 1.6 8 1.6 8s2.6 4.8 6.4 4.8S14.4 8 14.4 8 11.8 3.2 8 3.2zm0 7.4A2.6 2.6 0 1 1 8 5.4a2.6 2.6 0 0 1 0 5.2z"/><circle cx="8" cy="8" r="1.3"/></svg>',
    wall_engineer: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M9.8 2.4 13.6 6l-1.4 1.4-1.2-1.2-5.2 5.2H3.4V8.8l5.2-5.2-1.2-1.2z"/></svg>',
    snow_ghost: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.6v12.8M3.4 4.2 12.6 11.8M12.6 4.2 3.4 11.8M8 4.4 6.2 7.1 8 9.8l1.8-2.7z"/></svg>',
    arrow_tower: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 14V7.1h1.4V5.4h1.5V3.8h1.4V2.4h.8V3.8h1.4V5.4h1.5V7.1h1.4V14H3.2z"/><path d="M6.6 9.2h2.8v4.2H6.6z"/></svg>',
    cannon: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.8 8.6h7.4l4.4-2.6v3.4L9.2 12H1.8z"/><circle cx="4.2" cy="12.5" r="1.6"/><circle cx="8.4" cy="12.5" r="1.6"/><path d="M3.2 6.4h3.2L7.6 8H3.6z"/></svg>',
  };
  return icons[kind] || icons.infantry;
}

function siegeReloadState(unit) {
  const raw = String(unit?.siege_reload_state || "");
  if (raw === "loading" || raw === "ready" || raw === "empty") return raw;
  return unit?.siege_loaded ? "ready" : "empty";
}

function cannonAmmoName(unit) {
  const army = state.battle?.army;
  const ammoId = army?.orders?.[unit?.player_id]?.cannon?.ammo || unit?.siege_ammo || "shell";
  const options = army?.ammo_options?.[unit?.player_id] || [];
  const match = options.find((item) => item.id === ammoId);
  if (match?.name) return match.name;
  const names = { shell: "炮弹", heavy_shell: "重型炮弹", ultra_shell: "超重型炮弹" };
  return names[ammoId] || "炮弹";
}

function siegeReloadLabel(loadState, unit = null) {
  const ammo = unit ? cannonAmmoName(unit) : "";
  if (loadState === "ready") return ammo ? `已装填${ammo}` : "已装填";
  if (loadState === "loading") return ammo ? `正在装填${ammo}` : "正在装填";
  return ammo ? `未装填${ammo}` : "未装填";
}

function pieceCoreMarkup(unit) {
  const kind = soldierKindOf(unit);
  if (kind === "arrow_tower") {
    return `<div class="keep-body" aria-label="${unit.name}">${soldierIconSvg(kind)}</div>`;
  }
  if (kind === "cannon") {
    const load = siegeReloadState(unit);
    return `
      <div class="cannon-body" aria-label="${unit.name}">
        ${soldierIconSvg(kind)}
        <span class="cannon-load is-${load}" title="${siegeReloadLabel(load, unit)}"></span>
      </div>
    `;
  }
  if (kind) {
    return `<div class="piece-icon" aria-label="${unit.name}">${soldierIconSvg(kind)}</div>`;
  }
  return `<div class="piece-name">${unit.name}</div>`;
}

function boardCellAt(x, y) {
  return document.querySelector(`#board .cell[data-x="${x}"][data-y="${y}"]`);
}

function placeMarchPiece(piece, x, y) {
  if (!piece) return;
  const width = Math.max(1, Number(piece.dataset.footprintWidth || 1));
  const height = Math.max(1, Number(piece.dataset.footprintHeight || 1));
  piece.dataset.x = String(x);
  piece.dataset.y = String(y);
  if (piece.classList.contains("is-footprint")) {
    piece.style.gridColumn = `${x + 1} / span ${width}`;
    piece.style.gridRow = `${y + 1} / span ${height}`;
    return;
  }
  const host = boardCellAt(x, y);
  if (!host) return;
  host.classList.add("has-unit");
  host.append(piece);
}

function armyMarchTraces() {
  const army = state.battle?.army;
  const traces = Array.isArray(army?.move_traces)
    ? army.move_traces.filter((item) => Array.isArray(item?.path) && item.path.length > 1)
    : [];
  return { marchId: String(army?.march_id || ""), traces };
}

function ensureArmyMarchPlayback() {
  const { marchId, traces } = armyMarchTraces();
  if (!hasBattle() || isReplayMode() || isGameOver()) {
    state.lastArmyMarchId = "";
    state.armyMarch = null;
    if (ui.armyMarchTimer) {
      window.clearTimeout(ui.armyMarchTimer);
      ui.armyMarchTimer = 0;
    }
    return [];
  }
  if (!marchId || !traces.length) return [];
  if (globalThis.WujiangBattleFeedback?.reducedMotion()) {
    state.lastArmyMarchId = marchId;
    state.armyMarch = { id: marchId, tick: traces.reduce((max, item) => Math.max(max, item.path.length), 1) - 1 };
    flushPendingArmyVfx();
    return traces;
  }
  if (state.armyMarch?.id !== marchId) {
    state.lastArmyMarchId = marchId;
    state.armyMarch = { id: marchId, tick: 0 };
  }
  return traces;
}

function armyMarchCellForUnit(unit, traces) {
  if (!state.armyMarch || !traces.length) return null;
  const trace = traces.find((item) => String(item.unit_id) === String(unit.id));
  if (!trace) return null;
  return trace.path[Math.min(state.armyMarch.tick, trace.path.length - 1)] || null;
}

function applyArmyMarchFrame(traces, tick) {
  traces.forEach((trace) => {
    const cell = trace.path[Math.min(tick, trace.path.length - 1)];
    if (!cell) return;
    const piece = document.querySelector(`.board-piece[data-unit-id="${trace.unit_id}"]`);
    if (piece) placeMarchPiece(piece, cell.x, cell.y);
  });
}

function playArmyMarchIfNeeded() {
  const traces = ensureArmyMarchPlayback();
  const marchId = state.armyMarch?.id || "";
  if (!marchId || !traces.length || globalThis.WujiangBattleFeedback?.reducedMotion()) {
    if (!traces.length) flushPendingArmyVfx();
    return;
  }
  const maxLen = traces.reduce((max, item) => Math.max(max, item.path.length), 0);
  applyArmyMarchFrame(traces, state.armyMarch.tick);
  if (state.armyMarch.tick >= maxLen - 1 || ui.armyMarchTimer) return;
  const step = () => {
    if (!state.armyMarch || state.armyMarch.id !== marchId) {
      ui.armyMarchTimer = 0;
      return;
    }
    state.armyMarch.tick += 1;
    applyArmyMarchFrame(traces, state.armyMarch.tick);
    if (state.armyMarch.tick < maxLen - 1) {
      ui.armyMarchTimer = window.setTimeout(step, 110);
    } else {
      ui.armyMarchTimer = 0;
      flushPendingArmyVfx();
    }
  };
  ui.armyMarchTimer = window.setTimeout(step, 110);
}

export function renderBoard() {
  const board = $("board");
  const piecesLayer = $("board-pieces");
  board.innerHTML = "";
  if (piecesLayer) piecesLayer.innerHTML = "";
  const preview = currentPreview();
  const selected = inspectedUnit();

  if (!state.battle) {
    board.classList.remove("is-large-board");
    board.style.width = "";
    board.style.minWidth = "";
    board.style.maxWidth = "";
    board.style.gridTemplateRows = "";
    if (piecesLayer) {
      piecesLayer.style.gridTemplateColumns = "";
      piecesLayer.style.gridTemplateRows = "";
    }
    applyBoardCamera();
    return;
  }
  const isLargeBoard = state.battle.board.width > 8 || state.battle.board.height > 8;
  board.classList.toggle("is-large-board", isLargeBoard);
  const columns = `repeat(${state.battle.board.width}, minmax(0, 1fr))`;
  const rows = `repeat(${state.battle.board.height}, minmax(0, 1fr))`;
  board.style.gridTemplateColumns = columns;
  board.style.gridTemplateRows = rows;
  board.style.aspectRatio = `${state.battle.board.width} / ${state.battle.board.height}`;
  if (piecesLayer) {
    piecesLayer.style.gridTemplateColumns = columns;
    piecesLayer.style.gridTemplateRows = rows;
  }
  const boardPixels = boardBasePixels(state.battle.board);
  state.boardZoom = clampBoardZoom(state.boardZoom);
  board.style.width = `${Math.round(boardPixels)}px`;
  board.style.minWidth = `${Math.round(boardPixels)}px`;
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
  const boardWidth = state.battle.board.width;
  const cellAt = [];

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

      const unitsHere = boardUnits().filter(
        (unit) => unit.position && unitOccupiedCells(unit).some((cellPosition) => cellPosition.x === x && cellPosition.y === y),
      );
      const occupant = unitsHere.find((unit) => !unit.banished) || unitsHere[0] || null;
      const ghostUnits = unitsHere.filter((unit) => unit.banished);

      const key = `${x},${y}`;
      const terrain = Array.isArray(state.battle?.board?.terrain) ? state.battle.board.terrain : [];
      const isWall = terrain.some((item) => Number(item.x) === x && Number(item.y) === y && item.kind === "wall");
      const cellEffects = fieldCellMap.get(key) || [];
      if (isWall) cell.classList.add("is-terrain-wall");
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

      cellAt[y * boardWidth + x] = cell;
      board.append(cell);
    }
  }

  const marchTraces = ensureArmyMarchPlayback();
  boardUnits()
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
      const marchCell = armyMarchCellForUnit(unit, marchTraces);
      const placeX = marchCell ? Number(marchCell.x) : bounds.minX;
      const placeY = marchCell ? Number(marchCell.y) : bounds.minY;
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
      const kind = soldierKindOf(unit);
      const piece = document.createElement("div");
      piece.className = [
        "piece",
        "board-piece",
        `player-${unit.player_id}`,
        largeFootprint ? "is-footprint" : "",
        isStealthed ? "is-stealthed" : "",
        kind ? "is-soldier" : "is-hero",
        kind === "arrow_tower" ? "is-structure is-arrow-tower" : "",
        kind === "cannon" ? "is-siege-engine is-cannon" : "",
        kind === "cannon" && siegeReloadState(unit) === "ready" ? "is-loaded" : "",
      ].filter(Boolean).join(" ");
      piece.dataset.unitId = unit.id;
      piece.dataset.footprintWidth = String(bounds.width);
      piece.dataset.footprintHeight = String(bounds.height);
      if (unit.position || marchCell) {
        piece.dataset.x = String(placeX);
        piece.dataset.y = String(placeY);
      }
      piece.style.zIndex = String(boardPieceZIndex(unit));
      piece.style.setProperty("--hp-angle", `${hpRatio(unit) * 360}deg`);
      const hideMana = Boolean(kind);
      piece.innerHTML = `
        ${footprintCellsMarkup}
        <div class="piece-ring ${isStealthed ? "is-stealthed" : ""}">
          <div class="piece-core">
            ${pieceCoreMarkup(unit)}
          </div>
        </div>
        ${hideMana ? "" : `
        <div class="${manaDisplayClass(unit)}" aria-label="魔力 ${trimNumber(unit.mana)} / ${trimNumber(unit.max_mana || unit.base_stats?.mana || unit.stats?.max_mana || unit.stats?.mana || unit.mana)}">
          ${manaPipsMarkup(unit)}
        </div>`}
      `;
      const host = !largeFootprint ? cellAt[placeY * boardWidth + placeX] : null;
      if (host) {
        piece.classList.add("is-in-cell");
        host.classList.add("has-unit");
        host.append(piece);
      } else {
        piece.style.gridColumn = `${placeX + 1} / span ${bounds.width}`;
        piece.style.gridRow = `${placeY + 1} / span ${bounds.height}`;
        (piecesLayer || board).append(piece);
      }
    });
  applyBoardCamera();
  playArmyMarchIfNeeded();
}

export function renderActionPanel() {
  const panel = $("action-panel");
  if (!panel) return;
  panel.innerHTML = "";
  const actions = displayActions();
  if (!actions.length) {
    const empty = document.createElement("div");
    empty.className = "queue-item";
    empty.textContent = isReplayMode() ? "回放中" : "暂无可用动作";
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

export function renderActionForecast() {
  const panel = $("action-forecast");
  if (!panel) return;
  const action = selectedAction();
  if (!action) {
    panel.className = "action-forecast is-empty hidden";
    panel.replaceChildren();
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
  if (bounds.right < 0 || bounds.bottom < 0 || bounds.left > stageRect.width || bounds.top > stageRect.height) {
    return;
  }
  const chosen = placements.find((placement) => placement.score >= placement.required)
    || placements.sort((a, b) => b.score - a.score)[0];
  const anchorLeft = chosen.left;
  const anchorTop = chosen.top;
  if (
    anchorLeft + clusterWidth < 0
    || anchorTop + clusterHeight < 0
    || anchorLeft > stageRect.width
    || anchorTop > stageRect.height
  ) {
    return;
  }

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

export function renderBoardOverlays() {
  renderBattleVfx();
  renderBoardAlert();
  renderActionWheel();
}

export function scheduleBoardOverlayRender() {
  if (ui.boardOverlayRenderHandle && typeof window.cancelAnimationFrame === "function") {
    window.cancelAnimationFrame(ui.boardOverlayRenderHandle);
  }
  if (typeof window.requestAnimationFrame === "function") {
    ui.boardOverlayRenderHandle = window.requestAnimationFrame(() => {
      ui.boardOverlayRenderHandle = 0;
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
    <strong>${actionTitle(action)}</strong>
    <p>${action.description}</p>
    <p>${actionTierLabel(action)} · ${actionTimingLabel(action)} · ${actionManaLabel(action) || "不消耗魔力"}</p>
    <p>${actionLimitLabel(action)} · ${actionNeedsTarget(action) ? "需要选取目标" : "无需额外目标"}</p>
  `;
}

function chainQueuedActionSummary(chain) {
  return chain?.queued_action_effect_summary || chain?.queued_action?.description || "\u539f\u52a8\u4f5c\u5c06\u6309\u539f\u58f0\u660e\u7ee7\u7eed\u7ed3\u7b97\u3002";
}

export function chainQueuedActionPrompt(chain) {
  const summary = chainQueuedActionSummary(chain);
  if (summary && summary.startsWith("\u3010")) return summary;
  const actionName = chain?.queued_action?.display_name || "\u539f\u52a8\u4f5c";
  return `\u3010${actionName}\u3011\uff1a${summary}`;
}

export function renderHoverCard() {
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

export function hideTooltip() {
  const node = tooltipNode();
  if (!node) return;
  node.classList.add("hidden");
  node.textContent = "";
}

export function showTooltip(text, pointer) {
  const node = tooltipNode();
  if (!node || !text || !pointer) return;
  node.textContent = text;
  node.classList.remove("hidden");
  const x = Math.min(window.innerWidth - 16, pointer.x + 12);
  const y = Math.min(window.innerHeight - 16, pointer.y + 12);
  node.style.left = `${x}px`;
  node.style.top = `${y}px`;
}

function simulationPendingAction() {
  return state.room?.simulation?.pending_action || null;
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

export function syncAiPreview(previousBattle, nextBattle) {
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

export function renderSidebarPanels() {
  const dock = $("battle-right-rail");
  if (!dock) return;
  const open = !state.rightRailCollapsed;
  const army = state.battle?.army;
  const hasAnyArmy = Boolean(army?.has_army?.[1] || army?.has_army?.[2]);
  const armyTab = dock.querySelector('[data-battle-tab="army"]');
  if (armyTab) {
    armyTab.classList.toggle("hidden", !hasAnyArmy);
  }
  if (!hasAnyArmy && state.battleDockTab === "army") {
    state.battleDockTab = "info";
  }
  dock.classList.toggle("is-open", open);
  dock.classList.toggle("is-collapsed", !open);
  dock.querySelectorAll("[data-battle-tab]").forEach((button) => {
    const active = open && button.getAttribute("data-battle-tab") === (state.battleDockTab || "info");
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  dock.querySelectorAll("[data-battle-page]").forEach((page) => {
    page.classList.toggle("hidden", page.getAttribute("data-battle-page") !== (state.battleDockTab || "info"));
  });
  const toggle = $("toggle-right-rail");
  if (toggle) {
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.textContent = open ? "›" : "‹";
    toggle.title = open ? "收起操作面板" : "展开操作面板";
  }
  const lobbyBtn = $("return-room-lobby");
  if (lobbyBtn) {
    const campaignBattle = isCampaignBattleLaunch();
    lobbyBtn.textContent = campaignBattle ? "返回战役" : "房间大厅";
    lobbyBtn.classList.toggle("hidden", !hasRoom());
  }
  const takeoverPanel = $("ai-takeover-panel");
  const takeoverBtn = $("ai-takeover-toggle");
  if (takeoverPanel) {
    const tutorial = state.room?.experience_kind === "tutorial";
    const visible = Boolean(
      hasRoom()
      && hasBattle()
      && !isGameOver()
      && !isReplayMode()
      && !tutorial
      && viewerPlayerId()
    );
    takeoverPanel.classList.toggle("hidden", !visible);
  }
  if (takeoverBtn) {
    const active = isAiTakeover();
    takeoverBtn.textContent = active ? "停止接管" : "让 AI 接管";
    takeoverBtn.classList.toggle("is-active", active);
    takeoverBtn.setAttribute("aria-pressed", active ? "true" : "false");
  }
  const heroStyle = $("ai-hero-style");
  const armyStyle = $("ai-army-style");
  const seat = (state.room?.seats || []).find((item) => item.player_id === viewerPlayerId());
  if (heroStyle) {
    heroStyle.value = seat?.hero_ai_style || state.room?.viewer_hero_ai_style || "follow";
  }
  if (armyStyle) {
    armyStyle.value = seat?.army_ai_style || state.room?.viewer_army_ai_style || "seek";
  }
}

function resourceRatio(current, max) {
  const top = Number(max || 0);
  if (top <= 0) return 0;
  return Math.max(0, Math.min(1, Number(current || 0) / top));
}

function unitMaxMana(unit) {
  return Number(unit.max_mana || unit.base_stats?.mana || unit.stats?.max_mana || unit.stats?.mana || unit.mana || 0);
}

export function renderSelectedCard() {
  const panel = $("selected-card");
  const unit = inspectedUnit();
  if (!unit) {
    panel.className = "selected-card is-empty";
    panel.textContent = isGameOver()
      ? `玩家 ${state.battle?.winner || ""} 已获胜。`
      : "";
    return;
  }
  const statusEntries = (unit.statuses || []).map((status) => `${status.name}${status.duration ? `(${status.duration})` : ""}`);
  if (unit.banished) {
    statusEntries.unshift(`消失${unit.banish_turns_remaining > 0 ? `(${unit.banish_turns_remaining})` : ""}`);
  }
  const statuses = statusEntries.join(" · ") || "无";
  const traits = (unit.traits || []).map((trait) => trait.name).join(" · ") || "无";
  const maxMana = unitMaxMana(unit);
  const hpPercent = Math.round(resourceRatio(unit.hp, unit.max_hp) * 100);
  const manaPercent = Math.round(resourceRatio(unit.mana, maxMana) * 100);
  const viewer = viewerTeamId();
  const sideLabel = viewer == null
    ? `玩家 ${unit.player_id}`
    : (unit.player_id === viewer ? "己方" : "对方");
  panel.className = unit.id !== state.selectedUnitId ? "selected-card is-inspecting" : "selected-card";
  panel.innerHTML = `
    <div class="selected-card__head">
      <div class="selected-card__who">
        <strong>${unit.name}</strong>
        <span>${sideLabel} · 玩家 ${unit.player_id} · ${unit.role} / ${unit.attribute} / ${unit.race}</span>
      </div>
      <span class="hero-level-tag">Lv ${trimNumber(unit.level || 1)}</span>
    </div>
    <div class="selected-card__section">
      <div class="selected-card__meter">
        <span>血</span>
        <div class="selected-card__bar" aria-hidden="true"><i style="width: ${hpPercent}%"></i></div>
        <b>${trimNumber(unit.hp)} / ${trimNumber(unit.max_hp)}</b>
      </div>
      <div class="selected-card__meter is-mana">
        <span>魔</span>
        <div class="selected-card__bar" aria-hidden="true"><i style="width: ${manaPercent}%"></i></div>
        <b>${trimNumber(unit.mana)} / ${trimNumber(maxMana)}</b>
      </div>
      <div class="selected-card__vitals">
        固定护盾 ${unit.shields || 0} · 临时护盾 ${unit.temporary_shields || 0} · 闪避 ${unit.dodge_charges || 0}
      </div>
      <div class="selected-card__vitals"><strong>状态</strong> ${statuses}</div>
      ${unit.siege_reload_cycle ? `<div class="selected-card__vitals">装填：${siegeReloadLabel(siegeReloadState(unit), unit)}</div>` : ""}
    </div>
    <div class="selected-card__section">
      <div class="selected-card__stats">
        <span>攻 ${trimNumber(unit.stats.attack)}</span>
        <span>守 ${trimNumber(unit.stats.defense)}</span>
        <span>速 ${trimNumber(unit.stats.speed)}</span>
        <span>范 ${trimNumber(unit.stats.attack_range)}</span>
        <span>魔力点 ${trimNumber(unit.mana_points || unit.stats?.mana_points || 0)}</span>
      </div>
    </div>
    <div class="selected-card__section">
      <div class="statline"><strong>特性</strong> ${traits}</div>
      <div class="statline"><strong>原始技能</strong> ${unit.raw_skill_text || "无"}</div>
      <div class="statline"><strong>原始特性</strong> ${unit.raw_trait_text || "无"}</div>
    </div>
  `;
}

export function roomStateLabel(room) {
  if (!room) return "";
  if (room.status === "battle") return "\u5bf9\u6218\u4e2d";
  if (room.status === "finished") return "\u5df2\u7ed3\u675f";
  if (room.can_join) return "\u53ef\u52a0\u5165";
  if (room.is_full) return "\u5df2\u6ee1";
  return "\u5927\u5385\u4e2d";
}

function armyKindGroups(army, teamId) {
  const own = army?.present_kinds?.[teamId] || [];
  const ownStructures = army?.structures?.[teamId] || [];
  if (own.length || ownStructures.length) {
    return [{ teamId, kinds: own, own: true }];
  }
  const groups = [];
  for (const playerId of [1, 2]) {
    const kinds = army?.present_kinds?.[playerId] || [];
    if (kinds.length) {
      groups.push({ teamId: playerId, kinds, own: false });
    }
  }
  return groups;
}

function armyKindCommand(army, teamId, kind) {
  return army?.orders?.[teamId]?.[kind] || {
    order: "advance",
    direction: teamId === 2 ? "W" : "E",
    stride: "full",
  };
}

function currentTurnPlayerId(battle) {
  const armyTurn = Boolean(battle?.is_army_turn || battle?.army?.is_army_turn);
  const unit = unitById(battle?.active_turn_unit_id);
  const raw = armyTurn
    ? (battle?.army?.army_turn_player_id ?? battle?.active_player)
    : (unit?.player_id ?? battle?.active_player);
  return Number(raw) === 2 ? 2 : 1;
}

function currentTurnSubject(battle) {
  if (Boolean(battle?.is_army_turn || battle?.army?.is_army_turn)) {
    return "军队";
  }
  const unit = unitById(battle?.active_turn_unit_id);
  return unit?.name || battle?.active_turn_unit_name || "武将";
}

export function renderBattleTurnBanner() {
  const banner = $("battle-turn-banner");
  if (!banner) return;
  const battle = state.battle;
  const visible = hasBattle() && (!isGameOver() || isReplayMode());
  banner.classList.toggle("hidden", !visible);
  banner.replaceChildren();
  if (!visible) return;
  const playerId = currentTurnPlayerId(battle);
  const completed = Number(battle?.completed_turns || 0);
  const turnIndex = Math.max(1, Number(battle?.turn_number || completed + 1 || 1));
  const limit = Number(battle?.turn_timeout_limit || 0);
  const round = document.createElement("span");
  round.className = "battle-turn-banner__round";
  if (isGameOver()) {
    round.textContent = limit > 0 ? `终局 · 原第 ${turnIndex}/${limit} 回合` : `终局 · 原第 ${turnIndex} 回合`;
    banner.append(round);
    return;
  }
  round.textContent = limit > 0 ? `第 ${turnIndex}/${limit} 回合` : `第 ${turnIndex} 回合`;
  const tag = document.createElement("span");
  tag.className = `battle-turn-banner__player is-player-${playerId}`;
  tag.textContent = `玩家 ${playerId}`;
  const subject = document.createElement("span");
  subject.className = "battle-turn-banner__subject";
  subject.textContent = `的${currentTurnSubject(battle)}回合`;
  banner.append(round, "，当前", tag, subject);
}

export function renderArmyOrderBar() {
  const panel = $("army-order-panel");
  if (!panel) return;
  const army = state.battle?.army;
  const teamId = viewerTeamId();
  const hasAnyArmy = Boolean(army?.has_army?.[1] || army?.has_army?.[2]);
  if (!hasAnyArmy) {
    panel.replaceChildren();
    return;
  }
  const groups = armyKindGroups(army, teamId);
  const canCommandOwn = Boolean(
    hasBattle()
    && !isGameOver()
    && !isReplayMode()
    && !isAiTakeover()
    && teamId
    && army?.has_army?.[teamId]
    && state.playerToken
  );
  const orders = army?.order_options || [
    { code: "advance", name: "进军" },
    { code: "seek", name: "寻敌" },
    { code: "hold", name: "固守" },
    { code: "retreat", name: "后撤" },
  ];
  const directions = army?.direction_options || [
    { code: "NW", name: "西北" }, { code: "N", name: "北" }, { code: "NE", name: "东北" },
    { code: "W", name: "西" }, { code: "E", name: "东" },
    { code: "SW", name: "西南" }, { code: "S", name: "南" }, { code: "SE", name: "东南" },
  ];
  panel.replaceChildren();
  const lead = document.createElement("div");
  lead.className = "army-order-panel__lead";
  lead.textContent = isAiTakeover()
    ? "AI 接管中，军队指令由 AI 调整。停止接管后可以再改。"
    : canCommandOwn
      ? "同一兵种共用一套全局指令。进军沿所选朝向走；后撤沿反方向走；寻敌则每人朝最近的敌人走。"
      : "士兵按各兵种指令行动。进军沿所选朝向走；后撤沿反方向走；寻敌则每人朝最近的敌人走。";
  panel.append(lead);
  groups.forEach((group) => {
    const canCommand = canCommandOwn && group.own;
    if (groups.length > 1 || !group.own) {
      const heading = document.createElement("div");
      heading.className = "army-order-panel__side";
      heading.textContent = `玩家 ${group.teamId}`;
      panel.append(heading);
    }
    group.kinds.forEach((item) => {
      const command = armyKindCommand(army, group.teamId, item.kind);
      const card = document.createElement("section");
      card.className = `army-order-card${canCommand ? "" : " is-readonly"}`;
      const title = document.createElement("div");
      title.className = "army-order-card__title";
      title.textContent = `${item.name} ×${item.count}`;
      card.append(title);
      if (item.kind === "cannon") {
        const note = document.createElement("div");
        note.className = "army-order-card__note";
        note.textContent = "不动则自动装填；装填完毕后移动结束仍可开炮。无法对范 1 开火，伤害不分敌我。";
        card.append(note);
        const ammoOptions = army?.ammo_options?.[group.teamId] || [{ id: "shell", name: "炮弹", splash: 0 }];
        if (ammoOptions.length) {
          const ammoGroup = document.createElement("div");
          ammoGroup.className = "army-order-card__ammo";
          ammoOptions.forEach((option) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `army-order-card__ammo-btn${(command.ammo || "shell") === option.id ? " is-active" : ""}`;
            button.textContent = option.name || option.id;
            button.title = `溅射 ${option.splash || 0}`;
            button.disabled = !canCommand;
            button.addEventListener("click", () => {
              if (!canCommand || (command.ammo || "shell") === option.id) return;
              setArmyOrder(command.order, command.direction, group.teamId, item.kind, command.stride, option.id);
            });
            ammoGroup.append(button);
          });
          card.append(ammoGroup);
        }
      }
      const orderGroup = document.createElement("div");
      orderGroup.className = "army-order-card__orders";
      orders.forEach((option) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `army-order-card__order${command.order === option.code ? " is-active" : ""}`;
        button.textContent = option.name;
        button.disabled = !canCommand;
        button.addEventListener("click", () => {
          if (!canCommand || command.order === option.code) return;
          setArmyOrder(option.code, command.direction, group.teamId, item.kind, command.stride, command.ammo);
        });
        orderGroup.append(button);
      });
      card.append(orderGroup);
      if (command.order !== "hold" && command.order !== "seek") {
        const dirGroup = document.createElement("div");
        dirGroup.className = "army-order-card__dirs";
        ["NW", "N", "NE", "W", "", "E", "SW", "S", "SE"].forEach((code) => {
          if (!code) {
            const spacer = document.createElement("span");
            spacer.className = "army-order-card__dir is-blank";
            dirGroup.append(spacer);
            return;
          }
          const option = directions.find((entry) => entry.code === code);
          const button = document.createElement("button");
          button.type = "button";
          button.className = `army-order-card__dir${command.direction === code ? " is-active" : ""}`;
          button.textContent = option?.name || code;
          button.title = option?.name || code;
          button.disabled = !canCommand;
          button.addEventListener("click", () => {
            if (!canCommand || command.direction === code) return;
            setArmyOrder(command.order, code, group.teamId, item.kind, command.stride, command.ammo);
          });
          dirGroup.append(button);
        });
        card.append(dirGroup);
        const strideBtn = document.createElement("button");
        strideBtn.type = "button";
        strideBtn.className = `army-order-card__stride${command.stride === "step" ? " is-active" : ""}`;
        strideBtn.textContent = command.stride === "step" ? "逐步：开" : "逐步：关";
        strideBtn.title = "开启后每次军队回合只走一格";
        strideBtn.disabled = !canCommand;
        strideBtn.addEventListener("click", () => {
          if (!canCommand) return;
          setArmyOrder(
            command.order,
            command.direction,
            group.teamId,
            item.kind,
            command.stride === "step" ? "full" : "step",
            command.ammo,
          );
        });
        card.append(strideBtn);
      }
      panel.append(card);
    });
    (army?.structures?.[group.teamId] || []).forEach((item) => {
      const card = document.createElement("section");
      card.className = "army-order-card is-readonly is-structure";
      const title = document.createElement("div");
      title.className = "army-order-card__title";
      title.textContent = `${item.name} ×${item.count}`;
      const note = document.createElement("div");
      note.className = "army-order-card__note";
      note.textContent = "自动射击 · 无法移动 · 需火炮摧毁";
      card.append(title, note);
      panel.append(card);
    });
  });
}

export function renderUnitStrip() {
  const strip = $("unit-strip");
  const label = $("unit-strip-label");
  strip.innerHTML = "";
  if (isGameOver()) {
    label?.classList.add("hidden");
    const item = document.createElement("div");
    item.className = "queue-item";
    item.textContent = "对局已结束";
    strip.append(item);
    return;
  }
  const bundles = activeBundles();
  label?.classList.toggle("hidden", !bundles.length);
  bundles.forEach((entry) => {
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
      inspectBoardUnit(unit, { adoptIfControllable: true });
      clearActionSelection();
      render();
    });
    strip.append(btn);
  });
}

export function renderChainPanel() {
  const panel = $("chain-panel");
  const caption = $("chain-caption");
  const skipBtn = $("skip-chain");
  const bar = $("battle-chain-bar");
  const text = $("battle-chain-text");
  if (panel) panel.innerHTML = "";
  const hideBar = () => {
    bar?.classList.add("hidden");
    if (text) text.textContent = "";
  };
  if (isGameOver()) {
    if (caption) caption.textContent = "对局已结束,无法再进行连锁。";
    skipBtn?.classList.add("hidden");
    hideBar();
    return;
  }
  if (isRespawnMode()) {
    const prompt = currentRespawnPrompt();
    const unit = unitById(prompt?.unit_id || "");
    if (caption) caption.textContent = `${unit?.name || "消失单位"} 正等待重新出现。`;
    skipBtn?.classList.add("hidden");
    hideBar();
    return;
  }
  if (!isChainMode()) {
    if (caption) caption.textContent = "选择一个行动。连锁出现时，可选响应也会列在这里。";
    skipBtn?.classList.add("hidden");
    hideBar();
    return;
  }

  const chain = state.battle.pending_chain;
  const sourceUnit = unitById(chain.queued_action.actor_id);
  const currentReactor = unitById(chain.current_unit_id);
  const sourceSummary = chainQueuedActionPrompt(chain);
  const summary = `等待 ${currentReactor?.name || "响应方"} · ${sourceUnit?.name || "来源"} 的【${chain.queued_action.display_name || "动作"}】`;
  if (caption) caption.textContent = summary;
  if (text) text.textContent = summary;
  bar?.classList.remove("hidden");
  skipBtn?.classList.remove("hidden");
}

export function renderLogs() {
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

export function renderPostgameSummary() {
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

  const fillPostgameRows = (body, rows, emptyLabel) => {
    if (!body) return;
    body.innerHTML = "";
    (rows || []).forEach((item) => {
      const row = document.createElement("tr");
      [
        `${item.name}${item.owner_name ? ` · ${item.owner_name}` : ""}`,
        formatPostgameValue(item.damage_dealt),
        formatPostgameValue(item.healing_done),
        formatPostgameValue(item.damage_taken),
        String(item.kills || 0),
        String(item.shields_broken || 0),
        String(item.chain_reactions || 0),
      ].forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      });
      body.append(row);
    });
    if (!(rows || []).length && emptyLabel) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.textContent = emptyLabel;
      row.append(cell);
      body.append(row);
    }
  };
  fillPostgameRows($("postgame-hero-stats"), summary.hero_stats || [], "本局没有单独的武将统计。");
  const soldierRows = summary.soldier_stats || [];
  fillPostgameRows($("postgame-soldier-stats"), soldierRows, "");
  $("postgame-soldier-section")?.classList.toggle("hidden", !soldierRows.length);

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

export function renderGameOverOverlay() {
  const overlay = $("game-over-overlay");
  const title = $("game-over-title");
  const rematch = $("game-over-rematch");
  const strategy = $("game-over-strategy");
  const back = $("game-over-back");
  const launch = currentBattleLaunch();
  if (!state.battle || !isGameOver() || state.screen !== "battle" || isReplayMode() || state.gameOverDismissed) {
    overlay.classList.add("hidden");
    if (!state.gameOverDismissed) state.gameOverShowDetails = false;
    return;
  }
  renderPostgameSummary();
  if (title) title.textContent = `玩家 ${state.battle.winner} 获胜`;
  const showDetails = Boolean(state.gameOverShowDetails);
  $("game-over-overview")?.classList.toggle("hidden", showDetails);
  $("game-over-details")?.classList.toggle("hidden", !showDetails);
  const detailsToggle = $("game-over-details-toggle");
  if (detailsToggle) {
    detailsToggle.textContent = showDetails ? "返回总览" : "战斗详情";
    detailsToggle.classList.toggle("hidden", !state.room?.postgame?.available);
  }
  const tutorial = tutorialState();
  if (strategy) {
    const showStrategy = launch.source === "campaign" || Boolean(state.strategyCampaign);
    strategy.classList.toggle("hidden", !showStrategy);
    strategy.disabled = !showStrategy;
    strategy.classList.toggle("primary", showStrategy);
    strategy.classList.toggle("ghost", !showStrategy);
  }
  if (back) {
    back.classList.toggle("hidden", launch.source === "campaign");
    back.textContent = launch.allowLobby ? "返回房间大厅" : "离开战场";
  }
  if (rematch) {
    rematch.classList.toggle("hidden", !launch.allowRematch);
    rematch.disabled = !launch.allowRematch || !Boolean(state.room?.can_rematch && state.room?.viewer_player_id !== null);
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

export function renderRoomActionButtons() {
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

export function renderTargetCancelButton() {
  const btn = $("cancel-targeting");
  const visible = hasCancelableTargetSelection();
  btn.classList.toggle("hidden", !visible);
  btn.classList.toggle("is-attention", visible);
  btn.disabled = !visible;
}

export function renderTargetCompleteButton() {
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
  btn.classList.toggle("is-attention", visible);
  btn.disabled = !visible;
}
