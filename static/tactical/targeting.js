// Target selection, range preview and action legality on the grid.
import { allUnits, backstepFollowUpTargetIds, backstepSelectionCanComplete, canInteract, currentRespawnPrompt, isChainMode, isGameOver, isRespawnMode, selectedUnit, stagedBackstepRetreatCell, stagedTarget, unitById, unitFootprintCellsAt, unitOccupiedCells, unitsAtCell, unitsCanOverlapOnBoard, viewerPlayerId, viewerTeamId } from '../core/net.js';
import { state } from '../core/state.js';
import { effectiveProfileName } from '../platform/auth.js';
import { fieldEffects, hoveredAction, positionKey, positionsToSet, selectedAction, targetIdsToSet, trimNumber } from '../tactical/vfx.js';

function viewerOwnsUnit(unit) {
  return Boolean(unit) && viewerTeamId() !== null && unit.player_id === viewerTeamId();
}

function actingSideCanSeeUnit(unit) {
  const actor = selectedUnit();
  return Boolean(unit)
    && ((viewerOwnsUnit(unit)) || (viewerPlayerId() === null && actor && actor.player_id === unit.player_id));
}

export function unitIsSelectableTarget(unit) {
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

export function sameCell(left, right) {
  return Boolean(left && right) && left.x === right.x && left.y === right.y;
}

export function patternSelection(action) {
  const mode = action?.preview?.selection?.mode;
  return mode === "pattern_cells" || mode === "choice_pattern" ? action.preview.selection : null;
}

export function patternSelectionIsOrdered(action) {
  return Boolean(patternSelection(action)?.ordered);
}

export function choicePatternSelection(action) {
  return action?.preview?.selection?.mode === "choice_pattern" ? action.preview.selection : null;
}

export function attackChoicePatternSelection(action) {
  return action?.kind === "attack" ? choicePatternSelection(action) : null;
}

export function movePathSelection(action) {
  return action?.preview?.selection?.mode === "move_path" ? action.preview.selection : null;
}

export function multiUnitSelection(action) {
  return action?.preview?.selection?.mode === "multi_unit" ? action.preview.selection : null;
}

export function statCellSelection(action) {
  return action?.preview?.selection?.mode === "stat_cells" ? action.preview.selection : null;
}

export function bodyDirectionSelection(action) {
  return action?.preview?.selection?.mode === "body_direction" ? action.preview.selection : null;
}

export function reviveUnitCellSelection(action) {
  return action?.preview?.selection?.mode === "revive_unit_cell" ? action.preview.selection : null;
}

export function normalizedPatternCells(cells = []) {
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

export function stagedPatternCells(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !patternSelection(action)) return [];
  return normalizedPatternCells(Array.isArray(state.stagedPayload?.cells) ? state.stagedPayload.cells : []);
}

export function stagedPatternChoiceCode(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !choicePatternSelection(action)) return "";
  return String(state.stagedPayload?.choiceCode || "").trim();
}

export function setStagedPatternChoice(choiceCode) {
  const action = selectedAction();
  if (!choicePatternSelection(action)) return;
  const next = String(choiceCode || "").trim();
  const keepCells = next && next === stagedPatternChoiceCode(action) ? stagedPatternCells(action) : [];
  state.stagedPayload = next || keepCells.length
    ? { ...(next ? { choiceCode: next } : {}), ...(keepCells.length ? { cells: keepCells } : {}) }
    : null;
}

export function setStagedPatternCells(cells) {
  const normalized = normalizedPatternCells(cells);
  const choiceCode = stagedPatternChoiceCode();
  state.stagedPayload = normalized.length || choiceCode
    ? { ...(choiceCode ? { choiceCode } : {}), ...(normalized.length ? { cells: normalized } : {}) }
    : null;
}

export function stagedMovePath(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !movePathSelection(action)) return [];
  return normalizedMovePath(Array.isArray(state.stagedPayload?.path) ? state.stagedPayload.path : []);
}

export function setStagedMovePath(path) {
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

export function stagedMultiTargetIds(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !multiUnitSelection(action)) return [];
  const explicit = Array.isArray(state.stagedPayload?.targetUnitIds) ? state.stagedPayload.targetUnitIds : [];
  return normalizedTargetIds(explicit);
}

export function setStagedMultiTargetIds(ids) {
  const normalized = normalizedTargetIds(ids);
  state.stagedPayload = normalized.length ? { targetUnitIds: normalized } : null;
}

export function stagedStatName(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !statCellSelection(action)) return "";
  return String(state.stagedPayload?.statName || "").trim();
}

export function setStagedStatName(statName) {
  const cells = stagedStatCells();
  const next = String(statName || "").trim();
  state.stagedPayload = next || cells.length ? { statName: next, cells } : null;
}

export function stagedStatCells(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !statCellSelection(action)) return [];
  return normalizedPatternCells(Array.isArray(state.stagedPayload?.cells) ? state.stagedPayload.cells : []);
}

export function setStagedStatCells(cells) {
  const statName = stagedStatName();
  const normalized = normalizedPatternCells(cells);
  state.stagedPayload = statName || normalized.length ? { statName, cells: normalized } : null;
}

export function statCellRequired(action) {
  return Number(statCellSelection(action)?.required_cells || 0);
}

function statCellSelectionCanComplete(action, chosen = stagedStatCells(action)) {
  const selection = statCellSelection(action);
  if (!selection) return false;
  const statName = stagedStatName(action);
  const validStats = new Set((selection.stats || []).map((entry) => String(entry.code || "")));
  return Boolean(statName && validStats.has(statName) && chosen.length === statCellRequired(action));
}

export function stagedBodyCells(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !bodyDirectionSelection(action)) return [];
  return normalizedPatternCells(Array.isArray(state.stagedPayload?.cells) ? state.stagedPayload.cells : []);
}

export function setStagedBodyCells(cells) {
  const direction = stagedBodyDirection();
  const normalized = normalizedPatternCells(cells);
  state.stagedPayload = normalized.length || direction ? { cells: normalized, ...(direction ? { direction } : {}) } : null;
}

export function stagedBodyDirection(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !bodyDirectionSelection(action)) return null;
  const direction = state.stagedPayload?.direction;
  if (!direction || direction.dx == null || direction.dy == null) return null;
  return { dx: Number(direction.dx), dy: Number(direction.dy) };
}

export function setStagedBodyDirection(direction) {
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

export function stagedReviveUnitId(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !reviveUnitCellSelection(action)) return "";
  return String(state.stagedPayload?.reviveUnitId || "").trim();
}

export function stagedReviveCell(action = selectedAction()) {
  if (!action || state.selectedActionCode !== action.code || !reviveUnitCellSelection(action)) return null;
  const cell = state.stagedPayload?.cell;
  if (!cell || cell.x == null || cell.y == null) return null;
  return { x: Number(cell.x), y: Number(cell.y) };
}

export function setStagedReviveUnitId(unitId) {
  const action = selectedAction();
  if (!reviveUnitCellSelection(action)) return;
  const next = String(unitId || "").trim();
  const current = next && next === stagedReviveUnitId(action) ? stagedReviveCell(action) : null;
  state.stagedPayload = next || current ? { ...(next ? { reviveUnitId: next } : {}), ...(current ? { cell: current } : {}) } : null;
}

export function setStagedReviveCell(cell) {
  const reviveUnitId = stagedReviveUnitId();
  const next = cell && cell.x != null && cell.y != null ? { x: Number(cell.x), y: Number(cell.y) } : null;
  state.stagedPayload = reviveUnitId || next ? { ...(reviveUnitId ? { reviveUnitId } : {}), ...(next ? { cell: next } : {}) } : null;
}

function reviveCandidate(action, unitId = stagedReviveUnitId(action)) {
  const id = String(unitId || "").trim();
  return (reviveUnitCellSelection(action)?.candidates || []).find((entry) => String(entry.id || "") === id) || null;
}

export function reviveSelectionCells(action) {
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

export function movePathAnchorForClickedCell(action, clickedCell, chosen = stagedMovePath(action)) {
  if (!movePathSelection(action)) return null;
  const candidates = nextMovePathCells(action, chosen);
  const direct = candidates.find((anchor) => sameCell(anchor, clickedCell));
  if (direct) return direct;
  return candidates.find((anchor) => unitFootprintCellsAt(selectedUnit(), anchor).some((cell) => sameCell(cell, clickedCell))) || null;
}

export function movePathIndexForClickedCell(action, clickedCell, chosen = stagedMovePath(action)) {
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

export function patternSelectionCanComplete(action, chosen = stagedPatternCells(action)) {
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

export function fieldEffectsByCell() {
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

export function fieldEffectMarker(effect) {
  const marker = String(effect?.board_marker || effect?.name || "").trim();
  return marker ? marker.slice(0, 2) : "场";
}

export function actionManaLabel(action) {
  if (action.kind !== "skill") return "";
  if (action.mana_cost_text) return action.mana_cost_text;
  return action.mana_cost > 0 ? `费 ${trimNumber(action.mana_cost)} 魔` : "不费魔";
}

export function actionTierLabel(action) {
  if (action.kind !== "skill") return "基础动作";
  if (action.timing !== "active") return "被动技能";
  if (action.max_uses_per_battle === 1) return "大招";
  return "普通技能";
}

export function actionLimitLabel(action) {
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

export function currentPreview() {
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

export function actionNeedsTarget(action) {
  if (!action) return false;
  if (isChainMode()) return Boolean(action.preview?.requires_target);
  if (action.kind === "move" || action.kind === "attack") return true;
  return Boolean(action.preview?.requires_target);
}

export function hasCancelableTargetSelection() {
  if (!canInteract() || isRespawnMode()) return false;
  const action = selectedAction();
  return Boolean(action && actionNeedsTarget(action));
}

export function isBoardTargetSelectionActive() {
  if (!canInteract()) return false;
  if (isRespawnMode()) return true;
  const action = selectedAction();
  return Boolean(action && actionNeedsTarget(action));
}

export function canCompleteTargetSelection() {
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

export function actionLabel(action) {
  if (action.kind === "move") return "\u79fb";
  if (action.kind === "attack") return "\u653b";
  if (action.kind === "chain_skip") return "\u5426";
  if (action.action_name) return action.action_name.length <= 2 ? action.action_name : action.action_name.slice(0, 2);
  return action.name.length <= 2 ? action.name : action.name.slice(0, 2);
}

export function actionTitle(action) {
  return action.action_name || action.name;
}

export function actionTimingLabel(action) {
  if (action.kind === "chain_skip") return "放弃";
  const mapping = {
    active: "速度1",
    passive: "速度2",
    reaction: "速度2",
    instant: "速度3",
  };
  return mapping[action.timing] || `速度${action.chain_speed}`;
}

export function currentRoomSeat() {
  return state.room?.seats?.find((seat) => seat.player_id === viewerPlayerId()) || null;
}

export function controllerTypeLabel(seat) {
  if (!seat) return "";
  if (seat.is_ai || seat.controller_type === "ai") return "AI";
  if (seat.is_human || seat.controller_type === "human") return "真人";
  return "开放";
}

export function seatIdentityLabel(seat) {
  if (!seat) return "";
  return `席位 ${seat.player_id} · ${seat.team_name || (Number(seat.team_id) === 1 ? "红队" : "蓝队")} · ${controllerTypeLabel(seat)}`;
}

export function editableRoomSeat() {
  const viewerSeat = currentRoomSeat();
  if (!viewerSeat) return null;
  const requestedSeatId = Number(state.roomEditSeatId || viewerSeat.player_id);
  const targetSeat = (state.room?.seats || []).find((seat) => seat.player_id === requestedSeatId) || viewerSeat;
  if (targetSeat.player_id === viewerSeat.player_id) return targetSeat;
  if (state.room?.viewer_is_host && targetSeat.is_ai) return targetSeat;
  return viewerSeat;
}

export function setRoomEditSeat(seatId) {
  state.roomEditSeatId = Number(seatId || 0) || viewerPlayerId();
}

export function seatHeroCount(seat, heroCode) {
  return Number(seat?.hero_counts?.[heroCode] || 0);
}

export function seatHeroTotalCount(seat) {
  return Number(seat?.hero_total_count || 0);
}

export function randomRoomRosterSize(room = state.room) {
  return Math.max(1, Number(room?.random_roster_size || 1));
}

export function randomRoomFallbackSummary(room = state.room) {
  const count = randomRoomRosterSize(room);
  return `开局后各随机分配 ${count} 个不重复武将`;
}

export function sanitizeRandomRosterSizeInput(value) {
  return String(value ?? "").replace(/\D/g, "");
}

export function seatHeroSummary(seat, { randomFallback = false, randomRoom = state.room } = {}) {
  if (randomFallback && seat?.occupied && !seat.hero_summary) return randomRoomFallbackSummary(randomRoom);
  if (!seat) return "";
  if (seat.hero_summary) return seat.hero_summary;
  if (randomFallback && seat.occupied) return randomRoomFallbackSummary(randomRoom);
  return "未选择";
}

export function roomSummaries() {
  return state.rooms || [];
}

function fallbackJoinName() {
  return effectiveProfileName();
}
