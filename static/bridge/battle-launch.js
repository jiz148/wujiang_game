// Battlefield launch contract.
//
// Skirmish lobbies, tutorials, quick AI and campaign sieges all open the same
// grid battle. Each host already decided the roster and where the player should
// land when the fight ends. The room carries that contract; this module is the
// only place the UI reads it.

import { state } from '../core/state.js';
import { setScreen } from '../core/ui.js';
import { leaveRoomView } from '../tactical/session.js';

const LAUNCH_DEFAULTS = {
  campaign: {
    source: "campaign",
    returnFlow: "campaign",
    allowLobby: false,
    allowRematch: false,
    allowRosterEdit: false,
  },
  skirmish: {
    source: "skirmish",
    returnFlow: "skirmish",
    allowLobby: true,
    allowRematch: true,
    allowRosterEdit: true,
  },
  tutorial: {
    source: "tutorial",
    returnFlow: "skirmish",
    allowLobby: true,
    allowRematch: true,
    allowRosterEdit: false,
  },
  quick_ai: {
    source: "quick_ai",
    returnFlow: "skirmish",
    allowLobby: true,
    allowRematch: true,
    allowRosterEdit: false,
  },
};

function launchSourceFromRaw(raw = {}, fallback = "skirmish") {
  const explicit = String(raw.source || "").trim();
  if (LAUNCH_DEFAULTS[explicit]) return explicit;
  const kind = String(raw.experience_kind || "").trim();
  if (kind === "strategy_campaign" || kind === "campaign") return "campaign";
  if (LAUNCH_DEFAULTS[kind]) return kind;
  return LAUNCH_DEFAULTS[fallback] ? fallback : "skirmish";
}

export function normalizeBattleLaunch(raw = {}, fallback = "skirmish") {
  const source = launchSourceFromRaw(raw, fallback);
  const defaults = LAUNCH_DEFAULTS[source];
  const allow = (snake, camel, fallbackValue) => {
    if (raw[snake] != null) return Boolean(raw[snake]);
    if (raw[camel] != null) return Boolean(raw[camel]);
    return fallbackValue;
  };
  return {
    source: defaults.source,
    returnFlow: String(raw.return_flow || raw.returnFlow || defaults.returnFlow),
    allowLobby: allow("allow_lobby", "allowLobby", defaults.allowLobby),
    allowRematch: allow("allow_rematch", "allowRematch", defaults.allowRematch),
    allowRosterEdit: allow("allow_roster_edit", "allowRosterEdit", defaults.allowRosterEdit),
    campaignId: raw.campaign_id ?? raw.campaignId ?? null,
    battleId: String(raw.battle_id || raw.battleId || ""),
  };
}

export function rememberBattleLaunch(raw = {}, fallback = "skirmish") {
  state.battleLaunch = normalizeBattleLaunch(raw, fallback);
  return state.battleLaunch;
}

export function adoptBattleLaunchFromRoom(room) {
  if (!room) {
    return state.battleLaunch;
  }
  state.battleLaunch = normalizeBattleLaunch(room.launch_context || room, room.experience_kind || "skirmish");
  return state.battleLaunch;
}

export function currentBattleLaunch() {
  if (state.room?.launch_context) {
    return normalizeBattleLaunch(state.room.launch_context, state.room.experience_kind);
  }
  if (state.room) {
    return normalizeBattleLaunch(state.room, state.room.experience_kind || "skirmish");
  }
  if (state.battleLaunch) {
    return normalizeBattleLaunch(state.battleLaunch);
  }
  return normalizeBattleLaunch({ source: state.homeFlow === "campaign" ? "campaign" : "skirmish" });
}

export function isCampaignBattleLaunch(launch = currentBattleLaunch()) {
  return launch?.source === "campaign";
}

/**
 * Leave the grid battle and return to the host that opened it.
 *
 * Campaign fights never drop into the skirmish room lobby: the roster and
 * board were already decided on the map, so the only destination is the war
 * room. Skirmish fights keep the room so players can rematch or leave seats.
 */
export function exitBattle() {
  const launch = currentBattleLaunch();
  if (!launch.allowLobby) {
    leaveRoomView();
    state.battleLaunch = launch.source === "campaign" ? launch : null;
  }
  if (launch.returnFlow) {
    state.homeFlow = launch.returnFlow;
  }
  if (launch.source === "campaign") {
    state.strategyMessage = state.strategyMessage || "已返回战役。";
  }
  setScreen("draft");
}
