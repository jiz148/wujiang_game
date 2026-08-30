// Battle screen rendering: board, units, action panel and log.
import { $ } from '../core/dom.js';
import { applyBoardCamera, boardBasePixels, clampBoardZoom } from '../core/events.js';
import { activeBundles, allUnits, backstepFollowUpTargetIds, boardPieceZIndex, bundleFor, canInteract, currentRespawnPrompt, hasBattle, hasRoom, hoveredUnit, inspectBoardUnit, inspectedUnit, isChainMode, isGameOver, isReplayMode, isRespawnMode, selectedUnit, stagedBackstepRetreatCell, stagedTarget, unitById, unitFootprintBounds, unitHasLargeFootprint, unitOccupiedCells, unitsAtCell, viewerPlayerId, viewerTeamId } from '../core/net.js';
import { render } from '../core/render.js';
import { applyScreen } from '../core/router.js';
import { state, ui } from '../core/state.js';
import { effectiveProfileName } from '../platform/auth.js';
import { isRandomRoomMode, loadReplayStep, onActionClick, roomModeMeta, shouldShowLobbyPanel } from '../tactical/room-api.js';
import { clearActionSelection } from '../tactical/session.js';
import { actionLabel, actionLimitLabel, actionManaLabel, actionNeedsTarget, actionTierLabel, actionTimingLabel, actionTitle, bodyDirectionSelection, canCompleteTargetSelection, choicePatternSelection, currentPreview, fieldEffectMarker, fieldEffectsByCell, hasCancelableTargetSelection, movePathSelection, multiUnitSelection, normalizedPatternCells, patternSelection, patternSelectionCanComplete, randomRoomFallbackSummary, randomRoomRosterSize, reviveUnitCellSelection, stagedAttackVariantCode, stagedBodyCells, stagedBodyDirection, stagedMovePath, stagedMultiTargetIds, stagedPatternCells, stagedPatternChoiceCode, stagedReviveCell, stagedReviveUnitId, stagedStatCells, stagedStatName, statCellRequired, statCellSelection, unitIsSelectableTarget } from '../tactical/targeting.js';
import { actionByCode, actionWheelLayer, displayActions, fieldEffectDuration, fieldEffects, hoveredAction, hpRatio, manaDisplayClass, manaPipsMarkup, positionKey, positionsToSet, renderBattleVfx, selectedAction, trimNumber, tutorialState, unitBoundsRelativeToStage, unitStatusSummary } from '../tactical/vfx.js';

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

      cellAt[y * boardWidth + x] = cell;
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
      piece.dataset.unitId = unit.id;
      if (unit.position) {
        piece.dataset.x = String(unit.position.x);
        piece.dataset.y = String(unit.position.y);
      }
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
      const host = !largeFootprint ? cellAt[bounds.minY * boardWidth + bounds.minX] : null;
      if (host) {
        piece.classList.add("is-in-cell");
        host.classList.add("has-unit");
        host.append(piece);
      } else {
        piece.style.gridColumn = `${bounds.minX + 1} / span ${bounds.width}`;
        piece.style.gridRow = `${bounds.minY + 1} / span ${bounds.height}`;
        (piecesLayer || board).append(piece);
      }
    });
  applyBoardCamera();
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
  if (lobbyBtn) lobbyBtn.classList.toggle("hidden", !hasRoom());
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

export function renderGameOverOverlay() {
  const overlay = $("game-over-overlay");
  const title = $("game-over-title");
  const rematch = $("game-over-rematch");
  const strategy = $("game-over-strategy");
  if (!state.battle || !isGameOver() || state.screen !== "battle" || isReplayMode()) {
    overlay.classList.add("hidden");
    state.gameOverShowDetails = false;
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
