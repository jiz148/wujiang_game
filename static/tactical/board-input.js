// Pointer and keyboard interaction on the battle board.
import { $ } from '../core/dom.js';
import { backstepFollowUpTargetIds, canInteract, currentRespawnPrompt, inspectBoardUnit, isChainMode, isRespawnMode, setStagedBackstepRetreatCell, stagedBackstepRetreatCell, unitById } from '../core/net.js';
import { render } from '../core/render.js';
import { state, ui } from '../core/state.js';
import { keyboardHelpIsOpen, syncModalIsolation } from '../core/ui.js';
import { closeProfileModal, profileModalVisible } from '../platform/auth.js';
import { attackTargetIdAtCell, completeTutorialUnitSelection, explainInvalidBoardChoice, performAction } from '../tactical/room-api.js';
import { clearActionSelection } from '../tactical/session.js';
import { actionNeedsTarget, attackChoicePatternSelection, bodyDirectionSelection, currentPreview, movePathAnchorForClickedCell, movePathIndexForClickedCell, movePathSelection, multiUnitSelection, patternSelection, patternSelectionIsOrdered, reviveSelectionCells, reviveUnitCellSelection, sameCell, setStagedBodyCells, setStagedMovePath, setStagedMultiTargetIds, setStagedPatternCells, setStagedReviveCell, setStagedStatCells, stagedAttackActionPayload, stagedBodyCells, stagedMovePath, stagedMultiTargetIds, stagedPatternCells, stagedPatternChoiceCode, stagedReviveCell, stagedReviveUnitId, stagedStatCells, statCellRequired, statCellSelection, unitIsSelectableTarget } from '../tactical/targeting.js';
import { positionKey, positionsToSet, selectedAction, targetIdsToSet } from '../tactical/vfx.js';

export function onBoardClick(x, y, occupant) {
  if (!canInteract()) {
    clearActionSelection();
    inspectBoardUnit(occupant);
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
    inspectBoardUnit(occupant, { adoptIfControllable: true });
    clearActionSelection();
    if (occupant) completeTutorialUnitSelection(occupant.id);
    render();
    return;
  }

  if (occupant) inspectBoardUnit(occupant, { openInfo: false });

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
    if (!canUseCell && !canUseUnit) {
      explainInvalidBoardChoice(action, occupant);
      return;
    }
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
      ...stagedAttackActionPayload(action),
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
      ...stagedAttackActionPayload(action),
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

export function openKeyboardHelp() {
  const panel = $("keyboard-help");
  if (!panel) return;
  ui.keyboardHelpReturnFocus = document.activeElement;
  panel.classList.remove("hidden");
  panel.setAttribute("aria-hidden", "false");
  syncModalIsolation();
  $("close-keyboard-help")?.focus();
}

export function closeKeyboardHelp() {
  const panel = $("keyboard-help");
  if (!panel) return;
  panel.classList.add("hidden");
  panel.setAttribute("aria-hidden", "true");
  syncModalIsolation();
  ui.keyboardHelpReturnFocus?.focus?.();
  ui.keyboardHelpReturnFocus = null;
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

export function focusMainContent(event) {
  event?.preventDefault?.();
  const main = $("main-content");
  if (!main) return;
  main.scrollIntoView({ block: "start" });
  main.focus({ preventScroll: true });
}

function activeModalDialog() {
  for (const id of ["keyboard-help", "profile-modal"]) {
    const dialog = $(id);
    if (dialog && !dialog.classList.contains("hidden")) return dialog;
  }
  return null;
}

function trapDialogFocus(event, dialog) {
  if (event.key !== "Tab") return false;
  const controls = [...dialog.querySelectorAll(
    "button:not([disabled]):not(.hidden), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex='-1'])",
  )].filter((control) => control.getClientRects().length > 0);
  if (!controls.length) return false;
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) last.focus();
  else if (!event.shiftKey && document.activeElement === last) first.focus();
  else return false;
  event.preventDefault();
  return true;
}

export function handleBattleKeyboard(event) {
  const activeDialog = activeModalDialog();
  if (activeDialog) {
    if (trapDialogFocus(event, activeDialog)) return;
    if (event.key === "Escape") {
      event.preventDefault();
      if (activeDialog.id === "keyboard-help") closeKeyboardHelp();
      else closeProfileModal();
    }
    return;
  }
  if (eventComesFromTextControl(event)) return;
  if (event.key === "?" || (event.key === "/" && event.shiftKey)) {
    event.preventDefault();
    if (keyboardHelpIsOpen()) closeKeyboardHelp();
    else openKeyboardHelp();
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
