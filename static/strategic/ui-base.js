// Campaign shell: map, panels and shared campaign widgets.
import { createHint } from '../core/components.js';
import { $ } from '../core/dom.js';
import { fetchJson, syncLocation } from '../core/net.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { userLoggedIn } from '../platform/auth.js';
import { closeStrategyMonthDeadline, focusStrategySelectedCityCommand, grantStrategyOfficeTakeover, openStrategyBattleRoom, queueStrategyAction, requestStrategyOfficeChange, respondStrategyOfficeChange, restartStrategyBattleFromSnapshot, revokeStrategyJoinCode, revokeStrategyOfficeTakeover, rotateStrategyJoinCode, setStrategyMonthReady } from '../strategic/api.js';
import { appendTextLine, createStrategyHeroDeploymentPicker, renderStrategyPanel, strategyCityRebellionForce, strategyCityStateLabels, strategyDeployableHeroes, strategyHeroDeploymentLimit, strategyNumber, strategyQueuedActionLabel } from '../strategic/workbench.js';
import { actionTitle, applyRoomPayload, syncSelectedUnitAfterStateChange } from '../bridge/campaign-battle.js';

export function connectionStatusLabel(status) {
  return ({online: "在线", unstable: "连接不稳", offline: "已掉线", ai: "AI 在线", open: "开放"})[status] || "未知";
}

export function readyStateLabel(seat) {
  if (!seat?.occupied) return "未占用";
  if (seat.is_ai) return "自动准备";
  return seat.ready ? "已准备" : "未准备";
}

function recentMatchRosterText(match) {
  return globalThis.WujiangHomeUi?.rosterText(match) || "";
}

export function renderRecentMatches() {
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

export const STRATEGY_OFFICE_LABELS = {
  lord: "主公",
  grand_general: "大将军",
  general: "将军",
  governor: "城主",
};

export const STRATEGY_DUTY_LABELS = {
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

export const STRATEGY_OFFICE_STATUS_LABELS = {
  pending: "待处理",
  accepted: "已接受",
  completed: "已完成",
  rejected: "已拒绝",
  cancelled: "已撤销",
};

export function strategyControlledOffices(campaign = state.strategyCampaign) {
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

export function strategyControlledHero(campaign = state.strategyCampaign) {
  const userId = Number(state.authUser?.id || 0);
  return (campaign?.world?.strategic_hero_pool || []).find((hero) => (
    hero.controller_type === "player" && Number(hero.controller_user_id || 0) === userId
  )) || null;
}

export function strategyActiveOffice(campaign = state.strategyCampaign) {
  const offices = strategyControlledOffices(campaign);
  let active = offices.find((office) => office.id === state.strategyActiveOfficeId);
  if (!active) {
    active = offices.find((office) => office.office_type === "lord") || offices[0] || null;
    state.strategyActiveOfficeId = active?.id || "";
  }
  return active;
}

export function strategyOfficeLabel(office, campaign = state.strategyCampaign) {
  if (!office) return "未任职";
  const base = STRATEGY_OFFICE_LABELS[office.office_type] || office.office_type;
  if (office.office_type === "governor") {
    const cityId = (office.managed_entity_ids || [])[0];
    const city = (campaign?.world?.cities || []).find((item) => item.id === cityId);
    const label = city ? `${city.name}城主` : base;
    return office.holder_type === "temporary_player" ? `${label}（临时代管）` : label;
  }
  const peers = (campaign?.world?.offices || []).filter((item) => (
    item.faction_id === office.faction_id && item.office_type === office.office_type && item.status === "active"
  ));
  const label = peers.length > 1 ? `${base} ${peers.findIndex((item) => item.id === office.id) + 1}` : base;
  return office.holder_type === "temporary_player" ? `${label}（临时代管）` : label;
}

export function strategyOfficeManagedCities(campaign, office) {
  const managed = new Set(office?.managed_entity_ids || []);
  return (campaign?.world?.cities || []).filter((city) => managed.has(city.id));
}

export function strategyFaction(campaign = state.strategyCampaign) {
  const hero = strategyControlledHero(campaign);
  const office = strategyActiveOffice(campaign);
  const member = strategyMember(campaign);
  const factionId = hero?.faction_id || office?.faction_id || member?.faction_id;
  return (campaign?.world?.factions || []).find((faction) => faction.id === factionId) || null;
}

export function strategyFactionCommandPoints(campaign = state.strategyCampaign, faction = strategyFaction(campaign)) {
  return campaign?.command_points_by_faction?.[faction?.id] || { maximum: 4, used: 0, remaining: 4 };
}

export function strategyMonthlyCycle(campaign = state.strategyCampaign, faction = strategyFaction(campaign)) {
  return campaign?.world?.monthly_cycle?.[faction?.id] || {
    previous_month: null,
    must_handle: [],
    advance_forecast: { cities: [] },
    planned_actions: [],
  };
}

export function strategyOfficeCoordination(campaign = state.strategyCampaign, faction = strategyFaction(campaign)) {
  return campaign?.world?.office_coordination?.[faction?.id] || null;
}

export function strategyCommandCost(actionType, payload = {}) {
  if (["send_office_request", "request_registered_units", "approve_registered_unit_request", "assign_strategic_hero_duty"].includes(actionType)) return 0;
  if (actionType === "declare_attack" || actionType === "rebellion_battle") return 2;
  if (actionType === "peaceful_integration") return 2;
  if (actionType === "rebellion_action" && (payload.rebellion_action_id || payload.action_id) === "suppress") return 2;
  return 1;
}

export function strategyCanAffordCommand(campaign, faction, actionType, payload = {}, actionKey = "") {
  let available = strategyFactionCommandPoints(campaign, faction).remaining;
  if (actionKey) {
    const existing = (campaign?.queued_actions || []).find((action) => (
      action.faction_id === faction?.id && action.action_type === actionType && action.action_key === actionKey
    ));
    if (existing) available += existing.command_cost || strategyCommandCost(existing.action_type, existing.payload || {});
  }
  return available >= strategyCommandCost(actionType, payload);
}

export function strategyPendingStoryEvent(campaign, faction) {
  return (campaign?.world?.story_events || []).find((event) => event.faction_id === faction?.id && event.status === "pending") || null;
}

function strategyCommandDraft(campaign, city) {
  const key = `${campaign?.id || "campaign"}:${city?.id || "city"}`;
  if (!state.strategyCommandDrafts[key]) state.strategyCommandDrafts[key] = {};
  return state.strategyCommandDrafts[key];
}

export function strategyAttackTargetsForCity(campaign, sourceCity, factionId) {
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

export function strategyFactionName(campaign, factionId) {
  const faction = (campaign?.world?.factions || []).find((item) => item.id === factionId);
  return faction?.name || factionId || "未归属";
}

export function strategyFactionById(campaign, factionId) {
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

export function strategyMemberLabel(campaign, userId) {
  const member = (campaign?.members || []).find((item) => Number(item.user_id) === Number(userId));
  return member?.username || `用户 ${userId}`;
}

export function strategyMemberIsAi(member) {
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

export function strategyMissingInitialPlayerLabels(campaign) {
  return (campaign?.resume?.missing_initial_user_ids || []).map((userId) => strategyMemberLabel(campaign, userId));
}

export function renderStrategyMembersPanel(current, campaign, isOwner) {
  const members = campaign?.members || [];
  if (!members.length) return;

  const title = document.createElement("h4");
  title.textContent = "成员与邀请";
  current.append(title);

  const panel = document.createElement("div");
  panel.className = "strategy-member-panel";
  const invite = campaign.invite || {
    status: campaign.status === "lobby" && campaign.join_code ? "open" : "locked",
    join_code: campaign.join_code || "",
  };
  // 加入码开着的时候它是可操作信息，其余两种状态只是在解释为什么没有加入码——
  // 而旁边本来就没有加入码可用。"不需要保存加入码"同理，进提示气泡。
  if (invite.status === "open") {
    appendTextLine(panel, "strategy-meta", `加入码：${invite.join_code || campaign.join_code}`);
    panel.append(createHint("已加入的成员用自己的账号恢复战役，不需要保存加入码。", { align: "start" }));
  }

  const actions = document.createElement("div");
  actions.className = "strategy-campaign-actions";
  if (isOwner && campaign.status === "lobby") {
    if (invite.status === "open") {
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "ghost";
      revoke.textContent = "撤销当前加入码";
      revoke.disabled = state.strategyBusy;
      revoke.addEventListener("click", () => revokeStrategyJoinCode(campaign.id));
      actions.append(revoke);
    }
    const rotate = document.createElement("button");
    rotate.type = "button";
    rotate.className = "ghost";
    rotate.textContent = invite.status === "open" ? "重发新加入码" : "生成新加入码";
    rotate.disabled = state.strategyBusy;
    rotate.addEventListener("click", () => rotateStrategyJoinCode(campaign.id));
    actions.append(rotate);
  } else if (!isOwner && campaign.status === "lobby") {
    appendTextLine(actions, "strategy-meta", "只有战役房主可以撤销或重发加入码。");
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

export function renderStrategyResumePanel(current, campaign) {
  const initialMembers = strategyInitialMembers(campaign);
  if (!initialMembers.length) return;

  const title = document.createElement("h4");
  title.textContent = "月度提交与在线状态";
  current.append(title);

  const resume = campaign.resume || {};
  const panel = document.createElement("div");
  panel.className = "strategy-resume-panel";
  // 下面那张格子已经逐人写着每个席位的提交与在线状态。归档/锁定前的两句纯解说
  // 去掉；进行中只保留"可以结算了"这一条——它会改变房主的下一步。谁还没交在格子
  // 里看得见，不必再复述一遍。
  if (campaign.status === "active" && resume.can_advance_month) {
    appendTextLine(panel, "strategy-meta", "所有真人均已提交；房主可以推进本月结算。");
  }

  const onlineIds = new Set((resume.online_initial_user_ids || []).map((userId) => Number(userId)));
  const grid = document.createElement("div");
  grid.className = "strategy-resume-grid";
  initialMembers.forEach((member) => {
    const userId = Number(member.user_id);
    let status = "待锁定";
    let className = "strategy-resume-member is-pending";
    if (campaign.status === "active" && strategyMemberIsAi(member)) {
      status = "永久 AI 席位";
      className = "strategy-resume-member is-online";
    } else if (campaign.status === "active") {
      const submission = strategyMemberSubmissionStatus(campaign, userId);
      const online = onlineIds.has(userId);
      status = submission === "ready"
        ? `已提交 · ${online ? "在线" : "离线"}`
        : submission === "proxy_ai"
          ? `本月 AI 临时托管 · ${online ? "已重连" : "离线"}`
          : `拟定中 · ${online ? "在线" : "离线"}`;
      className = submission === "ready"
        ? "strategy-resume-member is-online"
        : "strategy-resume-member is-missing";
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
  if (campaign.status === "active") {
    const controls = document.createElement("div");
    controls.className = "strategy-campaign-actions";
    const currentUserId = Number(state.authUser?.id || 0);
    const currentMember = initialMembers.find((member) => Number(member.user_id) === currentUserId);
    if (currentMember && !strategyMemberIsAi(currentMember)) {
      const submission = strategyMemberSubmissionStatus(campaign, currentUserId);
      const readyButton = document.createElement("button");
      readyButton.type = "button";
      readyButton.className = submission === "drafting" ? "primary" : "ghost";
      readyButton.textContent = submission === "drafting" ? "提交本月计划" : "撤回提交并取回控制";
      readyButton.disabled = state.strategyBusy;
      readyButton.addEventListener("click", () => setStrategyMonthReady(submission === "drafting"));
      controls.append(readyButton);
    }
    if (Number(campaign.owner_user_id) === currentUserId) {
      const close = document.createElement("button");
      close.type = "button";
      close.className = "ghost";
      close.textContent = "关闭本月截止并托管离线成员";
      const ownerReady = (resume.ready_user_ids || []).some((item) => Number(item) === currentUserId);
      const onlineDrafting = (resume.drafting_user_ids || []).some(
        (userId) => onlineIds.has(Number(userId)) && Number(userId) !== currentUserId
      );
      close.disabled = state.strategyBusy || !ownerReady || onlineDrafting || !(resume.drafting_user_ids || []).length;
      close.addEventListener("click", closeStrategyMonthDeadline);
      controls.append(close);
    }
    panel.append(controls);
    panel.append(createHint("已提交后军令锁定；撤回即可继续修改。临时托管只持续当前月份。", { align: "start" }));
  }
  current.append(panel);
}

export function renderStrategyRecoveryOverview(current, campaign) {
  const recovery = campaign?.recovery || {};
  const rows = Array.isArray(recovery.battles) ? recovery.battles : [];
  // 没有检查点时，这一节此前仍会画出标题加两行"可恢复 0 场 · 待安全重开 0 场 ·
  // 已完成 0 场"和"还没有需要恢复的检查点"——一整块只为了说这里什么都没有。
  if (!rows.length) return;

  const title = document.createElement("h4");
  title.textContent = "恢复总览";
  current.append(title);

  const panel = document.createElement("div");
  panel.className = "strategy-resume-panel";
  appendTextLine(
    panel,
    "strategy-meta",
    recovery.read_only
      ? "归档只读：可查看地图、复盘与本人参与的历史战斗。"
      : `可恢复 ${Number(recovery.resume_available_count || 0)} 场 · 待安全重开 ${Number(recovery.restart_required_count || 0)} 场 · 已完成 ${Number(recovery.completed_count || 0)} 场`
  );

  const currentUserId = Number(state.authUser?.id || 0);
  const statusLabels = {
    resume_available: "可继续",
    restart_required: "需从战前快照安全重开",
    completed: "已完成",
    archived_replay: "归档复盘",
  };
  const grid = document.createElement("div");
  grid.className = "strategy-event-list";
  rows.slice().reverse().forEach((row) => {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const strong = document.createElement("strong");
    strong.textContent = `战斗 ${row.battle_id || row.room_id}`;
    card.append(strong);
    appendTextLine(card, "strategy-meta", `状态：${statusLabels[row.status] || row.status} · 房间：${row.room_id}`);
    appendTextLine(card, "strategy-meta", `参与者：${(row.participant_names || []).join("、") || "未知"}`);
    appendTextLine(card, "strategy-meta", `检查点 v${Number(row.checkpoint_version || 0)} · 重开 ${Number(row.restart_count || 0)} 次`);
    const participates = (row.participant_user_ids || []).some((userId) => Number(userId) === currentUserId);
    if (participates) {
      const actions = document.createElement("div");
      actions.className = "strategy-campaign-actions";
      if (row.status === "restart_required" && !row.read_only) {
        const restart = document.createElement("button");
        restart.type = "button";
        restart.className = "ghost";
        restart.textContent = "从战前快照安全重开";
        restart.disabled = state.strategyBusy;
        restart.addEventListener("click", () => restartStrategyBattleFromSnapshot(row.room_id));
        actions.append(restart);
      } else {
        const open = document.createElement("button");
        open.type = "button";
        open.className = "primary";
        open.textContent = row.read_only || ["completed", "archived_replay"].includes(row.status) ? "只读查看战斗" : "恢复战斗";
        open.disabled = state.strategyBusy;
        open.addEventListener("click", () => openStrategyBattleRoom({room_id: row.room_id}));
        actions.append(open);
      }
      card.append(actions);
    } else {
      appendTextLine(card, "strategy-meta", "只有该场原参与账号可以打开战斗检查点。");
    }
    grid.append(card);
  });
  panel.append(grid);
  current.append(panel);
}

export function renderStrategyOfficeCollaborationPanel(current, campaign) {
  if (campaign?.status !== "active") return;
  const userId = Number(state.authUser?.id || 0);
  const member = strategyMember(campaign);
  if (!member) return;
  const factionMembers = (campaign.members || []).filter((item) => (
    item.faction_id === member.faction_id
    && Number(item.user_id) > 0
    && !strategyMemberIsAi(item)
  ));
  if (factionMembers.length < 2) return;

  const title = document.createElement("h4");
  title.textContent = "同势力官职协作";
  current.append(title);
  const panel = document.createElement("div");
  panel.className = "strategy-member-panel strategy-office-collaboration";
  const activeOffice = strategyActiveOffice(campaign);
  const userIsLord = strategyControlledOffices(campaign).some((office) => office.office_type === "lord");
  appendTextLine(
    panel,
    "strategy-meta",
    activeOffice
      ? `当前操作职位：${strategyOfficeLabel(activeOffice, campaign)}。交接或撤换只有在相关玩家确认后才生效。`
      : "当前没有正式官职；你仍控制自己的武将，但不能签发职位军令。"
  );

  const requests = (campaign.office_change_requests || []).filter(
    (request) => request.faction_id === member.faction_id
  );
  const pending = requests.filter((request) => request.status === "pending");
  pending.forEach((request) => {
    const row = document.createElement("article");
    row.className = "strategy-member-card";
    const office = (campaign.world?.offices || []).find((item) => item.id === request.office_id);
    const requestLabel = request.request_type === "handover" ? "交接" : "撤换";
    const strong = document.createElement("strong");
    strong.textContent = `${requestLabel} · ${strategyOfficeLabel(office, campaign)}`;
    row.append(strong);
    appendTextLine(
      row,
      "strategy-meta",
      `${strategyMemberLabel(campaign, request.initiator_user_id)} → ${strategyMemberLabel(campaign, request.target_user_id)}`
    );
    if (Number(request.target_user_id) === userId) {
      const actions = document.createElement("div");
      actions.className = "strategy-campaign-actions";
      const accept = document.createElement("button");
      accept.type = "button";
      accept.className = "primary";
      accept.textContent = "确认变更";
      accept.disabled = state.strategyBusy;
      accept.addEventListener("click", () => respondStrategyOfficeChange(request.id, true));
      const reject = document.createElement("button");
      reject.type = "button";
      reject.className = "ghost";
      reject.textContent = "拒绝";
      reject.disabled = state.strategyBusy;
      reject.addEventListener("click", () => respondStrategyOfficeChange(request.id, false));
      actions.append(accept, reject);
      row.append(actions);
    } else {
      appendTextLine(row, "strategy-meta", "等待目标玩家确认");
    }
    panel.append(row);
  });

  const userHasPending = pending.some((request) => (
    Number(request.initiator_user_id) === userId || Number(request.target_user_id) === userId
  ));
  if (activeOffice && !userHasPending) {
    const actions = document.createElement("div");
    actions.className = "strategy-campaign-actions";
    factionMembers
      .filter((item) => Number(item.user_id) !== userId)
      .forEach((target) => {
        const handover = document.createElement("button");
        handover.type = "button";
        handover.className = "ghost";
        handover.textContent = `与 ${target.username} 交接${strategyOfficeLabel(activeOffice, campaign)}`;
        handover.disabled = state.strategyBusy;
        handover.addEventListener("click", () => requestStrategyOfficeChange("handover", activeOffice.id, target.user_id));
        actions.append(handover);
      });
    if (userIsLord) {
      (campaign.world?.offices || [])
        .filter((office) => (
          office.faction_id === member.faction_id
          && office.office_type !== "lord"
          && office.controller_type === "player"
          && Number(office.controller_user_id || 0) !== userId
        ))
        .forEach((office) => {
          const vacate = document.createElement("button");
          vacate.type = "button";
          vacate.className = "ghost";
          vacate.textContent = `请求撤换${strategyOfficeLabel(office, campaign)}`;
          vacate.disabled = state.strategyBusy;
          vacate.addEventListener("click", () => requestStrategyOfficeChange("vacate", office.id));
          actions.append(vacate);
        });
    }
    panel.append(actions);
  }
  const takeovers = (campaign.office_takeovers || []).filter(
    (takeover) => takeover.faction_id === member.faction_id
  );
  const activeTakeovers = takeovers.filter((takeover) => takeover.status === "active");
  activeTakeovers.forEach((takeover) => {
    const row = document.createElement("article");
    row.className = "strategy-member-card";
    const office = (campaign.world?.offices || []).find((item) => item.id === takeover.office_id);
    const strong = document.createElement("strong");
    strong.textContent = `当月代管 · ${strategyOfficeLabel(office, campaign)}`;
    row.append(strong);
    appendTextLine(
      row,
      "strategy-meta",
      `${strategyMemberLabel(campaign, takeover.grantor_user_id)} 授权 ${strategyMemberLabel(campaign, takeover.delegate_user_id)} · 第 ${takeover.month} 月`
    );
    if (
      [takeover.grantor_user_id, takeover.delegate_user_id, ...(userIsLord ? [userId] : [])]
        .some((item) => Number(item || 0) === userId)
    ) {
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "ghost";
      revoke.textContent = "结束临时代管";
      revoke.disabled = state.strategyBusy;
      revoke.addEventListener("click", () => revokeStrategyOfficeTakeover(takeover.id));
      row.append(revoke);
    }
    panel.append(row);
  });
  if (userIsLord) {
    const activeOfficeIds = new Set(activeTakeovers.map((takeover) => takeover.office_id));
    const vacantOffices = (campaign.world?.offices || []).filter((office) => (
      office.faction_id === member.faction_id
      && office.office_type !== "lord"
      && office.status === "vacant"
      && !activeOfficeIds.has(office.id)
    ));
    if (vacantOffices.length) {
      const grants = document.createElement("div");
      grants.className = "strategy-campaign-actions";
      factionMembers
        .filter((item) => Number(item.user_id) !== userId)
        .forEach((target) => {
          vacantOffices.forEach((office) => {
            const grant = document.createElement("button");
            grant.type = "button";
            grant.className = "ghost";
            grant.textContent = `授权 ${target.username} 当月代管${strategyOfficeLabel(office, campaign)}`;
            grant.disabled = state.strategyBusy;
            grant.addEventListener("click", () => grantStrategyOfficeTakeover(office.id, target.user_id));
            grants.append(grant);
          });
        });
      panel.append(grants);
    }
  }
  const recent = requests.find((request) => request.status !== "pending");
  if (recent) {
    const labels = { accepted: "已确认", rejected: "已拒绝", expired: "已过期" };
    appendTextLine(panel, "strategy-meta", `最近结果：${labels[recent.status] || recent.status}`);
  }
  const recentTakeover = takeovers.find((takeover) => takeover.status !== "active");
  if (recentTakeover) {
    const labels = { expired: "新月到期", revoked: "提前结束" };
    appendTextLine(panel, "strategy-meta", `最近代管审计：${labels[recentTakeover.status] || recentTakeover.status}`);
  }
  current.append(panel);
}

export function strategyMapNodeId(node) {
  return node?.id || node?.node_id || "";
}

export function strategyNodeName(campaign, nodeId) {
  const node = (campaign?.world?.nodes || []).find((item) => strategyMapNodeId(item) === nodeId);
  const city = (campaign?.world?.cities || []).find((item) => item.node_id === nodeId);
  return city?.name || node?.name || nodeId || "未知节点";
}

export function strategyArmyStatusLabel(status) {
  return ({ garrisoned: "驻扎", deployed: "部署", marching: "行军", engaged: "交战", besieging: "围城", retreating: "撤退", disbanded: "已解散", destroyed: "已覆灭" })[status] || status;
}

export function strategyArmyOrderLabel(order) {
  return ({ hold: "待命", march: "行军", intercept: "拦截", reinforce: "增援", retreat: "撤退", besiege: "围城" })[order] || order;
}

export function strategyArmySupplyStatusLabel(status) {
  return ({ unassessed: "待首次月结", local: "本地", open: "畅通", strained: "吃紧", severed: "已切断", none: "无来源" })[status] || status;
}

export function strategyActiveEncounters(campaign) {
  return (campaign?.world?.encounters || []).filter((encounter) => encounter.status === "active");
}

export function strategyEncounterArmyIds(encounter) {
  return Object.values(encounter?.faction_army_ids || {}).flat().map(String);
}

export function strategyEncounterForArmy(campaign, armyId) {
  return strategyActiveEncounters(campaign).find((encounter) => strategyEncounterArmyIds(encounter).includes(String(armyId))) || null;
}

export function strategyActiveSieges(campaign) {
  return (campaign?.world?.sieges || []).filter((siege) => ["active", "contested", "breached", "battle_pending"].includes(siege.status));
}

export function strategySiegeForArmy(campaign, armyId) {
  return strategyActiveSieges(campaign).find((siege) => (siege.attacker_army_ids || []).includes(String(armyId))) || null;
}

export function strategySiegeStatusLabel(status) {
  return ({ active: "围城中", contested: "援军争夺", breached: "城防已破", battle_pending: "等待战斗", ended: "已结束" })[status] || status;
}

export function strategySiegeAttackerStanceLabel(stance) {
  return ({ blockade: "封锁", starve: "断粮", assault: "强攻", withdraw: "撤围" })[stance] || stance;
}

export function strategySiegeDefenderStanceLabel(stance) {
  return ({ hold: "坚守", breakout: "突围", await_relief: "待援", surrender: "投降" })[stance] || stance;
}

export function strategyArmiesHostile(campaign, first, second) {
  if (!first || !second || first.faction_id === second.faction_id) return false;
  const firstFaction = strategyFactionById(campaign, first.faction_id);
  const secondFaction = strategyFactionById(campaign, second.faction_id);
  const firstNeutral = firstFaction?.faction_type === "neutral_city_state";
  const secondNeutral = secondFaction?.faction_type === "neutral_city_state";
  if (firstNeutral === secondNeutral) return true;
  const majorId = firstNeutral ? second.faction_id : first.faction_id;
  const neutralId = firstNeutral ? first.faction_id : second.faction_id;
  return !(campaign?.world?.diplomatic_agreements || []).some((agreement) => (
    agreement.status === "active" && agreement.agreement_type === "non_aggression"
    && agreement.major_faction_id === majorId && agreement.neutral_faction_id === neutralId
  ));
}

function createStrategySvgElement(tagName) {
  if (document.createElementNS) {
    return document.createElementNS("http://www.w3.org/2000/svg", tagName);
  }
  return document.createElement(tagName);
}

export function strategyCityById(campaign, cityId) {
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

export function strategyCanResume(campaign) {
  if (!campaign) return false;
  const resume = campaign.resume || {};
  if (resume.can_resume) return true;
  const active = campaign.status === "active" || resume.campaign_status === "active";
  const missing = Array.isArray(resume.missing_initial_user_ids) ? resume.missing_initial_user_ids : null;
  const initial = Array.isArray(resume.initial_user_ids) ? resume.initial_user_ids : [];
  return Boolean(active && missing && missing.length === 0 && initial.length);
}

export function strategyCanIssueOrders(campaign) {
  if (!strategyCanResume(campaign)) return false;
  const userId = Number(state.authUser?.id || 0);
  const lockedIds = new Set([
    ...(campaign?.resume?.ready_user_ids || []),
    ...(campaign?.resume?.proxy_ai_user_ids || []),
  ].map((item) => Number(item)));
  if (userId && lockedIds.has(userId)) return false;
  return campaign?.world?.strategic_status?.can_advance_month !== false;
}

function strategyMemberSubmissionStatus(campaign, userId) {
  const id = Number(userId);
  if ((campaign?.resume?.ready_user_ids || []).some((item) => Number(item) === id)) return "ready";
  if ((campaign?.resume?.proxy_ai_user_ids || []).some((item) => Number(item) === id)) return "proxy_ai";
  return "drafting";
}

export function strategyHostCanRequestAdvance(campaign) {
  if (!campaign || Number(campaign.owner_user_id) !== Number(state.authUser?.id || 0)) return false;
  const draftingIds = (campaign.resume?.drafting_user_ids || []).map((item) => Number(item));
  const ownerId = Number(campaign.owner_user_id);
  return Boolean(
    campaign.resume?.can_advance_month
    || draftingIds.every((userId) => userId === ownerId)
  );
}

function strategyCityOrderLimitReached(campaign, cityId) {
  return strategyQueuedActionsForCity(campaign, cityId).length >= strategyCityOrderLimit(campaign);
}

function strategyDefaultSelectedCity(campaign, faction) {
  const cities = campaign?.world?.cities || [];
  return cities.find((city) => (
    city.owner_faction_id === faction?.id && strategyCityRebellionForce(city) > 0
  )) || cities.find((city) => (
    city.owner_faction_id === faction?.id && strategyAttackTargetsForCity(campaign, city, faction?.id).length
  )) || cities.find((city) => city.owner_faction_id === faction?.id) || cities[0] || null;
}

export function strategySelectionContextKey(campaign, office = strategyActiveOffice(campaign)) {
  const campaignId = Number(campaign?.id || 0);
  return `${campaignId || "campaign"}::${office?.id || "viewer"}`;
}

export function strategyRememberSelectedCity(cityId, campaign = state.strategyCampaign, office = strategyActiveOffice(campaign)) {
  const normalizedCityId = String(cityId || "");
  state.strategySelectedCityId = normalizedCityId;
  state.strategySelectedCampaignId = Number(campaign?.id || 0);
  if (campaign) {
    state.strategySelectedCityByContext[strategySelectionContextKey(campaign, office)] = normalizedCityId;
  }
  return normalizedCityId;
}

export function strategySelectedCity(campaign, faction) {
  const office = strategyActiveOffice(campaign);
  const contextKey = strategySelectionContextKey(campaign, office);
  const sameCampaignSelection = Number(state.strategySelectedCampaignId || 0) === Number(campaign?.id || 0)
    ? state.strategySelectedCityId
    : "";
  const preferredCityId = state.strategySelectedCityByContext[contextKey] || sameCampaignSelection;
  const selected = strategyCityById(campaign, preferredCityId);
  if (selected) {
    strategyRememberSelectedCity(selected.id, campaign, office);
    return selected;
  }
  const fallback = strategyDefaultSelectedCity(campaign, faction);
  strategyRememberSelectedCity(fallback?.id || "", campaign, office);
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
  const crisisFrontier = (campaign?.world?.world_crises || []).some((crisis) =>
    (crisis.frontier_node_ids || []).includes(city.node_id)
  );
  if (crisisFrontier) classes.push("is-crisis-frontier");
  const crisisThreatened = (campaign?.world?.world_crises || []).some((crisis) =>
    (crisis.threatened_city_ids || []).includes(city.id)
  );
  if (crisisThreatened) classes.push("is-crisis-threatened");
  return classes.join(" ");
}

function strategyMapOwnership(city, campaign, faction) {
  if (city?.owner_faction_id === faction?.id) {
    return { className: "is-owned", marker: "◆", label: "己方" };
  }
  if (strategyIsNeutralCityState(campaign, city?.owner_faction_id)) {
    return { className: "is-city-state", marker: "◇", label: "中立城邦" };
  }
  if (city?.owner_faction_id) {
    return { className: "is-enemy", marker: "⚔", label: "敌方" };
  }
  return { className: "is-neutral", marker: "○", label: "无主" };
}

// 世界的像素尺寸。节点坐标是 0~100 的百分比，落在这块画布上就成了具体位置；
// 画布比视口大，所以地图需要被拖着看——这正是要的效果。
const STRATEGY_MAP_WORLD = { width: 1680, height: 1120 };
const STRATEGY_MAP_ZOOM = { min: 0.45, max: 2.2 };

function clampMapScale(scale) {
  return Math.max(STRATEGY_MAP_ZOOM.min, Math.min(STRATEGY_MAP_ZOOM.max, scale));
}

/**
 * 让地图可以被拖拽和缩放。
 *
 * 视图状态存在 state 里而不是 DOM 上：这一屏每次轮询都可能整块重建，把平移量
 * 留在元素上就意味着每次刷新都把玩家拽回原点。
 */
function attachStrategyMapView(viewport, canvas, campaignId) {
  const view = state.strategyMapView;
  const apply = () => {
    canvas.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  };

  // 开局把整张图装进视口——第一眼要回答的是"这局的地形是什么样"，而不是某个
  // 角落。视口和画布同底色，所以没装满的地方看起来是延续出去的空地，不是黑边。
  const center = () => {
    const height = viewport.clientHeight || 0;
    // 操作面板盖住了视口右边一条。取景要按看得见的那块算，否则地图正中会被压在
    // 面板底下，屏幕左边留出一片空地。
    const dock = viewport.closest?.(".campaign-stage")?.querySelector?.(".campaign-dock");
    const occluded = dock ? Math.round(dock.getBoundingClientRect().width) : 0;
    const width = Math.max(240, (viewport.clientWidth || 0) - occluded);
    if (!width || !height) return;
    view.scale = clampMapScale(Math.min(width / STRATEGY_MAP_WORLD.width, height / STRATEGY_MAP_WORLD.height) * 0.98);
    view.x = (width - STRATEGY_MAP_WORLD.width * view.scale) / 2;
    view.y = (height - STRATEGY_MAP_WORLD.height * view.scale) / 2;
    view.campaignId = campaignId;
    apply();
  };

  apply();
  // 换了一局就重新取景，否则新战役会继承上一局的平移量，开局对着一片空地。
  if (Number(view.campaignId || 0) !== Number(campaignId || 0)) {
    if (typeof window !== "undefined" && window.requestAnimationFrame) window.requestAnimationFrame(center);
    else center();
  }

  let drag = null;
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    if (event.target?.closest?.(".strategy-map-node")) return;
    drag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, originX: view.x, originY: view.y };
    viewport.classList.add("is-dragging");
    viewport.setPointerCapture?.(event.pointerId);
  });
  viewport.addEventListener("pointermove", (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    view.x = drag.originX + (event.clientX - drag.startX);
    view.y = drag.originY + (event.clientY - drag.startY);
    apply();
  });
  const endDrag = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag = null;
    viewport.classList.remove("is-dragging");
    if (viewport.hasPointerCapture?.(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
  };
  viewport.addEventListener("pointerup", endDrag);
  viewport.addEventListener("pointercancel", endDrag);

  // 以光标为锚点缩放，否则放大时目标会从视野里滑走。
  viewport.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const pointX = event.clientX - rect.left;
    const pointY = event.clientY - rect.top;
    const next = clampMapScale(view.scale * (event.deltaY < 0 ? 1.12 : 1 / 1.12));
    const ratio = next / view.scale;
    view.x = pointX - (pointX - view.x) * ratio;
    view.y = pointY - (pointY - view.y) * ratio;
    view.scale = next;
    apply();
  }, { passive: false });

  const zoomBy = (factor) => {
    const width = viewport.clientWidth || 0;
    const height = viewport.clientHeight || 0;
    const next = clampMapScale(view.scale * factor);
    const ratio = next / view.scale;
    view.x = width / 2 - (width / 2 - view.x) * ratio;
    view.y = height / 2 - (height / 2 - view.y) * ratio;
    view.scale = next;
    apply();
  };

  return { zoomBy, reset: center };
}

export function renderStrategyMap(current, campaign, faction) {
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
  const activeEncounters = strategyActiveEncounters(campaign);
  const encountersByNodeId = new Map(activeEncounters.map((encounter) => [encounter.node_id, encounter]));
  const activeSieges = strategyActiveSieges(campaign);
  const siegesByNodeId = new Map(activeSieges.map((siege) => [siege.node_id, siege]));
  const armiesByNodeId = new Map();
  activeArmies.forEach((army) => {
    const rows = armiesByNodeId.get(army.location_node_id) || [];
    rows.push(army);
    armiesByNodeId.set(army.location_node_id, rows);
  });
  const activeArmyRouteKeys = new Set();
  const armySupplyRouteKeys = new Set();
  const armySupplyRiskRouteKeys = new Set();
  const crisisRouteEffects = (campaign?.world?.world_crises || []).flatMap((crisis) => crisis.route_effects || []);
  const crisisRouteKeys = new Set(crisisRouteEffects.map((effect) => effect.route_key));
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

  const viewport = document.createElement("div");
  viewport.className = "strategy-map-viewport";
  const canvas = document.createElement("div");
  canvas.className = "strategy-map-canvas strategy-map-stage-canvas";
  canvas.style.width = `${STRATEGY_MAP_WORLD.width}px`;
  canvas.style.height = `${STRATEGY_MAP_WORLD.height}px`;
  const routeLayer = createStrategySvgElement("svg");
  // SVG 元素的 className 是只读的 SVGAnimatedString，赋值会抛 TypeError——而这
  // 一句就在地图渲染的开头，于是整个战役屏什么都渲染不出来，异常还被
  // refreshState() 的 try/catch 吞掉，控制台干干净净。SVG 上只能用 setAttribute。
  routeLayer.setAttribute("class", "strategy-map-route-layer");
  routeLayer.setAttribute("viewBox", "0 0 100 100");
  routeLayer.setAttribute("preserveAspectRatio", "none");
  canvas.append(routeLayer);

  const routeKeys = new Set();
  nodes.forEach((node) => {
    const sourceId = strategyMapNodeId(node);
    (node.connected_node_ids || []).forEach((targetId) => {
      if (!sourceId || !targetId) return;
      const key = [sourceId, targetId].sort().join("::");
      if (routeKeys.has(key)) return;
      routeKeys.add(key);
      const sourcePos = positions.get(sourceId);
      const targetPos = positions.get(targetId);
      if (!sourcePos || !targetPos) return;
      const line = createStrategySvgElement("line");
      line.setAttribute("x1", String(sourcePos.x));
      line.setAttribute("y1", String(sourcePos.y));
      line.setAttribute("x2", String(targetPos.x));
      line.setAttribute("y2", String(targetPos.y));
      line.setAttribute("class", `strategy-map-route-line${armySupplyRouteKeys.has(key) ? " is-supply-route" : ""}${armySupplyRiskRouteKeys.has(key) ? " is-supply-risk" : ""}${activeArmyRouteKeys.has(key) ? " is-army-route" : ""}${crisisRouteKeys.has(key) ? " is-crisis-route" : ""}`);
      routeLayer.append(line);
    });
  });

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
    const cityArmies = armiesByNodeId.get(city.node_id) || [];
    const encounter = encountersByNodeId.get(city.node_id);
    const siege = siegesByNodeId.get(city.node_id);
    const ownership = strategyMapOwnership(city, campaign, faction);
    const cityStateLabels = strategyCityStateLabels(city);
    const isSelected = city.id === selectedCityId;
    const isCrisisThreatened = (campaign?.world?.world_crises || []).some((crisis) =>
      (crisis.threatened_city_ids || []).includes(city.id)
    );
    const accessibleStates = [
      isSelected ? "当前目标" : "",
      ...cityStateLabels,
      isCrisisThreatened ? "雪鬼威胁" : "",
      queuedActions.length ? `已计划 ${queuedActions.length} 条军令` : "",
      cityArmies.length ? `${cityArmies.length} 支军队` : "",
      encounter ? "发生遭遇" : "",
      siege ? "正在围城" : "",
    ].filter(Boolean);
    card.setAttribute("aria-pressed", isSelected ? "true" : "false");
    card.setAttribute(
      "aria-label",
      `${city.name}，${ownership.label}，${strategyFactionName(campaign, city.owner_faction_id)}，兵力 ${city.resources?.troops || 0}，城防 ${city.defense || 0}${accessibleStates.length ? `，${accessibleStates.join("，")}` : ""}`
    );
    card.addEventListener("click", () => {
      strategyRememberSelectedCity(city.id, campaign);
      renderStrategyPanel();
      if (window.innerWidth <= 720) {
        focusStrategySelectedCityCommand();
      }
    });
    const nodeHead = document.createElement("span");
    nodeHead.className = "strategy-map-node-head";
    const strong = document.createElement("strong");
    strong.textContent = city.name;
    const ownerTag = document.createElement("span");
    ownerTag.className = `strategy-map-owner-tag ${ownership.className}`;
    ownerTag.textContent = `${ownership.marker} ${ownership.label}`;
    nodeHead.append(strong, ownerTag);
    const factionLine = document.createElement("span");
    factionLine.className = "strategy-map-faction-line";
    const cityFaction = strategyFactionById(campaign, city.owner_faction_id);
    factionLine.textContent = cityFaction?.faction_type === "neutral_city_state"
      ? `${cityFaction.name} · 城主 ${cityFaction.governor_name || "无名"}`
      : strategyFactionName(campaign, city.owner_faction_id);
    const statLine = document.createElement("span");
    statLine.className = "strategy-map-stat-line";
    statLine.textContent = `兵${city.resources?.troops || 0} / 防${city.defense || 0}`;
    card.append(nodeHead, factionLine, statLine);
    if (isSelected) {
      const selection = document.createElement("span");
      selection.className = "strategy-map-selection";
      selection.textContent = "◎ 当前目标";
      card.append(selection);
    }
    if (cityStateLabels.length) {
      const warning = document.createElement("span");
      warning.className = "strategy-map-alert";
      warning.textContent = `⚠ ${cityStateLabels[0]}${cityStateLabels.length > 1 ? ` +${cityStateLabels.length - 1}` : ""}`;
      card.append(warning);
    }
    if (isCrisisThreatened) {
      const threat = document.createElement("span");
      threat.className = "strategy-map-alert is-crisis";
      threat.textContent = "❄ 雪鬼威胁";
      card.append(threat);
    }
    if (queuedActions.length) {
      const plan = document.createElement("span");
      plan.className = "strategy-map-plan";
      plan.textContent = `✓ 已计划 ${queuedActions.length}`;
      card.append(plan);
    }
    cityArmies.slice(0, 2).forEach((army) => {
      const badge = document.createElement("span");
      badge.className = `strategy-map-army${army.faction_id === faction?.id ? " is-owned" : ""}${army.army_kind === "snow_ghost" ? " is-snow-ghost" : ""}`;
      badge.textContent = army.status === "marching"
        ? `▸ ${army.name || "军队"} · 行军 ${Number(army.route_progress_index || 0)}/${Math.max(1, (army.route_node_ids || []).length - 1)}`
        : `▸ ${army.name || "军队"} · ${strategyArmyStatusLabel(army.status)}`;
      card.append(badge);
    });
    if (cityArmies.length > 2) appendTextLine(card, "strategy-map-army-more", `另有 ${cityArmies.length - 2} 支军队`);
    if (encounter) {
      const badge = document.createElement("span");
      badge.className = "strategy-map-encounter";
      badge.textContent = `⚔ 遭遇 · ${Object.keys(encounter.faction_army_ids || {}).length} 方`;
      card.append(badge);
    }
    if (siege) {
      const badge = document.createElement("span");
      badge.className = "strategy-map-siege";
      badge.textContent = `▣ 围城 · ${strategySiegeStatusLabel(siege.status)}`;
      card.append(badge);
    }
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
      badge.className = `strategy-map-army${army.faction_id === faction?.id ? " is-owned" : ""}${army.army_kind === "snow_ghost" ? " is-snow-ghost" : ""}`;
      badge.textContent = `${army.name || "军队"} · ${strategyArmyStatusLabel(army.status)} · 补给${strategyArmySupplyStatusLabel(army.supply_line_status)}`;
      card.append(badge);
    });
    const encounter = encountersByNodeId.get(nodeId);
    if (encounter) {
      const badge = document.createElement("span");
      badge.className = "strategy-map-encounter";
      badge.textContent = `遭遇 · ${Object.keys(encounter.faction_army_ids || {}).length} 方 / ${strategyEncounterArmyIds(encounter).length} 军`;
      card.append(badge);
    }
    appendTextLine(card, "strategy-map-hidden-text", `节点 ${nodeId} · ${node.type || "地形"}`);
    canvas.append(card);
  });
  viewport.append(canvas);
  map.append(viewport);
  const controller = attachStrategyMapView(viewport, canvas, campaign?.id || 0);

  // 缩放条压在地图角上，而不是另占一行——它服务于地图，不是屏幕上的一块内容。
  const tools = document.createElement("div");
  tools.className = "strategy-map-tools";
  [
    ["放大", "＋", () => controller.zoomBy(1.2)],
    ["缩小", "－", () => controller.zoomBy(1 / 1.2)],
    ["复位", "⤾", () => controller.reset()],
  ].forEach(([label, glyph, onClick]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "strategy-map-tool";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.textContent = glyph;
    button.addEventListener("click", onClick);
    tools.append(button);
  });
  map.append(tools);

  const hint = document.createElement("span");
  hint.className = "strategy-map-hint";
  hint.textContent = "拖拽移动地图 · 滚轮缩放 · 点击城市下令";
  map.append(hint);
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

export function createStrategyField(labelText, control) {
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

export function strategyRegisteredUnitsLabel(campaign, inventory = {}) {
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

function strategyCityContextRiskLabels(campaign, city) {
  if (!city) return [];
  const labels = [...strategyCityStateLabels(city)];
  const crisisThreatened = (campaign?.world?.world_crises || []).some((crisis) => (
    (crisis.threatened_city_ids || []).includes(city.id)
  ));
  if (crisisThreatened) labels.push("雪鬼威胁");
  const encounter = strategyActiveEncounters(campaign).find((item) => item.node_id === city.node_id);
  if (encounter) labels.push(`敌军遭遇 · ${Object.keys(encounter.faction_army_ids || {}).length} 方`);
  const siege = strategyActiveSieges(campaign).find((item) => (
    item.city_id === city.id || item.node_id === city.node_id
  ));
  if (siege) labels.push(`围城 · ${strategySiegeStatusLabel(siege.status)}`);
  return [...new Set(labels.filter(Boolean))];
}

function createStrategyCityContextHead(campaign, city, office, ownership, kicker) {
  const contextHead = document.createElement("header");
  contextHead.className = "strategy-city-context-head";
  const contextTitle = document.createElement("div");
  appendTextLine(contextTitle, "strategy-quick-opening-kicker", kicker);
  const title = document.createElement("strong");
  title.textContent = `${city.name} · ${city.policy}`;
  contextTitle.append(title);
  const contextBadges = document.createElement("div");
  contextBadges.className = "strategy-city-context-badges";
  const ownerBadge = document.createElement("span");
  ownerBadge.className = `strategy-city-context-owner ${ownership.className}`;
  ownerBadge.textContent = `${ownership.marker} ${ownership.label}`;
  const officeBadge = document.createElement("span");
  officeBadge.className = "strategy-city-context-office";
  officeBadge.textContent = office ? strategyOfficeLabel(office, campaign) : "城市军令";
  contextBadges.append(ownerBadge, officeBadge);
  contextHead.append(contextTitle, contextBadges);
  return contextHead;
}

function createStrategyCityPlanBox(campaign, city, faction) {
  const queuedActions = strategyQueuedActionsForCity(campaign, city.id);
  const commandPoints = strategyFactionCommandPoints(campaign, faction);
  const planBox = document.createElement("div");
  planBox.className = `strategy-command-plan${queuedActions.length ? "" : " is-empty"}`;
  const planTitle = document.createElement("strong");
  planTitle.textContent = `本月已计划 ${queuedActions.length}/${strategyCityOrderLimit(campaign)} 条军令`;
  planBox.append(planTitle);
  appendTextLine(planBox, "strategy-command-budget", `势力军令 ${commandPoints.remaining}/${commandPoints.maximum} 可用`);
  if (queuedActions.length) {
    queuedActions.slice(0, 3).forEach((action) => appendTextLine(planBox, "strategy-meta", strategyQueuedActionLabel(campaign, action)));
  } else {
    appendTextLine(planBox, "strategy-meta", "尚未为本城安排军令。");
  }
  return planBox;
}

/**
 * 城市详情。
 *
 * 「城市」页只回答"这座城是什么样"：归属、家底、编制、风险、本月已排的军令。
 * 能对它做什么在「军令」页，那是另一个问题，不该挤在同一页里。
 */
export function createStrategyCityDetailCard(campaign, city, faction, office = strategyActiveOffice(campaign)) {
  const card = document.createElement("article");
  card.className = "strategy-city-card strategy-city-detail-card";
  if (!city) {
    appendTextLine(card, "strategy-meta", "地图上还没有可查看的城市。");
    return card;
  }

  const cityFaction = strategyFactionById(campaign, city.owner_faction_id);
  const ownership = strategyMapOwnership(city, campaign, faction);
  const cityRiskLabels = strategyCityContextRiskLabels(campaign, city);
  card.classList.add(ownership.className);
  if (cityFaction?.faction_type === "neutral_city_state") card.classList.add("is-neutral-city-state");
  if (cityRiskLabels.length) card.classList.add("has-risk");
  card.append(createStrategyCityContextHead(campaign, city, office, ownership, "当前城市"));

  const stats = document.createElement("div");
  stats.className = "strategy-city-context-stats";
  [
    ["粮", city.resources?.food || 0],
    ["钱", city.resources?.money || 0],
    ["人口", city.resources?.population || 0],
    ["以太", city.resources?.ether || 0],
    ["兵力", city.resources?.troops || 0],
    ["城防", city.defense || 0],
  ].forEach(([label, value]) => {
    const item = document.createElement("div");
    appendTextLine(item, "meta-label", label);
    const strong = document.createElement("strong");
    strong.textContent = String(value);
    item.append(strong);
    stats.append(item);
  });
  card.append(stats);
  appendTextLine(card, "strategy-city-context-faction", strategyFactionName(campaign, city.owner_faction_id));
  appendTextLine(
    card,
    "strategy-conversion",
    `兵种：${(city.troop_conversion || []).map((row) => `${row.unit_type} ${row.ratio}%`).join(" / ") || "暂无编制"}`
  );
  const buildingNames = Object.entries(city.building_levels || {})
    .filter(([, level]) => Number(level) > 0)
    .map(([id, level]) => `${(campaign?.world?.building_projects || []).find((item) => item.id === id)?.name || id} ${level}级`);
  appendTextLine(card, "strategy-meta", `设施：${buildingNames.join("、") || "尚未建设"}`);

  const risks = document.createElement("div");
  risks.className = `strategy-city-context-risks${cityRiskLabels.length ? " has-alert" : " is-clear"}`;
  const riskTitle = document.createElement("strong");
  riskTitle.textContent = cityRiskLabels.length ? "⚠ 当前风险" : "✓ 当前风险";
  const riskList = document.createElement("div");
  riskList.className = "strategy-city-context-risk-list";
  if (cityRiskLabels.length) {
    cityRiskLabels.forEach((label) => appendTextLine(riskList, "strategy-city-context-risk", label));
  } else {
    appendTextLine(riskList, "strategy-city-context-clear", "当前无警报");
  }
  risks.append(riskTitle, riskList);
  card.append(risks);
  card.append(createStrategyCityPlanBox(campaign, city, faction));
  return card;
}

export function createStrategyCityCommandCard(campaign, city, faction, canResume, office = strategyActiveOffice(campaign)) {
  const card = document.createElement("article");
  card.className = "strategy-city-card strategy-command-card strategy-city-command-card";
  if (!city) {
    appendTextLine(card, "strategy-meta", "地图上还没有可操作城市。");
    return card;
  }

  const ownCity = city.owner_faction_id === faction?.id;
  const cityFaction = strategyFactionById(campaign, city.owner_faction_id);
  const neutralCityState = cityFaction?.faction_type === "neutral_city_state";
  const ownership = strategyMapOwnership(city, campaign, faction);
  const queuedActions = strategyQueuedActionsForCity(campaign, city.id);
  const draft = strategyCommandDraft(campaign, city);
  const orderLimit = strategyCityOrderLimit(campaign);
  const orderCount = queuedActions.length;
  const orderLimitReached = strategyCityOrderLimitReached(campaign, city.id);
  const commandPoints = strategyFactionCommandPoints(campaign, faction);
  card.classList.add(ownership.className);
  if (neutralCityState) card.classList.add("is-neutral-city-state");

  card.append(createStrategyCityContextHead(campaign, city, office, ownership, "下令目标"));
  card.append(createStrategyCityPlanBox(campaign, city, faction));

  const stack = document.createElement("div");
  stack.className = "strategy-command-stack";
  const disabledReason = strategyCommandDisabledReason(canResume, ownCity);
  const orderLimitReason = orderLimitReached ? `本城本月军令已满（${orderCount}/${orderLimit}）。` : "";
  const noCommandReason = commandPoints.remaining <= 0 ? "本势力本月军令已用尽。" : "";

  const canGovern = !office || office.office_type === "governor";
  const canManageOccupation = !office || ["lord", "governor"].includes(office.office_type);
  // 主公不再从城市面板亲征：带兵是将军的差事，主公签发的是国策。
  const canAttack = !office || office.office_type === "general";

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

  const targets = strategyAttackTargetsForCity(campaign, city, faction?.id);
  if (canAttack && !lordPoliticalCrisisView && targets.length) {
    const attackSection = createStrategyCommandSection(
      "进攻",
      "选择邻接目标和处理方式。快速用于沙盒结算，手动/AI 会生成真实格子战。"
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
    const noDirectCommand = ownCity
      ? "当前职位没有针对本城的直接军令；职位级操作继续列在下方。"
      : "该城市不属于你的势力；请选择己方城市下达军令，或使用当前职位明确允许的外交、政治动作。";
    appendTextLine(stack, "strategy-meta", noDirectCommand);
  }
  const actionHead = document.createElement("div");
  actionHead.className = "strategy-city-command-actions-head";
  const actionTitle = document.createElement("strong");
  actionTitle.textContent = "当前职位可执行";
  const actionOffice = document.createElement("span");
  actionOffice.textContent = office ? strategyOfficeLabel(office, campaign) : "城市军令";
  actionHead.append(actionTitle, actionOffice);
  card.append(actionHead, stack);
  return card;
}
