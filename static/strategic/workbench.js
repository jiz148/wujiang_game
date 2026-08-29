// Campaign workbench: monthly orders, offices, relics and dossiers.
import { $ } from '../core/dom.js';
import { hasRoom, roomQueryId, viewerPlayerId } from '../core/net.js';
import { FALLBACK_STRATEGY_VARIANTS, RECORDED_MATCH_ENDS_KEY, state } from '../core/state.js';
import { syncModalIsolation } from '../core/ui.js';
import { profileModalVisible, userLoggedIn } from '../platform/auth.js';
import { archiveStrategyCampaign, chooseStrategyHeroPath, closeStrategyCampaignCreator, continueStrategySandbox, createStrategicBattleResolver, createStrategyCampaign, deleteStrategyCampaign, enterStrategyCampaign, exitStrategyCampaignView, focusStrategyCommandPanel, focusStrategyMapStage, leaveStrategyCampaign, lockStrategyCampaign, openStrategyBattleRoom, openStrategyCampaignCreator, queueStrategyAction, resolveWorldCrisisShowdown, restartStrategyBattleFromSnapshot, setStrategyBattleDefenseHero, setStrategyCreateStep, setStrategyDefenseHero } from '../strategic/api.js';
import { createButton, createHint } from '../core/components.js';
import { campaignFactionHeroes, campaignIdleHeroCount, renderCampaignHeroList, renderCampaignScreen } from './campaign-shell.js';
import { STRATEGY_DUTY_LABELS, STRATEGY_OFFICE_LABELS, STRATEGY_OFFICE_STATUS_LABELS, createStrategyCityCommandCard, createStrategyCityDetailCard, createStrategyField, renderStrategyMap, renderStrategyMembersPanel, renderStrategyOfficeCollaborationPanel, renderStrategyRecoveryOverview, renderStrategyResumePanel, strategyActiveEncounters, strategyActiveOffice, strategyActiveSieges, strategyArmiesHostile, strategyArmyOrderLabel, strategyArmyStatusLabel, strategyArmySupplyStatusLabel, strategyCanAffordCommand, strategyCanIssueOrders, strategyCanResume, strategyCityById, strategyCommandCost, strategyControlledHero, strategyControlledOffices, strategyEncounterArmyIds, strategyEncounterForArmy, strategyFaction, strategyFactionById, strategyFactionName, strategyHostCanRequestAdvance, strategyMapNodeId, strategyMemberIsAi, strategyMemberLabel, strategyMissingInitialPlayerLabels, strategyMonthlyCycle, strategyNodeName, strategyOfficeCoordination, strategyOfficeLabel, strategyOfficeManagedCities, strategyPendingStoryEvent, strategyRegisteredUnitsLabel, strategyRememberSelectedCity, strategySelectedCity, strategySelectionContextKey, strategySiegeAttackerStanceLabel, strategySiegeDefenderStanceLabel, strategySiegeForArmy, strategySiegeStatusLabel } from '../strategic/ui-base.js';
import { actionLabel, canReclaimSeatByName, storedIdentityForCurrentRoom } from '../bridge/campaign-battle.js';

function renderStrategyStoryEvent(parent, campaign, faction) {
  const event = strategyPendingStoryEvent(campaign, faction);
  if (!event) return;
  const office = strategyActiveOffice(campaign);
  const managedCityIds = new Set(strategyOfficeManagedCities(campaign, office).map((city) => city.id));
  if (office && (office.office_type !== "governor" || !managedCityIds.has(event.city_id))) return;
  const queued = (campaign?.queued_actions || []).find((action) => (
    action.faction_id === faction?.id && action.action_type === "resolve_story_event" && action.action_key === event.id
  ));
  const city = strategyCityById(campaign, event.city_id);
  const panel = document.createElement("section");
  panel.className = "strategy-story-event";
  const eyebrow = document.createElement("span");
  eyebrow.className = "strategy-story-eyebrow";
  eyebrow.textContent = `待决事件 · ${city?.name || "未知地点"}`;
  const title = document.createElement("strong");
  title.textContent = event.title || "突发事件";
  panel.append(eyebrow, title);
  appendTextLine(panel, "strategy-story-description", event.description || "");
  appendTextLine(panel, "strategy-story-deadline", "本月底未处理将自动采用放任结果。事件选择消耗 1 点势力军令。");
  if (queued) {
    const selected = (event.choices || []).find((choice) => choice.id === queued.payload?.choice_id);
    appendTextLine(panel, "strategy-story-planned", `已计划：${selected?.label || queued.payload?.choice_id || "未知选择"}（可以替换）`);
  }
  const choices = document.createElement("div");
  choices.className = "strategy-story-choices";
  (event.choices || []).forEach((choice) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = queued?.payload?.choice_id === choice.id ? "primary" : "ghost";
    const label = document.createElement("strong");
    label.textContent = choice.label || choice.id;
    const preview = document.createElement("span");
    preview.textContent = choice.preview || "";
    button.append(label, preview);
    button.disabled = state.strategyBusy || !strategyCanIssueOrders(campaign) || !choice.enabled || !strategyCanAffordCommand(
      campaign,
      faction,
      "resolve_story_event",
      { event_id: event.id, choice_id: choice.id },
      event.id
    );
    button.addEventListener("click", () => queueStrategyAction("resolve_story_event", {
      event_id: event.id,
      choice_id: choice.id,
    }));
    choices.append(button);
    if (!choice.enabled && choice.disabled_reason) appendTextLine(choices, "strategy-command-lock", choice.disabled_reason);
  });
  panel.append(choices);
  const consequences = (campaign?.world?.scheduled_consequences || []).filter((item) => item.faction_id === faction?.id);
  consequences.slice(0, 2).forEach((item) => appendTextLine(
    panel,
    "strategy-story-thread",
    `未完影响 · 第 ${item.due_month} 月：${item.description}`
  ));
  parent.append(panel);
}

function renderStrategyOfficeCoordination(parent, campaign, faction) {
  const coordination = strategyOfficeCoordination(campaign, faction);
  if (!coordination) return;
  const panel = document.createElement("section");
  panel.className = "strategy-office-coordination";
  const title = document.createElement("strong");
  title.textContent = "本月关键决策";
  panel.append(title);
  const decisions = (coordination.high_consequence_decisions || []).slice(0, 3);
  appendTextLine(panel, "strategy-meta", decisions.length
    ? `这里最多突出 ${decisions.length} 项高后果决定；常规维护由持续方针或 AI 官职承担。`
    : "本月没有额外高后果决定；常规维护继续由持续方针或 AI 官职承担。");
  decisions.forEach((decision, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = decision.planned ? "strategy-decision planned" : "strategy-decision ghost";
    const number = document.createElement("span");
    number.textContent = decision.planned ? "✓" : String(index + 1);
    const body = document.createElement("span");
    const strong = document.createElement("strong");
    strong.textContent = decision.title;
    const detail = document.createElement("span");
    detail.textContent = decision.planned ? `${decision.detail}（已安排）` : decision.detail;
    body.append(strong, detail);
    button.append(number, body);
    button.disabled = !decision.city_id;
    button.addEventListener("click", () => {
      strategyRememberSelectedCity(decision.city_id, campaign);
      renderStrategyPanel();
      focusStrategyCommandPanel();
    });
    panel.append(button);
  });

  const routine = document.createElement("details");
  routine.className = "strategy-routine-maintenance";
  const summary = document.createElement("summary");
  summary.textContent = `常规维护 · ${(coordination.routine_maintenance || []).length} 座城市`;
  routine.append(summary);
  appendTextLine(routine, "strategy-meta", coordination.automation_rule || "默认方针持续生效。 ");
  (coordination.routine_maintenance || []).forEach((item) => {
    const executor = (campaign?.world?.offices || []).find((office) => office.id === item.executor_office_id);
    appendTextLine(
      routine,
      "strategy-meta",
      `${item.city_name} · ${item.policy} · ${item.mode === "ai_emergency" ? `${strategyOfficeLabel(executor, campaign)}在生存危机时自动干预` : "沿用默认方针"}`
    );
  });
  panel.append(routine);

  const feedback = coordination.order_feedback || [];
  if (feedback.length) {
    const feedbackTitle = document.createElement("strong");
    feedbackTitle.textContent = "命令与请求回执";
    panel.append(feedbackTitle);
    feedback.slice(-4).reverse().forEach((item) => {
      const issuer = (campaign?.world?.offices || []).find((office) => office.id === item.issuer_office_id);
      const executor = (campaign?.world?.offices || []).find((office) => office.id === item.executor_office_id);
      appendTextLine(
        panel,
        "strategy-order-feedback",
        `${strategyOfficeLabel(issuer, campaign)} → ${strategyOfficeLabel(executor, campaign)} · ${item.command_cost} 军令 · 预计第 ${item.expected_completion_month} 月 · ${STRATEGY_OFFICE_STATUS_LABELS[item.status] || "已计划"}：${item.result_summary}`
      );
    });
  }
  parent.append(panel);
}

function renderStrategyWarStateBanner(parent, campaign, canResume, isOwner) {
  if (
    campaign?.status === "archived"
    || campaign?.world?.strategic_status?.conclusion?.state
  ) return;
  if (campaign?.status === "active" && canResume) return;
  const banner = document.createElement("div");
  banner.className = "strategy-war-state";
  const text = document.createElement("strong");
  if (campaign?.status !== "active") {
    text.textContent = isOwner ? "战役大厅尚未锁定" : "等待房主锁定初始玩家";
    banner.append(text);
    appendTextLine(
      banner,
      "strategy-meta",
      isOwner ? "锁定后未加入的初始势力会由 AI 操作，真人初始玩家需要在线才能继续。" : "锁定后才能进入正式战役，空席会交给 AI。"
    );
    if (isOwner) {
      const lock = document.createElement("button");
      lock.type = "button";
      lock.className = "primary";
      lock.textContent = "锁定并启用 AI";
      lock.disabled = state.strategyBusy;
      lock.addEventListener("click", () => lockStrategyCampaign(campaign.id));
      banner.append(lock);
    }
  } else {
    const missing = strategyMissingInitialPlayerLabels(campaign);
    text.textContent = "等待初始玩家回到战役";
    banner.append(text);
    appendTextLine(banner, "strategy-meta", `仍缺席：${missing.join("、") || "未知玩家"}`);
  }
  parent.append(banner);
}

function renderStrategyOfficeSwitcher(parent, campaign, activeOffice) {
  const offices = strategyControlledOffices(campaign);
  // 只有一个职位时这里没有"切换"可言，却仍占掉一整行。当前身份已经写在资源条上。
  if (offices.length < 2) return;
  const bar = document.createElement("nav");
  bar.className = "strategy-office-switcher";
  bar.setAttribute("aria-label", "职位切换");
  offices.forEach((office) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = office.id === activeOffice?.id ? "active" : "ghost";
    button.textContent = strategyOfficeLabel(office, campaign);
    button.dataset.officeType = office.office_type;
    button.addEventListener("click", () => {
      state.strategyActiveOfficeId = office.id;
      const rememberedCity = strategyCityById(
        campaign,
        state.strategySelectedCityByContext[strategySelectionContextKey(campaign, office)]
      );
      const managedCity = strategyOfficeManagedCities(campaign, office)[0];
      const currentCity = strategyCityById(campaign, state.strategySelectedCityId);
      const nextCity = rememberedCity
        || (["general", "governor"].includes(office.office_type) ? managedCity : currentCity)
        || managedCity
        || currentCity;
      strategyRememberSelectedCity(nextCity?.id || "", campaign, office);
      renderStrategyPanel();
    });
    bar.append(button);
  });
  parent.append(bar);
}

function createStrategyOfficeDesk(campaign, office, canResume) {
  const desk = document.createElement("section");
  desk.className = "strategy-office-desk";
  const title = document.createElement("h4");
  title.textContent = `${strategyOfficeLabel(office, campaign)}案牍`;
  desk.append(title);
  const duties = (campaign?.world?.office_duties || []).filter((duty) => (
    duty.office_id === office?.id
      && duty.status === "pending"
      && Number(duty.due_month || campaign?.world?.current_month) === Number(campaign?.world?.current_month)
  ));
  const dutyList = document.createElement("div");
  dutyList.className = "strategy-office-duties";
  duties.slice(0, 4).forEach((duty) => {
    const row = document.createElement("div");
    row.className = `strategy-office-duty priority-${duty.priority || 1}`;
    row.textContent = STRATEGY_DUTY_LABELS[duty.duty_type] || "待办职责";
    dutyList.append(row);
  });
  if (!duties.length) appendTextLine(dutyList, "strategy-meta", "本月职责已清。");
  desk.append(dutyList);
  const orders = (campaign?.world?.office_orders || []).filter((order) => order.issuer_office_id === office?.id || order.receiver_office_id === office?.id);
  orders.slice(-3).reverse().forEach((order) => {
    appendTextLine(
      desk,
      "strategy-office-order",
      `${order.receiver_office_id === office?.id ? "收到" : "发出"} · ${order.objective} · ${STRATEGY_OFFICE_STATUS_LABELS[order.status] || order.status}${order.details?.result_summary ? ` · ${order.details.result_summary}` : ""}`
    );
  });
  const isRequest = ["general", "governor"].includes(office?.office_type);
  const receiverIds = isRequest ? [office?.parent_office_id] : (office?.subordinate_office_ids || []);
  const receivers = (campaign?.world?.offices || []).filter((item) => receiverIds.includes(item.id) && item.status === "active");
  if (receivers.length) {
    const controls = document.createElement("div");
    controls.className = "strategy-office-order-controls";
    const receiver = document.createElement("select");
    receivers.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = strategyOfficeLabel(item, campaign);
      receiver.append(option);
    });
    const orderKind = document.createElement("select");
    [
      ["order", "一般目标"],
      ["attack_city", "进攻城市"],
      ["defend_city", "防守城市"],
      ["set_policy", "设置城市方针"],
    ].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      orderKind.append(option);
    });
    const targetCity = document.createElement("select");
    (campaign?.world?.cities || []).forEach((city) => {
      const option = document.createElement("option");
      option.value = city.id;
      option.textContent = `${city.name} · ${strategyFactionName(campaign, city.owner_faction_id)}`;
      option.dataset.ownerFactionId = city.owner_faction_id;
      targetCity.append(option);
    });
    const cityPolicy = document.createElement("select");
    (campaign?.world?.policy_choices || []).forEach((policy) => {
      const option = document.createElement("option");
      option.value = policy;
      option.textContent = policy;
      cityPolicy.append(option);
    });
    orderKind.hidden = isRequest;
    targetCity.hidden = true;
    cityPolicy.hidden = true;
    const syncMilitaryOrder = () => {
      const military = !isRequest && ["attack_city", "defend_city"].includes(orderKind.value);
      const policyOrder = !isRequest && orderKind.value === "set_policy";
      targetCity.hidden = !(military || policyOrder);
      cityPolicy.hidden = !policyOrder;
      Array.from(targetCity.children).forEach((option) => {
        option.disabled = policyOrder && option.dataset.ownerFactionId !== office?.faction_id;
      });
      if (military && office?.office_type === "lord") {
        const grand = receivers.find((item) => item.office_type === "grand_general");
        if (grand) receiver.value = grand.id;
      }
      if (policyOrder && office?.office_type === "lord") {
        const governor = receivers.find((item) => item.id === receiver.value && item.office_type === "governor")
          || receivers.find((item) => item.office_type === "governor");
        if (governor) {
          receiver.value = governor.id;
          const governedCityId = governor.managed_entity_ids?.[0];
          if (governedCityId) targetCity.value = governedCityId;
        }
      }
    };
    orderKind.addEventListener("change", syncMilitaryOrder);
    receiver.addEventListener("change", syncMilitaryOrder);
    syncMilitaryOrder();
    const objective = document.createElement("input");
    objective.type = "text";
    objective.maxLength = 120;
    objective.placeholder = isRequest ? "向上级请求支援或批准" : "向直属下级下达目标";
    const issue = document.createElement("button");
    issue.type = "button";
    issue.className = "primary";
    issue.textContent = isRequest ? "提交请求" : "下达命令 · 1军令";
    issue.disabled = state.strategyBusy || !canResume;
    issue.addEventListener("click", () => {
      const military = !isRequest && ["attack_city", "defend_city"].includes(orderKind.value);
      const policyOrder = !isRequest && orderKind.value === "set_policy";
      const objectiveText = objective.value.trim() || (
        military
          ? `${orderKind.value === "attack_city" ? "进攻" : "防守"}${strategyCityName(campaign, targetCity.value)}`
          : policyOrder
            ? `将${strategyCityName(campaign, targetCity.value)}设为${cityPolicy.value}`
            : ""
      );
      if (!objectiveText) {
        objective.focus();
        return;
      }
      queueStrategyAction(isRequest ? "send_office_request" : "issue_office_order", {
        receiver_office_id: receiver.value,
        objective: objectiveText,
        office_order_type: isRequest ? "request" : orderKind.value,
        target_entity_id: military || policyOrder ? targetCity.value : "",
        city_policy: policyOrder ? cityPolicy.value : "",
        priority: 1,
      });
    });
    controls.append(receiver, orderKind, targetCity, cityPolicy, objective, issue);
    desk.append(controls);
  }
  return desk;
}

function createLordHeroBindingPanel(campaign, office, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-ritual-bindings";
  const title = document.createElement("h4");
  title.textContent = "祭祀绑定名册";
  panel.append(title);
  const faction = (campaign?.world?.factions || []).find((item) => item.id === office?.faction_id);
  const capacity = faction?.hero_ritual_capacity || { maximum: 0, used: 0, remaining: 0 };
  appendTextLine(panel, "strategy-meta", `职位承载 ${capacity.used}/${capacity.maximum} · 可继续召唤 ${capacity.remaining}`);
  const heroes = (campaign?.world?.strategic_hero_pool || []).filter((hero) => (
    hero.faction_id === office?.faction_id && hero.ritual_city_id && hero.office_id !== office?.id
  ));
  heroes.forEach((hero) => {
    const row = document.createElement("div");
    row.className = "strategy-hero-duty-row strategy-binding-row";
    const identity = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = hero.name || hero.code;
    identity.append(name);
    const heldOffice = (campaign?.world?.offices || []).find((item) => item.id === hero.office_id);
    appendTextLine(identity, "strategy-meta", `${strategyCityName(campaign, hero.ritual_city_id)}祭祀场 · ${heldOffice ? strategyOfficeLabel(heldOffice, campaign) : "待任命"}`);
    const unbind = document.createElement("button");
    unbind.type = "button";
    unbind.className = "danger";
    unbind.textContent = "解除绑定";
    unbind.disabled = state.strategyBusy || !canResume;
    unbind.addEventListener("click", () => queueStrategyAction("unbind_strategic_hero", { hero_code: hero.code }));
    row.append(identity, unbind);
    panel.append(row);
  });
  if (!heroes.length) appendTextLine(panel, "strategy-meta", "没有可由主公解除绑定的武将。");
  return panel;
}

function createStrategyHeroAppointmentPanel(campaign, office, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-hero-appointments";
  const title = document.createElement("h4");
  title.textContent = "任命武将";
  panel.append(title);
  const officeOrder = { grand_general: 0, general: 1, governor: 2 };
  const offices = (campaign?.world?.offices || []).filter((item) => (
    item.faction_id === office?.faction_id
      && item.office_type !== "lord"
      && item.status !== "disabled"
  )).sort((first, second) => (
    (officeOrder[first.office_type] ?? 9) - (officeOrder[second.office_type] ?? 9)
      || strategyOfficeLabel(first, campaign).localeCompare(strategyOfficeLabel(second, campaign), "zh-CN")
  ));
  const heroes = (campaign?.world?.strategic_hero_pool || []).filter((hero) => (
    hero.faction_id === office?.faction_id && hero.status === "serving" && !hero.office_id
  ));
  if (!offices.length || !heroes.length) {
    appendTextLine(panel, "strategy-meta", heroes.length ? "当前没有可任命职位。" : "先在祭祀场召唤武将，再进行任命。");
    return panel;
  }
  const controls = document.createElement("div");
  controls.className = "strategy-office-order-controls";
  const heroSelect = document.createElement("select");
  heroes.forEach((hero) => {
    const option = document.createElement("option");
    option.value = hero.code;
    option.textContent = `${hero.name} · ${hero.role || "武将"}`;
    heroSelect.append(option);
  });
  const officeSelect = document.createElement("select");
  offices.forEach((target) => {
    const option = document.createElement("option");
    option.value = target.id;
    const holder = target.holder_type === "hero" ? ` · 现任 ${strategyHeroName(campaign, target.holder_id)}` : " · 空缺";
    option.textContent = `${strategyOfficeLabel(target, campaign)}${holder}`;
    officeSelect.append(option);
  });
  const appoint = document.createElement("button");
  appoint.type = "button";
  appoint.className = "primary";
  appoint.textContent = "任命 · 1军令";
  appoint.disabled = state.strategyBusy || !canResume;
  appoint.addEventListener("click", () => queueStrategyAction("appoint_strategic_hero", {
    target_office_id: officeSelect.value,
    hero_code: heroSelect.value,
  }));
  controls.append(createStrategyField("武将", heroSelect), createStrategyField("任命职位", officeSelect), appoint);
  panel.append(controls);
  return panel;
}

function createRoleWorkspaceHeader(campaign, office, title, subtitle) {
  const header = document.createElement("header");
  header.className = `strategy-role-header role-${office?.office_type || "none"}`;
  const copy = document.createElement("div");
  appendTextLine(copy, "meta-label", strategyOfficeLabel(office, campaign));
  const heading = document.createElement("h3");
  heading.textContent = title;
  copy.append(heading);
  appendTextLine(copy, "strategy-meta", subtitle);
  const seal = document.createElement("strong");
  seal.className = "strategy-role-seal";
  seal.textContent = STRATEGY_OFFICE_LABELS[office?.office_type] || "职位";
  header.append(copy, seal);
  return header;
}

function createLordRitualPanel(campaign, office, faction, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-lord-ritual";
  const title = document.createElement("h4");
  title.textContent = "举行召唤祭祀";
  panel.append(title);
  const capacity = faction?.hero_ritual_capacity || { maximum: 0, used: 0, remaining: 0 };
  appendTextLine(panel, "strategy-meta", `当前职位承载 ${capacity.used}/${capacity.maximum}；每次祭祀消耗 30 城市以太。`);
  const cities = (campaign?.world?.cities || []).filter((city) => (
    city.owner_faction_id === faction?.id && Number(city.building_levels?.ritual_site || 0) > 0
  ));
  const citySelect = document.createElement("select");
  cities.forEach((city) => {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = `${city.name} · 祭祀场 ${city.building_levels?.ritual_site || 0}级 · 以太 ${city.resources?.ether || 0}`;
    citySelect.append(option);
  });
  citySelect.value = cities[0]?.id || "";
  const issue = document.createElement("button");
  issue.type = "button";
  issue.className = "primary";
  issue.textContent = "举行祭祀 · 1 军令";
  const update = () => {
    const city = strategyCityById(campaign, citySelect.value);
    issue.disabled = state.strategyBusy || !canResume || !citySelect.children.length || Number(capacity.remaining || 0) < 1 || Number(city?.resources?.ether || 0) < 30;
  };
  citySelect.addEventListener("change", update);
  issue.addEventListener("click", () => queueStrategyAction("perform_hero_ritual", { city_id: citySelect.value }));
  panel.append(createStrategyField("祭祀城市", citySelect), issue);
  update();
  return panel;
}

function createLordHeroDutyPanel(campaign, office, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-hero-duty-board";
  const title = document.createElement("h4");
  title.textContent = "武将任务总览";
  panel.append(title);
  const heroes = (campaign?.world?.strategic_hero_pool || []).filter((hero) => hero.faction_id === office?.faction_id && hero.status !== "roaming");
  const cities = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === office?.faction_id);
  const dutyLabels = {
    reserve: "待命",
    administration: "辅佐内政",
    training: "训练军队",
    garrison: "驻守城市",
    campaign: "随军出征",
  };
  heroes.forEach((hero) => {
    const row = document.createElement("div");
    row.className = "strategy-hero-duty-row";
    const identity = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = hero.name || hero.code;
    identity.append(name);
    const heldOffice = (campaign?.world?.offices || []).find((entry) => entry.id === hero.office_id);
    appendTextLine(identity, "strategy-meta", heldOffice ? strategyOfficeLabel(heldOffice, campaign) : "未任职");
    appendTextLine(
      identity,
      "strategy-meta",
      `忠诚 ${hero.loyalty ?? 50} · ${hero.loyalty_band?.label || "稳定"} · 对主公关系 ${hero.lord_relationship ?? "—"}`
    );
    if (hero.specialty) {
      appendTextLine(identity, "strategy-meta", `专长：${hero.specialty.name} · ${hero.specialty.effect}`);
    }
    if (hero.personal_mission) {
      const missionStatus = {
        active: `进行中 ${hero.personal_mission.progress}/${hero.personal_mission.required} · 截止第 ${hero.personal_mission.due_month} 月`,
        completed: "已完成",
        failed: "已逾期",
      };
      appendTextLine(
        identity,
        "strategy-meta",
        `个人任务：${hero.personal_mission.name} · ${missionStatus[hero.personal_mission.status] || hero.personal_mission.status}`
      );
    }
    const duty = document.createElement("select");
    Object.entries(dutyLabels).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      duty.append(option);
    });
    duty.value = hero.assignment_type || "reserve";
    const target = document.createElement("select");
    cities.forEach((city) => {
      const option = document.createElement("option");
      option.value = city.id;
      option.textContent = city.name;
      target.append(option);
    });
    target.value = hero.assignment_target_id || cities[0]?.id || "";
    const assign = document.createElement("button");
    assign.type = "button";
    assign.className = "ghost";
    assign.textContent = "安排";
    const syncTarget = () => {
      target.hidden = !["training", "garrison"].includes(duty.value);
      const accepted = hero.command_acceptance?.[duty.value] !== false;
      assign.disabled = state.strategyBusy || !canResume || !accepted;
      assign.textContent = accepted ? "安排" : "本月拒绝";
      assign.title = accepted ? "" : `${hero.name || hero.code}因当前忠诚状态拒绝这项任务。`;
    };
    duty.addEventListener("change", syncTarget);
    syncTarget();
    assign.addEventListener("click", () => queueStrategyAction("assign_strategic_hero_duty", {
      hero_code: hero.code,
      assignment_type: duty.value,
      target_id: target.hidden ? "" : target.value,
    }));
    row.append(identity, duty, target, assign);
    panel.append(row);
  });
  if (!heroes.length) appendTextLine(panel, "strategy-meta", "当前没有可安排的已仕官武将。");
  return panel;
}

function createGrandGeneralMilitaryPanel(campaign, office, faction, canResume, selectedCity) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-theater-command";
  const title = document.createElement("h4");
  title.textContent = "战区军务";
  panel.append(title);
  const generals = (campaign?.world?.offices || []).filter((entry) => (office?.subordinate_office_ids || []).includes(entry.id));
  const roster = document.createElement("div");
  roster.className = "strategy-general-roster";
  generals.forEach((general) => {
    const item = document.createElement("div");
    item.className = "strategy-general-roster-item";
    const holder = general.holder_type === "hero" ? strategyHeroName(campaign, general.holder_id) : "职位空缺";
    appendTextLine(item, "strategy-meta", strategyOfficeLabel(general, campaign));
    const strong = document.createElement("strong");
    strong.textContent = holder;
    item.append(strong);
    appendTextLine(item, "strategy-meta", `${(general.managed_entity_ids || []).filter((id) => strategyCityById(campaign, id)).map((id) => strategyCityName(campaign, id)).join("、") || "尚未分配驻地"}`);
    appendTextLine(item, "strategy-unit-ledger", `军团单位：${strategyRegisteredUnitsLabel(campaign, general.unit_inventory)}`);
    roster.append(item);
  });
  panel.append(roster);
  const cities = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === faction?.id);
  const citySelect = document.createElement("select");
  cities.forEach((city) => {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = `${city.name} · ${strategyRegisteredUnitsLabel(campaign, city.registered_units)}`;
    citySelect.append(option);
  });
  citySelect.value = cities[0]?.id || "";
  if (selectedCity && cities.some((city) => city.id === selectedCity.id)) citySelect.value = selectedCity.id;
  const generalSelect = document.createElement("select");
  generals.filter((general) => general.status === "active").forEach((general) => {
    const option = document.createElement("option");
    option.value = general.id;
    option.textContent = `${strategyOfficeLabel(general, campaign)} · ${strategyHeroName(campaign, general.holder_id)}`;
    generalSelect.append(option);
  });
  generalSelect.value = generalSelect.children[0]?.value || "";
  const unitSelect = document.createElement("select");
  const count = document.createElement("input");
  count.type = "number";
  count.min = "1";
  count.max = "12";
  count.value = "1";
  const transfer = document.createElement("button");
  transfer.type = "button";
  transfer.className = "primary";
  transfer.textContent = "调拨给直属将军 · 1 军令";
  const syncUnits = () => {
    unitSelect.innerHTML = "";
    const city = strategyCityById(campaign, citySelect.value);
    Object.entries(city?.registered_units || {}).filter(([, amount]) => Number(amount) > 0).forEach(([unitType, amount]) => {
      const option = document.createElement("option");
      option.value = unitType;
      option.textContent = `${strategyRegisteredUnitsLabel(campaign, { [unitType]: amount })} 可调`;
      unitSelect.append(option);
    });
    unitSelect.value = unitSelect.children[0]?.value || "";
    transfer.disabled = state.strategyBusy || !canResume || !unitSelect.children.length || !generalSelect.children.length;
  };
  citySelect.addEventListener("change", syncUnits);
  transfer.addEventListener("click", () => queueStrategyAction("transfer_registered_units", {
    city_id: citySelect.value,
    general_office_id: generalSelect.value,
    unit_type: unitSelect.value,
    count: Math.max(1, Number(count.value || 1)),
  }));
  panel.append(
    createStrategyField("调出城市", citySelect),
    createStrategyField("接收将军", generalSelect),
    createStrategyField("确切兵种", unitSelect),
    createStrategyField("数量", count),
    transfer,
  );
  syncUnits();

  const requests = (campaign?.world?.office_orders || []).filter((order) => (
    order.order_type === "unit_request" && order.receiver_office_id === office?.id && order.status === "pending"
  ));
  if (requests.length) {
    const requestTitle = document.createElement("h4");
    requestTitle.textContent = "待批调兵申请";
    panel.append(requestTitle);
    requests.forEach((request) => {
      const row = document.createElement("div");
      row.className = "strategy-unit-request";
      const general = (campaign?.world?.offices || []).find((item) => item.id === request.issuer_office_id);
      appendTextLine(row, "strategy-meta", `${strategyOfficeLabel(general, campaign)} · ${request.objective} · ${strategyCityName(campaign, request.details?.city_id || request.target_entity_id)}`);
      const approve = document.createElement("button");
      approve.type = "button";
      approve.className = "primary";
      approve.textContent = "批准调拨";
      approve.disabled = state.strategyBusy || !canResume;
      approve.addEventListener("click", () => queueStrategyAction("approve_registered_unit_request", { request_id: request.id }));
      row.append(approve);
      panel.append(row);
    });
  }
  return panel;
}

function createGeneralLogisticsPanel(campaign, office, faction, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-general-logistics";
  const title = document.createElement("h4");
  title.textContent = "军团编制与请兵";
  panel.append(title);
  appendTextLine(panel, "strategy-unit-ledger strategy-unit-ledger-prominent", `当前军团：${strategyRegisteredUnitsLabel(campaign, office?.unit_inventory)}`);
  const cities = (campaign?.world?.cities || []).filter((city) => (
    city.owner_faction_id === faction?.id && Object.values(city.registered_units || {}).some((amount) => Number(amount) > 0)
  ));
  if (!cities.length) {
    appendTextLine(panel, "strategy-meta", "己方城市暂无已注册单位；请城主先注册士兵。");
    return panel;
  }
  const citySelect = document.createElement("select");
  cities.forEach((city) => {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = `${city.name} · ${strategyRegisteredUnitsLabel(campaign, city.registered_units)}`;
    citySelect.append(option);
  });
  citySelect.value = cities[0]?.id || "";
  const unitSelect = document.createElement("select");
  const count = document.createElement("input");
  count.type = "number";
  count.min = "1";
  count.max = "12";
  count.value = "1";
  const request = document.createElement("button");
  request.type = "button";
  request.className = "primary";
  request.textContent = "请示直属大将军";
  const sync = () => {
    unitSelect.innerHTML = "";
    const city = strategyCityById(campaign, citySelect.value);
    Object.entries(city?.registered_units || {}).filter(([, amount]) => Number(amount) > 0).forEach(([unitType, amount]) => {
      const option = document.createElement("option");
      option.value = unitType;
      option.textContent = `${strategyRegisteredUnitsLabel(campaign, { [unitType]: amount })} 可申请`;
      unitSelect.append(option);
    });
    unitSelect.value = unitSelect.children[0]?.value || "";
    request.disabled = state.strategyBusy || !canResume || !unitSelect.children.length;
  };
  citySelect.addEventListener("change", sync);
  request.addEventListener("click", () => queueStrategyAction("request_registered_units", {
    city_id: citySelect.value,
    unit_type: unitSelect.value,
    count: Math.max(1, Number(count.value || 1)),
  }));
  panel.append(
    createStrategyField("兵源城市", citySelect),
    createStrategyField("兵种", unitSelect),
    createStrategyField("数量", count),
    request,
  );
  sync();
  return panel;
}

function createGeneralArmyPanel(campaign, office, faction, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-army-form";
  const title = document.createElement("h4");
  title.textContent = "持久军队";
  panel.append(title);
  const armies = (campaign?.world?.armies || []).filter((army) => (
    army.commander_office_id === office?.id && !["disbanded", "destroyed"].includes(army.status)
  ));
  const army = armies[0];
  const plannedManeuver = army ? (campaign?.queued_actions || []).find((action) => (
    action.action_type === "set_army_movement" && action.action_key === army.id
  )) : null;
  if (army) {
    appendTextLine(panel, "strategy-unit-ledger strategy-unit-ledger-prominent", `现役 ${army.id} · ${strategyRegisteredUnitsLabel(campaign, army.unit_inventory)}`);
    appendTextLine(panel, "strategy-meta", `兵员 ${strategyNumber(army.manpower)} · 粮草 ${strategyNumber(army.supply)}/${strategyNumber(army.supply_capacity)} · 士气 ${strategyNumber(army.morale)}`);
    appendTextLine(panel, "strategy-meta", `状态 ${strategyArmyStatusLabel(army.status)} · 命令 ${strategyArmyOrderLabel(army.current_order)} · 当前位置 ${strategyNodeName(campaign, army.location_node_id)}`);
    if (plannedManeuver) {
      appendTextLine(panel, "strategy-meta", `本月已计划：${strategyArmyOrderLabel(plannedManeuver.payload?.movement_order)}（再次下令会替换）`);
    }
    const supplySource = strategyCityById(campaign, army.supply_source_city_id);
    appendTextLine(panel, "strategy-army-supply", `补给线 ${strategyArmySupplyStatusLabel(army.supply_line_status)} · 来源 ${supplySource?.name || "无"} · 距离 ${army.supply_distance ?? "—"} · 月需 ${strategyNumber(army.monthly_supply_need)}`);
    appendTextLine(panel, "strategy-meta", `上月接收 ${strategyNumber(army.last_supply_received)} / 消耗 ${strategyNumber(army.last_supply_consumed)}${Number(army.starvation_months || 0) ? ` · 已连续断粮 ${strategyNumber(army.starvation_months)} 月` : ""}`);
    if (army.last_cold_exposure_month) {
      appendTextLine(
        panel,
        "strategy-army-cold-loss",
        `第 ${army.last_cold_exposure_month} 月穿越严寒路线：额外损失 ${strategyNumber(army.last_cold_supply_loss)} 粮草 / ${strategyNumber(army.last_cold_morale_loss)} 士气`
      );
    }
    if ((army.supply_line_node_ids || []).length) {
      appendTextLine(panel, "strategy-army-supply-route", `补给路径：${army.supply_line_node_ids.map((nodeId) => strategyNodeName(campaign, nodeId)).join(" → ")}`);
    }
    if ((army.route_node_ids || []).length) {
      appendTextLine(panel, "strategy-army-route", `路线：${army.route_node_ids.map((nodeId) => strategyNodeName(campaign, nodeId)).join(" → ")}`);
      appendTextLine(panel, "strategy-meta", `进度 ${Number(army.route_progress_index || 0)}/${Math.max(0, army.route_node_ids.length - 1)} · 预计第 ${army.estimated_arrival_month} 月抵达`);
    }
    const encounter = strategyEncounterForArmy(campaign, army.id);
    if (encounter) {
      appendTextLine(panel, "strategy-army-encounter", `遭遇 ${strategyNodeName(campaign, encounter.node_id)} · 第 ${encounter.opened_month} 月开始 · ${Object.keys(encounter.faction_army_ids || {}).map((factionId) => strategyFactionName(campaign, factionId)).join(" / ")}`);
    }
  } else {
    appendTextLine(panel, "strategy-meta", "尚未编成现役军队。单位与粮草会从将军库存和驻城真实转入。");
  }

  const inventory = office?.unit_inventory || {};
  const unitInputs = {};
  let defaultsAssigned = false;
  Object.entries(inventory).filter(([, amount]) => Number(amount) > 0).forEach(([unitType, amount]) => {
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = String(amount);
    input.value = defaultsAssigned ? "0" : "1";
    defaultsAssigned = true;
    unitInputs[unitType] = input;
    panel.append(createStrategyField(`${strategyRegisteredUnitsLabel(campaign, { [unitType]: amount })} 转入`, input));
  });
  const cities = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === faction?.id);
  const citySelect = document.createElement("select");
  cities.forEach((city) => {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = `${city.name} · 可装粮 ${strategyNumber(city.resources?.food)}`;
    citySelect.append(option);
  });
  if (army?.home_city_id && cities.some((city) => city.id === army.home_city_id)) citySelect.value = army.home_city_id;
  const supply = document.createElement("input");
  supply.type = "number";
  supply.min = "50";
  supply.value = "50";
  const form = document.createElement("button");
  form.type = "button";
  form.className = "primary";
  form.textContent = army ? "补充军队 · 1 军令" : "编成军队 · 1 军令";
  form.disabled = state.strategyBusy || !canResume || !cities.length || !Object.keys(unitInputs).length || Boolean(army && army.status !== "garrisoned");
  form.addEventListener("click", () => queueStrategyAction("form_army", {
    city_id: citySelect.value,
    unit_inventory: Object.fromEntries(Object.entries(unitInputs).map(([unitType, input]) => [unitType, Math.max(0, Number(input.value || 0))])),
    supply: Math.max(0, Number(supply.value || 0)),
  }));
  panel.append(createStrategyField("编军城市", citySelect), createStrategyField("装载粮草", supply), form);

  if (army) {
    const currentCity = (campaign?.world?.cities || []).find((city) => (
      city.node_id === army.location_node_id && city.owner_faction_id === faction?.id
    ));
    const loadSupply = document.createElement("input");
    loadSupply.type = "number";
    const maximumLoad = Math.max(0, Math.min(
      Number(currentCity?.resources?.food || 0),
      Number(army.supply_capacity || 0) - Number(army.supply || 0),
    ));
    loadSupply.min = maximumLoad > 0 ? "1" : "0";
    loadSupply.max = String(maximumLoad);
    loadSupply.value = maximumLoad > 0 ? String(Math.min(50, maximumLoad)) : "0";
    const load = document.createElement("button");
    load.type = "button";
    load.className = "primary";
    load.textContent = "驻城装粮 · 1 军令";
    load.disabled = state.strategyBusy || !canResume || army.status !== "garrisoned" || Number(loadSupply.max || 0) <= 0;
    load.addEventListener("click", () => queueStrategyAction("load_army_supply", {
      army_id: army.id,
      supply: Math.max(1, Number(loadSupply.value || 1)),
    }));
    panel.append(createStrategyField(`携行补给（可装 ${loadSupply.max}）`, loadSupply), load);
    const destination = document.createElement("select");
    (campaign?.world?.nodes || []).filter((node) => strategyMapNodeId(node) !== army.location_node_id).forEach((node) => {
      const option = document.createElement("option");
      option.value = strategyMapNodeId(node);
      option.textContent = strategyNodeName(campaign, option.value);
      destination.append(option);
    });
    if (army.destination_node_id && army.destination_node_id !== army.location_node_id) destination.value = army.destination_node_id;
    const march = document.createElement("button");
    march.type = "button";
    march.className = "primary";
    march.textContent = army.status === "marching" ? "改道 · 1 军令" : "下达行军 · 1 军令";
    march.disabled = state.strategyBusy || !canResume || !destination.children.length || !["garrisoned", "deployed", "marching"].includes(army.status);
    march.addEventListener("click", () => queueStrategyAction("set_army_movement", {
      army_id: army.id,
      movement_order: "march",
      destination_node_id: destination.value,
    }));
    panel.append(createStrategyField("行军目的地", destination), march);
    if (army.status === "marching" || (plannedManeuver && plannedManeuver.payload?.movement_order !== "hold")) {
      const halt = document.createElement("button");
      halt.type = "button";
      halt.className = "ghost";
      halt.textContent = army.status === "marching" ? "停止行军 · 1 军令" : "取消本月机动 · 1 军令";
      halt.disabled = state.strategyBusy || !canResume;
      halt.addEventListener("click", () => queueStrategyAction("set_army_movement", {
        army_id: army.id,
        movement_order: "hold",
      }));
      panel.append(halt);
    }
    const activeEncounter = strategyEncounterForArmy(campaign, army.id);
    const currentNode = (campaign?.world?.nodes || []).find((node) => strategyMapNodeId(node) === army.location_node_id);
    const adjacentNodeIds = currentNode?.connected_node_ids || [];
    if (activeEncounter && ["engaged", "retreating"].includes(army.status)) {
      const retreatDestination = document.createElement("select");
      adjacentNodeIds.filter((nodeId) => !(campaign?.world?.armies || []).some((other) => (
        !["disbanded", "destroyed"].includes(other.status)
        && other.location_node_id === nodeId
        && strategyArmiesHostile(campaign, army, other)
      ))).forEach((nodeId) => {
        const option = document.createElement("option");
        option.value = nodeId;
        option.textContent = strategyNodeName(campaign, nodeId);
        retreatDestination.append(option);
      });
      const retreat = document.createElement("button");
      retreat.type = "button";
      retreat.className = "ghost";
      retreat.textContent = army.status === "retreating" ? "改换退路 · 1 军令" : "撤出遭遇 · 1 军令";
      retreat.disabled = state.strategyBusy || !canResume || !retreatDestination.children.length;
      retreat.addEventListener("click", () => queueStrategyAction("set_army_movement", {
        army_id: army.id,
        movement_order: "retreat",
        destination_node_id: retreatDestination.value,
      }));
      panel.append(createStrategyField("合法退路", retreatDestination), retreat);
      const sideCount = Object.keys(activeEncounter.faction_army_ids || {}).length;
      panel.append(createStrategicBattleResolver(
        campaign,
        "encounter",
        activeEncounter.id,
        canResume,
        sideCount === 2,
      ));
      if (sideCount !== 2) appendTextLine(panel, "strategy-meta", "三方遭遇需先撤退或外交拆分为两方，才能进入战斗。");
    } else if (!["besieging", "retreating"].includes(army.status)) {
      const nearbyEnemies = (campaign?.world?.armies || []).filter((other) => (
        !["disbanded", "destroyed", "engaged", "besieging", "retreating"].includes(other.status)
        && adjacentNodeIds.includes(other.location_node_id)
        && strategyArmiesHostile(campaign, army, other)
      ));
      if (nearbyEnemies.length) {
        const targetArmy = document.createElement("select");
        nearbyEnemies.forEach((other) => {
          const option = document.createElement("option");
          option.value = other.id;
          option.textContent = `${strategyFactionName(campaign, other.faction_id)} · ${strategyNodeName(campaign, other.location_node_id)} · 兵员 ${strategyNumber(other.manpower)}`;
          targetArmy.append(option);
        });
        const intercept = document.createElement("button");
        intercept.type = "button";
        intercept.className = "primary";
        intercept.textContent = "拦截相邻敌军 · 1 军令";
        intercept.disabled = state.strategyBusy || !canResume;
        intercept.addEventListener("click", () => queueStrategyAction("set_army_movement", {
          army_id: army.id,
          movement_order: "intercept",
          target_army_id: targetArmy.value,
        }));
        panel.append(createStrategyField("拦截目标", targetArmy), intercept);
      }
      const nearbyEncounters = strategyActiveEncounters(campaign).filter((encounter) => (
        adjacentNodeIds.includes(encounter.node_id)
        && Object.prototype.hasOwnProperty.call(encounter.faction_army_ids || {}, faction?.id)
      ));
      if (nearbyEncounters.length) {
        const targetEncounter = document.createElement("select");
        nearbyEncounters.forEach((encounter) => {
          const option = document.createElement("option");
          option.value = encounter.id;
          option.textContent = `${strategyNodeName(campaign, encounter.node_id)} · ${strategyEncounterArmyIds(encounter).length} 军交战`;
          targetEncounter.append(option);
        });
        const reinforce = document.createElement("button");
        reinforce.type = "button";
        reinforce.className = "primary";
        reinforce.textContent = "增援己方遭遇 · 1 军令";
        reinforce.disabled = state.strategyBusy || !canResume;
        reinforce.addEventListener("click", () => queueStrategyAction("set_army_movement", {
          army_id: army.id,
          movement_order: "reinforce",
          target_encounter_id: targetEncounter.value,
        }));
        panel.append(createStrategyField("增援目标", targetEncounter), reinforce);
      }
    }
    const disband = document.createElement("button");
    disband.type = "button";
    disband.className = "ghost";
    disband.textContent = "解散并归库 · 1 军令";
    disband.disabled = state.strategyBusy || !canResume || army.status !== "garrisoned";
    disband.addEventListener("click", () => queueStrategyAction("disband_army", { army_id: army.id }));
    panel.append(disband);
  }
  return panel;
}

function createGeneralSiegePanel(campaign, office, faction, canResume) {
  const army = (campaign?.world?.armies || []).find((item) => (
    item.commander_office_id === office?.id && !["disbanded", "destroyed"].includes(item.status)
  ));
  const siege = army ? strategySiegeForArmy(campaign, army.id) : null;
  if (!siege) return null;
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-siege-command";
  const title = document.createElement("h4");
  title.textContent = `围攻 ${strategyNodeName(campaign, siege.node_id)}`;
  panel.append(title);
  appendTextLine(panel, "strategy-siege-status", `${strategySiegeStatusLabel(siege.status)} · 城防 ${strategyNumber(siege.fortification_remaining)}/${strategyNumber(siege.fortification_initial)}`);
  appendTextLine(panel, "strategy-meta", `当前方针 ${strategySiegeAttackerStanceLabel(siege.attacker_stance)} · 守方 ${strategySiegeDefenderStanceLabel(siege.defender_stance)} · 第 ${strategyNumber(siege.started_month)} 月开始`);
  appendTextLine(panel, "strategy-meta", `上月：城内耗粮 ${strategyNumber(siege.last_city_food_consumed)} · 守军损失 ${strategyNumber(siege.last_garrison_lost)} · 城防损失 ${strategyNumber(siege.last_fortification_damage)}`);
  if (siege.battle_trigger) appendTextLine(panel, "strategy-siege-alert", `战斗触发：${siege.battle_trigger === "breakout" ? "守军突围" : "城防突破后的攻城战"}`);

  const stance = document.createElement("select");
  [["blockade", "封锁：稳定削弱城防"], ["starve", "断粮：扩大城内粮耗"], ["assault", "强攻：高城防伤害，损耗军粮与士气"]].forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    option.selected = value === siege.attacker_stance;
    stance.append(option);
  });
  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "primary";
  apply.textContent = "调整围城方针 · 1 军令";
  apply.disabled = state.strategyBusy || !canResume || siege.status === "battle_pending";
  apply.addEventListener("click", () => queueStrategyAction("set_siege_attacker_stance", {
    siege_id: siege.id,
    stance: stance.value,
  }));
  panel.append(createStrategyField("攻方方针", stance), apply);
  if (siege.battle_trigger && ["breached", "battle_pending"].includes(siege.status)) {
    panel.append(createStrategicBattleResolver(campaign, "siege", siege.id, canResume));
  }

  const node = (campaign?.world?.nodes || []).find((item) => strategyMapNodeId(item) === siege.node_id);
  const destination = document.createElement("select");
  (node?.connected_node_ids || []).filter((nodeId) => !(campaign?.world?.armies || []).some((other) => (
    other.id !== army.id && !["disbanded", "destroyed"].includes(other.status)
    && other.location_node_id === nodeId && strategyArmiesHostile(campaign, army, other)
  ))).forEach((nodeId) => {
    const option = document.createElement("option");
    option.value = nodeId;
    option.textContent = strategyNodeName(campaign, nodeId);
    destination.append(option);
  });
  const withdraw = document.createElement("button");
  withdraw.type = "button";
  withdraw.className = "ghost strategy-danger-action";
  withdraw.textContent = "撤围 · 1 军令";
  withdraw.disabled = state.strategyBusy || !canResume || !destination.children.length;
  withdraw.addEventListener("click", () => queueStrategyAction("set_siege_attacker_stance", {
    siege_id: siege.id,
    stance: "withdraw",
    destination_node_id: destination.value,
  }));
  panel.append(createStrategyField("安全退路", destination), withdraw);
  return panel;
}

function createGovernorSiegePanel(campaign, office, faction, canResume) {
  const managedCityIds = new Set(office?.managed_entity_ids || []);
  const siege = strategyActiveSieges(campaign).find((item) => (
    item.defender_faction_id === faction?.id && managedCityIds.has(item.city_id)
  ));
  if (!siege) return null;
  const city = strategyCityById(campaign, siege.city_id);
  const panel = document.createElement("section");
  panel.className = "strategy-office-desk strategy-siege-command is-defender";
  const title = document.createElement("h4");
  title.textContent = `${city?.name || strategyNodeName(campaign, siege.node_id)}守城议事`;
  panel.append(title);
  appendTextLine(panel, "strategy-siege-status", `${strategySiegeStatusLabel(siege.status)} · 城防 ${strategyNumber(siege.fortification_remaining)}/${strategyNumber(siege.fortification_initial)} · 城粮 ${strategyNumber(city?.resources?.food)} · 守军 ${strategyNumber(city?.resources?.troops)}`);
  appendTextLine(panel, "strategy-meta", `攻方 ${strategyFactionName(campaign, siege.attacker_faction_id)} · ${strategySiegeAttackerStanceLabel(siege.attacker_stance)} · 当前守策 ${strategySiegeDefenderStanceLabel(siege.defender_stance)}`);
  const stance = document.createElement("select");
  [["hold", "坚守：减少城防损伤，额外耗粮"], ["await_relief", "待援：保存粮草，等待援军"], ["breakout", "突围：停止消耗并触发战斗"]].forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    option.selected = value === siege.defender_stance;
    stance.append(option);
  });
  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "primary";
  apply.textContent = "调整守城方针 · 1 军令";
  apply.disabled = state.strategyBusy || !canResume;
  apply.addEventListener("click", () => queueStrategyAction("set_siege_defender_stance", {
    siege_id: siege.id,
    stance: stance.value,
  }));
  const surrender = document.createElement("button");
  surrender.type = "button";
  surrender.className = "ghost strategy-danger-action";
  surrender.textContent = "开城投降 · 1 军令";
  surrender.disabled = state.strategyBusy || !canResume;
  surrender.addEventListener("click", () => queueStrategyAction("set_siege_defender_stance", {
    siege_id: siege.id,
    stance: "surrender",
  }));
  panel.append(createStrategyField("守方方针", stance), apply, surrender);
  if (siege.battle_trigger && ["breached", "battle_pending"].includes(siege.status)) {
    panel.append(createStrategicBattleResolver(campaign, "siege", siege.id, canResume));
  }
  return panel;
}

function renderLordWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  const occupation = selectedCity?.occupation_governance || {};
  const funding = selectedCity?.rebellion_funding_options?.[faction?.id];
  const occupationCrisis = Boolean(occupation.status && occupation.status !== "ended");
  const ownRebellion = selectedCity?.owner_faction_id === faction?.id && strategyCityRebellionForce(selectedCity) > 0;
  const externalFundingTarget = selectedCity?.owner_faction_id !== faction?.id && Boolean(funding) && (
    occupationCrisis || strategyCityRebellionForce(selectedCity) > 0 || Number(funding.rebellion_risk || 0) >= 45
  );
  const cityCard = createStrategyCityCommandCard(campaign, selectedCity, faction, canResume, office);
  if (occupationCrisis || ownRebellion || externalFundingTarget) {
    cityCard.classList.add("is-political-crisis");
  }
  command.append(cityCard);
  // 科技、圣物、祭祀与任命各自有专页；这里只留主公在"这个月"要签的东西。
  command.append(createRoleWorkspaceHeader(campaign, office, "主公中枢", "签发本月国策；科技在「科技」页，人事在「武将」页。"));
  command.append(createStrategyOfficeDesk(campaign, office, canResume));
}

function createLordRelicOperationsPanel(campaign, office, faction, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-role-panel strategy-relic-operations";
  const title = document.createElement("h3");
  title.textContent = "圣物行动";
  panel.append(title);
  appendTextLine(panel, "strategy-meta", "主公可搜索、转移、修复、绑定或释放圣物；每项占用 1 军令，绑定与释放还会占用祭坛本月唯一行动。完整圣物绑定后，需从下月起连续完成 3 次维护才能获胜。");

  const intel = campaign?.world?.relic_system?.intel_by_faction?.[faction?.id] || {};
  const searchOptions = Array.isArray(intel.search_options) ? intel.search_options : [];
  const transferOptions = Array.isArray(intel.transfer_options) ? intel.transfer_options : [];
  const repairOptions = Array.isArray(intel.repair_options) ? intel.repair_options : [];
  const bindingOptions = Array.isArray(intel.binding_options) ? intel.binding_options : [];
  const releaseOptions = Array.isArray(intel.release_options) ? intel.release_options : [];
  const actions = campaign?.queued_actions || [];
  const grid = document.createElement("div");
  grid.className = "strategy-tech-grid strategy-relic-grid";

  searchOptions.forEach((option) => {
    const card = document.createElement("article");
    card.className = "strategy-tech-card";
    const heading = document.createElement("strong");
    heading.textContent = `搜索 · ${option.relic_name}`;
    card.append(heading);
    appendTextLine(card, "strategy-meta", `线索：${option.clue_city_name || "未知节点"} · 20 粮 · 25% 稳定受损风险`);
    const origins = Array.isArray(option.origins) ? option.origins : [];
    const select = document.createElement("select");
    origins.forEach((origin) => {
      const item = document.createElement("option");
      item.value = `${origin.hero_code}|${origin.city_id}`;
      item.textContent = `${origin.hero_name} 从 ${origin.city_name} 出发${origin.available ? "" : ` · ${origin.reason}`}`;
      item.disabled = !origin.available;
      select.append(item);
    });
    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    const queued = actions.some((action) => action.action_type === "search_relic" && action.action_key === option.relic_id);
    button.textContent = queued ? "已委派搜索 · 1 军令" : "委派搜索 · 1 军令";
    button.disabled = state.strategyBusy || !canResume || !strategyCanIssueOrders(campaign) || !origins.some((item) => item.available);
    button.addEventListener("click", () => {
      const [heroCode, cityId] = String(select.value || "").split("|");
      queueStrategyAction("search_relic", {
        relic_id: option.relic_id,
        hero_code: heroCode,
        city_id: cityId,
        issuer_office_id: office?.id,
      });
    });
    if (!origins.length) appendTextLine(card, "strategy-meta", "暂无位于线索一跳范围内、且本月可行动的己方英灵。");
    card.append(select, button);
    grid.append(card);
  });

  transferOptions.forEach((option) => {
    const card = document.createElement("article");
    card.className = "strategy-tech-card";
    const heading = document.createElement("strong");
    heading.textContent = `转移 · ${option.relic_name}`;
    card.append(heading);
    appendTextLine(card, "strategy-meta", `当前保管：${option.source_city_name} · 每月一条己方地图边 · 10 粮`);
    const targets = Array.isArray(option.targets) ? option.targets : [];
    const select = document.createElement("select");
    targets.forEach((target) => {
      const item = document.createElement("option");
      item.value = target.city_id;
      item.textContent = target.city_name;
      select.append(item);
    });
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.textContent = actions.some((action) => action.action_type === "transfer_relic" && action.action_key === option.relic_id)
      ? "已规划转移 · 1 军令"
      : "规划转移 · 1 军令";
    button.disabled = state.strategyBusy || !canResume || !strategyCanIssueOrders(campaign) || !option.available || !targets.length;
    button.addEventListener("click", () => queueStrategyAction("transfer_relic", {
      relic_id: option.relic_id,
      target_city_id: select.value,
      issuer_office_id: office?.id,
    }));
    card.append(select, button);
    grid.append(card);
  });

  repairOptions.forEach((option) => {
    const card = document.createElement("article");
    card.className = "strategy-tech-card";
    const heading = document.createElement("strong");
    heading.textContent = `修复 · ${option.relic_name}`;
    card.append(heading);
    appendTextLine(card, "strategy-meta", `${option.city_name} · 40 势力金钱 + 20 城市以太`);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.textContent = actions.some((action) => action.action_type === "repair_relic" && action.action_key === option.relic_id)
      ? "已安排修复 · 1 军令"
      : "安排修复 · 1 军令";
    button.disabled = state.strategyBusy || !canResume || !strategyCanIssueOrders(campaign) || !option.available;
    button.addEventListener("click", () => queueStrategyAction("repair_relic", {
      relic_id: option.relic_id,
      issuer_office_id: office?.id,
    }));
    card.append(button);
    grid.append(card);
  });

  bindingOptions.forEach((option) => {
    const card = document.createElement("article");
    card.className = "strategy-tech-card";
    const heading = document.createElement("strong");
    heading.textContent = `绑定 · ${option.relic_name}`;
    card.append(heading);
    appendTextLine(
      card,
      "strategy-meta",
      `${option.city_name} · ${option.altar_name} · 下月起连续 3 次、每月 ${option.maintenance_ether_cost} 城市以太可完成圣物胜利 · 祭坛行动余 ${option.altar_actions_remaining}`
    );
    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.textContent = actions.some((action) => action.action_type === "bind_relic" && action.action_key === option.altar_id)
      ? "已安排绑定 · 1 军令"
      : "绑定祭坛 · 1 军令";
    button.disabled = state.strategyBusy || !canResume || !strategyCanIssueOrders(campaign) || !option.available;
    button.addEventListener("click", () => queueStrategyAction("bind_relic", {
      relic_id: option.relic_id,
      altar_id: option.altar_id,
      issuer_office_id: office?.id,
    }));
    card.append(button);
    grid.append(card);
  });

  releaseOptions.forEach((option) => {
    const card = document.createElement("article");
    card.className = "strategy-tech-card";
    const heading = document.createElement("strong");
    heading.textContent = `释放 · ${option.relic_name}`;
    card.append(heading);
    appendTextLine(
      card,
      "strategy-meta",
      `${option.city_name} · ${option.altar_name} · 释放后圣物将重新散布；己方保留追踪线索 · 祭坛行动余 ${option.altar_actions_remaining}`
    );
    const button = document.createElement("button");
    button.type = "button";
    button.className = "danger";
    button.textContent = actions.some((action) => action.action_type === "release_relic" && action.action_key === option.altar_id)
      ? "已安排释放 · 1 军令"
      : "主动释放 · 1 军令";
    button.disabled = state.strategyBusy || !canResume || !strategyCanIssueOrders(campaign) || !option.available;
    button.addEventListener("click", () => queueStrategyAction("release_relic", {
      relic_id: option.relic_id,
      issuer_office_id: office?.id,
    }));
    card.append(button);
    grid.append(card);
  });

  if (!searchOptions.length && !transferOptions.length && !repairOptions.length && !bindingOptions.length && !releaseOptions.length) {
    appendTextLine(panel, "strategy-meta", "当前没有可执行的圣物行动；先取得线索或等待圣物状态变化。");
  } else {
    panel.append(grid);
  }
  return panel;
}

function renderGrandGeneralWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  command.append(createStrategyCityCommandCard(campaign, selectedCity, faction, canResume, office));
  command.append(createRoleWorkspaceHeader(campaign, office, "战区统帅部", "管理直属将军，把城市已注册单位调入具体军团。"));
  command.append(createStrategyOfficeDesk(campaign, office, canResume));
  command.append(createGrandGeneralMilitaryPanel(campaign, office, faction, canResume, selectedCity));
}

function renderGeneralWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  const managed = strategyOfficeManagedCities(campaign, office);
  const source = managed.find((city) => city.id === selectedCity?.id) || managed[0] || selectedCity;
  command.append(createStrategyCityCommandCard(campaign, source, faction, canResume, office));
  const siegePanel = createGeneralSiegePanel(campaign, office, faction, canResume);
  if (siegePanel) command.append(siegePanel);
  command.append(createRoleWorkspaceHeader(campaign, office, "军团行营", "持有确切作战单位；缺兵时必须向直属大将军请示。"));
  command.append(createStrategyOfficeDesk(campaign, office, canResume));
  command.append(createGeneralArmyPanel(campaign, office, faction, canResume));
  command.append(createGeneralLogisticsPanel(campaign, office, faction, canResume));
}

function renderGovernorWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  const managedCity = strategyOfficeManagedCities(campaign, office)[0] || selectedCity;
  command.append(createStrategyCityCommandCard(campaign, managedCity, faction, canResume, office));
  const siegePanel = createGovernorSiegePanel(campaign, office, faction, canResume);
  if (siegePanel) command.append(siegePanel);
  command.append(createRoleWorkspaceHeader(campaign, office, "城主府", "管理所辖城市的兵力增长、士兵注册、建筑、叛乱与祭祀。"));
  command.append(createStrategyOfficeDesk(campaign, office, canResume));
}

function createStrategyHeroPathPanel(campaign) {
  const currentHero = strategyControlledHero(campaign);
  const currentOffice = strategyActiveOffice(campaign);
  const isLobby = campaign?.status === "lobby";
  const pool = campaign?.world?.strategic_hero_pool || [];
  const availableHeroes = pool.filter((hero) => (
    hero.status === "roaming" || hero.code === currentHero?.code
  ));
  const panel = document.createElement("section");
  panel.className = "strategy-hero-path-panel";
  const head = document.createElement("div");
  head.className = "strategy-hero-path-head";
  const titleBox = document.createElement("div");
  appendTextLine(titleBox, "meta-label", isLobby ? "出身抉择" : "在野行止");
  const title = document.createElement("h3");
  title.textContent = currentHero ? strategyHeroName(campaign, currentHero.code) : "选择武将";
  titleBox.append(title);
  const seal = document.createElement("strong");
  seal.className = `strategy-hero-status-seal ${currentHero?.status || "roaming"}`;
  seal.textContent = currentHero?.status === "serving" ? "仕官" : "在野";
  head.append(titleBox, seal);
  panel.append(head);

  const form = document.createElement("div");
  form.className = "strategy-hero-path-form";
  const heroSelect = document.createElement("select");
  availableHeroes.forEach((hero) => {
    const option = document.createElement("option");
    option.value = hero.code;
    const city = (campaign?.world?.cities || []).find((item) => item.id === hero.city_id);
    option.textContent = `${hero.name} · ${hero.role || "武将"} · ${city?.name || "行踪不明"}`;
    option.selected = hero.code === currentHero?.code;
    heroSelect.append(option);
  });
  heroSelect.disabled = state.strategyBusy || !isLobby;

  const pathSelect = document.createElement("select");
  const pathOptions = isLobby
    ? [
      ...(currentOffice?.office_type === "lord" ? [["lord", "成为主公"]] : []),
      ["roaming", "以在野身份入世"],
      ["found", "在所在城举旗建国"],
      ["join", "请求投靠其他主公"],
    ]
    : [
      ["found", "在所在城举旗建国"],
      ["join", "请求投靠其他主公"],
    ];
  pathOptions.forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    pathSelect.append(option);
  });
  if (isLobby && currentHero?.status === "roaming") pathSelect.value = "roaming";

  const targetSelect = document.createElement("select");
  (campaign?.world?.factions || []).forEach((faction) => {
    if (faction.id === currentHero?.faction_id) return;
    const option = document.createElement("option");
    option.value = faction.id;
    option.textContent = `${faction.name} · 主城 ${strategyCityName(campaign, faction.capital_city_id)}`;
    targetSelect.append(option);
  });
  const targetField = createStrategyField("投靠对象", targetSelect);
  targetField.className = "strategy-hero-target-field";

  const detail = document.createElement("p");
  detail.className = "strategy-hero-path-detail";
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "primary strategy-hero-path-submit";
  const updatePathState = () => {
    const selectedHero = pool.find((hero) => hero.code === heroSelect.value) || currentHero;
    const cityName = strategyCityName(campaign, selectedHero?.city_id);
    const details = {
      lord: "接掌分配给你的初始势力，可亲征，也可向大将军下达攻防军令。",
      roaming: "不隶属任何势力，不可调动城市；之后可举旗建国或递交投靠请求。",
      found: `在${cityName || "所在城"}举旗并夺取该城，成为新势力主公。`,
      join: "向所选主公递交投靠请求；对方录用后才正式成为其麾下武将。",
    };
    if (isLobby && currentOffice?.office_type !== "lord" && pathSelect.value === "roaming") {
      details.roaming = `你当前被分配为${strategyOfficeLabel(currentOffice, campaign)}；选择在野会放弃这个合作官职。`;
    }
    targetField.hidden = pathSelect.value !== "join";
    detail.textContent = details[pathSelect.value] || "";
    submit.textContent = pathSelect.value === "join" ? "递交投靠书" : pathSelect.value === "found" ? "举旗建国" : "确认武将道路";
    submit.disabled = state.strategyBusy || !heroSelect.value || (pathSelect.value === "join" && !targetSelect.value);
  };
  heroSelect.addEventListener("change", updatePathState);
  pathSelect.addEventListener("change", updatePathState);
  targetSelect.addEventListener("change", updatePathState);
  submit.addEventListener("click", () => chooseStrategyHeroPath(heroSelect.value, pathSelect.value, targetSelect.value));
  form.append(
    createStrategyField("你所操作的武将", heroSelect),
    createStrategyField("道路", pathSelect),
    targetField,
    detail,
    submit,
  );
  panel.append(form);
  updatePathState();
  return panel;
}

function renderStrategyRoamingWorkspace(current, campaign, hero) {
  const location = (campaign?.world?.cities || []).find((city) => city.id === hero?.city_id);
  renderCampaignScreen(current, {
    campaign,
    faction: null,
    office: null,
    renderMap: (stage) => renderStrategyMap(stage, campaign, null),
    onDockChange: () => renderStrategyPanel(),
    modules: [
      {
        id: "city",
        label: "行止",
        title: `${strategyHeroName(campaign, hero?.code)} · 在野`,
        caption: `所在 ${location?.name || "行踪不明"}`,
        render: (host) => host.append(createStrategyHeroPathPanel(campaign)),
      },
    ],
  });
}

function renderStrategyConclusion(current, campaign, canResume, isOwner) {
  const status = campaign?.world?.strategic_status || {};
  const conclusion = status.conclusion || {};
  if (!conclusion.state) return false;

  const section = document.createElement("section");
  section.className = `strategy-quick-conclusion is-${conclusion.state}`;
  const head = document.createElement("div");
  head.className = "strategy-quick-conclusion-head";
  const copy = document.createElement("div");
  appendTextLine(copy, "strategy-quick-opening-kicker", `第 ${conclusion.concluded_month || campaign.world.current_month} 月 · 战役评议`);
  const title = document.createElement("h3");
  title.textContent = conclusion.result_label || "战役评议";
  copy.append(title);
  const stateLabel = document.createElement("span");
  stateLabel.className = "strategy-quick-conclusion-state";
  stateLabel.textContent = conclusion.state === "archived"
    ? "已归档"
    : conclusion.state === "sandbox" ? "自由沙盒中" : "等待你的决定";
  head.append(copy, stateLabel);
  section.append(head);

  const rankings = Array.isArray(conclusion.rankings) ? conclusion.rankings : [];
  const rankingGrid = document.createElement("div");
  rankingGrid.className = "strategy-quick-ranking-grid";
  rankings.forEach((row) => {
    const card = document.createElement("article");
    card.className = `strategy-quick-ranking${Number(row.rank) === 1 ? " is-winner" : ""}`;
    const rank = document.createElement("strong");
    rank.textContent = `第 ${row.rank} 名 · ${row.faction_name || strategyFactionName(campaign, row.faction_id)}`;
    const score = document.createElement("span");
    score.textContent = `${row.total_score || 0} 分`;
    card.append(rank, score);
    appendTextLine(card, "strategy-meta", `城市 ${row.city_score || 0} · 民心 ${row.support_score || 0} · 存续 ${row.survival_score || 0} · 战斗 ${row.battle_score || 0} · 城邦 ${row.influence_score || 0}`);
    rankingGrid.append(card);
  });
  section.append(rankingGrid);

  appendStrategyRetrospective(section, campaign, campaign.world?.campaign_retrospective || conclusion.retrospective);
  if (status.awaiting_conclusion_choice) {
    appendTextLine(
      section,
      "strategy-quick-conclusion-prompt",
      isOwner ? "这局已经完整结算。你可以保留结果继续沙盒、冻结归档，或回到列表另开一局。" : "这局已经完整结算，正在等待房主选择后续。",
    );
  }

  if (isOwner) {
    const actions = document.createElement("div");
    actions.className = "strategy-quick-conclusion-actions";
    if (status.awaiting_conclusion_choice) {
      const continueButton = document.createElement("button");
      continueButton.type = "button";
      continueButton.className = "primary";
      continueButton.textContent = "保留评议并继续沙盒";
      continueButton.disabled = state.strategyBusy || !canResume;
      continueButton.addEventListener("click", continueStrategySandbox);
      const archiveButton = document.createElement("button");
      archiveButton.type = "button";
      archiveButton.className = "ghost danger";
      archiveButton.textContent = "结束并归档这局";
      archiveButton.disabled = state.strategyBusy || !canResume;
      archiveButton.addEventListener("click", archiveStrategyCampaign);
      actions.append(continueButton, archiveButton);
    }
    const restartButton = document.createElement("button");
    restartButton.type = "button";
    restartButton.className = status.awaiting_conclusion_choice ? "ghost" : "primary";
    restartButton.textContent = "再开一局";
    restartButton.disabled = state.strategyBusy;
    restartButton.addEventListener("click", () => {
      exitStrategyCampaignView();
      openStrategyCampaignCreator();
    });
    actions.append(restartButton);
    section.append(actions);
  }
  current.append(section);
  return true;
}

/**
 * 战役屏。
 *
 * 地图占满整屏，其余一切收进浮在地图上的面板。面板分五页，各回答一个问题：
 * 这座城怎么办、我手上有哪些人、这个月排了什么、势力家底如何、最近发生了什么。
 * 此前这里是"局势详情 / 战役引导 / 月报详情"之类的解说块，它们描述屏幕本身，
 * 而不是让你做出下一个决定，所以都不在了。
 */
function renderStrategyWarRoom(current, campaign, faction, canIssueOrders, isOwner) {
  const office = strategyActiveOffice(campaign);
  const canResume = strategyCanResume(campaign);
  const managedCities = strategyOfficeManagedCities(campaign, office);
  let selectedCity = strategySelectedCity(campaign, faction);
  if (["general", "governor"].includes(office?.office_type) && !managedCities.some((city) => city.id === selectedCity?.id)) {
    const rememberedManagedCity = strategyCityById(
      campaign,
      state.strategySelectedCityByContext[strategySelectionContextKey(campaign, office)]
    );
    selectedCity = managedCities.find((city) => city.id === rememberedManagedCity?.id) || managedCities[0] || null;
    strategyRememberSelectedCity(selectedCity?.id || "", campaign, office);
  }

  const queuedCount = (campaign?.queued_actions || []).filter((action) => action.faction_id === faction?.id).length;
  const idleHeroes = campaignIdleHeroCount(campaign, faction);
  const workspaceRenderers = {
    lord: renderLordWorkspace,
    grand_general: renderGrandGeneralWorkspace,
    general: renderGeneralWorkspace,
    governor: renderGovernorWorkspace,
  };

  // 一页只答一个问题。城市页讲这座城是什么样，军令页讲这个月要做什么，武将、
  // 科技、危机、圣物各自成页——它们本来就是彼此独立的决定，堆在一起只会让人
  // 每次都要先在一屏文字里找自己要的那一块。
  const heroes = campaignFactionHeroes(campaign, faction);
  const selectedHero = heroes.find((hero) => hero.code === state.strategyDockHeroCode) || null;
  const isLord = office?.office_type === "lord";
  const crisis = (campaign?.world?.world_crises || [])[0];
  const relicEnabled = Boolean(campaign?.world?.relic_system?.enabled);
  const unlockedTech = (faction?.tactic_tech_tree || []).filter((tech) => tech.unlocked).length;

  const modules = [
    {
      id: "city",
      label: "城市",
      title: selectedCity ? selectedCity.name : "等待选择城市",
      caption: selectedCity ? strategyFactionName(campaign, selectedCity.owner_faction_id) : "点地图选城",
      render: (host) => {
        if (!selectedCity) {
          appendTextLine(host, "strategy-meta", "先在地图上点一座城，这里会给出它的家底、风险和城内武将。");
          return;
        }
        host.append(createStrategyCityDetailCard(campaign, selectedCity, faction, office));
        renderStrategyCityHeroes(host, campaign, selectedCity);
      },
    },
    {
      id: "heroes",
      label: "武将",
      title: "本势力武将",
      caption: idleHeroes ? `${idleHeroes} 人闲置` : "全员已派差事",
      badge: idleHeroes || 0,
      render: (host) => {
        renderCampaignHeroList(host, campaign, faction, {
          selectedCode: selectedHero?.code || "",
          onSelect: (code) => {
            state.strategyDockHeroCode = state.strategyDockHeroCode === code ? "" : code;
            renderStrategyPanel();
          },
        });
        renderStrategyHeroDetail(host, campaign, faction, office, selectedHero);
        if (isLord) {
          host.append(createLordRitualPanel(campaign, office, faction, canResume));
          host.append(createLordHeroBindingPanel(campaign, office, canResume));
          host.append(createStrategyHeroAppointmentPanel(campaign, office, canResume));
          host.append(createLordHeroDutyPanel(campaign, office, canResume));
        }
      },
    },
    {
      id: "orders",
      label: "军令",
      title: `第 ${campaign.world.current_month} 月`,
      caption: queuedCount ? `已排 ${queuedCount} 条` : "本月尚未下令",
      badge: queuedCount || 0,
      render: (host) => {
        renderStrategyStoryEvent(host, campaign, faction);
        (workspaceRenderers[office?.office_type] || renderGovernorWorkspace)(
          host, campaign, office, selectedCity, faction, canIssueOrders,
        );
        renderStrategyExileActions(host, campaign);
        renderStrategyOfficeCoordination(host, campaign, faction);
        renderStrategyActionQueue(host, campaign);
        renderStrategyMonthlyCycle(host, campaign, faction);
        renderStrategyResumePanel(host, campaign);
        renderStrategyOfficeCollaborationPanel(host, campaign);
      },
    },
    {
      id: "tech",
      label: "科技",
      title: "国家科技树",
      caption: `已解锁 ${unlockedTech} 项`,
      render: (host) => renderStrategyTechPanel(host, campaign, faction, canResume, office),
    },
    crisis ? {
      id: "crisis",
      label: "危机",
      title: crisis.name || "北方雪鬼危机",
      caption: crisis.stage_label || crisis.stage || "潜伏",
      render: (host) => renderStrategyWorldCrisis(host, campaign, faction, office, canIssueOrders),
    } : null,
    relicEnabled ? {
      id: "relic",
      label: "圣物",
      title: "圣物与祭坛",
      caption: "情报、搜索与祭坛维护",
      render: (host) => {
        renderStrategyRelicPanel(host, campaign, faction);
        if (isLord) host.append(createLordRelicOperationsPanel(campaign, office, faction, canResume));
      },
    } : null,
    {
      id: "log",
      label: "战况",
      title: "战斗与事件",
      caption: "近期战报、事件与席位",
      render: (host) => {
        renderStrategyConclusion(host, campaign, canResume, isOwner);
        renderStrategyBattleRecords(host, campaign, faction, canResume);
        renderStrategyEventLog(host, campaign);
        renderStrategyMembersPanel(host, campaign, isOwner);
        renderStrategyRecoveryOverview(host, campaign);
      },
    },
  ];

  renderCampaignScreen(current, {
    campaign,
    faction,
    office,
    renderMap: (stage) => {
      renderStrategyMap(stage, campaign, faction);
      // 战况横幅与职位切换浮在地图左上角。它们此前各占一整行，而两者加起来通常
      // 只有一句话——现在它们压在图上，不再从地图身上扣高度。
      const overlay = document.createElement("div");
      overlay.className = "campaign-stage__overlay";
      renderStrategyWarStateBanner(overlay, campaign, canResume, isOwner);
      renderStrategyOfficeSwitcher(overlay, campaign, office);
      if (overlay.children.length) stage.append(overlay);
    },
    modules,
    onDockChange: () => renderStrategyPanel(),
  });
}

function renderStrategyWorldCrisis(current, campaign, faction, office, canResume) {
  const crisis = (campaign?.world?.world_crises || [])[0];
  if (!crisis) return;
  const section = document.createElement("section");
  section.className = `strategy-world-crisis is-${crisis.stage || "dormant"}`;

  const heading = document.createElement("div");
  heading.className = "strategy-crisis-heading";
  const title = document.createElement("div");
  appendTextLine(title, "strategy-crisis-eyebrow", "世界主线 · 公开情报");
  const strong = document.createElement("strong");
  strong.textContent = crisis.name || "北方雪鬼危机";
  title.append(strong);
  const badge = document.createElement("span");
  badge.className = "strategy-crisis-stage";
  badge.textContent = crisis.stage_label || crisis.stage || "潜伏";
  heading.append(title, badge);
  section.append(heading);

  const clock = document.createElement("div");
  clock.className = "strategy-crisis-clock";
  [
    ["危机压力", `${Number(crisis.pressure || 0)}/100`],
    ["北境起源", crisis.origin_name || "尚未定位"],
    ["下次升级", crisis.next_stage_month ? `第 ${crisis.next_stage_month} 月` : "暂无"],
  ].forEach(([label, value]) => {
    const item = document.createElement("div");
    appendTextLine(item, "meta-label", label);
    const valueNode = document.createElement("strong");
    valueNode.textContent = value;
    item.append(valueNode);
    clock.append(item);
  });
  section.append(clock);
  appendTextLine(section, "strategy-crisis-effect", crisis.effect_summary || "危机影响仍在评估。");
  if ((crisis.route_effects || []).length) {
    appendTextLine(
      section,
      "strategy-crisis-route-rule",
      `严寒路线 ${crisis.route_effects.length} 段 · 新行军需 ${strategyNumber(crisis.route_effects[0]?.minimum_supply || 80)} 粮草 · 每段额外消耗 ${strategyNumber(crisis.route_effects[0]?.supply_cost || 20)} 粮草`
    );
  }
  if ((crisis.threatened_cities || []).length) {
    const threatened = document.createElement("div");
    threatened.className = "strategy-crisis-threatened-cities";
    appendTextLine(threatened, "strategy-crisis-frontier-label", "受威胁城市");
    const threatActions = document.createElement("div");
    threatActions.className = "strategy-crisis-actions";
    const threatLabels = {
      encounter: "敌军遭遇",
      siege: "正在被围",
      threatened: "寒潮威胁",
    };
    crisis.threatened_cities.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = item.is_origin_target ? "danger" : "ghost";
      button.textContent = `${item.city_name || item.city_id} · ${threatLabels[item.threat_status] || "受威胁"}`;
      button.disabled = state.strategyBusy;
      button.addEventListener("click", () => {
        strategyRememberSelectedCity(item.city_id, campaign);
        renderStrategyPanel();
        focusStrategyMapStage();
      });
      threatActions.append(button);
    });
    threatened.append(threatActions);
    section.append(threatened);
  }
  if ((crisis.crisis_armies || []).length) {
    const forces = document.createElement("div");
    forces.className = "strategy-crisis-armies";
    appendTextLine(forces, "strategy-crisis-frontier-label", "雪鬼军队");
    crisis.crisis_armies.forEach((army) => {
      appendTextLine(
        forces,
        `strategy-crisis-army${["destroyed", "disbanded"].includes(army.status) ? " is-destroyed" : ""}`,
        `${army.name || army.id} · ${army.location_name || strategyNodeName(campaign, army.location_node_id)} · 兵员 ${strategyNumber(army.manpower)} · ${strategyArmyStatusLabel(army.status)}`
      );
    });
    section.append(forces);
  }
  if ((crisis.contribution_rows || []).length && ["mobilization", "showdown", "aftermath", "resolved"].includes(crisis.stage)) {
    const mobilization = document.createElement("div");
    mobilization.className = "strategy-crisis-mobilization";
    appendTextLine(mobilization, "strategy-crisis-frontier-label", "联军贡献");
    const contributionGrid = document.createElement("div");
    contributionGrid.className = "strategy-crisis-contributions";
    crisis.contribution_rows.forEach((row) => {
      const card = document.createElement("article");
      card.className = "strategy-crisis-contribution";
      const value = document.createElement("strong");
      value.textContent = `${row.faction_name || strategyFactionName(campaign, row.faction_id)} · ${Number(row.contribution || 0)}`;
      card.append(value);
      if (row.pledged_target_faction_id) {
        appendTextLine(card, "strategy-meta", `合作承诺 → ${strategyFactionName(campaign, row.pledged_target_faction_id)}`);
      }
      contributionGrid.append(card);
    });
    mobilization.append(contributionGrid);
    if ((crisis.ai_intent_rows || []).length) {
      appendTextLine(mobilization, "strategy-crisis-frontier-label", "AI 公开取舍");
      const priorityLabels = { survival: "生存", mainline: "主线", expansion: "扩张" };
      const choiceLabels = {
        contribute: "独立贡献",
        cooperate: "寻求合作",
        betray: "背约利用",
        avoid: "暂不投入",
      };
      crisis.ai_intent_rows.forEach((intent) => {
        const target = intent.target_faction_name ? ` → ${intent.target_faction_name}` : "";
        appendTextLine(
          mobilization,
          "strategy-crisis-ai-intent",
          `${intent.faction_name} · ${priorityLabels[intent.ai_priority] || intent.ai_priority || "局势"}优先 · ${choiceLabels[intent.choice_id] || intent.choice_id}${target}：${intent.ai_rationale || "按当前公开局势作出选择。"}`
        );
      });
    }
    if ((crisis.cooperations || []).length) {
      crisis.cooperations.forEach((pair) => {
        appendTextLine(
          mobilization,
          `strategy-crisis-cooperation is-${pair.status || "active"}`,
          `${(pair.faction_ids || []).map((factionId) => strategyFactionName(campaign, factionId)).join(" ↔ ")} · ${pair.status === "broken" ? "合作破裂" : "合作成立"}`
        );
      });
    }
    const options = crisis.choice_options_by_faction?.[faction?.id] || [];
    if (crisis.stage === "mobilization" && office?.office_type === "lord" && options.length) {
      const chooser = document.createElement("div");
      chooser.className = "strategy-crisis-choice";
      const choiceSelect = document.createElement("select");
      choiceSelect.setAttribute("aria-label", "危机选择");
      options.forEach((option) => {
        const item = document.createElement("option");
        item.value = option.id;
        item.textContent = `${option.name} · ${option.description}`;
        item.disabled = !option.available;
        choiceSelect.append(item);
      });
      const targetSelect = document.createElement("select");
      targetSelect.setAttribute("aria-label", "危机目标势力");
      const explanation = document.createElement("p");
      explanation.className = "strategy-meta";
      const submit = document.createElement("button");
      submit.type = "button";
      submit.textContent = "提交危机选择 · 1军令";
      const refreshChoice = () => {
        const option = options.find((item) => item.id === choiceSelect.value) || options[0];
        targetSelect.replaceChildren();
        (option?.targets || []).filter((target) => target.available).forEach((target) => {
          const item = document.createElement("option");
          item.value = target.faction_id;
          item.textContent = target.faction_name;
          targetSelect.append(item);
        });
        targetSelect.hidden = !option?.requires_target;
        explanation.textContent = option?.reason || `消耗：粮 ${Number(option?.food_cost || 0)} · 钱 ${Number(option?.money_cost || 0)}；基础贡献 +${Number(option?.contribution_gain || 0)}`;
        submit.disabled = state.strategyBusy || !canResume || !option?.available || (option?.requires_target && !targetSelect.value);
      };
      choiceSelect.addEventListener("change", refreshChoice);
      submit.addEventListener("click", () => queueStrategyAction("world_crisis_choice", {
        choice_id: choiceSelect.value,
        target_faction_id: targetSelect.hidden ? "" : targetSelect.value,
        issuer_office_id: office.id,
      }));
      chooser.append(choiceSelect, targetSelect, explanation, submit);
      refreshChoice();
      mobilization.append(chooser);
    } else if (crisis.stage === "mobilization" && office?.office_type !== "lord") {
      appendTextLine(mobilization, "strategy-meta", "危机选择必须由主公签发；当前职位只能查看贡献与合作状态。");
    }
    if (crisis.showdown) {
      const showdown = document.createElement("div");
      showdown.className = `strategy-crisis-showdown is-${crisis.showdown.outcome || "pending"}`;
      appendTextLine(
        showdown,
        "strategy-crisis-frontier-label",
        `决战分支 · ${crisis.showdown.branch_label || crisis.showdown.branch || "待定"}`
      );
      appendTextLine(
        showdown,
        "strategy-crisis-showdown-summary",
        `联军领袖 ${crisis.showdown.leader_faction_name || strategyFactionName(campaign, crisis.showdown.leader_faction_id)} · 联军 ${Number(crisis.showdown.coalition_units || 0)} 单位 · 雪鬼 ${Number(crisis.showdown.snow_ghost_units || 0)} 单位`
      );
      const battle = (campaign?.world?.pending_battles || []).find(
        (item) => item.id === crisis.showdown.battle_id
      );
      if (crisis.stage === "showdown" && battle?.status === "pending") {
        if (battle.battle_room_id) {
          const enter = document.createElement("button");
          enter.type = "button";
          enter.className = "primary";
          enter.textContent = `进入北境决战房间 · ${battle.battle_room_id}`;
          enter.disabled = state.strategyBusy;
          enter.addEventListener("click", () => openStrategyBattleRoom(
            currentStrategyBattleRoomForBattle(battle)
          ));
          showdown.append(enter);
        } else if (office?.office_type === "lord") {
          const controls = document.createElement("div");
          controls.className = "strategy-crisis-showdown-controls";
          const mode = document.createElement("select");
          mode.setAttribute("aria-label", "北境决战处理方式");
          [
            ["quick", "快速结算"],
            ["manual", "手动格子战"],
            ["ai_auto", "AI 自动战斗"],
            ["watch_ai", "观看 AI 战斗"],
          ].forEach(([value, label]) => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = label;
            mode.append(option);
          });
          const engage = document.createElement("button");
          engage.type = "button";
          engage.className = "danger";
          engage.textContent = "开启北境决战 · 不消耗军令";
          engage.disabled = state.strategyBusy || !canResume;
          engage.addEventListener("click", () => resolveWorldCrisisShowdown(mode.value));
          controls.append(createStrategyField("处理方式", mode), engage);
          showdown.append(controls);
        } else {
          appendTextLine(showdown, "strategy-meta", "北境决战必须由主要势力的主公开启。");
        }
      } else if (crisis.showdown.outcome === "victory") {
        appendTextLine(
          showdown,
          "strategy-crisis-showdown-result",
          `决战胜利 · 主线胜利势力：${(crisis.showdown.winner_faction_ids || []).map((id) => strategyFactionName(campaign, id)).join("、") || "联军领袖"}`
        );
      } else if (crisis.showdown.outcome === "defeat") {
        appendTextLine(
          showdown,
          "strategy-crisis-showdown-result",
          "决战失利 · 受威胁城邦已承受粮食与统治支持损失，第 12 月进入正常评议。"
        );
      }
      mobilization.append(showdown);
    }
    section.append(mobilization);
  }

  const frontier = document.createElement("div");
  frontier.className = "strategy-crisis-frontier";
  appendTextLine(frontier, "strategy-crisis-frontier-label", "北境关注城市");
  const actions = document.createElement("div");
  actions.className = "strategy-crisis-actions";
  (crisis.frontier || []).forEach((item) => {
    if (!item.city_id) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.textContent = item.city_name || item.node_name || item.city_id;
    button.disabled = state.strategyBusy;
    button.addEventListener("click", () => {
      strategyRememberSelectedCity(item.city_id, campaign);
      renderStrategyPanel();
      focusStrategyMapStage();
    });
    actions.append(button);
  });
  if (!actions.children.length) appendTextLine(actions, "strategy-meta", "前线坐标尚未确认。");
  frontier.append(actions);
  section.append(frontier);
  current.append(section);
}

function renderStrategyMonthlyCycle(current, campaign, faction) {
  const cycle = strategyMonthlyCycle(campaign, faction);
  const section = document.createElement("section");
  section.className = "strategy-monthly-cycle strategy-campaign-card";
  const title = document.createElement("h4");
  title.textContent = "月度决策";
  section.append(title);

  const previousTitle = document.createElement("strong");
  previousTitle.textContent = "上月发生了什么";
  section.append(previousTitle);
  const previous = cycle.previous_month;
  if (!previous) {
    appendTextLine(section, "strategy-meta", "战役首月，尚无上月结算记录。");
  } else {
    const changes = previous.city_changes || [];
    const events = previous.important_events || [];
    if (!changes.length && !events.length) appendTextLine(section, "strategy-meta", `第 ${previous.month} 月没有与你势力直接相关的重大变化。`);
    changes.slice(0, 4).forEach((change) => {
      const delta = change.resource_delta || {};
      const owner = change.owner_changed ? `，归属由${strategyFactionName(campaign, change.owner_before)}变为${strategyFactionName(campaign, change.owner_after)}` : "";
      appendTextLine(
        section,
        "strategy-meta",
        `${change.city_name}：粮 ${delta.food >= 0 ? "+" : ""}${delta.food || 0}、钱 ${delta.money >= 0 ? "+" : ""}${delta.money || 0}、兵 ${delta.troops >= 0 ? "+" : ""}${delta.troops || 0}、民心 ${change.support_delta >= 0 ? "+" : ""}${change.support_delta || 0}${owner}`
      );
    });
    events.slice(0, 3).forEach((event) => appendTextLine(section, "strategy-meta", event.message));
  }

  const mustTitle = document.createElement("strong");
  mustTitle.textContent = "本月必须处理什么";
  section.append(mustTitle);
  const mustHandle = cycle.must_handle || [];
  if (!mustHandle.length) appendTextLine(section, "strategy-meta", "没有迫在眉睫的危机，可以围绕战役目标主动规划。");
  mustHandle.slice(0, 3).forEach((item) => appendTextLine(section, "strategy-meta", `• ${item}`));

  const forecastTitle = document.createElement("strong");
  forecastTitle.textContent = "推进后预计发生什么";
  section.append(forecastTitle);
  const forecast = cycle.advance_forecast || {};
  (forecast.cities || []).forEach((city) => {
    const delta = city.resource_delta || {};
    appendTextLine(
      section,
      "strategy-meta",
      `${city.city_name}（${city.policy}）：粮 ${delta.food >= 0 ? "+" : ""}${delta.food || 0}（维护 ${city.food_upkeep || 0}）、钱 ${delta.money >= 0 ? "+" : ""}${delta.money || 0}、以太 ${delta.ether >= 0 ? "+" : ""}${delta.ether || 0}、兵 ${delta.troops >= 0 ? "+" : ""}${delta.troops || 0}；民心 ${city.support_delta >= 0 ? "+" : ""}${city.support_delta || 0}，叛乱 ${city.rebellion_risk || 0}（${city.rebellion_stage || "安全"}）`
    );
  });
  const planned = cycle.planned_actions || [];
  if (planned.length) {
    appendTextLine(section, "strategy-meta", `行动队列：${planned.length} 项；均在城市月结前执行。`);
    planned.slice(0, 4).forEach((action) => {
      appendTextLine(section, "strategy-meta", `• ${strategyQueuedActionLabel(campaign, action)} → 第 ${(action.affected_months || []).join(" / ")} 月`);
    });
  }
  // 预测口径是"这份数字为什么可信"的注脚，看一次就够，不必每月复述一遍。
  section.append(createHint(forecast.disclaimer || "战争和未知事件结果不会提前泄露。", { align: "start" }));
  current.append(section);
}

export function appendTextLine(parent, className, text) {
  const node = document.createElement("div");
  node.className = className;
  node.textContent = text;
  parent.append(node);
  return node;
}

function currentStrategyBattleRoomForBattle(battle) {
  const latest = state.strategyBattleRoom || {};
  const battleRoomId = String(battle?.battle_room_id || "").trim().toUpperCase();
  const latestRoomId = String(latest.room_id || "").trim().toUpperCase();
  if (battleRoomId && latestRoomId === battleRoomId) {
    return latest;
  }
  return {
    room_id: battleRoomId,
    invite_path: battle?.battle_room_invite_path || "",
    player_token: "",
  };
}

function strategyRosterManifestSummary(manifest = []) {
  if (!Array.isArray(manifest) || !manifest.length) return "";
  return manifest
    .filter((row) => Number(row?.grid_units || 0) > 0)
    .map((row) => `${row.unit_type || "单位"}×${row.grid_units}`)
    .join(" / ");
}

function strategyCityName(campaign, cityId) {
  const city = (campaign?.world?.cities || []).find((item) => item.id === cityId);
  return city?.name || cityId || "未知城市";
}

export function strategyCityStateLabels(city = {}) {
  const labels = (city.event_states || []).map((state) => {
    const parts = String(state || "").split(":");
    if (parts[0] === "rebellion_risk" && parts.length >= 3) {
      return `叛乱风险 ${parts[1]} ${parts[2]}`;
    }
    if (parts[0] === "rebellion_force" && parts.length >= 2) {
      return `叛军 ${parts[1]}`;
    }
    if (parts[0] === "rebellion_crisis") {
      const riskIndex = parts.indexOf("risk");
      return riskIndex >= 0 && parts[riskIndex + 1] ? `叛乱危机 ${parts[riskIndex + 1]}` : "叛乱危机";
    }
    if (parts[0] === "rebellion_action" && parts.length >= 2) {
      return `本月处理 ${parts[1]}`;
    }
    return "";
  }).filter(Boolean);
  const occupation = city.occupation_governance || city.occupation || {};
  if (occupation.status === "pending") labels.push("占领政策待定");
  if (occupation.status === "active") labels.push(`占领政策 ${occupation.policy_label || occupation.policy_id || "执行中"}`);
  return labels;
}

export function strategyCityRebellionForce(city = {}) {
  for (const state of city.event_states || []) {
    const parts = String(state || "").split(":");
    if (parts[0] !== "rebellion_force" || !parts[1]) continue;
    const force = Number(parts[1]);
    return Number.isFinite(force) ? Math.max(0, Math.floor(force)) : 0;
  }
  return 0;
}

function strategyExileActionName(campaign, actionId) {
  const action = (campaign?.world?.exile_action_choices || []).find((item) => item.id === actionId);
  return action?.name || actionId || "未知流亡行动";
}

function strategyRebellionActionName(campaign, actionId) {
  const action = (campaign?.world?.rebellion_action_choices || []).find((item) => item.id === actionId);
  return action?.name || actionId || "未知叛乱处理";
}

function strategyHeroName(campaign, heroCode) {
  const hero = (campaign?.world?.strategic_hero_pool || []).find((item) => item.code === heroCode);
  return hero?.name || heroCode || "未知英灵";
}

export function strategyDeployableHeroes(faction) {
  return (faction?.strategic_heroes || []).filter((hero) => hero.status === "serving");
}

export function strategyHeroDeploymentLimit(faction) {
  const value = Number(faction?.strategic_hero_deployment_limit || 1);
  return Math.max(0, Math.floor(Number.isFinite(value) ? value : 1));
}

export function createStrategyHeroDeploymentPicker(faction, selectedCodes = []) {
  const heroes = strategyDeployableHeroes(faction);
  const limit = strategyHeroDeploymentLimit(faction);
  const selected = new Set((Array.isArray(selectedCodes) ? selectedCodes : []).map((code) => String(code || "")));

  if (limit <= 1) {
    const select = document.createElement("select");
    const noHeroOption = document.createElement("option");
    noHeroOption.value = "";
    noHeroOption.textContent = "不投入";
    select.append(noHeroOption);
    heroes.forEach((hero) => {
      const option = document.createElement("option");
      option.value = hero.code;
      const acceptsBattle = hero.command_acceptance?.battle !== false;
      option.textContent = acceptsBattle ? (hero.name || hero.code) : `${hero.name || hero.code}（本月拒绝）`;
      option.disabled = !acceptsBattle;
      option.selected = selected.has(hero.code);
      select.append(option);
    });
    return {
      element: select,
      selectedCodes: () => (select.value ? [select.value] : []),
      setDisabled: (disabled) => { select.disabled = disabled; },
    };
  }

  const wrapper = document.createElement("div");
  wrapper.className = "strategy-hero-picker";
  const inputs = [];
  heroes.forEach((hero) => {
    const item = document.createElement("label");
    item.className = "strategy-hero-choice";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = hero.code;
    const acceptsBattle = hero.command_acceptance?.battle !== false;
    input.checked = acceptsBattle && selected.has(hero.code);
    input.disabled = !acceptsBattle;
    input.dataset.commandAccepted = acceptsBattle ? "true" : "false";
    input.addEventListener("change", () => {
      const checked = inputs.filter((node) => node.checked);
      if (checked.length > limit) input.checked = false;
    });
    const span = document.createElement("span");
    span.textContent = acceptsBattle ? (hero.name || hero.code) : `${hero.name || hero.code}（本月拒绝）`;
    item.append(input, span);
    wrapper.append(item);
    inputs.push(input);
  });
  return {
    element: wrapper,
    selectedCodes: () => inputs.filter((input) => input.checked).map((input) => input.value).slice(0, limit),
    setDisabled: (disabled) => {
      inputs.forEach((input) => {
        input.disabled = disabled || input.dataset.commandAccepted === "false";
      });
    },
  };
}

export function strategyQueuedActionLabel(campaign, action = {}) {
  const payload = action.payload || {};
  if (action.action_type === "set_city_policy") {
    return `${strategyCityName(campaign, payload.city_id)}：方针计划为 ${payload.policy || "未知"}`;
  }
  if (action.action_type === "neutral_diplomacy") {
    const neutral = strategyFactionById(campaign, payload.neutral_faction_id);
    const relation = neutral?.neutral_politics?.relationships?.find((item) => item.faction_id === action.faction_id);
    const option = relation?.diplomacy_options?.find((item) => item.id === payload.diplomacy_action_id);
    return `中立交涉：${neutral?.name || payload.neutral_faction_id || "未知城邦"} · ${option?.name || payload.diplomacy_action_id || "未知行动"}`;
  }
  if (action.action_type === "world_crisis_choice") {
    const names = { contribute: "独立贡献", cooperate: "提出合作", betray: "背弃合作" };
    const target = payload.target_faction_id ? ` → ${strategyFactionName(campaign, payload.target_faction_id)}` : "";
    return `雪鬼危机：${names[payload.choice_id] || payload.choice_id || "未知选择"}${target}`;
  }
  if (action.action_type === "peaceful_integration") {
    const neutral = (campaign?.world?.factions || []).find((item) => item.id === payload.neutral_faction_id);
    return `和平整合：${neutral?.name || payload.neutral_faction_id || "未知城邦"}`;
  }
  if (action.action_type === "resolve_story_event") {
    const event = (campaign?.world?.story_events || []).find((item) => item.id === payload.event_id);
    const choice = (event?.choices || []).find((item) => item.id === payload.choice_id);
    return `事件抉择：${event?.title || payload.event_id || "未知事件"} · ${choice?.label || payload.choice_id || "未知选项"}`;
  }
  if (action.action_type === "unlock_tactic_tech") {
    const faction = (campaign?.world?.factions || []).find((item) => item.id === action.faction_id);
    const tech = (faction?.tactic_tech_tree || []).find((item) => item.id === payload.tech_id);
    return `解锁战术科技：${tech?.name || payload.tech_id || "未知"}`;
  }
  if (action.action_type === "declare_attack") {
    const modeNames = {
      manual: "手动",
      ai_auto: "AI 自动",
      watch_ai: "观看 AI",
      quick: "快速",
    };
    const heroCodes = Array.isArray(payload.attacker_hero_codes) ? payload.attacker_hero_codes : [];
    const heroes = heroCodes.length ? ` · 英灵 ${heroCodes.map((code) => strategyHeroName(campaign, code)).join(", ")}` : "";
    return `${strategyCityName(campaign, payload.source_city_id)} → ${strategyCityName(campaign, payload.target_city_id)}：${modeNames[payload.resolution_mode] || payload.resolution_mode || "快速"}进攻${heroes}`;
  }
  if (action.action_type === "exile_action") {
    const target = payload.target_city_id ? `：${strategyCityName(campaign, payload.target_city_id)}` : "";
    return `流亡行动：${strategyExileActionName(campaign, payload.exile_action_id || payload.action_id)}${target}`;
  }
  if (action.action_type === "rebellion_action") {
    return `${strategyCityName(campaign, payload.city_id || payload.target_city_id)}：叛乱处理 ${strategyRebellionActionName(campaign, payload.rebellion_action_id || payload.action_id)}`;
  }
  if (action.action_type === "rebellion_battle") {
    const troops = payload.troops ? ` · 投入 ${payload.troops}` : "";
    return `${strategyCityName(campaign, payload.city_id || payload.target_city_id)}：清剿叛军${troops}`;
  }
  if (action.action_type === "choose_occupation_policy") {
    const city = (campaign?.world?.cities || []).find((item) => item.id === payload.city_id);
    const choice = (city?.occupation_governance?.policy_choices || []).find((item) => item.id === payload.policy_id);
    return `${strategyCityName(campaign, payload.city_id)}：占领政策 ${choice?.name || payload.policy_id}`;
  }
  if (action.action_type === "fund_rebellion") {
    return `${strategyCityName(campaign, payload.city_id)}：外部资助叛乱`;
  }
  if (action.action_type === "perform_hero_ritual") {
    return `${strategyCityName(campaign, payload.city_id)}：举行召唤祭祀`;
  }
  if (action.action_type === "search_relic") {
    return `搜索圣物 · ${action.payload?.relic_id || "未知圣物"} · 英灵 ${strategyHeroName(campaign, action.payload?.hero_code)}`;
  }
  if (action.action_type === "transfer_relic") {
    return `转移圣物 · ${action.payload?.relic_id || "未知圣物"} → ${strategyCityName(campaign, action.payload?.target_city_id)}`;
  }
  if (action.action_type === "repair_relic") {
    return `修复圣物 · ${action.payload?.relic_id || "未知圣物"}`;
  }
  if (action.action_type === "bind_relic") {
    return `绑定圣物 · ${action.payload?.relic_id || "未知圣物"} → ${action.payload?.altar_id || "未知祭坛"}`;
  }
  if (action.action_type === "release_relic") {
    return `释放圣物 · ${action.payload?.relic_id || "未知圣物"}`;
  }
  if (action.action_type === "unbind_strategic_hero") {
    return `解除祭祀绑定：${strategyHeroName(campaign, payload.hero_code)}`;
  }
  if (action.action_type === "appoint_strategic_hero") {
    const target = (campaign?.world?.offices || []).find((office) => office.id === payload.target_office_id);
    return `任命${strategyHeroName(campaign, payload.hero_code)}为${strategyOfficeLabel(target, campaign)}`;
  }
  if (action.action_type === "assign_strategic_hero_duty") {
    const labels = { reserve: "待命", administration: "辅佐内政", training: "训练军队", garrison: "驻守城市", campaign: "随军出征" };
    const target = payload.target_id ? ` · ${strategyCityName(campaign, payload.target_id)}` : "";
    return `安排${strategyHeroName(campaign, payload.hero_code)}：${labels[payload.assignment_type] || payload.assignment_type}${target}`;
  }
  if (action.action_type === "increase_city_troops") {
    return `${strategyCityName(campaign, payload.city_id)}：增加本城兵力`;
  }
  if (action.action_type === "register_city_soldiers") {
    return `${strategyCityName(campaign, payload.city_id)}：注册 ${payload.unit_count || 1} 个士兵单位`;
  }
  if (action.action_type === "transfer_registered_units") {
    const general = (campaign?.world?.offices || []).find((item) => item.id === payload.general_office_id);
    return `${strategyCityName(campaign, payload.city_id)}：向${strategyOfficeLabel(general, campaign)}调拨 ${payload.count || 1} 个单位`;
  }
  if (action.action_type === "request_registered_units") {
    return `请兵：${strategyCityName(campaign, payload.city_id)} · ${payload.count || 1} 个单位`;
  }
  if (action.action_type === "approve_registered_unit_request") {
    return `批准调兵申请：${payload.request_id || "未知申请"}`;
  }
  if (action.action_type === "construct_city_building") {
    const project = (campaign?.world?.building_projects || []).find((item) => item.id === payload.building_id);
    return `${strategyCityName(campaign, payload.city_id)}：兴建${project?.name || payload.building_id}`;
  }
  if (action.action_type === "issue_office_order" || action.action_type === "send_office_request") {
    const receiver = (campaign?.world?.offices || []).find((office) => office.id === payload.receiver_office_id);
    const kind = action.action_type === "send_office_request" ? "职位请求" : "职位命令";
    return `${kind}：${strategyOfficeLabel(receiver, campaign)} · ${payload.objective || "未填写目标"}`;
  }
  return action.action_type || "未知行动";
}

function renderStrategyActionQueue(current, campaign) {
  const title = document.createElement("h4");
  title.textContent = "本月行动队列";
  current.append(title);

  const panel = document.createElement("div");
  panel.className = "strategy-event-list";
  const actions = campaign?.queued_actions || [];
  if (!actions.length) {
    appendTextLine(panel, "strategy-meta", "暂无已提交的本月行动。");
  } else {
    actions.forEach((action) => {
      const card = document.createElement("article");
      card.className = "strategy-campaign-card";
      const strong = document.createElement("strong");
      strong.textContent = strategyQueuedActionLabel(campaign, action);
      card.append(strong);
      appendTextLine(card, "strategy-meta", `消耗 ${action.command_cost || strategyCommandCost(action.action_type, action.payload || {})} 点军令`);
      appendTextLine(
        card,
        "strategy-meta",
        `${action.username || strategyMemberLabel(campaign, action.user_id)} · ${strategyFactionName(campaign, action.faction_id)} · 第 ${action.month} 月`
      );
      panel.append(card);
    });
  }
  current.append(panel);
}

function appendStrategyRetrospective(card, campaign, retrospective) {
  if (!retrospective?.version) return;
  const heading = document.createElement("h4");
  heading.className = "strategy-retrospective-title";
  heading.textContent = "完整战役复盘";
  card.append(heading);

  const summary = retrospective.summary || {};
  appendTextLine(
    card,
    "strategy-meta strategy-retrospective-summary",
    `共 ${summary.resolved_battles || 0} 场城市战（${summary.grid_battles || 0} 场真实格子战）· ${summary.cities_changed_hands || 0} 次城市易主 · ${summary.story_choices || 0} 次事件抉择`
  );

  const sections = [
    {
      title: "势力结局",
      rows: retrospective.faction_outcomes || [],
      line: (row) => `${row.outcome_label} · ${row.faction_name} · 第 ${row.rank || "-"} 名 / ${row.total_score || 0} 分。${row.summary || ""}`,
    },
    {
      title: "关键月份",
      rows: retrospective.key_months || [],
      line: (row) => `第 ${row.month} 月 · ${(row.events || [row.headline]).join("；")}`,
    },
    {
      title: "城市变化",
      rows: retrospective.city_changes || [],
      line: (row) => `第 ${row.month} 月 · ${row.city_name}：${row.owner_before_name} → ${row.owner_after_name}`,
    },
    {
      title: "战斗记录",
      rows: retrospective.battles || [],
      line: (row) => `第 ${row.month} 月 · ${row.source_city_name} → ${row.target_city_name} · ${row.grid_battle ? "真实格子战" : "快速结算"} · ${row.winner_faction_name}获胜`,
    },
    {
      title: "角色经历",
      rows: retrospective.hero_experiences || [],
      line: (row) => `${strategyHeroName(campaign, row.hero_code)} · ${row.office_label || "未任职"} · 参战 ${row.battle_appearances || 0} / 胜 ${row.battle_wins || 0} · ${row.faction_name || "无所属"}`,
    },
  ];
  sections.forEach((section, index) => {
    const details = document.createElement("details");
    details.className = "strategy-retrospective-section";
    if (index < 2) details.open = true;
    const summaryNode = document.createElement("summary");
    summaryNode.textContent = `${section.title}（${section.rows.length}）`;
    details.append(summaryNode);
    if (!section.rows.length) {
      appendTextLine(details, "strategy-meta", "本次战役没有相关记录。 ");
    } else {
      section.rows.slice(0, 12).forEach((row) => appendTextLine(details, "strategy-meta", section.line(row)));
    }
    card.append(details);
  });
}

function renderStrategyObjectivePanel(current, campaign) {
  const status = campaign?.world?.strategic_status || {};
  const contract = status.campaign_contract || {};
  const conditions = Array.isArray(status.victory_conditions) ? status.victory_conditions : [];
  const exiledFactions = Array.isArray(status.exiled_factions) ? status.exiled_factions : [];
  if (!conditions.length && !exiledFactions.length) return;

  const title = document.createElement("h4");
  title.textContent = "战略目标与流亡";
  current.append(title);

  const panel = document.createElement("div");
  panel.className = "strategy-event-list";
  if (contract.id) {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = contract.name || "限时战役";
    card.append(name);
    const duration = Array.isArray(contract.expected_duration_minutes) ? contract.expected_duration_minutes.join("～") : "60～90";
    const monthText = status.campaign_state === "sandbox"
      ? `第 ${campaign.world.current_month} 月 · 已转入自由沙盒`
      : `第 ${campaign.world.current_month}/${contract.month_limit} 月 · 剩余 ${status.months_remaining} 月`;
    appendTextLine(card, "strategy-meta", monthText);
    appendTextLine(card, "strategy-meta", `${contract.city_count} 城 · ${contract.major_faction_count} 个主要势力 · ${contract.neutral_city_state_count} 个中立城邦 · 预计 ${duration} 分钟`);
    const openingVariant = contract.opening_variant || {};
    if (openingVariant.id) {
      appendTextLine(card, "strategy-meta", `开局变体：${openingVariant.name} · ${openingVariant.core_question}`);
      appendTextLine(card, "strategy-meta", `内容版本 ${contract.content_version || "旧版"} · 平衡版本 ${contract.balance_version || "旧版"}`);
      (openingVariant.modifiers || []).forEach((modifier) => appendTextLine(card, "strategy-meta", `规则修正：${modifier}`));
    }
    appendTextLine(card, "strategy-meta", "已开放：统一、消灭主要敌对势力、圣物祭坛、雪鬼主线与十二月评议；中立政治外交和正式战争均可用于竞速或反制。");
    panel.append(card);
  }
  conditions.forEach((condition) => {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = condition.name || condition.id || "未知目标";
    card.append(name);
    const stateLabel = !condition.implemented ? "未开放" : condition.achieved ? "已达成" : "未达成";
    const winnerName = condition.winner_faction_id ? strategyFactionName(campaign, condition.winner_faction_id) : "";
    appendTextLine(card, "strategy-meta", winnerName ? `${stateLabel} · ${winnerName}` : stateLabel);
    if (condition.description) appendTextLine(card, "strategy-meta", condition.description);
    panel.append(card);
  });
  if (exiledFactions.length) {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = "流亡势力";
    card.append(name);
    appendTextLine(card, "strategy-meta", exiledFactions.map((faction) => faction.name || faction.id).join("、"));
    panel.append(card);
  } else {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = "流亡势力";
    card.append(name);
    appendTextLine(card, "strategy-meta", "暂无");
    panel.append(card);
  }
  const conclusion = status.conclusion || {};
  if (conclusion.state) {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = conclusion.result_label || "战役结算";
    card.append(name);
    const conclusionStateLabels = { settled: "等待房主选择", sandbox: "已继续沙盒", archived: "已结束归档" };
    appendTextLine(card, "strategy-meta", `结算月份：第 ${conclusion.concluded_month} 月 · ${conclusionStateLabels[conclusion.state] || conclusion.state}`);
    const rankings = Array.isArray(conclusion.rankings) ? conclusion.rankings : [];
    rankings.forEach((row) => {
      appendTextLine(
        card,
        "strategy-meta",
        `第 ${row.rank} 名 ${row.faction_name || strategyFactionName(campaign, row.faction_id)}：${row.total_score} 分（城市 ${row.city_score} / 民心 ${row.support_score} / 存续 ${row.survival_score} / 战斗 ${row.battle_score} / 城邦影响 ${row.influence_score || 0} / 主线 ${row.mainline_score}）`
      );
    });
    appendStrategyRetrospective(card, campaign, campaign.world?.campaign_retrospective || conclusion.retrospective);
    if (status.awaiting_conclusion_choice && Number(campaign.owner_user_id) === Number(state.authUser?.id || 0)) {
      const actions = document.createElement("div");
      actions.className = "strategy-campaign-actions";
      const continueButton = document.createElement("button");
      continueButton.type = "button";
      continueButton.className = "primary";
      continueButton.textContent = "保留结算并继续沙盒";
      continueButton.disabled = state.strategyBusy || !strategyCanResume(campaign);
      continueButton.addEventListener("click", continueStrategySandbox);
      actions.append(continueButton);
      const archiveButton = document.createElement("button");
      archiveButton.type = "button";
      archiveButton.className = "ghost danger";
      archiveButton.textContent = "结束并归档战役";
      archiveButton.disabled = state.strategyBusy || !strategyCanResume(campaign);
      archiveButton.addEventListener("click", archiveStrategyCampaign);
      actions.append(archiveButton);
      card.append(actions);
    }
    panel.append(card);
  }
  current.append(panel);
}

/**
 * 流亡行动。
 *
 * 无城势力这个月唯一能做的事，所以它属于「军令」页而不是目标清单：它是一条要
 * 排进本月计划的军令，不是一段战况说明。
 */
function renderStrategyExileActions(current, campaign) {
  const status = campaign?.world?.strategic_status || {};
  const faction = strategyFaction(campaign);
  const isExiled = Boolean(faction?.id && (status.exiled_faction_ids || []).includes(faction.id));
  const canResume = strategyCanIssueOrders(campaign);
  if (isExiled) {
    const panel = document.createElement("div");
    panel.className = "strategy-event-list";
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const name = document.createElement("strong");
    name.textContent = "你的流亡行动";
    card.append(name);
    appendTextLine(card, "strategy-meta", "无城势力可以求援、募兵、潜伏联络，并在条件足够时重建据点。");
    const actions = document.createElement("div");
    actions.className = "strategy-campaign-actions";

    const actionLabel = document.createElement("label");
    const actionText = document.createElement("span");
    actionText.textContent = "行动";
    const actionSelect = document.createElement("select");
    (campaign?.world?.exile_action_choices || []).forEach((choice) => {
      const option = document.createElement("option");
      option.value = choice.id;
      option.textContent = choice.name || choice.id;
      option.dataset.requiresTargetCity = choice.requires_target_city ? "1" : "";
      actionSelect.append(option);
    });
    if (actionSelect.children.length && !actionSelect.value) actionSelect.value = actionSelect.children[0].value;
    actionLabel.append(actionText, actionSelect);
    actions.append(actionLabel);

    const targetLabel = document.createElement("label");
    const targetText = document.createElement("span");
    targetText.textContent = "目标城市";
    const targetSelect = document.createElement("select");
    (campaign?.world?.cities || [])
      .filter((city) => city.owner_faction_id !== faction.id)
      .forEach((city) => {
        const option = document.createElement("option");
        option.value = city.id;
        option.textContent = city.name;
        targetSelect.append(option);
      });
    if (targetSelect.children.length && !targetSelect.value) targetSelect.value = targetSelect.children[0].value;
    targetLabel.append(targetText, targetSelect);
    actions.append(targetLabel);

    const updateTargetState = () => {
      const selectedOption = actionSelect.children[actionSelect.selectedIndex] || null;
      const requiresTarget = selectedOption?.dataset?.requiresTargetCity === "1";
      targetSelect.disabled = state.strategyBusy || !canResume || !requiresTarget;
    };
    actionSelect.addEventListener("change", updateTargetState);
    updateTargetState();

    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.textContent = "加入月度计划 · 1 军令";
    button.disabled = state.strategyBusy || !canResume || !actionSelect.children.length || !strategyCanAffordCommand(campaign, faction, "exile_action");
    button.addEventListener("click", () => {
      const selectedOption = actionSelect.children[actionSelect.selectedIndex] || null;
      const requiresTarget = selectedOption?.dataset?.requiresTargetCity === "1";
      const payload = { exile_action_id: actionSelect.value };
      if (requiresTarget) payload.target_city_id = targetSelect.value;
      queueStrategyAction("exile_action", payload);
    });
    actions.append(button);
    card.append(actions);
    panel.append(card);
    current.append(panel);
  }
}

/**
 * 武将详情。
 *
 * 上面那份紧凑名单回答的是"我还有几个人没派活"，这里回答"这个人是谁"——而且
 * 只回答被点开的那一个：十几份履历同时铺在一页上，等于谁都没读。
 */
function renderStrategyHeroDetail(current, campaign, faction, office, hero) {
  const canSetDefense = !office || office.office_type === "grand_general";
  if (!hero) {
    const heroes = campaignFactionHeroes(campaign, faction);
    if (heroes.length) appendTextLine(current, "strategy-meta", "点名单里的武将查看他的属性、忠诚、专长与任务。");
    return;
  }

  const title = document.createElement("h4");
  title.textContent = "武将详情";
  current.append(title);

  const panel = document.createElement("div");
  panel.className = "strategy-tech-grid strategy-hero-detail";
  [hero].forEach((hero) => {
    const card = document.createElement("article");
    card.className = "strategy-tech-card";
    const name = document.createElement("strong");
    name.textContent = hero.name || hero.code;
    card.append(name);
    appendTextLine(card, "strategy-meta", `${hero.role || "未知职业"} · ${hero.attribute || "未知属性"} · Lv ${hero.level || 1}`);
    appendTextLine(card, "strategy-meta", `所在：${strategyCityName(campaign, hero.city_id) || "行踪不明"}`);
    if (hero.status === "sleeping") {
      appendTextLine(card, "strategy-meta", `状态：沉睡中 · 第 ${hero.sleeping_until_month || "?"} 月恢复`);
    } else {
      appendTextLine(card, "strategy-meta", `状态：${hero.status === "serving" ? "仕官中" : "在野"}`);
    }
    if (hero.office_id) {
      const heldOffice = (campaign?.world?.offices || []).find((item) => item.id === hero.office_id);
      appendTextLine(card, "strategy-meta", `职位：${strategyOfficeLabel(heldOffice, campaign)}`);
    }
    if (hero.ritual_city_id) {
      appendTextLine(card, "strategy-meta", `祭祀绑定：${strategyCityName(campaign, hero.ritual_city_id)}`);
    }
    if (hero.defender_assigned) {
      appendTextLine(card, "strategy-meta", "防守：默认出战");
    }
    appendTextLine(
      card,
      "strategy-meta",
      `忠诚：${hero.loyalty ?? 50}（${hero.loyalty_band?.label || "稳定"}） · 对主公关系：${hero.lord_relationship ?? "—"}`
    );
    if (hero.specialty) {
      appendTextLine(card, "strategy-meta", `战略专长：${hero.specialty.name} · ${hero.specialty.effect}`);
    }
    if (hero.personal_mission) {
      const statusLabels = { active: "进行中", completed: "已完成", failed: "已逾期" };
      appendTextLine(
        card,
        "strategy-meta",
        `个人任务：${hero.personal_mission.name} · ${statusLabels[hero.personal_mission.status] || hero.personal_mission.status} · ${hero.personal_mission.progress}/${hero.personal_mission.required}${hero.personal_mission.due_month ? ` · 截止第 ${hero.personal_mission.due_month} 月` : ""}`
      );
    }
    const recentPersonal = Array.isArray(hero.recent_personal_history) ? hero.recent_personal_history.slice(-1)[0] : null;
    if (recentPersonal?.summary) {
      appendTextLine(card, "strategy-meta", `最近经历：第 ${recentPersonal.month} 月 · ${recentPersonal.summary}`);
    }
    const actions = document.createElement("div");
    actions.className = "strategy-tech-actions";
    if (canSetDefense && hero.status === "serving") {
      const defense = document.createElement("button");
      defense.type = "button";
      defense.className = hero.defender_assigned ? "ghost" : "primary";
      defense.textContent = hero.defender_assigned ? "防守中" : "设为防守";
      defense.disabled = state.strategyBusy || !strategyCanIssueOrders(campaign) || hero.defender_assigned || hero.command_acceptance?.battle === false;
      if (hero.command_acceptance?.battle === false) defense.textContent = "本月拒绝出战";
      defense.addEventListener("click", () => setStrategyDefenseHero(hero.code));
      actions.append(defense);
    }
    if (actions.children.length) card.append(actions);
    panel.append(card);
  });
  current.append(panel);
}

/**
 * 城内武将。
 *
 * 城市页要回答的第二件事：这座城里现在有谁。它和「武将」页那份势力花名册不同
 * ——这里只列站在这座城里的人，包括敌方在城内的守将。
 */
function renderStrategyCityHeroes(current, campaign, city) {
  const heroes = (campaign?.world?.strategic_hero_pool || []).filter((hero) => hero.city_id === city.id);
  const title = document.createElement("h4");
  title.textContent = `城内武将 · ${heroes.length}`;
  current.append(title);
  if (!heroes.length) {
    appendTextLine(current, "strategy-meta", "城里目前没有武将驻留。");
    return;
  }
  const list = document.createElement("div");
  list.className = "campaign-hero-list";
  heroes.forEach((hero) => {
    const card = document.createElement("article");
    card.className = "hero-slot";
    const head = document.createElement("div");
    head.className = "hero-slot__head";
    const name = document.createElement("strong");
    name.className = "hero-slot__name";
    name.textContent = hero.name || hero.code;
    head.append(name);
    const duty = document.createElement("span");
    duty.className = "hero-slot__duty";
    const heldOffice = (campaign?.world?.offices || []).find((item) => item.id === hero.office_id);
    duty.textContent = heldOffice
      ? `${strategyOfficeLabel(heldOffice, campaign)} · ${strategyFactionName(campaign, hero.faction_id)}`
      : `${hero.status === "roaming" ? "在野" : strategyFactionName(campaign, hero.faction_id)}`;
    card.append(head, duty);
    list.append(card);
  });
  current.append(list);
}

function renderStrategyRelicPanel(current, campaign, faction) {
  const relicSystem = campaign?.world?.relic_system || {};
  if (!relicSystem.enabled || !faction?.id) return;
  const intel = relicSystem.intel_by_faction?.[faction.id] || {
    known_relics: [],
    known_count: 0,
    unknown_count: relicSystem.total_relics || 0,
  };

  const panel = document.createElement("div");
  panel.className = "strategy-tech-grid strategy-relic-grid";

  const rulesCard = document.createElement("article");
  rulesCard.className = "strategy-tech-card";
  const rulesTitle = document.createElement("strong");
  rulesTitle.textContent = "两套设施，不同职责";
  rulesCard.append(rulesTitle);
  appendTextLine(rulesCard, "strategy-meta", relicSystem.rules?.ritual_site || "祭祀场负责随机召唤英灵。");
  appendTextLine(rulesCard, "strategy-meta", relicSystem.rules?.relic_altar || "圣物祭坛负责圣物路线。");
  appendTextLine(rulesCard, "strategy-meta", relicSystem.rules?.current_scope || "当前只开放情报档案。");
  panel.append(rulesCard);

  const intelCard = document.createElement("article");
  intelCard.className = "strategy-tech-card";
  const intelTitle = document.createElement("strong");
  intelTitle.textContent = `本势力圣物情报 · ${intel.known_count || 0}/${relicSystem.total_relics || 0}`;
  intelCard.append(intelTitle);
  const knownRelics = Array.isArray(intel.known_relics) ? intel.known_relics : [];
  if (!knownRelics.length) {
    appendTextLine(intelCard, "strategy-meta", "尚未掌握确切圣物传闻。");
  }
  knownRelics.forEach((relic) => {
    appendTextLine(
      intelCard,
      "strategy-meta",
      `${relic.name} · ${relic.state_label || relic.state} / ${relic.condition_label || relic.condition} · 线索指向 ${relic.location_city_name || relic.location_node_name || "未知区域"}`
    );
  });
  appendTextLine(intelCard, "strategy-meta", `仍有 ${intel.unknown_count || 0} 件圣物位置未知；搜索、转移与修复由主公在职位工作台签发。`);
  panel.append(intelCard);

  const altarCard = document.createElement("article");
  altarCard.className = "strategy-tech-card";
  const altarTitle = document.createElement("strong");
  altarTitle.textContent = "已知圣物祭坛";
  altarCard.append(altarTitle);
  const altars = Array.isArray(relicSystem.altars) ? relicSystem.altars : [];
  if (!altars.length) {
    appendTextLine(altarCard, "strategy-meta", "当前剧本没有已登记的圣物祭坛。");
  }
  altars.forEach((altar) => {
    const consecration = altar.consecration || {};
    const progress = Number(consecration.progress || 0);
    const required = Number(consecration.required || 3);
    const progressOwner = consecration.faction_id
      ? strategyFactionName(campaign, consecration.faction_id)
      : "";
    const progressText = consecration.completed
      ? `圣物胜利已完成 · ${progressOwner}`
      : progress > 0
        ? `胜利准备 ${progress}/${required} · ${progressOwner} · 最早第 ${consecration.earliest_completion_month} 月完成`
        : altar.bound_count
          ? `胜利准备 0/${required} · 下一次成功维护后开始`
          : `胜利准备 0/${required} · 尚未绑定完整圣物`;
    appendTextLine(
      altarCard,
      "strategy-meta",
      `${altar.city_name || "未知城市"} · ${altar.state_label || altar.state} · ${strategyFactionName(campaign, altar.owner_faction_id)}控制 · 绑定 ${altar.bound_count || 0}/${altar.capacity || 1} · 月维护 ${altar.monthly_maintenance_ether || 0} 以太${altar.monthly_maintenance_ether && !altar.maintenance_affordable ? "（当前不足）" : ""} · ${progressText} · 本月行动 ${altar.actions_remaining ?? 0}/${(altar.actions_used || 0) + (altar.actions_remaining ?? 0)}`
    );
  });
  appendTextLine(altarCard, "strategy-meta", "绑定当月不计进度；连续 3 次月初全额维护后立即获胜。断供、释放、更换圣物或城市易主都会清零公开进度。");
  panel.append(altarCard);
  current.append(panel);
}

function strategySideLabel(side) {
  if (side === "attacker") return "攻方";
  if (side === "defender") return "守方";
  return side || "未知";
}

export function strategyNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function strategyBattleResultLines(battle = {}) {
  const result = battle.battle_result || {};
  if (!result || typeof result !== "object" || !Object.keys(result).length) return [];

  const lost = result.lost_troops_by_side || {};
  const remaining = result.remaining_troops_by_side || {};
  const initialGrid = result.initial_grid_units_by_side || {};
  const survivingGrid = result.surviving_grid_units_by_side || {};
  const lines = [
    `结果：${strategySideLabel(result.winner_side)}胜利 · ${result.city_captured ? "攻城成功" : "守城成功"}`,
    `损失：攻方 ${strategyNumber(lost.attacker)} · 守方 ${strategyNumber(lost.defender)}`,
    `剩余兵力：攻方 ${strategyNumber(remaining.attacker)} · 守方 ${strategyNumber(remaining.defender)}`,
  ];
  if (
    Object.prototype.hasOwnProperty.call(survivingGrid, "attacker") ||
    Object.prototype.hasOwnProperty.call(survivingGrid, "defender") ||
    Object.prototype.hasOwnProperty.call(initialGrid, "attacker") ||
    Object.prototype.hasOwnProperty.call(initialGrid, "defender")
  ) {
    const attackerInitial = Object.prototype.hasOwnProperty.call(initialGrid, "attacker") ? strategyNumber(initialGrid.attacker) : "?";
    const defenderInitial = Object.prototype.hasOwnProperty.call(initialGrid, "defender") ? strategyNumber(initialGrid.defender) : "?";
    lines.push(
      `存活单位：攻方 ${strategyNumber(survivingGrid.attacker)}/${attackerInitial} · 守方 ${strategyNumber(survivingGrid.defender)}/${defenderInitial}`
    );
  }
  const strategicHeroes = result.strategic_heroes_by_side || {};
  ["attacker", "defender"].forEach((side) => {
    const row = strategicHeroes[side] || {};
    const committed = Array.isArray(row.committed) ? row.committed : [];
    const surviving = Array.isArray(row.surviving) ? row.surviving : [];
    const sleeping = Array.isArray(row.sleeping) ? row.sleeping : [];
    if (!committed.length && !surviving.length && !sleeping.length) return;
    const fragments = [];
    if (committed.length) fragments.push(`参战 ${committed.join(", ")}`);
    if (surviving.length) fragments.push(`存活 ${surviving.join(", ")}`);
    if (sleeping.length) fragments.push(`沉睡 ${sleeping.join(", ")}`);
    lines.push(`英灵：${strategySideLabel(side)} ${fragments.join(" · ")}`);
  });
  const controlChange = result.city_control_change || {};
  const capturedRelics = Array.isArray(controlChange.captured_relic_names)
    ? controlChange.captured_relic_names
    : [];
  const disruptedAltars = Array.isArray(controlChange.disrupted_altar_names)
    ? controlChange.disrupted_altar_names
    : [];
  const unboundHeroes = Array.isArray(controlChange.unbound_hero_codes)
    ? controlChange.unbound_hero_codes
    : [];
  if (capturedRelics.length) lines.push(`圣物夺取：${capturedRelics.join("、")}`);
  if (disruptedAltars.length) lines.push(`祭坛失养：${disruptedAltars.join("、")}`);
  if (unboundHeroes.length) lines.push(`解除祭祀绑定：${unboundHeroes.join("、")}`);
  if (result.battle_log_summary) {
    lines.push(`摘要：${result.battle_log_summary}`);
  }
  return lines;
}

const STRATEGY_CREATE_STEPS = [
  { id: "scenario", label: "开局变体" },
  { id: "identity", label: "战役身份" },
  { id: "confirm", label: "确认开局" },
];

function strategyCreateVariants() {
  return state.strategyVariants.length ? state.strategyVariants : FALLBACK_STRATEGY_VARIANTS;
}

/**
 * 新建战役。
 *
 * 此前这是列表页顶上常驻的一整排输入框——名字、种子、变体、势力数、加入码挤在同
 * 一行网格里，其中一半还是禁用的。可是"开一局新的"是偶尔发生一次的事，没有理由
 * 每次打开战役列表都先看它一遍。所以它成了一条独立流程：先选这局要问什么问题，
 * 再给它起个名字，最后确认。三步各只回答一件事。
 */
function renderStrategyCreateWizard(host) {
  const variants = strategyCreateVariants();
  const selectedVariant = variants.find((item) => item.id === state.strategyVariantId) || variants[0];
  state.strategyVariantId = selectedVariant.id;
  const step = Math.max(0, Math.min(STRATEGY_CREATE_STEPS.length - 1, Number(state.strategyCreateStep) || 0));

  const wizard = document.createElement("section");
  wizard.className = "strategy-wizard";

  const head = document.createElement("header");
  head.className = "strategy-wizard__head";
  const title = document.createElement("h3");
  title.textContent = "新建战役";
  head.append(title, createButton({
    label: "取消",
    variant: "subtle",
    size: "sm",
    disabled: state.strategyBusy,
    onClick: closeStrategyCampaignCreator,
  }));
  wizard.append(head);

  const steps = document.createElement("ol");
  steps.className = "strategy-wizard__steps";
  STRATEGY_CREATE_STEPS.forEach((item, index) => {
    const node = document.createElement("li");
    node.className = `strategy-wizard__step${index === step ? " is-active" : ""}${index < step ? " is-done" : ""}`;
    const order = document.createElement("span");
    order.className = "strategy-wizard__step-order";
    order.textContent = index < step ? "✓" : String(index + 1);
    const label = document.createElement("span");
    label.className = "strategy-wizard__step-label";
    label.textContent = item.label;
    node.append(order, label);
    if (index < step) {
      node.classList.add("is-clickable");
      node.addEventListener("click", () => setStrategyCreateStep(index));
    }
    steps.append(node);
  });
  wizard.append(steps);

  const body = document.createElement("div");
  body.className = "strategy-wizard__body";

  if (step === 0) {
    appendTextLine(body, "strategy-wizard__lead", "变体决定这一局要回答的核心问题，也决定开局资源。");
    const grid = document.createElement("div");
    grid.className = "strategy-wizard__variants";
    variants.forEach((variant) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = `strategy-wizard__variant${variant.id === selectedVariant.id ? " is-active" : ""}`;
      card.setAttribute("aria-pressed", variant.id === selectedVariant.id ? "true" : "false");
      const name = document.createElement("strong");
      name.textContent = variant.name;
      card.append(name);
      appendTextLine(card, "strategy-wizard__variant-question", variant.core_question || "");
      (Array.isArray(variant.modifiers) ? variant.modifiers : []).forEach((line) => {
        appendTextLine(card, "strategy-meta", line);
      });
      card.addEventListener("click", () => {
        state.strategyVariantId = variant.id;
        renderStrategyPanel();
      });
      grid.append(card);
    });
    body.append(grid);
  } else if (step === 1) {
    appendTextLine(body, "strategy-wizard__lead", "名字只给你自己和同局玩家看；同一个种子会生成同一张地图。");
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.maxLength = 40;
    nameInput.value = state.strategyName || "";
    nameInput.disabled = state.strategyBusy;
    nameInput.addEventListener("input", (event) => {
      state.strategyName = String(event.target.value || "").slice(0, 40);
    });
    body.append(createStrategyField("战役名", nameInput));

    const seedRow = document.createElement("div");
    seedRow.className = "strategy-wizard__seed";
    const seedInput = document.createElement("input");
    seedInput.type = "number";
    seedInput.step = "1";
    seedInput.value = state.strategySeed || "1";
    seedInput.disabled = state.strategyBusy;
    seedInput.addEventListener("input", (event) => {
      const cleaned = String(event.target.value || "").replace(/[^\d]/g, "").slice(0, 9);
      state.strategySeed = cleaned || "1";
    });
    seedRow.append(createStrategyField("地图种子", seedInput), createButton({
      label: "换一张",
      variant: "subtle",
      size: "sm",
      disabled: state.strategyBusy,
      onClick: () => {
        state.strategySeed = String(1 + Math.floor(Math.random() * 999982));
        renderStrategyPanel();
      },
    }));
    body.append(seedRow);
  } else {
    appendTextLine(body, "strategy-wizard__lead", "创建后先进入开局准备：每个席位各自挑选出身，房主锁定后战役才真正开始。");
    const summary = document.createElement("dl");
    summary.className = "strategy-wizard__summary";
    [
      ["战役名", state.strategyName || "英灵城邦"],
      ["开局变体", selectedVariant.name],
      ["核心问题", selectedVariant.core_question || "—"],
      ["地图种子", String(state.strategySeed || "1")],
      ["规模", "8 城 · 2 个主要势力 · 6 个中立城邦 · 12 个月"],
      ["席位", "2～4 名真人，每个势力最多 2 人；空席由 AI 接管"],
    ].forEach(([label, value]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value;
      summary.append(term, detail);
    });
    body.append(summary);
  }
  wizard.append(body);

  const footer = document.createElement("div");
  footer.className = "strategy-wizard__footer";
  if (step > 0) {
    footer.append(createButton({
      label: "上一步",
      variant: "subtle",
      disabled: state.strategyBusy,
      onClick: () => setStrategyCreateStep(step - 1),
    }));
  }
  footer.append(createButton({
    label: step === STRATEGY_CREATE_STEPS.length - 1 ? "创建战役" : "下一步",
    variant: "primary",
    disabled: state.strategyBusy || !userLoggedIn(),
    onClick: () => {
      if (step === STRATEGY_CREATE_STEPS.length - 1) createStrategyCampaign();
      else setStrategyCreateStep(step + 1);
    },
  }));
  wizard.append(footer);
  host.append(wizard);
}

/**
 * 开局准备。
 *
 * 出身抉择此前和地图挤在同一屏里往下滚——一边还没决定自己是谁，一边已经能点城
 * 市下令了。它其实是战役开始之前的一件事，所以它自己一屏：谁在这局、你要走哪条
 * 路、房主什么时候开始。
 */
function renderStrategyPrepScreen(current, campaign, isOwner) {
  const screen = document.createElement("section");
  screen.className = "campaign-prep";

  const head = document.createElement("header");
  head.className = "campaign-prep__head";
  const heading = document.createElement("div");
  const kicker = document.createElement("span");
  kicker.className = "campaign-prep__kicker";
  kicker.textContent = "开局准备";
  const title = document.createElement("h2");
  title.textContent = campaign.name || "战役";
  heading.append(kicker, title);
  head.append(heading);

  const invite = campaign.invite || {};
  const joinCode = invite.join_code || campaign.join_code || "";
  if (invite.status !== "revoked" && joinCode) {
    const code = document.createElement("div");
    code.className = "campaign-prep__code";
    appendTextLine(code, "meta-label", "加入码");
    const value = document.createElement("strong");
    value.textContent = joinCode;
    code.append(value);
    head.append(code);
  }
  head.append(createButton({
    label: "战役列表",
    variant: "subtle",
    size: "sm",
    disabled: state.strategyBusy,
    onClick: exitStrategyCampaignView,
  }));
  screen.append(head);

  const columns = document.createElement("div");
  columns.className = "campaign-prep__columns";

  const path = document.createElement("div");
  path.className = "campaign-prep__column";
  path.append(createStrategyHeroPathPanel(campaign));
  columns.append(path);

  const seats = document.createElement("div");
  seats.className = "campaign-prep__column";
  renderStrategyMembersPanel(seats, campaign, isOwner);
  renderStrategyObjectivePanel(seats, campaign);
  columns.append(seats);
  screen.append(columns);

  const footer = document.createElement("footer");
  footer.className = "campaign-prep__footer";
  const missing = strategyMissingInitialPlayerLabels(campaign);
  appendTextLine(
    footer,
    "strategy-meta",
    missing.length
      ? `仍有 ${missing.length} 个初始席位没有真人：${missing.join("、")}。锁定后它们由 AI 接管。`
      : "所有初始席位都已有人。",
  );
  if (isOwner) {
    footer.append(createButton({
      label: "锁定并开始战役",
      variant: "primary",
      disabled: state.strategyBusy,
      onClick: () => lockStrategyCampaign(campaign.id),
    }));
  } else {
    appendTextLine(footer, "strategy-meta", "等待房主锁定后开始。");
  }
  screen.append(footer);
  current.append(screen);
}

function renderStrategyTechPanel(current, campaign, faction, canResume, office = strategyActiveOffice(campaign)) {
  const techs = faction?.tactic_tech_tree || [];
  if (!techs.length) return;
  // 标题由面板页头给出，这里只说规则——同一句话写两遍就是在浪费一屏。
  const canResearch = !office || office.office_type === "lord";
  appendTextLine(
    current,
    "strategy-meta",
    canResearch
      ? "研究占用 1 军令，在月结时结算；分支之间有前置关系。"
      : "只有主公能签发研究，其余职位在这里看进度。",
  );
  const grid = document.createElement("div");
  grid.className = "strategy-tech-grid";
  techs.forEach((tech) => {
    const card = document.createElement("article");
    card.className = `strategy-tech-card tech-branch-${tech.branch || "military"}`;
    const name = document.createElement("strong");
    name.textContent = tech.name;
    card.append(name);
    appendTextLine(card, "meta-label", ({ office: "职位分支", unit: "兵种分支", building: "建筑分支", military: "战术分支" })[tech.branch] || "战术分支");
    appendTextLine(card, "strategy-meta", tech.description);
    appendTextLine(card, "strategy-meta", `费用：钱 ${tech.money_cost} · 以太 ${tech.ether_cost}`);
    const actions = document.createElement("div");
    actions.className = "strategy-tech-actions";
    const queueTech = document.createElement("button");
    queueTech.type = "button";
    queueTech.className = tech.unlocked ? "ghost" : "primary";
    queueTech.textContent = tech.unlocked ? "已解锁" : "研究科技 · 1 军令";
    queueTech.disabled = state.strategyBusy || !canResume || !canResearch || tech.unlocked || !tech.available || !strategyCanAffordCommand(campaign, faction, "unlock_tactic_tech");
    queueTech.addEventListener("click", () => queueStrategyAction("unlock_tactic_tech", { tech_id: tech.id }));
    actions.append(queueTech);
    card.append(actions);
    grid.append(card);
  });
  current.append(grid);
}

function renderStrategyEventLog(current, campaign) {
  const title = document.createElement("h4");
  title.textContent = "事件";
  current.append(title);
  const events = document.createElement("div");
  events.className = "strategy-event-list";
  const eventLog = (campaign?.world?.event_log || []).slice(-8).reverse();
  eventLog.forEach((event) => {
    appendTextLine(events, "strategy-event", `第 ${event.month} 月 · ${event.message}`);
  });
  if (!eventLog.length) appendTextLine(events, "strategy-meta", "目前还没有战役事件记录。");
  current.append(events);
}

function renderStrategyBattleRecords(current, campaign, faction, canResume) {
  const battleRecords = (campaign?.world?.pending_battles || []).slice(-6).reverse();
  if (!battleRecords.length) return;
  const battlesTitle = document.createElement("h4");
  battlesTitle.textContent = "战斗记录";
  current.append(battlesTitle);
  const battles = document.createElement("div");
  battles.className = "strategy-event-list";
  battleRecords.forEach((battle) => {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const title = document.createElement("strong");
    title.textContent = battle.source_kind === "encounter"
      ? `遭遇战 · ${strategyNodeName(campaign, battle.battle_node_id)}`
      : battle.source_kind === "siege"
        ? `${battle.battle_trigger === "breakout" ? "突围战" : "破城强攻"} · ${strategyCityName(campaign, battle.target_city_id)}`
        : `${strategyCityName(campaign, battle.source_city_id)} → ${strategyCityName(campaign, battle.target_city_id)}`;
    card.append(title);
    const statusNames = { pending: "待处理", resolved: "已结算" };
    appendTextLine(card, "strategy-meta", `处理方式：${battle.resolution_mode || "quick"} · 状态：${statusNames[battle.status] || battle.status}`);
    strategyBattleResultLines(battle).forEach((line) => appendTextLine(card, "strategy-meta", line));
    if (battle.battle_room_id) {
      const roomInfo = currentStrategyBattleRoomForBattle(battle);
      appendTextLine(card, "strategy-meta", `真实战斗房间：${battle.battle_room_id}`);
      if (battle.battle_room_invite_path) {
        appendTextLine(card, "strategy-meta", `入口：${battle.battle_room_invite_path}`);
      }
      const attackerSummary = strategyRosterManifestSummary(roomInfo.attacker_roster_manifest);
      const defenderSummary = strategyRosterManifestSummary(roomInfo.defender_roster_manifest);
      if (attackerSummary) appendTextLine(card, "strategy-meta", `攻方单位：${attackerSummary}`);
      if (defenderSummary) appendTextLine(card, "strategy-meta", `守方单位：${defenderSummary}`);
      const actions = document.createElement("div");
      actions.className = "strategy-campaign-actions";
      const open = document.createElement("button");
      open.type = "button";
      open.className = "primary";
      open.textContent = battle.status === "resolved" ? "查看真实战斗" : battle.resolution_mode === "watch_ai" ? "观看 AI 战斗" : "进入真实战斗";
      open.disabled = state.strategyBusy;
      open.addEventListener("click", () => openStrategyBattleRoom(roomInfo));
      actions.append(open);
      const recovery = state.strategyBattleRecovery;
      if (
        battle.status === "pending"
        && recovery?.can_restart_from_prebattle
        && String(recovery.room_id || "").toUpperCase() === String(battle.battle_room_id).toUpperCase()
      ) {
        const restart = document.createElement("button");
        restart.type = "button";
        restart.textContent = "从战前快照安全重开";
        restart.disabled = state.strategyBusy;
        restart.addEventListener("click", () => restartStrategyBattleFromSnapshot(battle.battle_room_id));
        actions.append(restart);
        appendTextLine(card, "strategy-meta", "服务器检查点不可恢复；安全重开不会重复扣除战略成本，也不会预写胜负。");
      }
      card.append(actions);
    } else if (battle.status === "pending") {
      appendTextLine(card, "strategy-meta", "等待创建真实格子战房间。");
      if (battle.defender_faction_id === faction?.id) {
        const actions = document.createElement("div");
        actions.className = "strategy-campaign-actions";
        const heroLabel = document.createElement("label");
        const heroSpan = document.createElement("span");
        heroSpan.textContent = "本场防守";
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
        const currentDefenderHeroes = Array.isArray(battle.defender_hero_codes) ? battle.defender_hero_codes : [];
        heroSelect.value = currentDefenderHeroes[0] || "";
        heroSelect.disabled = state.strategyBusy || !canResume;
        const heroMultiPicker = createStrategyHeroDeploymentPicker(faction, currentDefenderHeroes);
        if (strategyHeroDeploymentLimit(faction) > 1) {
          heroSelect.disabled = true;
          heroSelect.style.display = "none";
          heroMultiPicker.setDisabled(state.strategyBusy || !canResume);
        }
        heroLabel.append(heroSpan, heroSelect);
        if (strategyHeroDeploymentLimit(faction) > 1) heroLabel.append(heroMultiPicker.element);
        actions.append(heroLabel);
        const defend = document.createElement("button");
        defend.type = "button";
        defend.className = "ghost";
        defend.textContent = "设置本场防守";
        defend.disabled = state.strategyBusy || !canResume;
        defend.addEventListener("click", () => setStrategyBattleDefenseHero(
          battle.id || battle.battle_id,
          strategyHeroDeploymentLimit(faction) > 1 ? heroMultiPicker.selectedCodes() : heroSelect.value
        ));
        actions.append(defend);
        card.append(actions);
      }
    } else if (!strategyBattleResultLines(battle).length && Array.isArray(battle.report) && battle.report.length) {
      appendTextLine(card, "strategy-meta", `战报：${battle.report[battle.report.length - 1]}`);
    }
    battles.append(card);
  });
  current.append(battles);
}

function renderStrategyCampaignList(list) {
  if (!state.strategyCampaigns.length) {
    appendTextLine(list, "empty-line", "当前账号还没有战略战役。点右上角新建一局。");
    return;
  }
  state.strategyCampaigns.forEach((campaign) => {
    const card = document.createElement("article");
    card.className = "strategy-campaign-card";
    const campaignStatus = campaign.status === "archived"
      ? "已归档"
      : campaign.status === "active" ? "进行中" : "开局准备";
    const humanMembers = (campaign.members || []).filter((member) => !strategyMemberIsAi(member));
    appendTextLine(
      card,
      "strategy-meta",
      `第 ${campaign.world.current_month}${campaign.world.strategic_status?.month_limit ? `/${campaign.world.strategic_status.month_limit}` : ""} 月 · ${campaign.world.cities.length} 城 · ${humanMembers.length}/${campaign.world.factions.length} 真人 · ${campaignStatus}`
    );
    const invite = campaign.invite || {};
    const joinCode = invite.join_code || campaign.join_code || "";
    if (campaign.status === "lobby" && invite.status !== "revoked" && joinCode) {
      appendTextLine(card, "strategy-meta", `加入码：${joinCode}`);
    }
    const title = document.createElement("strong");
    title.textContent = campaign.name;
    card.prepend(title);
    const resume = campaign.resume || {};
    if (campaign.status === "active") {
      const strategicStatus = campaign.world?.strategic_status || {};
      const pending = strategicStatus.awaiting_conclusion_choice
        ? `${strategicStatus.conclusion?.result_label || "战役评议"}已完成，等待选择后续。`
        : resume.can_advance_month
          ? "所有真人已提交，等待房主结算。"
          : "";
      if (pending) appendTextLine(card, "strategy-meta", pending);
    }
    const actions = document.createElement("div");
    actions.className = "strategy-campaign-actions";
    const enter = document.createElement("button");
    enter.className = "primary";
    enter.type = "button";
    enter.textContent = campaign.status === "archived" ? "查看归档" : campaign.status === "active" ? "继续战役" : "进入准备";
    enter.disabled = state.strategyBusy;
    enter.addEventListener("click", () => enterStrategyCampaign(campaign.id));
    actions.append(enter);
    const isOwnerOfCard = Number(campaign.owner_user_id) === Number(state.authUser?.id || 0);
    const viewerId = Number(state.authUser?.id || 0);
    const online = (campaign.resume?.online_initial_user_ids || []).map(Number).includes(viewerId);
    if (campaign.status !== "archived" && online) {
      const leave = document.createElement("button");
      leave.className = "ghost";
      leave.type = "button";
      leave.textContent = "标记离线";
      leave.title = "告诉其他玩家你暂时不在，房主可以不等你直接推进月份。";
      leave.disabled = state.strategyBusy;
      leave.addEventListener("click", () => leaveStrategyCampaign(campaign.id));
      actions.append(leave);
    }
    if (isOwnerOfCard) {
      const remove = document.createElement("button");
      remove.className = "ghost danger";
      remove.type = "button";
      remove.textContent = "删除";
      remove.disabled = state.strategyBusy;
      remove.addEventListener("click", () => deleteStrategyCampaign(campaign.id));
      actions.append(remove);
    }
    card.append(actions);
    list.append(card);
  });
}

export function renderStrategyPanel() {
  const panel = $("strategy-panel");
  if (!panel) return;
  const browser = $("strategy-browser");
  const createHost = $("strategy-create");
  const joinCodeInput = $("strategy-join-code");
  const joinHostFactionInput = $("strategy-join-host-faction");
  const joinButton = $("strategy-join");
  const newCampaignButton = $("strategy-new-campaign");
  const browserRefreshButton = $("strategy-browser-refresh");
  const exitCampaignButton = $("strategy-exit-campaign");
  const refreshButton = $("strategy-refresh");
  const advanceButton = $("strategy-advance-month");
  const message = $("strategy-message");
  const list = $("strategy-campaign-list");
  const current = $("strategy-current");
  const caption = state.homeFlow === "campaign" ? $("lobby-caption") : null;
  const roomHome = $("room-home");
  const loggedIn = userLoggedIn();
  const selected = state.strategyCampaign;
  const creating = Boolean(state.strategyCreateOpen) && !selected;
  const canResume = strategyCanResume(selected);
  const canIssueOrders = strategyCanIssueOrders(selected);
  const selectedIsOwner = Boolean(selected && Number(selected.owner_user_id) === Number(state.authUser?.id || 0));

  panel.classList.toggle("is-war-room", Boolean(selected));
  if (roomHome) roomHome.classList.toggle("strategy-war-layout", Boolean(selected));
  // 选中一局之后，这一屏就是整个游戏：外壳的页头、说明行和按钮条都让位给它。
  document.body.classList.toggle(
    "campaign-mode",
    Boolean(selected) && state.screen === "draft" && state.homeFlow === "campaign",
  );

  if (caption) {
    caption.textContent = selected || loggedIn ? "" : "请先登录账号，战略战役会绑定到账号存档。";
  }
  if (browser) browser.classList.toggle("hidden", Boolean(selected) || creating);
  if (createHost) createHost.classList.toggle("hidden", !creating);
  if (joinCodeInput) {
    joinCodeInput.value = state.strategyJoinCode;
    joinCodeInput.disabled = state.strategyBusy || !loggedIn;
  }
  if (joinHostFactionInput) {
    joinHostFactionInput.checked = Boolean(state.strategyJoinHostFaction);
    joinHostFactionInput.disabled = state.strategyBusy || !loggedIn;
  }
  if (joinButton) joinButton.disabled = state.strategyBusy || !loggedIn || !String(state.strategyJoinCode || "").trim();
  if (newCampaignButton) newCampaignButton.disabled = state.strategyBusy || !loggedIn;
  if (browserRefreshButton) browserRefreshButton.disabled = state.strategyBusy || !loggedIn;
  if (exitCampaignButton) exitCampaignButton.disabled = state.strategyBusy || !selected;
  if (refreshButton) refreshButton.disabled = state.strategyBusy || !loggedIn;
  if (advanceButton) {
    // 战役屏顶上的推进按钮照抄这里的判断（见 campaign-shell.js），所以规则只写一处。
    advanceButton.disabled = state.strategyBusy
      || !loggedIn
      || !selected
      || !canResume
      || !strategyHostCanRequestAdvance(selected)
      || selected?.world?.strategic_status?.can_advance_month === false
      || !selectedIsOwner;
  }
  if (message) message.textContent = state.strategyMessage || "";
  if (!list || !current) return;

  list.innerHTML = "";
  current.innerHTML = "";
  if (createHost) createHost.innerHTML = "";
  if (!loggedIn) {
    appendTextLine(list, "strategy-meta", "登录后会显示你参与过的战役。");
    return;
  }
  if (creating && createHost) {
    renderStrategyCreateWizard(createHost);
    return;
  }
  if (!selected) {
    renderStrategyCampaignList(list);
    return;
  }

  const controlledHero = strategyControlledHero(selected);
  const faction = strategyFaction(selected);
  if (selected.status === "lobby") {
    renderStrategyPrepScreen(current, selected, selectedIsOwner);
    return;
  }
  if (controlledHero?.status === "roaming") {
    renderStrategyRoamingWorkspace(current, selected, controlledHero);
    return;
  }
  renderStrategyWarRoom(
    current,
    selected,
    faction || selected.world.factions[0],
    canIssueOrders,
    selectedIsOwner,
  );
}

export function renderProfileModal() {
  const modal = $("profile-modal");
  const input = $("profile-name-input");
  const title = $("profile-modal-title");
  const text = $("profile-modal-text");
  const save = $("profile-save");
  const cancel = $("profile-cancel");
  if (!modal || !input || !title || !text || !save) return;
  const visible = profileModalVisible();
  modal.classList.toggle("hidden", !visible);
  modal.setAttribute("aria-hidden", visible ? "false" : "true");
  syncModalIsolation();
  input.value = state.profileDraftName;
  title.textContent = state.profileReady ? "用户信息" : "先设置你的昵称";
  text.textContent = state.profileReady
    ? "昵称会显示给同局玩家。留空也可以,系统会继续使用自动昵称。"
    : "这个昵称会用于创建房间和加入房间。留空也可以,系统会自动给你默认昵称。";
  save.textContent = state.profileReady ? "保存" : "进入大厅";
  cancel?.classList.toggle("hidden", !state.profileReady);
  // 登录账号是只读的身份，和可改的昵称分开显示，免得两者被当成同一个东西。
  const accountRow = $("profile-account-row");
  const accountName = $("profile-account-name");
  const username = state.authUser?.username || "";
  accountRow?.classList.toggle("hidden", !username);
  if (accountName && username) accountName.textContent = username;
  if (visible && document.activeElement !== input) {
    window.requestAnimationFrame(() => input.focus());
  }
}

function canResumeStoredSeat() {
  const identity = storedIdentityForCurrentRoom();
  return Boolean(roomQueryId() && identity.token && !viewerPlayerId() && !state.playerToken);
}

export function renderRecoveryButton() {
  const button = $("recover-room");
  if (!button) return;
  const canResume = canResumeStoredSeat();
  const canReclaim = canReclaimSeatByName();
  const visible = canResume || canReclaim;
  button.classList.toggle("hidden", !visible);
  button.disabled = !visible;
  button.textContent = canResume
    ? "\u7ee7\u7eed\u539f\u8eab\u4efd"
    : "\u7528\u5f53\u524d\u6635\u79f0\u6062\u590d\u5e2d\u4f4d";
}

/**
 * 轮询每几秒就会重绘一次席位卡。正在操作的控件不能在手底下被换掉：下拉刚展开
 * 就重建会把选项收回去，输入框重建会吃掉还没提交的那半个数字。
 *
 * 只认席位卡自己的控件。弹窗里的控件不归 renderRoomPanels 管，各自的渲染函数
 * 会绕开正在编辑的那一个；把它们也算进来，只会让弹窗一开、身后的席位就冻住。
 */
export function isRoomConfigControlActive() {
  const active = typeof document !== "undefined" ? document.activeElement : null;
  if (!active || !hasRoom() || state.room?.status !== "lobby") return false;
  const data = active.dataset || {};
  return Boolean(data.seatTeam || data.seatController || data.seatQuota);
}

export function isStrategyControlActive() {
  const active = typeof document !== "undefined" ? document.activeElement : null;
  if (!active || typeof active.closest !== "function") return false;
  if (!active.closest("#strategy-panel")) return false;
  const tagName = String(active.tagName || "").toUpperCase();
  return tagName === "SELECT" || tagName === "INPUT" || tagName === "TEXTAREA";
}

export function loadRecordedMatchEnds() {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECORDED_MATCH_ENDS_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.map((item) => String(item || "")).filter(Boolean) : [];
  } catch (_error) {
    return [];
  }
}
