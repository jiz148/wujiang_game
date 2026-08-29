// Campaign endpoints and the request helpers around them.
import { confirmDialog } from '../core/dialog.js';
import { $ } from '../core/dom.js';
import { fetchJson, recordProductEvent, recordStrategyConclusionIfNeeded, recordStrategyEventOnce, syncLocation } from '../core/net.js';
import { refreshState, render } from '../core/render.js';
import { FALLBACK_STRATEGY_VARIANTS, state } from '../core/state.js';
import { setScreen } from '../core/ui.js';
import { effectiveProfileName, userLoggedIn } from '../platform/auth.js';
import { createStrategyField, strategyCanResume, strategyFactionCommandPoints, strategyMemberLabel, strategyOfficeLabel } from '../strategic/ui-base.js';
import { isStrategyControlActive, renderStrategyPanel } from '../strategic/workbench.js';
import { loadStoredIdentity, saveStoredIdentity } from '../bridge/campaign-battle.js';

export function clearStrategyState(message = "") {
  state.strategyCampaigns = [];
  state.strategyCampaign = null;
  state.strategyBattleRoom = null;
  state.strategyMessage = message;
}

export function syncStrategyCampaignFromRoomPayload(payload = {}) {
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

export async function refreshStrategyCampaigns({ renderAfter = true } = {}) {
  if (!userLoggedIn()) {
    clearStrategyState("请先登录账号。");
    if (renderAfter && !isStrategyControlActive()) renderStrategyPanel();
    return;
  }
  try {
    const payload = await fetchJson("/api/strategy/campaigns");
    state.strategyCampaigns = payload.campaigns || [];
    state.strategyVariants = Array.isArray(payload.campaign_variants) && payload.campaign_variants.length
      ? payload.campaign_variants
      : FALLBACK_STRATEGY_VARIANTS;
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
    const target = (document.querySelector && document.querySelector(".campaign-screen, .campaign-prep")) || $("strategy-panel");
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

export function focusDraftTarget(selector) {
  const target = document.querySelector ? document.querySelector(selector) : null;
  if (target && typeof target.scrollIntoView === "function") {
    target.scrollIntoView({ block: "start", inline: "nearest" });
  }
}

/**
 * 把操作面板翻到某一模块。
 *
 * 面板此前是屏幕右侧固定的一栏，"聚焦"只能是 scrollIntoView。现在它浮在地图上
 * 并且可以收起，所以聚焦的意思变成了"打开它，并翻到该看的那一页"。
 */
export function openStrategyDock(tab = "") {
  state.strategyDockOpen = true;
  if (tab) state.strategyDockTab = tab;
  renderStrategyPanel();
}

export function focusStrategyCommandPanel() {
  openStrategyDock("city");
}

export function focusStrategySelectedCityCommand() {
  openStrategyDock("city");
}

/** 地图现在是整屏，没有可滚过去的位置；把浮层收起来让它露出来就够了。 */
export function focusStrategyMapStage() {
  state.strategyDockOpen = false;
  renderStrategyPanel();
}

export function exitStrategyCampaignView() {
  state.strategyCampaign = null;
  state.strategySelectedCityId = "";
  state.strategySelectedCampaignId = 0;
  state.strategyMessage = "";
  renderStrategyPanel();
  focusDraftTarget("#strategy-panel");
}

/** 打开新建战役流程。名字与种子每次都重新起头，免得沿用上一局的残留。 */
export function openStrategyCampaignCreator() {
  state.strategyCreateOpen = true;
  state.strategyCreateStep = 0;
  state.strategyName = "英灵城邦";
  state.strategySeed = String(Math.max(1, Math.floor(Date.now() / 1000) % 999983));
  state.strategyVariantId = state.strategyVariantId || "classic_frontier";
  state.strategyMessage = "";
  renderStrategyPanel();
}

export function closeStrategyCampaignCreator() {
  state.strategyCreateOpen = false;
  state.strategyCreateStep = 0;
  renderStrategyPanel();
}

export function setStrategyCreateStep(step) {
  state.strategyCreateStep = Math.max(0, Math.min(2, Number(step) || 0));
  renderStrategyPanel();
}

export async function createStrategyCampaign() {
  const name = String(state.strategyName || "英灵城邦").trim() || "英灵城邦";
  const seed = Number.parseInt(state.strategySeed || "1", 10) || 1;
  const payload = await strategyPost("/api/strategy/campaigns/create", {
    name,
    seed,
    variant_id: state.strategyVariantId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCreateOpen = false;
  state.strategyCreateStep = 0;
  state.strategyCampaign = payload.campaign;
  recordProductEvent("strategy_campaign_create", {
    campaign_id: String(payload.campaign.id),
    scenario_id: payload.campaign.world?.campaign_contract?.id || "legacy_sandbox",
    variant_id: payload.campaign.world?.campaign_contract?.opening_variant?.id || "legacy_default",
    content_version: payload.campaign.world?.campaign_contract?.content_version || "legacy",
    balance_version: payload.campaign.world?.campaign_contract?.balance_version || "legacy",
  });
  state.strategyMessage = "战役已创建。先在开局准备里选定各自的出身，房主锁定后战役开始。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
  focusStrategyWarRoom();
}

export async function joinStrategyCampaignByCode() {
  const joinCode = String(state.strategyJoinCode || "").trim().toUpperCase();
  if (!joinCode) {
    state.strategyMessage = "请输入战役加入码。";
    renderStrategyPanel();
    return;
  }
  const payload = await strategyPost("/api/strategy/campaigns/join", {
    join_code: joinCode,
    join_host_faction: Boolean(state.strategyJoinHostFaction),
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyJoinCode = "";
  state.strategyJoinHostFaction = false;
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = "已加入战役大厅。等待房主锁定初始玩家。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
  focusStrategyWarRoom();
}

export async function chooseStrategyHeroPath(heroCode, path, targetFactionId = "") {
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

export async function updateStrategyCampaignGuide(action) {
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

export async function lockStrategyCampaign(campaignId) {
  const payload = await strategyPost("/api/strategy/campaigns/lock", { campaign_id: campaignId });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  recordProductEvent("strategy_campaign_lock", { campaign_id: String(payload.campaign.id) });
  state.strategyMessage = strategyCanResume(payload.campaign)
    ? "初始玩家已锁定，空席由 AI 接管；真人可异步进入并提交月度计划。"
    : "战役仍在大厅阶段。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
  focusStrategyWarRoom();
}

export async function rotateStrategyJoinCode(campaignId) {
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

export async function revokeStrategyJoinCode(campaignId) {
  const payload = await strategyPost("/api/strategy/campaigns/revoke-join-code", { campaign_id: campaignId });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = "当前加入码已撤销；已加入成员仍可通过自己的账号恢复战役。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

export async function enterStrategyCampaign(campaignId) {
  const payload = await strategyPost("/api/strategy/campaigns/enter", { campaign_id: campaignId });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  recordProductEvent("strategy_campaign_enter", { campaign_id: String(payload.campaign.id) });
  state.strategyMessage = payload.campaign.status === "archived"
    ? "已按当前账号恢复归档战役；地图、复盘和历史战斗均为只读。"
    : strategyCanResume(payload.campaign)
      ? "战役已恢复；可以异步下令并提交本月计划。"
      : "已进入战役大厅，等待房主锁定初始席位。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
  focusStrategyWarRoom();
}

export async function leaveStrategyCampaign(campaignId) {
  const payload = await strategyPost("/api/strategy/campaigns/leave", { campaign_id: campaignId });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  if (state.strategyCampaign?.id === campaignId) {
    state.strategyCampaign.resume = payload.resume;
  }
  state.strategyMessage = "已标记为离线；其他玩家会看到你不在，房主可以不等你推进月份。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

export async function deleteStrategyCampaign(campaignId) {
  const confirmed = await confirmDialog({
    title: "删除战役",
    body: "这局战役连同它的地图、月度记录与战斗存档会被永久删除，无法恢复。若只是想留着复盘，请用归档。",
    confirmLabel: "永久删除",
    tone: "danger",
  });
  if (!confirmed) return;
  const payload = await strategyPost("/api/strategy/campaigns/delete", { campaign_id: campaignId });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  // 删掉的正是当前打开的那一局时，先退回战役列表，否则整屏还在渲染一个已经
  // 不存在的战役。
  if (state.strategyCampaign?.id === campaignId) state.strategyCampaign = null;
  state.strategyMessage = "战役已删除。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

export async function setStrategyMonthReady(ready) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/month-ready", {
    campaign_id: state.strategyCampaign.id,
    ready: Boolean(ready),
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = ready
    ? "本月计划已提交；结算前可以撤回并继续修改。"
    : "已撤回本月提交，可以继续修改军令。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

export async function closeStrategyMonthDeadline() {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/close-month-deadline", {
    campaign_id: state.strategyCampaign.id,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  const proxyNames = (payload.campaign.resume?.proxy_ai_user_ids || [])
    .map((userId) => strategyMemberLabel(payload.campaign, userId));
  state.strategyMessage = proxyNames.length
    ? `本月截止已关闭：${proxyNames.join("、")}由 AI 临时托管。`
    : "所有真人均已提交，本月无需临时托管。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

export async function requestStrategyOfficeChange(requestType, officeId, targetUserId = 0) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/office-change/request", {
    campaign_id: state.strategyCampaign.id,
    request_type: requestType,
    office_id: officeId,
    target_user_id: Number(targetUserId || 0),
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = requestType === "handover"
    ? "官职交接请求已送达；对方确认前权限保持不变。"
    : "撤换请求已送达；在任玩家确认前不会失去权限。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

export async function respondStrategyOfficeChange(requestId, accept) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/office-change/respond", {
    campaign_id: state.strategyCampaign.id,
    request_id: Number(requestId),
    accept: Boolean(accept),
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyActiveOfficeId = "";
  state.strategyMessage = accept ? "官职变更已由双方确认并立即生效。" : "已拒绝官职变更，现有权限保持不变。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

export async function grantStrategyOfficeTakeover(officeId, delegateUserId) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/office-takeover/grant", {
    campaign_id: state.strategyCampaign.id,
    office_id: officeId,
    delegate_user_id: Number(delegateUserId),
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyMessage = "空缺职位已授权当月代管；新月份会自动恢复空缺。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

export async function revokeStrategyOfficeTakeover(takeoverId) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/office-takeover/revoke", {
    campaign_id: state.strategyCampaign.id,
    takeover_id: Number(takeoverId),
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyActiveOfficeId = "";
  state.strategyMessage = "临时代管已结束，职位恢复空缺。";
  await refreshStrategyCampaigns({ renderAfter: false });
  render();
}

export async function advanceStrategyMonth() {
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

export async function continueStrategySandbox() {
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

export async function archiveStrategyCampaign() {
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

export async function setStrategyDefenseHero(heroCode) {
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

export async function setStrategyBattleDefenseHero(battleId, heroCode) {
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

async function resolveStrategicBattle(sourceKind, sourceEntityId, resolutionMode) {
  if (!state.strategyCampaign || !sourceKind || !sourceEntityId) return;
  const payload = await strategyPost("/api/strategy/campaigns/resolve-strategic-battle", {
    campaign_id: state.strategyCampaign.id,
    source_kind: sourceKind,
    source_entity_id: sourceEntityId,
    resolution_mode: resolutionMode || "quick",
    issuer_office_id: state.strategyActiveOfficeId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyBattleRoom = payload.battle_room || null;
  if (payload.battle_room?.player_token) {
    saveStoredIdentity(payload.battle_room.room_id, payload.battle_room.player_token, effectiveProfileName());
  }
  state.strategyMessage = payload.battle_room
    ? (resolutionMode === "watch_ai" ? "战略接战已进入 AI 观战房间。" : "战略接战已创建真实格子战房间。")
    : "战略接战已快速结算，军队与战场状态已经回写。";
  render();
}

export async function resolveWorldCrisisShowdown(resolutionMode) {
  if (!state.strategyCampaign) return;
  const payload = await strategyPost("/api/strategy/campaigns/resolve-world-crisis-showdown", {
    campaign_id: state.strategyCampaign.id,
    resolution_mode: resolutionMode || "quick",
    issuer_office_id: state.strategyActiveOfficeId,
  });
  if (!payload) {
    renderStrategyPanel();
    return;
  }
  state.strategyCampaign = payload.campaign;
  state.strategyBattleRoom = payload.battle_room || null;
  if (payload.battle_room?.player_token) {
    saveStoredIdentity(
      payload.battle_room.room_id,
      payload.battle_room.player_token,
      effectiveProfileName()
    );
  }
  recordStrategyConclusionIfNeeded(payload.campaign);
  state.strategyMessage = payload.battle_room
    ? (resolutionMode === "watch_ai"
      ? "北境决战已进入 AI 观战房间。"
      : "北境决战已创建真实格子战房间。")
    : "北境决战已结算，寒潮结果与战役主线已经回写。";
  render();
}

export function createStrategicBattleResolver(campaign, sourceKind, sourceEntityId, canResume, enabled = true) {
  const controls = document.createElement("div");
  controls.className = "strategy-campaign-actions strategy-battle-resolver";
  const mode = document.createElement("select");
  [["quick", "快速结算"], ["manual", "手动格子战"], ["ai_auto", "AI 自动战斗"], ["watch_ai", "观看 AI 战斗"]].forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    mode.append(option);
  });
  const existing = (campaign?.world?.pending_battles || []).find((battle) => (
    battle.status === "pending" && battle.source_kind === sourceKind && battle.source_entity_id === sourceEntityId
  ));
  const engage = document.createElement("button");
  engage.type = "button";
  engage.className = "primary";
  engage.textContent = existing ? "战斗已待处理" : "进入战斗 · 不额外消耗军令";
  engage.disabled = state.strategyBusy || !canResume || !enabled || Boolean(existing);
  engage.addEventListener("click", () => resolveStrategicBattle(sourceKind, sourceEntityId, mode.value));
  controls.append(createStrategyField("处理方式", mode), engage);
  return controls;
}

export async function queueStrategyAction(actionType, actionPayload) {
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
  render();
}

export async function openStrategyBattleRoom(roomInfo = {}) {
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

export async function restartStrategyBattleFromSnapshot(roomId) {
  if (state.strategyBusy) return;
  state.strategyBusy = true;
  state.strategyMessage = "正在从战前不可变快照安全重开……";
  renderStrategyPanel();
  try {
    const payload = await fetchJson("/api/strategy/campaigns/restart-battle-from-snapshot", {
      method: "POST",
      body: JSON.stringify({room_id: roomId}),
    });
    state.strategyCampaign = payload.campaign;
    state.strategyBattleRoom = payload.battle_room;
    state.strategyBattleRecovery = null;
    state.strategyMessage = payload.battle_room?.recovery?.message || "已从战前快照安全重开。";
    if (payload.battle_room?.player_token) {
      saveStoredIdentity(roomId, payload.battle_room.player_token, effectiveProfileName());
    }
    await openStrategyBattleRoom(payload.battle_room || {room_id: roomId});
  } catch (error) {
    state.strategyMessage = error.error || "无法从战前快照安全重开。";
    renderStrategyPanel();
  } finally {
    state.strategyBusy = false;
  }
}

export function returnToStrategyCampaign() {
  if (!state.strategyCampaign) return;
  state.strategyMessage = state.strategyMessage || "已返回战役。";
  setScreen("draft", { renderAfter: false });
  render();
  const panel = $("strategy-panel");
  if (panel && typeof panel.scrollIntoView === "function") {
    panel.scrollIntoView({ block: "start" });
  }
}
