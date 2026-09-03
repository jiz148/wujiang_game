// Campaign workbench: monthly orders, offices, relics and dossiers.
import { $ } from '../core/dom.js';
import { hasRoom, roomQueryId, viewerPlayerId } from '../core/net.js';
import { FALLBACK_STRATEGY_VARIANTS, RECORDED_MATCH_ENDS_KEY, state } from '../core/state.js';
import { syncModalIsolation } from '../core/ui.js';
import { profileModalVisible, userLoggedIn } from '../platform/auth.js';
import { archiveStrategyCampaign, cancelQueuedStrategyAction, chooseStrategyHeroPath, closeStrategyCampaignCreator, continueStrategySandbox, createStrategicBattleResolver, createStrategyCampaign, deleteStrategyCampaign, enterStrategyCampaign, exitStrategyCampaignView, focusStrategyCommandPanel, inspectStrategyCityOnMap, leaveStrategyCampaign, lockStrategyCampaign, openStrategyBattleRoom, openStrategyCampaignCreator, queueStrategyAction, resolveStrategyBattleChoice, resolveWorldCrisisShowdown, restartStrategyBattleFromSnapshot, setStrategyBattleDefenseHero, setStrategyCreateStep, setStrategyDefenseHero } from '../strategic/api.js';
import { createButton, createHint } from '../core/components.js';
import { closeAppOverlay, openAppOverlay } from '../core/dialog.js';
import { campaignFactionHeroes, campaignIdleHeroCount, renderCampaignHeroList, renderCampaignMorePanel, renderCampaignScreen } from './campaign-shell.js';
import { STRATEGY_DUTY_LABELS, STRATEGY_OFFICE_LABELS, STRATEGY_OFFICE_STATUS_LABELS, appendStrategySkillTags, createStrategyCityCommandCard, createStrategyCityDetailCard, createStrategyField, formatStrategyCalendar, hideStrategyHoverTip, renderStrategyMap, renderStrategyMembersPanel, renderStrategyOfficeCollaborationPanel, renderStrategyRecoveryOverview, showStrategyHoverTip, strategyActiveEncounters, strategyActiveOffice, strategyActiveSieges, strategyArmiesHostile, strategyArmyOrderLabel, strategyArmyStatusLabel, strategyArmySupplyStatusLabel, strategyAttackTargetsForCity, strategyCanAffordCommand, strategyCanIssueOrders, strategyCanResume, strategyCityById, strategyCityExploreFromIds, strategyCityIsHidden, strategyCommandCost, strategyControlledHero, strategyControlledOffices, strategyEncounterArmyIds, strategyEncounterForArmy, strategyFaction, strategyFactionById, strategyFactionCommandPoints, strategyFactionName, strategyHostCanRequestAdvance, strategyMapNodeId, strategyMemberIsAi, strategyMemberLabel, strategyMissingInitialPlayerLabels, strategyMonthlyCycle, strategyNeutralIncitementTargets, strategyNodeName, strategyOfficeLabel, strategyOfficeManagedCities, strategyPendingStoryEvent, strategyRegisteredUnitsLabel, strategyRememberSelectedCity, strategySelectedCity, strategySelectionContextKey, strategySiegeAttackerStanceLabel, strategySiegeDefenderStanceLabel, strategySiegeForArmy, strategySiegeStatusLabel } from '../strategic/ui-base.js';
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
    const hasGovernor = receivers.some((item) => item.office_type === "governor");
    const hasMilitary = receivers.some((item) => ["grand_general", "general"].includes(item.office_type));
    const orderKinds = isRequest
      ? [
        ["request_reinforce", "请求增援"],
        ["request_defend", "请求防守"],
        ["request_food", "请求调粮"],
        ["request_policy", "请求改方针"],
      ]
      : [
        ...(hasMilitary ? [
          ["defend_city", "防守城市"],
          ["reinforce_city", "增援城市"],
        ] : []),
        ...(hasGovernor ? [
          ["set_policy", "设置城市方针"],
          ["levy_garrison", "征集守军"],
        ] : []),
      ];
    if (!orderKinds.length) return desk;
    orderKinds.forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      orderKind.append(option);
    });
    const targetCity = document.createElement("select");
    const cityPolicy = document.createElement("select");
    const targetField = createStrategyField("目标", targetCity);
    (campaign?.world?.policy_choices || []).forEach((policy) => {
      const option = document.createElement("option");
      option.value = policy;
      option.textContent = policy;
      cityPolicy.append(option);
    });
    const ownCityKinds = new Set(["set_policy", "levy_garrison", "request_policy", "request_food", "request_reinforce", "request_defend", "defend_city", "reinforce_city"]);
    const policyKinds = new Set(["set_policy", "request_policy"]);
    const militaryKinds = new Set(["defend_city", "reinforce_city"]);
    const fillOrderTargets = () => {
      const kind = orderKind.value;
      const previous = targetCity.value;
      while (targetCity.firstChild) targetCity.removeChild(targetCity.firstChild);
      (campaign?.world?.cities || []).forEach((city) => {
        const own = city.owner_faction_id === office?.faction_id;
        if (ownCityKinds.has(kind) && !own) return;
        const option = document.createElement("option");
        option.value = city.id;
        option.textContent = `${city.name} · ${strategyFactionName(campaign, city.owner_faction_id)}`;
        targetCity.append(option);
      });
      if (Array.from(targetCity.children).some((option) => option.value === previous)) {
        targetCity.value = previous;
      }
    };
    const syncMilitaryOrder = () => {
      const kind = orderKind.value;
      cityPolicy.hidden = !policyKinds.has(kind);
      fillOrderTargets();
      if (militaryKinds.has(kind) && office?.office_type === "lord") {
        const grand = receivers.find((item) => item.office_type === "grand_general");
        if (grand) receiver.value = grand.id;
      }
      if ((kind === "set_policy" || kind === "levy_garrison") && office?.office_type === "lord") {
        const governor = receivers.find((item) => item.id === receiver.value && item.office_type === "governor")
          || receivers.find((item) => item.office_type === "governor");
        if (governor) {
          receiver.value = governor.id;
          const governedCityId = governor.managed_entity_ids?.[0];
          if (governedCityId) targetCity.value = governedCityId;
        }
      }
      issue.textContent = isRequest ? "提交请求" : "下达命令 · 1军令";
    };
    const issue = document.createElement("button");
    issue.type = "button";
    issue.className = "primary";
    issue.textContent = isRequest ? "提交请求" : "下达命令 · 1军令";
    const syncIssueState = () => {
      issue.disabled = (
        state.strategyBusy
        || !canResume
        || !targetCity.children.length
      );
    };
    orderKind.addEventListener("change", () => {
      syncMilitaryOrder();
      syncIssueState();
    });
    receiver.addEventListener("change", () => {
      syncMilitaryOrder();
      syncIssueState();
    });
    targetCity.addEventListener("change", () => {
      syncIssueState();
    });
    syncMilitaryOrder();
    syncIssueState();
    issue.addEventListener("click", () => {
      const kind = orderKind.value;
      const cityName = strategyCityName(campaign, targetCity.value);
      const objectiveText = {
        defend_city: `防守${cityName}`,
        set_policy: `将${cityName}设为${cityPolicy.value}`,
        levy_garrison: `征集${cityName}守军`,
        reinforce_city: `增援${cityName}`,
        request_reinforce: `请求增援${cityName}`,
        request_defend: `请求协助防守${cityName}`,
        request_food: `请求向${cityName}调粮`,
        request_policy: `请求将${cityName}改为${cityPolicy.value}`,
      }[kind] || "";
      if (!objectiveText || !targetCity.value) return;
      queueStrategyAction(isRequest ? "send_office_request" : "issue_office_order", {
        receiver_office_id: receiver.value,
        objective: objectiveText,
        office_order_type: isRequest ? "request" : kind,
        target_entity_id: targetCity.value,
        city_policy: policyKinds.has(kind) ? cityPolicy.value : "",
        priority: 1,
      });
    });
    controls.append(
      receiver,
      orderKind,
      targetField,
      cityPolicy,
      issue,
    );
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
  if (subtitle) appendTextLine(copy, "strategy-meta", subtitle);
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
    const wounded = hero.status === "sleeping";
    const row = document.createElement("div");
    row.className = "strategy-hero-duty-row";
    if (wounded) row.classList.add("is-wounded");
    const identity = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = hero.name || hero.code;
    identity.append(name);
    const heldOffice = (campaign?.world?.offices || []).find((entry) => entry.id === hero.office_id);
    appendTextLine(identity, "strategy-meta", heldOffice ? strategyOfficeLabel(heldOffice, campaign) : "未任职");
    if (wounded) {
      appendTextLine(identity, "strategy-hero-wounded-banner", `负伤中 · 第 ${hero.sleeping_until_month || "?"} 月复原 · 只能待命`);
    }
    appendTextLine(
      identity,
      "strategy-meta",
      `忠诚 ${hero.loyalty ?? 50} · ${hero.loyalty_band?.label || "稳定"} · 对主公关系 ${hero.lord_relationship ?? "—"}`
    );
    appendStrategySkillTags(identity, hero);
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
    const availableDuties = wounded
      ? [["reserve", "待命"]]
      : Object.entries(dutyLabels);
    availableDuties.forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      duty.append(option);
    });
    duty.value = wounded ? "reserve" : (hero.assignment_type || "reserve");
    duty.disabled = wounded;
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
      if (wounded) {
        assign.disabled = state.strategyBusy || !canResume;
        assign.textContent = "安排待命";
        assign.title = "负伤武将只能待命。";
        return;
      }
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

function appendStrategyCityCommandCard(host, campaign, city, faction, canResume, office) {
  const card = createStrategyCityCommandCard(campaign, city, faction, canResume, office);
  const stack = card.querySelector(".strategy-command-stack");
  if (stack && !stack.children.length) return null;
  host.append(card);
  return card;
}

function createStrategyCommandSummary(campaign, faction) {
  const bar = document.createElement("section");
  bar.className = "strategy-command-summary";
  const points = strategyFactionCommandPoints(campaign, faction);
  [
    ["本月军令", `${points.remaining} / ${points.maximum}`],
    ["已用", String(points.used ?? Math.max(0, (points.maximum || 0) - (points.remaining || 0)))],
    ["已排军令", String((campaign?.queued_actions || []).filter((action) => action.faction_id === faction?.id).length)],
  ].forEach(([label, value]) => {
    const fact = document.createElement("div");
    const caption = document.createElement("span");
    caption.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    fact.append(caption, strong);
    bar.append(fact);
  });
  return bar;
}

function strategyHeroStationCityId(hero) {
  return String(hero?.city_id || hero?.assignment_target_id || "");
}

function strategyCityAttackCommitment(troops) {
  const available = Math.max(0, Number(troops) || 0);
  if (available < 50) return 0;
  return Math.max(50, Math.floor(available * 3 / 4));
}

function strategyQueuedAttackHeroCodes(campaign, excludeSourceId, excludeTargetId) {
  const codes = new Set();
  (campaign?.queued_actions || []).forEach((action) => {
    if (action.action_type !== "declare_attack") return;
    if (action.payload?.source_city_id === excludeSourceId && action.payload?.target_city_id === excludeTargetId) return;
    (action.payload?.attacker_hero_codes || []).forEach((code) => codes.add(code));
    if (action.payload?.commander_hero_code) codes.add(action.payload.commander_hero_code);
  });
  (campaign?.world?.pending_battles || []).forEach((battle) => {
    if (battle.status !== "pending") return;
    (battle.attacker_hero_codes || []).forEach((code) => codes.add(code));
    (battle.defender_hero_codes || []).forEach((code) => codes.add(code));
  });
  return codes;
}

function strategyReservedAttackTroops(campaign, cityId, excludeTargetId) {
  return (campaign?.queued_actions || []).reduce((sum, action) => {
    if (action.action_type !== "declare_attack") return sum;
    if (action.payload?.source_city_id !== cityId) return sum;
    if (excludeTargetId && action.payload?.target_city_id === excludeTargetId) return sum;
    const reserved = Number(action.payload?.committed_troops ?? action.payload?.attacker_troops ?? 0);
    return sum + Math.max(0, reserved);
  }, 0);
}

function strategyBattleModeName(mode) {
  const names = {
    manual: "手动开战",
    ai_auto: "AI 推演",
    watch_ai: "观看 AI",
    quick: "快速结算",
    formula: "快速结算",
    pending_choice: "待决",
    siege: "围城",
    retreat: "撤退",
  };
  return names[mode] || "待决";
}

function renderStrategyExpeditionPanel(host, campaign, faction, office, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-expedition";
  const canAttack = !office || ["lord", "general"].includes(office.office_type);
  if (!canAttack) {
    appendTextLine(panel, "strategy-command-lock", "出征由主公或将军发起。");
    host.append(panel);
    return;
  }

  const ownCities = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === faction?.id);
  const sources = ownCities.filter((city) => strategyAttackTargetsForCity(campaign, city, faction?.id).length);
  if (!sources.length) {
    appendTextLine(panel, "strategy-meta", "没有与敌城接壤的己方城市，无法出征。");
    host.append(panel);
    return;
  }

  const sourceSelect = document.createElement("select");
  sources.forEach((city) => {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = `${city.name} · 兵 ${city.resources?.troops || 0}`;
    sourceSelect.append(option);
  });
  const remembered = strategySelectedCity(campaign, faction);
  if (sources.some((city) => city.id === remembered?.id)) sourceSelect.value = remembered.id;

  const targetSelect = document.createElement("select");
  const heroBox = document.createElement("div");
  heroBox.className = "strategy-expedition-heroes";
  const gatherBox = document.createElement("div");
  gatherBox.className = "strategy-expedition-gather";
  const troopBox = document.createElement("div");
  troopBox.className = "strategy-expedition-troops";
  const troopInput = document.createElement("input");
  troopInput.type = "number";
  troopInput.min = "50";
  troopInput.step = "1";
  const selectedHeroCodes = new Set(state.strategyExpeditionHeroCodes || []);
  const persistHeroSelection = () => {
    state.strategyExpeditionHeroCodes = Array.from(selectedHeroCodes);
  };

  const fillTargets = () => {
    const source = sources.find((city) => city.id === sourceSelect.value);
    const previous = targetSelect.value;
    while (targetSelect.firstChild) targetSelect.removeChild(targetSelect.firstChild);
    strategyAttackTargetsForCity(campaign, source, faction?.id).forEach((city) => {
      const option = document.createElement("option");
      option.value = city.id;
      option.textContent = `${city.name} · ${strategyFactionName(campaign, city.owner_faction_id)}`;
      targetSelect.append(option);
    });
    if (Array.from(targetSelect.children).some((option) => option.value === previous)) {
      targetSelect.value = previous;
    }
  };

  const availableTroops = () => {
    const city = sources.find((item) => item.id === sourceSelect.value);
    const cityTroops = Number(city?.resources?.troops || 0);
    return Math.max(0, cityTroops - strategyReservedAttackTroops(campaign, sourceSelect.value, targetSelect.value));
  };

  const syncTroops = () => {
    const available = availableTroops();
    const city = sources.find((item) => item.id === sourceSelect.value);
    const reserved = strategyReservedAttackTroops(campaign, sourceSelect.value, targetSelect.value);
    const previous = Number(troopInput.value) || 0;
    const next = Math.min(available, Math.max(50, previous || strategyCityAttackCommitment(available)));
    troopInput.max = String(available);
    troopInput.value = available >= 50 ? String(next) : "0";
    while (troopBox.firstChild) troopBox.removeChild(troopBox.firstChild);
    appendTextLine(
      troopBox,
      "strategy-meta",
      `本城兵力 ${city?.resources?.troops || 0} · 其他出征已预定 ${reserved} · 本次可带走 ${available}`,
    );
    troopBox.append(createStrategyField("带走兵力", troopInput));
    const garrison = Math.max(0, available - Number(troopInput.value || 0));
    appendTextLine(troopBox, "strategy-meta", `守城将剩 ${garrison}。这些兵下月随军离开，本城城防会变空虚。`);
    if (available < 50) {
      appendTextLine(troopBox, "strategy-command-lock", "出发城可带走兵力不足 50，无法出征。");
    }
  };

  const syncHeroes = () => {
    const sourceId = sourceSelect.value;
    const limit = strategyHeroDeploymentLimit(faction);
    const busyCodes = strategyQueuedAttackHeroCodes(campaign, sourceSelect.value, targetSelect.value);
    const heroes = (faction?.strategic_heroes || []).filter((hero) => hero.status === "serving" || hero.status === "sleeping");
    const stationed = heroes.filter((hero) => strategyHeroStationCityId(hero) === sourceId);
    const elsewhere = heroes.filter((hero) => strategyHeroStationCityId(hero) !== sourceId);
    Array.from(selectedHeroCodes).forEach((code) => {
      const match = stationed.find((hero) => hero.code === code);
      if (!match || busyCodes.has(code) || match.status === "sleeping") selectedHeroCodes.delete(code);
    });
    while (selectedHeroCodes.size > limit) {
      const extra = Array.from(selectedHeroCodes)[0];
      if (!extra) break;
      selectedHeroCodes.delete(extra);
    }
    persistHeroSelection();
    while (heroBox.firstChild) heroBox.removeChild(heroBox.firstChild);
    appendTextLine(heroBox, "strategy-meta", `出发城可投入武将（${selectedHeroCodes.size}/${limit}）`);
    if (!stationed.length) {
      appendTextLine(heroBox, "strategy-command-lock", "出发城没有驻扎武将。先从下方把人调过来。");
    }
    stationed.forEach((hero) => {
      const busy = busyCodes.has(hero.code);
      const wounded = hero.status === "sleeping";
      const row = document.createElement("label");
      row.className = "strategy-expedition-hero";
      if (wounded) row.classList.add("is-wounded");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = hero.code;
      input.checked = selectedHeroCodes.has(hero.code);
      input.disabled = busy || wounded || (!input.checked && selectedHeroCodes.size >= limit);
      input.addEventListener("change", () => {
        if (input.checked) selectedHeroCodes.add(hero.code);
        else selectedHeroCodes.delete(hero.code);
        persistHeroSelection();
        syncHeroes();
      });
      const text = document.createElement("span");
      const dutyNames = { reserve: "待命", administration: "内政", training: "训练", garrison: "驻守", campaign: "随军" };
      text.textContent = wounded
        ? `${hero.name || hero.code} · 负伤中，无法出征`
        : busy
          ? `${hero.name || hero.code} · 已参加其他出征`
          : `${hero.name || hero.code} · ${dutyNames[hero.assignment_type] || "待命"}`;
      row.append(input, text);
      heroBox.append(row);
    });

    while (gatherBox.firstChild) gatherBox.removeChild(gatherBox.firstChild);
    appendTextLine(gatherBox, "strategy-meta", "调集武将到出发城");
    if (!elsewhere.length) {
      appendTextLine(gatherBox, "strategy-meta", "其他城市没有可调动的己方武将。");
      return;
    }
    elsewhere.forEach((hero) => {
      const wounded = hero.status === "sleeping";
      const row = document.createElement("div");
      row.className = "strategy-expedition-hero is-gather";
      if (wounded) row.classList.add("is-wounded");
      const name = document.createElement("span");
      const cityName = strategyCityName(campaign, strategyHeroStationCityId(hero));
      name.textContent = wounded
        ? `${hero.name || hero.code} · 负伤中 · 现驻 ${cityName}`
        : `${hero.name || hero.code} · 现驻 ${cityName}`;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.textContent = "调往出发城";
      button.disabled = state.strategyBusy || !canResume || office?.office_type !== "lord";
      button.addEventListener("click", () => queueStrategyAction("assign_strategic_hero_duty", {
        hero_code: hero.code,
        assignment_type: "garrison",
        target_id: sourceId,
      }));
      row.append(name, button);
      gatherBox.append(row);
    });
    if (office?.office_type !== "lord") {
      appendTextLine(gatherBox, "strategy-command-lock", "调任武将由主公签发。");
    }
  };

  const attack = document.createElement("button");
  attack.type = "button";
  attack.className = "primary";
  attack.textContent = "计划出征 · 2 军令";
  const attackError = document.createElement("p");
  attackError.className = "strategy-command-lock";
  const syncAttack = () => {
    const troops = Number(troopInput.value || 0);
    const unaffordable = !strategyCanAffordCommand(campaign, faction, "declare_attack");
    const shortTroops = availableTroops() < 50 || troops < 50 || troops > availableTroops();
    attack.disabled = state.strategyBusy || !canResume || unaffordable || shortTroops;
    const reasons = [];
    if (state.strategyExpeditionError) reasons.push(state.strategyExpeditionError);
    else if (unaffordable) reasons.push("本月军令不足 2 点，无法再计划出征。");
    else if (shortTroops) reasons.push("出发城可带走兵力不足 50，无法出征。");
    attackError.textContent = reasons.join(" ");
    attackError.classList.toggle("hidden", !attackError.textContent);
  };
  troopInput.addEventListener("change", () => {
    const available = availableTroops();
    const next = Math.min(available, Math.max(50, Number(troopInput.value) || 50));
    troopInput.value = available >= 50 ? String(next) : "0";
    syncTroops();
    syncAttack();
  });
  fillTargets();
  syncHeroes();
  syncTroops();
  syncAttack();
  sourceSelect.addEventListener("change", () => {
    state.strategyExpeditionError = "";
    fillTargets();
    syncHeroes();
    syncTroops();
    syncAttack();
  });
  targetSelect.addEventListener("change", () => {
    syncHeroes();
    syncTroops();
    syncAttack();
  });

  attack.addEventListener("click", () => {
    persistHeroSelection();
    queueStrategyAction("declare_attack", {
      source_city_id: sourceSelect.value,
      target_city_id: targetSelect.value,
      resolution_mode: "pending_choice",
      attacker_hero_codes: Array.from(selectedHeroCodes),
      committed_troops: Number(troopInput.value || 0),
    });
  });

  panel.append(
    createStrategyField("出发城", sourceSelect),
    createStrategyField("攻打", targetSelect),
    troopBox,
    heroBox,
    gatherBox,
    attack,
    attackError,
  );
  host.append(panel);
}

function renderLordWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  const occupation = selectedCity?.occupation_governance || {};
  const funding = selectedCity?.rebellion_funding_options?.[faction?.id];
  const occupationCrisis = Boolean(occupation.status && occupation.status !== "ended");
  const ownRebellion = selectedCity?.owner_faction_id === faction?.id && strategyCityRebellionForce(selectedCity) > 0;
  const externalFundingTarget = selectedCity?.owner_faction_id !== faction?.id && Boolean(funding) && (
    occupationCrisis || strategyCityRebellionForce(selectedCity) > 0 || Number(funding.rebellion_risk || 0) >= 45
  );
  command.className = `${command.className || ""} role-lord`.trim();
  const cityCard = appendStrategyCityCommandCard(command, campaign, selectedCity, faction, canResume, office);
  if (cityCard && (occupationCrisis || ownRebellion || externalFundingTarget)) {
    cityCard.classList.add("is-political-crisis");
  }
}

function createLordRelicOperationsPanel(campaign, office, faction, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-role-panel strategy-relic-operations";

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
      `${option.city_name} · ${option.altar_name} · ${option.effect?.summary || "安放后生效"} · 每月 ${option.maintenance_ether_cost} 城市以太维持 · 祭坛行动余 ${option.altar_actions_remaining}`
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
  appendStrategyCityCommandCard(command, campaign, selectedCity, faction, canResume, office);
  command.append(createRoleWorkspaceHeader(campaign, office, "战区统帅部", "管理直属将军，把城市已注册单位调入具体军团。"));
  command.append(createGrandGeneralMilitaryPanel(campaign, office, faction, canResume, selectedCity));
}

function renderGeneralWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  const managed = strategyOfficeManagedCities(campaign, office);
  const source = managed.find((city) => city.id === selectedCity?.id) || managed[0] || selectedCity;
  appendStrategyCityCommandCard(command, campaign, source, faction, canResume, office);
  const siegePanel = createGeneralSiegePanel(campaign, office, faction, canResume);
  if (siegePanel) command.append(siegePanel);
  command.append(createRoleWorkspaceHeader(campaign, office, "军团行营", "持有确切作战单位；缺兵时必须向直属大将军请示。"));
  command.append(createGeneralArmyPanel(campaign, office, faction, canResume));
  command.append(createGeneralLogisticsPanel(campaign, office, faction, canResume));
}

function renderGovernorWorkspace(command, campaign, office, selectedCity, faction, canResume) {
  const managedCity = strategyOfficeManagedCities(campaign, office)[0] || selectedCity;
  appendStrategyCityCommandCard(command, campaign, managedCity, faction, canResume, office);
  const siegePanel = createGovernorSiegePanel(campaign, office, faction, canResume);
  if (siegePanel) command.append(siegePanel);
  command.append(createRoleWorkspaceHeader(campaign, office, "城主府", "管理所辖城市的兵力增长、士兵注册、建筑、叛乱与祭祀。"));
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
      ["lord", "继承国家当君主"],
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
  if (state.strategyHeroCode && [...heroSelect.options].some((item) => item.value === state.strategyHeroCode)) {
    heroSelect.value = state.strategyHeroCode;
  }
  if (state.strategyHeroPath && pathOptions.some(([value]) => value === state.strategyHeroPath)) {
    pathSelect.value = state.strategyHeroPath;
  } else if (isLobby && currentHero?.status === "roaming") {
    pathSelect.value = "roaming";
  }

  const targetSelect = document.createElement("select");
  const fillTargets = (path) => {
    const keep = targetSelect.value || state.strategyHeroTargetId || "";
    if (typeof targetSelect.replaceChildren === "function") targetSelect.replaceChildren();
    else targetSelect.innerHTML = "";
    const factions = (campaign?.world?.factions || []).filter((item) => (
      item.faction_type !== "world_crisis" && !item.is_world_crisis
    ));
    const majors = factions.filter((item) => item.faction_type === "major" || !item.faction_type);
    const others = factions.filter((item) => item.faction_type && item.faction_type !== "major");
    const listed = path === "lord" ? majors : [...majors, ...others];
    listed.forEach((faction) => {
      const option = document.createElement("option");
      option.value = faction.id;
      option.textContent = `${faction.name} · 主城 ${strategyCityName(campaign, faction.capital_city_id)}`;
      targetSelect.append(option);
    });
    if (keep && [...targetSelect.options].some((item) => item.value === keep)) {
      targetSelect.value = keep;
    }
    state.strategyHeroTargetId = targetSelect.value || "";
  };
  fillTargets(pathSelect.value);
  const targetField = createStrategyField(pathSelect.value === "lord" ? "继承国家" : "投靠对象", targetSelect);
  targetField.className = "strategy-hero-target-field";

  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.maxLength = 16;
  nameInput.value = state.strategyHeroFactionName || "";
  nameInput.disabled = state.strategyBusy;
  const nameField = createStrategyField("国号", nameInput);
  nameField.className = "strategy-hero-nation-field";
  let lastDefaultFactionName = "";

  const defaultFactionName = () => {
    if (pathSelect.value === "lord") {
      return strategyFactionName(campaign, targetSelect.value) || "新国";
    }
    const selectedHero = pool.find((hero) => hero.code === heroSelect.value) || currentHero;
    const heroName = strategyHeroName(campaign, selectedHero?.code);
    return heroName && heroName !== "未知英灵" ? heroName : "新国";
  };

  const detail = document.createElement("p");
  detail.className = "strategy-hero-path-detail";
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "primary strategy-hero-path-submit";
  const updatePathState = () => {
    state.strategyHeroCode = heroSelect.value || "";
    state.strategyHeroPath = pathSelect.value || "";
    const selectedHero = pool.find((hero) => hero.code === heroSelect.value) || currentHero;
    const cityName = strategyCityName(campaign, selectedHero?.city_id);
    const chosenNation = strategyFactionName(campaign, targetSelect.value);
    const nextDefault = defaultFactionName();
    if (["found", "lord"].includes(pathSelect.value)) {
      if (!state.strategyHeroFactionName || state.strategyHeroFactionName === lastDefaultFactionName) {
        state.strategyHeroFactionName = nextDefault;
        nameInput.value = nextDefault;
      }
      lastDefaultFactionName = nextDefault;
    }
    const details = {
      lord: chosenNation ? `继承${chosenNation}，成为该国君主。` : "选择一个国家，继承其君主之位。",
      roaming: "不隶属任何势力，不可调动城市；之后可举旗建国或递交投靠请求。",
      found: `在${cityName || "所在城"}举旗并夺取该城，国号可改。`,
      join: chosenNation ? `向${chosenNation}递交投靠请求；对方录用后才正式成为其麾下武将。` : "向所选主公递交投靠请求。",
    };
    if (isLobby && currentOffice?.office_type !== "lord" && pathSelect.value === "roaming") {
      details.roaming = `你当前被分配为${strategyOfficeLabel(currentOffice, campaign)}；选择在野会放弃这个合作官职。`;
    }
    const targetLabel = targetField.querySelector("span");
    if (targetLabel) targetLabel.textContent = pathSelect.value === "lord" ? "继承国家" : "投靠对象";
    targetField.hidden = !["lord", "join"].includes(pathSelect.value);
    nameField.hidden = !["found", "lord"].includes(pathSelect.value);
    detail.textContent = details[pathSelect.value] || "";
    submit.textContent = pathSelect.value === "join" ? "递交投靠书" : pathSelect.value === "found" ? "举旗建国" : pathSelect.value === "lord" ? "继承这个国家" : "确认武将道路";
    submit.disabled = state.strategyBusy || !heroSelect.value || (["lord", "join"].includes(pathSelect.value) && !targetSelect.value);
  };
  heroSelect.addEventListener("change", updatePathState);
  pathSelect.addEventListener("change", () => {
    lastDefaultFactionName = state.strategyHeroFactionName || lastDefaultFactionName;
    fillTargets(pathSelect.value);
    updatePathState();
  });
  targetSelect.addEventListener("change", () => {
    state.strategyHeroTargetId = targetSelect.value || "";
    updatePathState();
  });
  nameInput.addEventListener("input", (event) => {
    state.strategyHeroFactionName = String(event.target.value || "").slice(0, 16);
  });
  submit.addEventListener("click", () => chooseStrategyHeroPath(
    heroSelect.value,
    pathSelect.value,
    targetSelect.value,
    ["found", "lord"].includes(pathSelect.value) ? nameInput.value : "",
  ));
  form.append(
    createStrategyField("你所操作的武将", heroSelect),
    createStrategyField("道路", pathSelect),
    targetField,
    nameField,
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
  appendTextLine(copy, "strategy-quick-opening-kicker", `${formatStrategyCalendar(conclusion.concluded_month || campaign.world.current_month)} · 战役评议`);
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

function fillStrategyConclusionNotice(host, campaign) {
  const status = campaign?.world?.strategic_status || {};
  const conclusion = status.conclusion || {};
  appendTextLine(host, "strategy-quick-opening-kicker", `${formatStrategyCalendar(conclusion.concluded_month || campaign.world.current_month)} · ${conclusion.result_label || "战役评议"}`);
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
  host.append(rankingGrid);
  appendStrategyRetrospective(host, campaign, campaign.world?.campaign_retrospective || conclusion.retrospective);
}

function campaignSurfaceReady() {
  return state.screen === "draft" && state.homeFlow === "campaign";
}

function pendingOccupationCities(campaign) {
  const faction = strategyFaction(campaign);
  const pendingIds = new Set(campaign?.world?.strategic_status?.pending_occupation_city_ids || []);
  return (campaign?.world?.cities || []).filter((city) => {
    const occupation = city.occupation_governance || city.occupation || {};
    return city.owner_faction_id === faction?.id
      && (occupation.status === "pending" || pendingIds.has(city.id));
  });
}

export function presentPendingOccupationNotice(campaign) {
  if (!campaignSurfaceReady()) return false;
  const cities = pendingOccupationCities(campaign).filter((city) => !(
    campaign?.queued_actions || []
  ).some((action) => action.action_type === "choose_occupation_policy" && action.payload?.city_id === city.id));
  if (!cities.length) return false;
  const city = cities[0];
  const key = `occupation:${city.id}:${city.occupation_governance?.status || "pending"}`;
  if (state.strategyNoticeKind === key) return false;
  state.strategyNoticeKind = key;
  const body = document.createElement("div");
  appendTextLine(body, "strategy-meta", `${formatStrategyCalendar(campaign.world.current_month)} · ${city.name}`);
  const canChoose = strategyCanIssueOrders(campaign) && strategyCanAffordCommand(campaign, strategyFaction(campaign), "choose_occupation_policy", {}, city.id);
  const opened = openAppOverlay({
    title: "攻城胜利",
    body,
    className: "is-occupation-choice",
    dismissible: false,
    actions: [
      {
        label: "占领",
        variant: "primary",
        disabled: state.strategyBusy || !canChoose,
        onClick: async () => {
          await queueStrategyAction("choose_occupation_policy", { city_id: city.id, policy_id: "autonomy" });
          state.strategyNoticeKind = "";
        },
      },
      {
        label: "劫掠",
        variant: "subtle",
        disabled: state.strategyBusy || !canChoose,
        onClick: async () => {
          await queueStrategyAction("choose_occupation_policy", { city_id: city.id, policy_id: "plunder" });
          state.strategyNoticeKind = "";
        },
      },
    ],
  });
  if (!opened) state.strategyNoticeKind = "";
  return opened;
}

export function presentStrategyConclusionNotice(campaign) {
  const status = campaign?.world?.strategic_status || {};
  if (!campaignSurfaceReady()) return false;
  if (status.awaiting_occupation_policy) return false;
  if (!status.awaiting_conclusion_choice) return false;
  state.strategyNoticeKind = "conclusion";
  const body = document.createElement("div");
  fillStrategyConclusionNotice(body, campaign);
  appendTextLine(body, "strategy-quick-conclusion-prompt", "这局已经结束。你可以继续在这张地图上经营，或返回主菜单。");
  const opened = openAppOverlay({
    title: status.conclusion?.result_label || "战役结束",
    body,
    className: "is-conclusion",
    dismissible: false,
    actions: [
      {
        label: "继续经营",
        variant: "primary",
        onClick: async () => {
          await continueStrategySandbox();
          state.strategyNoticeKind = "";
        },
      },
      {
        label: "返回主菜单",
        variant: "subtle",
        onClick: async () => {
          await archiveStrategyCampaign();
          state.strategyNoticeKind = "";
          exitStrategyCampaignView();
        },
      },
    ],
  });
  if (!opened) state.strategyNoticeKind = "";
  return opened;
}

export function presentStrategyCrisisNotice(crisis) {
  if (!crisis || crisis.stage === "dormant") return false;
  const key = `${crisis.id || crisis.crisis_id || "crisis"}:${crisis.stage}:${crisis.stage_started_month || ""}`;
  if (state.strategyNoticeKind === key) return false;
  state.strategyNoticeKind = key;
  const body = document.createElement("div");
  appendTextLine(body, "strategy-meta", `${crisis.stage_label || crisis.stage}${crisis.origin_name ? ` · 起源 ${crisis.origin_name}` : ""}`);
  appendTextLine(body, "dialog__body", crisis.effect_summary || "北境局势发生变化。");
  const opened = openAppOverlay({
    title: crisis.name || "北境危机",
    body,
    className: "is-crisis-notice",
    dismissible: true,
    actions: [
      {
        label: "知道了",
        variant: "primary",
        onClick: () => {
          state.strategyNoticeKind = key;
        },
      },
    ],
  });
  if (!opened) state.strategyNoticeKind = "";
  return opened;
}

export function presentStrategyNoticesAfterAdvance(previousCampaign, campaign) {
  const status = campaign?.world?.strategic_status || {};
  if (status.awaiting_occupation_policy || pendingOccupationCities(campaign).length) {
    presentPendingOccupationNotice(campaign);
    return;
  }
  if (status.awaiting_conclusion_choice) {
    presentStrategyConclusionNotice(campaign);
    return;
  }
  const previous = (previousCampaign?.world?.world_crises || [])[0];
  const current = (campaign?.world?.world_crises || [])[0];
  if (current && current.stage !== "dormant" && current.stage !== previous?.stage) {
    presentStrategyCrisisNotice(current);
    return;
  }
  maybePresentStrategyBattleChoice(campaign);
}

export function maybePresentStrategyConclusion(campaign) {
  if (!campaignSurfaceReady()) return;
  if (presentPendingOccupationNotice(campaign)) return;
  const status = campaign?.world?.strategic_status || {};
  if (!status.awaiting_conclusion_choice || state.strategyNoticeKind === "conclusion") return;
  presentStrategyConclusionNotice(campaign);
}

function strategyBattleId(battle) {
  return String(battle?.id || battle?.battle_id || "");
}

async function runAiBattleSimulation(battleId, composition) {
  closeAppOverlay();
  const body = document.createElement("div");
  body.className = "ai-sim-progress";
  const spinner = document.createElement("div");
  spinner.className = "ai-sim-spinner";
  spinner.setAttribute("aria-hidden", "true");
  const status = document.createElement("p");
  status.textContent = "正在后台即时结算整场战斗，没有动画或停顿。";
  const meter = document.createElement("p");
  meter.className = "strategy-meta";
  meter.textContent = "正在推演…攻城上限 200 个武将回合";
  body.append(spinner, status, meter);
  openAppOverlay({
    title: "AI 推演中",
    body,
    dismissible: false,
    actions: [],
  });
  const started = Date.now();
  const tick = window.setInterval(() => {
    const seconds = Math.max(1, Math.round((Date.now() - started) / 1000));
    meter.textContent = `已推演 ${seconds} 秒 · 攻城上限 200 个武将回合`;
  }, 400);
  try {
    await resolveStrategyBattleChoice(battleId, "ai_auto", composition);
  } finally {
    window.clearInterval(tick);
    closeAppOverlay();
  }
}

function strategyPendingPlayerBattles(campaign, faction) {
  return (campaign?.world?.pending_battles || []).filter((battle) => (
    battle.status === "pending"
    && battle.attacker_faction_id === faction?.id
    && (
      battle.resolution_mode === "pending_choice"
      || (!battle.battle_room_id && (battle.source_kind || "legacy_city_attack") === "legacy_city_attack")
    )
  ));
}

function defaultBattleComposition(troops, costs = { infantry: 10, archer: 20, cavalry: 50 }, limit = 50) {
  const infantryCost = Number(costs.infantry || 10);
  const count = Math.max(1, Math.min(limit, Math.floor(Math.max(0, Number(troops) || 0) / infantryCost) || 1));
  return { infantry: count, archer: 0, cavalry: 0 };
}

export function presentStrategyBattleChoiceNotice(campaign, faction, battle) {
  const target = battle && strategyBattleId(battle)
    ? battle
    : strategyPendingPlayerBattles(campaign, faction)[0];
  if (!target) return false;
  const battleId = strategyBattleId(target);
  if (!battleId) return false;
  const noticeId = `battle-choice:${battleId}`;
  if (state.strategyNoticeKind === noticeId) return false;
  state.strategyNoticeKind = noticeId;
  state.strategyBattleNoticeId = battleId;
  const costs = campaign?.world?.battle_unit_costs || { infantry: 10, archer: 20, cavalry: 50 };
  const unitLimit = 50;
  const troopBudget = Number(target.attacker_troops || 0);
  const composition = defaultBattleComposition(troopBudget, costs, unitLimit);
  const body = document.createElement("div");
  body.className = "strategy-battle-choice";
  appendTextLine(body, "strategy-meta", `${strategyCityName(campaign, target.source_city_id)} 进攻 ${strategyCityName(campaign, target.target_city_id)} · 可投入兵力 ${troopBudget}`);
  appendTextLine(body, "strategy-meta", "手动战斗进入格子战场；快速结算用公式立刻出结果，不能回看过程；AI 推演在后台瞬移演算整场（最多 200 回合），结束后可回看；围城按月消耗城防（需空闲将军）；撤退收回七成兵力。");
  const compositionBox = document.createElement("div");
  compositionBox.className = "strategy-battle-composition";
  const budget = document.createElement("p");
  budget.className = "strategy-meta strategy-battle-budget";
  const unitKeys = ["infantry", "archer", "cavalry"];
  const compositionSpent = () => unitKeys.reduce((sum, key) => (
    sum + (Number(composition[key] || 0) * Number(costs[key] || 0))
  ), 0);
  const compositionUnits = () => unitKeys.reduce((sum, key) => sum + (Number(composition[key] || 0)), 0);
  const compositionValid = () => {
    const units = compositionUnits();
    return units > 0 && units <= unitLimit && compositionSpent() <= troopBudget;
  };
  const syncBudget = () => {
    const spent = compositionSpent();
    const units = compositionUnits();
    const over = spent > troopBudget || units > unitLimit || units <= 0;
    budget.textContent = `已用 ${spent} / ${troopBudget} 兵力 · 单位 ${units}/${unitLimit}`;
    budget.classList.toggle("strategy-command-lock", over);
  };
  const clampUnitCount = (key, raw) => {
    const cost = Number(costs[key] || 0);
    const otherUnits = unitKeys.reduce((sum, item) => sum + (item === key ? 0 : Number(composition[item] || 0)), 0);
    const otherSpent = unitKeys.reduce((sum, item) => (
      sum + (item === key ? 0 : Number(composition[item] || 0) * Number(costs[item] || 0))
    ), 0);
    const maxByUnits = Math.max(0, unitLimit - otherUnits);
    const maxByBudget = cost > 0 ? Math.max(0, Math.floor((troopBudget - otherSpent) / cost)) : maxByUnits;
    return Math.min(maxByUnits, maxByBudget, Math.max(0, Number(raw) || 0));
  };
  [
    ["infantry", "步兵"],
    ["archer", "弓兵"],
    ["cavalry", "骑兵"],
  ].forEach(([key, label]) => {
    const row = document.createElement("label");
    const caption = document.createElement("span");
    caption.textContent = `${label} · ${costs[key] || 0} 兵力/个`;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = String(unitLimit);
    input.value = String(composition[key] || 0);
    input.dataset.unit = key;
    const applyCount = () => {
      const next = clampUnitCount(key, input.value);
      composition[key] = next;
      input.value = String(next);
      syncBudget();
    };
    input.addEventListener("change", applyCount);
    input.addEventListener("input", applyCount);
    row.append(caption, input);
    compositionBox.append(row);
  });
  syncBudget();
  compositionBox.prepend(budget);
  body.append(compositionBox);
  const opened = openAppOverlay({
    title: "战事待决",
    body,
    className: "is-battle-choice",
    dismissible: true,
    actions: [
      {
        label: "手动战斗",
        variant: "primary",
        onClick: async () => {
          if (!compositionValid()) return false;
          await resolveStrategyBattleChoice(battleId, "manual", composition);
        },
      },
      {
        label: "快速结算",
        variant: "subtle",
        onClick: async () => {
          if (!compositionValid()) return false;
          await resolveStrategyBattleChoice(battleId, "formula", composition);
        },
      },
      {
        label: "AI 推演",
        variant: "subtle",
        onClick: async () => {
          if (!compositionValid()) return false;
          await runAiBattleSimulation(battleId, composition);
          return false;
        },
      },
      {
        label: "围城",
        variant: "subtle",
        onClick: async () => {
          await resolveStrategyBattleChoice(battleId, "siege", {});
        },
      },
      {
        label: "撤退",
        variant: "subtle",
        onClick: async () => {
          await resolveStrategyBattleChoice(battleId, "retreat", {});
        },
      },
    ],
  });
  if (!opened) state.strategyNoticeKind = "";
  return opened;
}

export function maybePresentStrategyBattleChoice(campaign) {
  const faction = strategyFaction(campaign);
  if (!faction || state.strategyNoticeKind === "conclusion") return;
  if (!strategyPendingPlayerBattles(campaign, faction).length) return;
  if (state.strategyDockTab === "log" && state.strategyDockOpen) return;
  state.strategyDockTab = "log";
  state.strategyDockOpen = true;
  renderStrategyPanel();
}

function strategyDiplomacyActionButton(label, { disabled, titleLines, onClick, danger = false }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = danger ? "ghost danger" : "ghost";
  button.textContent = label;
  button.disabled = Boolean(disabled);
  if (titleLines?.length) {
    button.addEventListener("mouseenter", () => showStrategyHoverTip(button, titleLines));
    button.addEventListener("mouseleave", hideStrategyHoverTip);
  }
  if (onClick) button.addEventListener("click", onClick);
  return button;
}

function renderStrategyDiplomacyPanel(host, campaign, faction, office, canResume) {
  const panel = document.createElement("section");
  panel.className = "strategy-diplomacy";
  const canLord = !office || office.office_type === "lord";
  const self = document.createElement("div");
  self.className = "strategy-diplomacy-self";
  const selfTitle = document.createElement("strong");
  selfTitle.textContent = faction?.name || "本势力";
  const selfMeta = document.createElement("span");
  selfMeta.textContent = `钱 ${faction?.resources?.money || 0} · 粮 ${faction?.resources?.food || 0} · 信誉 ${Number(faction?.diplomatic_reputation ?? 50)}`;
  self.append(selfTitle, selfMeta);
  panel.append(self);
  if (!canLord) appendTextLine(panel, "strategy-command-lock", "战略外交由主公签署。");

  const filter = document.createElement("label");
  filter.className = "strategy-diplomacy-filter";
  const filterInput = document.createElement("input");
  filterInput.type = "checkbox";
  filterInput.checked = state.strategyDiplomacyActionableOnly !== false;
  const filterText = document.createElement("span");
  filterText.textContent = "只看可交涉（接壤）";
  filter.append(filterInput, filterText);
  filterInput.addEventListener("change", () => {
    state.strategyDiplomacyActionableOnly = filterInput.checked;
    renderStrategyPanel();
  });
  panel.append(filter);

  const factions = (campaign?.world?.factions || []).filter((item) => (
    item.id !== faction?.id && (item.faction_type === "major" || item.faction_type === "neutral_city_state")
  ));
  const visibleFactions = factions.filter((target) => {
    if (!filterInput.checked) return true;
    if (target.faction_type === "neutral_city_state") {
      const relation = (target.neutral_politics?.relationships || []).find((item) => item.faction_id === faction?.id);
      if ((relation?.diplomacy_options || []).some((option) => option.can_propose)) return true;
      if (relation?.peaceful_integration?.can_integrate) return true;
      const city = (campaign?.world?.cities || []).find((item) => item.owner_faction_id === target.id);
      return Boolean(city && strategyNeutralIncitementTargets(campaign, city, faction?.id).length);
    }
    return (faction?.faction_diplomacy?.[target.id]?.options || []).some((option) => option.can_propose);
  });
  visibleFactions.forEach((target) => {
    const row = document.createElement("article");
    const isNeutral = target.faction_type === "neutral_city_state";
    row.className = `strategy-diplomacy-row${isNeutral ? " is-neutral" : " is-major"}`;
    if (target.color) {
      row.style.setProperty("--diplomacy-faction-color", target.color);
    }
    const head = document.createElement("header");
    const name = document.createElement("strong");
    name.textContent = target.name || target.id;
    const mark = document.createElement("span");
    mark.className = "strategy-diplomacy-kind";
    mark.textContent = isNeutral ? "中立" : "势力";
    head.append(name, mark);

    const actions = document.createElement("div");
    actions.className = "strategy-diplomacy-actions";
    if (isNeutral) {
      const politics = target.neutral_politics || {};
      const relation = (politics.relationships || []).find((item) => item.faction_id === faction?.id);
      const score = Number(relation?.score || 0);
      const meta = document.createElement("span");
      meta.className = "strategy-diplomacy-relation";
      meta.textContent = `${score > 0 ? "+" : ""}${score}${relation?.label ? ` ${relation.label}` : ""}`;
      head.append(meta);
      (relation?.diplomacy_options || []).forEach((option) => {
        const costs = Object.entries(option.resource_cost || {}).filter(([, value]) => Number(value) > 0).map(([key, value]) => `${{ money: "钱", food: "粮", troops: "兵" }[key] || key} ${value}`);
        actions.append(strategyDiplomacyActionButton(option.name, {
          disabled: state.strategyBusy || !canResume || !canLord || !option.can_propose || !strategyCanAffordCommand(campaign, faction, "neutral_diplomacy"),
          titleLines: [option.description || option.name, option.direct_effect, costs.join(" / "), option.blocked_reason].filter(Boolean),
          onClick: () => queueStrategyAction("neutral_diplomacy", {
            neutral_faction_id: target.id,
            diplomacy_action_id: option.id,
          }),
        }));
      });
      const integration = relation?.peaceful_integration;
      if (integration) {
        actions.append(strategyDiplomacyActionButton("整合", {
          disabled: state.strategyBusy || !canResume || !canLord || !integration.can_integrate || !strategyCanAffordCommand(campaign, faction, "peaceful_integration"),
          titleLines: ["和平整合 · 100 钱 / 80 粮 / 2 军令", integration.blocked_reason].filter(Boolean),
          onClick: () => queueStrategyAction("peaceful_integration", { neutral_faction_id: target.id }),
        }));
      }
      const city = (campaign?.world?.cities || []).find((item) => item.owner_faction_id === target.id);
      const targets = city ? strategyNeutralIncitementTargets(campaign, city, faction?.id) : [];
      if (targets.length) {
        const targetSelect = document.createElement("select");
        targets.forEach((item) => {
          const option = document.createElement("option");
          option.value = item.id;
          option.textContent = item.name;
          targetSelect.append(option);
        });
        actions.append(targetSelect);
        actions.append(strategyDiplomacyActionButton("教唆", {
          danger: true,
          disabled: (
            state.strategyBusy
            || !canResume
            || !canLord
            || Number(faction?.resources?.money || 0) < 60
            || Number(campaign?.world?.current_month || 0) < Number(relation?.incitement_cooldown_until_month || 0)
            || !strategyCanAffordCommand(campaign, faction, "incite_neutral_city_state")
          ),
          titleLines: ["教唆出兵 · 60 钱 / 1 军令"],
          onClick: () => queueStrategyAction("incite_neutral_city_state", {
            neutral_faction_id: target.id,
            target_faction_id: targetSelect.value,
          }),
        }));
      }
    } else {
      const dossier = faction?.faction_diplomacy?.[target.id] || {};
      const score = Number(dossier.relation || 0);
      const meta = document.createElement("span");
      meta.className = "strategy-diplomacy-relation";
      meta.textContent = `${score > 0 ? "+" : ""}${score} ${dossier.relation_label || ""}`.trim();
      head.append(meta);
      (dossier.options || []).forEach((option) => {
        const costs = Object.entries(option.resource_cost || {}).filter(([, value]) => Number(value) > 0).map(([key, value]) => `${{ money: "钱", food: "粮" }[key] || key} ${value}`);
        const gains = Object.entries(option.resource_gain || {}).filter(([, value]) => Number(value) > 0).map(([key, value]) => `${{ money: "钱", food: "粮" }[key] || key} +${value}`);
        actions.append(strategyDiplomacyActionButton(option.name, {
          disabled: state.strategyBusy || !canResume || !canLord || !option.can_propose || !strategyCanAffordCommand(campaign, faction, "faction_diplomacy"),
          titleLines: [option.description || option.name, [...costs, ...gains].join(" / "), option.blocked_reason].filter(Boolean),
          onClick: () => queueStrategyAction("faction_diplomacy", {
            target_faction_id: target.id,
            diplomacy_action_id: option.id,
          }),
        }));
      });
    }
    row.append(head, actions);
    panel.append(row);
  });
  if (!visibleFactions.length) {
    appendTextLine(
      panel,
      "strategy-meta",
      factions.length && filterInput.checked
        ? "当前没有接壤或可交涉的势力。取消上方筛选可查看全部。"
        : "当前没有可交涉的其他势力。",
    );
  }
  host.append(panel);
}

/**
 * 战役屏。
 *
 * 地图占满整屏，其余一切收进浮在地图上的面板。城市、外交、武将、军令各自成页。
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
  const pendingBattleCount = strategyPendingPlayerBattles(campaign, faction).length;
  const pendingTradeCount = ((faction?.resource_board?.offers || campaign?.world?.trade_offers || []).filter((offer) => (
    offer.status === "pending" && offer.target_faction_id === faction?.id
  ))).length;
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
      title: selectedCity ? (strategyCityIsHidden(selectedCity) ? "未探明" : selectedCity.name) : "等待选择城市",
      titleTag: selectedCity && !strategyCityIsHidden(selectedCity) ? {
        label: strategyFactionName(campaign, selectedCity.owner_faction_id),
        color: strategyFactionById(campaign, selectedCity.owner_faction_id)?.color || "#9d9681",
      } : null,
      caption: selectedCity ? (strategyCityIsHidden(selectedCity) ? "需要斥候探明" : "") : "点地图选城",
      render: (host) => {
        if (!selectedCity) {
          appendTextLine(host, "strategy-meta", "先在地图上点一座城，这里会给出它的家底、风险和城内武将。");
          return;
        }
        host.append(createStrategyCityDetailCard(campaign, selectedCity, faction, office));
        renderStrategyExploreActions(host, campaign, selectedCity, faction, canIssueOrders);
        if (!strategyCityIsHidden(selectedCity)) {
          renderStrategyCityHeroes(host, campaign, selectedCity);
        }
      },
    },
    {
      id: "diplomacy",
      label: "外交",
      title: "势力与交涉",
      caption: `${(campaign?.world?.factions || []).filter((item) => item.faction_type === "major" || item.faction_type === "neutral_city_state").length} 个势力`,
      render: (host) => renderStrategyDiplomacyPanel(host, campaign, faction, office, canIssueOrders),
    },
    {
      id: "resources",
      label: "资源",
      title: "势力资源",
      caption: pendingTradeCount ? `${pendingTradeCount} 笔待回复` : "库存、矿脉与贸易",
      badge: pendingTradeCount || 0,
      render: (host) => renderStrategyResourcesPanel(host, campaign, faction, office, canIssueOrders),
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
          renderDetail: (detail, hero) => renderStrategyHeroDetail(detail, campaign, faction, office, hero),
        });
      },
    },
    {
      id: "orders",
      label: "军令",
      title: formatStrategyCalendar(campaign.world.current_month),
      caption: queuedCount ? `已排 ${queuedCount} 条` : "本月尚未下令",
      badge: queuedCount || 0,
      render: (host) => {
        host.append(createStrategyCommandSummary(campaign, faction));
        renderStrategyStoryEvent(host, campaign, faction);
        if (strategyCityIsHidden(selectedCity)) {
          renderStrategyExploreActions(host, campaign, selectedCity, faction, canIssueOrders);
        } else {
          (workspaceRenderers[office?.office_type] || renderGovernorWorkspace)(
            host, campaign, office, selectedCity, faction, canIssueOrders,
          );
        }
        renderStrategyExileActions(host, campaign);
        renderStrategyOfficeCollaborationPanel(host, campaign);
        renderStrategyActionQueue(host, campaign);
      },
    },
    {
      id: "expedition",
      label: "出征",
      title: "出征",
      caption: "调将与攻城",
      render: (host) => renderStrategyExpeditionPanel(host, campaign, faction, office, canIssueOrders),
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
      title: "圣物",
      caption: "当前情报与操作",
      render: (host) => {
        if (isLord) host.append(createLordRelicOperationsPanel(campaign, office, faction, canResume));
        renderStrategyRelicPanel(host, campaign, faction);
      },
    } : null,
    {
      id: "log",
      label: "事件",
      title: "事件",
      caption: pendingBattleCount ? `${pendingBattleCount} 场待决` : "贸易、战场、建筑与情报",
      badge: pendingBattleCount || 0,
      render: (host) => {
        renderStrategyConclusion(host, campaign, canResume, isOwner);
        renderStrategyEventLog(host, campaign, faction, canResume);
        renderStrategyRecoveryOverview(host, campaign);
      },
    },
    {
      id: "seats",
      label: "成员",
      title: "成员与邀请",
      caption: "席位、加入码与权限",
      render: (host) => renderStrategyMembersPanel(host, campaign, isOwner),
    },
    {
      id: "more",
      label: "更多",
      title: "系统",
      caption: "设置与离开",
      render: (host) => renderCampaignMorePanel(host),
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
      stage.querySelector(".campaign-stage__overlay")?.remove();
      const overlay = document.createElement("div");
      overlay.className = "campaign-stage__overlay";
      renderStrategyWarStateBanner(overlay, campaign, canResume, isOwner);
      renderStrategyOfficeSwitcher(overlay, campaign, office);
      if (overlay.children.length) stage.append(overlay);
    },
    modules,
    onDockChange: () => renderStrategyPanel(),
  });
  maybePresentStrategyConclusion(campaign);
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
    [crisis.stage === "dormant" ? "最早可能出现" : "下次升级", crisis.next_stage_month ? formatStrategyCalendar(crisis.next_stage_month) : "暂无"],
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
        inspectStrategyCityOnMap(item.city_id, campaign);
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
      inspectStrategyCityOnMap(item.city_id, campaign);
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
  if (strategyCityIsHidden(city)) return "未探明";
  return city?.name || cityId || "未知城市";
}

function strategyExploreTargetsFromCity(campaign, city) {
  if (!city || strategyCityIsHidden(city)) return [];
  const citiesByNode = new Map((campaign?.world?.cities || []).map((item) => [item.node_id, item]));
  const node = (campaign?.world?.nodes || []).find((item) => item.id === city.node_id);
  return (node?.connected_node_ids || [])
    .map((nodeId) => citiesByNode.get(nodeId))
    .filter((item) => item && strategyCityIsHidden(item));
}

function renderStrategyExploreActions(host, campaign, city, faction, canResume) {
  const hidden = strategyCityIsHidden(city);
  const targets = hidden
    ? (city ? [city] : [])
    : strategyExploreTargetsFromCity(campaign, city);
  if (!targets.length) return;
  const panel = document.createElement("section");
  panel.className = "strategy-explore-actions";
  const title = document.createElement("strong");
  title.textContent = hidden ? "斥候探索" : "探索相邻未探明城";
  panel.append(title);
  targets.forEach((target) => {
    const fromIds = hidden ? strategyCityExploreFromIds(target) : [city.id];
    const fromCityId = fromIds[0] || "";
    const queued = (campaign?.queued_actions || []).some((action) => (
      action.faction_id === faction?.id
      && action.action_type === "explore_city"
      && (action.action_key === target.id || action.payload?.target_city_id === target.id)
    ));
    const button = document.createElement("button");
    button.type = "button";
    button.className = queued ? "ghost" : "primary";
    button.textContent = queued
      ? (hidden ? "已安排斥候" : `已安排探索`)
      : (hidden ? "派出斥候 · 1 军令" : `探索相邻未知城 · 1 军令`);
    const unaffordable = !strategyCanAffordCommand(campaign, faction, "explore_city", {
      target_city_id: target.id,
      from_city_id: fromCityId,
    }, target.id);
    button.disabled = state.strategyBusy || !canResume || !fromCityId || unaffordable || queued;
    button.addEventListener("click", () => queueStrategyAction("explore_city", {
      target_city_id: target.id,
      from_city_id: fromCityId,
    }));
    panel.append(button);
    if (!fromCityId) {
      appendTextLine(panel, "strategy-command-lock", "没有相邻的已知城市可以派出斥候。");
    } else if (unaffordable && !queued) {
      appendTextLine(panel, "strategy-command-lock", "本月军令不足，无法再派出斥候。");
    }
  });
  host.append(panel);
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
  const code = String(heroCode || "");
  if (!code) return "未知英灵";
  const fromPool = (campaign?.world?.strategic_hero_pool || []).find((item) => item.code === code);
  if (fromPool?.name) return fromPool.name;
  const fromWorld = (campaign?.world?.strategic_heroes || []).find((item) => (
    item.code === code || item.hero_code === code
  ));
  if (fromWorld?.name) return fromWorld.name;
  for (const faction of campaign?.world?.factions || []) {
    const hero = (faction.strategic_heroes || []).find((item) => item.code === code || item.hero_code === code);
    if (hero?.name) return hero.name;
  }
  return code;
}

export function strategyDeployableHeroes(faction) {
  return (faction?.strategic_heroes || []).filter((hero) => hero.status === "serving");
}

export function strategyHeroDeploymentLimit(faction) {
  const value = Number(faction?.strategic_hero_deployment_limit || 3);
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
  if (action.action_type === "faction_diplomacy") {
    const actor = strategyFactionById(campaign, action.faction_id);
    const target = strategyFactionById(campaign, payload.target_faction_id);
    const option = (actor?.faction_diplomacy?.[payload.target_faction_id]?.options || []).find((item) => item.id === payload.diplomacy_action_id);
    return `外交：${target?.name || payload.target_faction_id || "未知势力"} · ${option?.name || payload.diplomacy_action_id || "未知行动"}`;
  }
  if (action.action_type === "world_crisis_choice") {
    const names = { contribute: "独立贡献", cooperate: "提出合作", betray: "背弃合作" };
    const target = payload.target_faction_id ? ` → ${strategyFactionName(campaign, payload.target_faction_id)}` : "";
    return `雪鬼危机：${names[payload.choice_id] || payload.choice_id || "未知选择"}${target}`;
  }
  if (action.action_type === "incite_neutral_city_state") {
    const neutral = strategyFactionById(campaign, payload.neutral_faction_id);
    const target = strategyFactionById(campaign, payload.target_faction_id);
    return `教唆：${neutral?.name || payload.neutral_faction_id || "未知城邦"} 出兵 ${target?.name || payload.target_faction_id || "未知目标"}`;
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
  if (action.action_type === "explore_city") {
    return `斥候探索：${strategyCityName(campaign, payload.from_city_id)} → ${strategyCityName(campaign, payload.target_city_id)}`;
  }
  if (action.action_type === "declare_attack") {
    const heroCodes = Array.isArray(payload.attacker_hero_codes) ? payload.attacker_hero_codes : [];
    const heroes = heroCodes.length ? ` · 英灵 ${heroCodes.map((code) => strategyHeroName(campaign, code)).join(", ")}` : "";
    const troops = Number(payload.committed_troops || payload.attacker_troops || 0);
    const troopText = troops ? ` · 带走兵力 ${troops}` : "";
    return `${strategyCityName(campaign, payload.source_city_id)} → ${strategyCityName(campaign, payload.target_city_id)}：出征${heroes}${troopText}`;
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
  if (action.action_type === "upgrade_city_settlement") {
    const labels = { town: "城镇", city: "城市", fortress: "要塞" };
    return `${strategyCityName(campaign, payload.city_id)}：升级为${labels[payload.settlement] || payload.settlement}`;
  }
  if (action.action_type === "propose_resource_trade") {
    const target = strategyFactionName(campaign, payload.target_faction_id);
    const good = ((campaign?.world?.trade_goods || [])).find((item) => item.id === payload.resource_id)
      || ((strategyFaction(campaign)?.resource_board?.goods || [])).find((item) => item.id === payload.resource_id);
    const verb = payload.direction === "buy" ? "求购" : "出售";
    return `贸易请求：向${target} ${verb} ${good?.name || payload.resource_id || "货物"} ${payload.amount || 0} · 金钱 ${payload.money || 0}`;
  }
  if (action.action_type === "accept_resource_trade") {
    return "接受贸易请求";
  }
  if (action.action_type === "reject_resource_trade") {
    return "拒绝贸易请求";
  }
  if (action.action_type === "start_city_work") {
    const work = (campaign?.world?.city_works || []).find((item) => item.id === payload.work_id);
    return `${strategyCityName(campaign, payload.city_id)}：${work?.name || payload.work_id || "城市工程"}`;
  }
  if (action.action_type === "issue_office_order" || action.action_type === "send_office_request") {
    const receiver = (campaign?.world?.offices || []).find((office) => office.id === payload.receiver_office_id);
    const kind = action.action_type === "send_office_request" ? "职位请求" : "职位命令";
    return `${kind}：${strategyOfficeLabel(receiver, campaign)} · ${payload.objective || "未填写目标"}`;
  }
  return action.action_type || "未知行动";
}

function renderStrategyActionQueue(current, campaign) {
  const section = document.createElement("section");
  section.className = "strategy-current-orders";
  const title = document.createElement("h4");
  title.textContent = "当前军令";
  section.append(title);

  const faction = strategyFaction(campaign);
  const canResume = strategyCanResume(campaign);
  const actions = (campaign?.queued_actions || []).filter((action) => action.faction_id === faction?.id);
  const panel = document.createElement("div");
  panel.className = "strategy-event-list";
  if (!actions.length) {
    appendTextLine(panel, "strategy-meta", "本月还没有军令。");
  } else {
    actions.forEach((action) => {
      const card = document.createElement("article");
      card.className = "strategy-campaign-card strategy-queued-action";
      const body = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = strategyQueuedActionLabel(campaign, action);
      body.append(strong);
      appendTextLine(body, "strategy-meta", `消耗 ${action.command_cost || strategyCommandCost(action.action_type, action.payload || {})} 点军令`);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost strategy-queued-action-remove";
      remove.textContent = "删除";
      const ownAction = Number(action.user_id) === Number(state.authUser?.id);
      remove.disabled = state.strategyBusy || !canResume || !ownAction;
      remove.addEventListener("click", () => cancelQueuedStrategyAction(action.id));
      card.append(body, remove);
      panel.append(card);
    });
  }
  section.append(panel);
  current.append(section);
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
      ? `${formatStrategyCalendar(campaign.world.current_month)} · 已转入自由沙盒`
      : contract.month_limit
        ? `${formatStrategyCalendar(campaign.world.current_month)} · 剩余 ${status.months_remaining} 月`
        : `${formatStrategyCalendar(campaign.world.current_month)} · 不限时`;
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
    appendTextLine(card, "strategy-meta", `结算时间：${formatStrategyCalendar(conclusion.concluded_month)} · ${conclusionStateLabels[conclusion.state] || conclusion.state}`);
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
  if (!hero) return;

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
      card.classList.add("is-wounded");
      appendTextLine(card, "strategy-hero-wounded-banner", `负伤中 · 第 ${hero.sleeping_until_month || "?"} 月复原`);
      appendTextLine(card, "strategy-meta", "只能待命或转移驻地，无法出战或接其他任务");
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
    appendStrategySkillTags(card, hero);
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
    const ownServing = hero.status === "serving" && hero.faction_id === faction?.id;
    const ownWounded = hero.status === "sleeping" && hero.faction_id === faction?.id;
    const canAssign = ownServing && (!office || office.office_type === "lord");
    const canTransferWounded = ownWounded && (!office || office.office_type === "lord");
    const stationCities = (campaign?.world?.cities || []).filter((city) => city.owner_faction_id === faction?.id);
    if (canAssign && stationCities.length) {
      const assignRow = document.createElement("div");
      assignRow.className = "strategy-hero-assign";
      const dutySelect = document.createElement("select");
      [
        ["garrison", "驻守"],
        ["training", "训练"],
        ["administration", "内政"],
        ["campaign", "出征"],
        ["reserve", "待命"],
      ].forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        dutySelect.append(option);
      });
      dutySelect.value = hero.assignment_type || "garrison";
      const citySelect = document.createElement("select");
      stationCities.forEach((city) => {
        const option = document.createElement("option");
        option.value = city.id;
        option.textContent = city.name;
        citySelect.append(option);
      });
      citySelect.value = hero.assignment_target_id || hero.city_id || stationCities[0].id;
      const assign = document.createElement("button");
      assign.type = "button";
      assign.className = "primary";
      assign.textContent = "派驻";
      const accepted = hero.command_acceptance?.[dutySelect.value] !== false;
      assign.disabled = state.strategyBusy || !strategyCanIssueOrders(campaign) || !accepted;
      if (!accepted) assign.textContent = "本月拒绝";
      dutySelect.addEventListener("change", () => {
        const ok = hero.command_acceptance?.[dutySelect.value] !== false;
        assign.disabled = state.strategyBusy || !strategyCanIssueOrders(campaign) || !ok;
        assign.textContent = ok ? "派驻" : "本月拒绝";
      });
      assign.addEventListener("click", () => queueStrategyAction("assign_strategic_hero_duty", {
        hero_code: hero.code,
        assignment_type: dutySelect.value,
        target_id: citySelect.value,
      }));
      assignRow.append(dutySelect, citySelect, assign);
      actions.append(assignRow);
    } else if (canTransferWounded && stationCities.length) {
      const transferRow = document.createElement("div");
      transferRow.className = "strategy-hero-assign";
      const citySelect = document.createElement("select");
      stationCities.forEach((city) => {
        const option = document.createElement("option");
        option.value = city.id;
        option.textContent = city.name;
        citySelect.append(option);
      });
      citySelect.value = hero.assignment_target_id || hero.city_id || stationCities[0].id;
      const transfer = document.createElement("button");
      transfer.type = "button";
      transfer.className = "primary";
      transfer.textContent = "转移";
      transfer.disabled = state.strategyBusy || !strategyCanIssueOrders(campaign);
      transfer.addEventListener("click", () => queueStrategyAction("assign_strategic_hero_duty", {
        hero_code: hero.code,
        assignment_type: "garrison",
        target_id: citySelect.value,
      }));
      transferRow.append(citySelect, transfer);
      actions.append(transferRow);
    } else if ((ownServing || ownWounded) && office && office.office_type !== "lord") {
      appendTextLine(card, "strategy-meta", "主公可在此把武将派到具体城市。");
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
    if (hero.status === "sleeping") {
      card.classList.add("is-wounded");
      const mark = document.createElement("span");
      mark.className = "hero-slot__mark is-wounded";
      mark.textContent = "伤";
      mark.title = `负伤中 · 第 ${hero.sleeping_until_month || "?"} 月复原`;
      head.append(mark);
    }
    const duty = document.createElement("span");
    duty.className = "hero-slot__duty";
    const heldOffice = (campaign?.world?.offices || []).find((item) => item.id === hero.office_id);
    duty.textContent = hero.status === "sleeping"
      ? `负伤 · 第 ${hero.sleeping_until_month || "?"} 月复原`
      : heldOffice
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
    const effect = relic.effect || {};
    const effectText = effect.summary ? ` · ${effect.summary}` : "";
    appendTextLine(
      intelCard,
      "strategy-meta",
      `${relic.name}${effectText} · ${relic.state_label || relic.state} / ${relic.condition_label || relic.condition} · 线索指向 ${relic.location_city_name || relic.location_node_name || "未知区域"}`
    );
  });
  if (intel.unknown_count) {
    appendTextLine(intelCard, "strategy-meta", `仍有 ${intel.unknown_count} 件圣物位置未知`);
  }
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
  const visibleAltars = altars.filter((altar) => (
    altar.owner_faction_id === faction.id || Number(altar.bound_count || 0) > 0
  ));
  if (!visibleAltars.length && altars.length) {
    appendTextLine(altarCard, "strategy-meta", "己方城市尚未建成可安放圣物的祭坛。");
  }
  visibleAltars.forEach((altar) => {
    const boundEffects = Array.isArray(altar.bound_relics)
      ? altar.bound_relics
        .map((item) => item.effect?.summary || item.name)
        .filter(Boolean)
      : [];
    const effectText = boundEffects.length
      ? boundEffects.join("；")
      : "尚未安放圣物";
    appendTextLine(
      altarCard,
      "strategy-meta",
      `${altar.city_name || "未知城市"} · ${altar.state_label || altar.state} · ${strategyFactionName(campaign, altar.owner_faction_id)}控制 · 绑定 ${altar.bound_count || 0}/${altar.capacity || 1} · 月维护 ${altar.monthly_maintenance_ether || 0} 以太${altar.monthly_maintenance_ether && !altar.maintenance_affordable ? "（当前不足）" : ""} · ${effectText} · 本月行动 ${altar.actions_remaining ?? 0}/${(altar.actions_used || 0) + (altar.actions_remaining ?? 0)}`
    );
  });
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

function strategyBattleResultLines(battle = {}, campaign = state.strategyCampaign) {
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
    if (committed.length) fragments.push(`参战 ${committed.map((code) => strategyHeroName(campaign, code)).join("、")}`);
    if (surviving.length) fragments.push(`存活 ${surviving.map((code) => strategyHeroName(campaign, code)).join("、")}`);
    if (sleeping.length) fragments.push(`负伤 ${sleeping.map((code) => strategyHeroName(campaign, code)).join("、")}`);
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
  if (unboundHeroes.length) lines.push(`解除祭祀绑定：${unboundHeroes.map((code) => strategyHeroName(campaign, code)).join("、")}`);
  return lines;
}

function renderStrategyResourcesPanel(host, campaign, faction, office, canIssueOrders) {
  const board = faction?.resource_board || {};
  const goods = Array.isArray(board.goods) ? board.goods : [];
  const counterparts = Array.isArray(board.counterparts) ? board.counterparts : [];
  const offers = Array.isArray(board.offers) ? board.offers : [];
  const canTrade = Boolean(canIssueOrders && office?.office_type === "lord" && strategyCanResume(campaign));

  const stock = document.createElement("div");
  stock.className = "strategy-resource-grid";
  const basics = [
    ["money", "钱", faction?.resources?.money || 0, ""],
    ["food", "粮", faction?.resources?.food || 0, ""],
    ["ether", "以太", faction?.resources?.ether || 0, ""],
    ["troops", "兵", faction?.resources?.troops || 0, ""],
  ];
  basics.forEach(([, label, value, note]) => {
    const card = document.createElement("article");
    card.className = "strategy-resource-card";
    const name = document.createElement("strong");
    name.textContent = label;
    const amount = document.createElement("em");
    amount.textContent = String(value);
    card.append(name, amount);
    if (note) appendTextLine(card, "strategy-meta", note);
    stock.append(card);
  });
  goods.filter((item) => item.kind === "rare").forEach((item) => {
    const card = document.createElement("article");
    card.className = "strategy-resource-card is-rare";
    const name = document.createElement("strong");
    name.textContent = item.name || item.id;
    const amount = document.createElement("em");
    amount.textContent = String(item.stock || 0);
    card.append(name, amount);
    appendTextLine(card, "strategy-meta", [
      item.building_name ? `供${item.building_name}` : "",
      item.veins ? `矿脉 ${item.veins}` : "境内无矿",
      item.monthly_income ? `每月 +${item.monthly_income}` : "",
      `本境单价 ${item.unit_price}`,
    ].filter(Boolean).join(" · "));
    stock.append(card);
  });
  host.append(stock);

  const incoming = offers.filter((offer) => offer.status === "pending" && offer.target_faction_id === faction?.id);
  const outgoing = offers.filter((offer) => offer.status === "pending" && offer.proposer_faction_id === faction?.id);
  const queuedOutgoing = (campaign?.queued_actions || []).filter((action) => (
    action.action_type === "propose_resource_trade" && action.faction_id === faction?.id
  ));
  const currentMonth = Number(campaign?.world?.current_month || 1);
  const resolvedThisMonth = offers.filter((offer) => (
    offer.status !== "pending" && Number(offer.resolved_month || offer.expires_month || 0) === currentMonth
  ));
  const box = document.createElement("section");
  box.className = "strategy-resource-offers";
  const boxTitle = document.createElement("h4");
  boxTitle.textContent = "往来贸易";
  box.append(boxTitle);
  const tradeDeadline = (offer) => {
    const expires = Number(offer.expires_month || 0);
    if (!expires) return "等待回复";
    const left = expires - currentMonth;
    if (left <= 0) return "本月过期";
    return `第 ${formatStrategyCalendar(expires)} 前回复 · 还剩 ${left} 个月`;
  };
  queuedOutgoing.forEach((action) => {
    const payload = action.payload || {};
    const good = goods.find((item) => item.id === payload.resource_id) || { name: payload.resource_id };
    appendTextLine(
      box,
      "strategy-meta",
      `本月结算发出：向${strategyFactionName(campaign, payload.target_faction_id)} ${payload.direction === "buy" ? "求购" : "出售"} ${good.name} ${payload.amount || 0} · 金钱 ${payload.money || 0}。对方下个月会看到并回复。`,
    );
  });
  incoming.forEach((offer) => {
    const row = document.createElement("div");
    row.className = "strategy-resource-offer";
    const good = goods.find((item) => item.id === offer.resource_id) || { name: offer.resource_id };
    appendTextLine(
      row,
      "strategy-meta",
      `${strategyFactionName(campaign, offer.proposer_faction_id)} ${offer.direction === "sell" ? "出售" : "求购"} ${good.name} ${offer.amount} · 金钱 ${offer.money} · ${tradeDeadline(offer)}`,
    );
    if (canTrade) {
      row.append(createButton({
        label: "接受",
        variant: "primary",
        size: "sm",
        disabled: state.strategyBusy,
        onClick: () => queueStrategyAction("accept_resource_trade", { offer_id: offer.id || offer.offer_id }),
      }), createButton({
        label: "拒绝",
        variant: "subtle",
        size: "sm",
        disabled: state.strategyBusy,
        onClick: () => queueStrategyAction("reject_resource_trade", { offer_id: offer.id || offer.offer_id }),
      }));
    }
    box.append(row);
  });
  outgoing.forEach((offer) => {
    const good = goods.find((item) => item.id === offer.resource_id) || { name: offer.resource_id };
    appendTextLine(
      box,
      "strategy-meta",
      `已送达${strategyFactionName(campaign, offer.target_faction_id)}：${offer.direction === "sell" ? "出售" : "求购"} ${good.name} ${offer.amount} · 金钱 ${offer.money} · ${tradeDeadline(offer)}`,
    );
  });
  resolvedThisMonth.forEach((offer) => {
    const good = goods.find((item) => item.id === offer.resource_id) || { name: offer.resource_id };
    const otherName = strategyFactionName(
      campaign,
      offer.proposer_faction_id === faction?.id ? offer.target_faction_id : offer.proposer_faction_id,
    );
    const deal = `${offer.direction === "sell" ? "出售" : "求购"} ${good.name} ${offer.amount} · 金钱 ${offer.money}`;
    const result = offer.status === "accepted"
      ? `${otherName}同意了这笔贸易：${deal}`
      : offer.status === "rejected"
        ? `${otherName}拒绝了这笔贸易：${deal}`
        : `与${otherName}的贸易已过期：${deal}`;
    appendTextLine(box, "strategy-meta", result);
  });
  if (!queuedOutgoing.length && !incoming.length && !outgoing.length && !resolvedThisMonth.length) {
    appendTextLine(box, "strategy-meta", "目前没有进行中的贸易请求。");
  }
  host.append(box);

  const form = document.createElement("section");
  form.className = "strategy-resource-trade";

  const targetSelect = document.createElement("select");
  counterparts.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.faction_id;
    option.textContent = item.name;
    targetSelect.append(option);
  });
  if (!state.strategyTradeTargetId && counterparts[0]) state.strategyTradeTargetId = counterparts[0].faction_id;
  targetSelect.value = state.strategyTradeTargetId || "";
  targetSelect.disabled = !canTrade || state.strategyBusy;
  form.append(createStrategyField("对象", targetSelect));

  const directionSelect = document.createElement("select");
  [["sell", "出售给我方货物，换对方的钱"], ["buy", "用钱买对方的货"]].forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    directionSelect.append(option);
  });
  directionSelect.value = state.strategyTradeDirection || "sell";
  directionSelect.disabled = !canTrade || state.strategyBusy;
  form.append(createStrategyField("方向", directionSelect));

  const resourceSelect = document.createElement("select");
  (goods.length ? goods : (campaign?.world?.trade_goods || [])).forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = `${item.name}（${item.kind === "rare" ? "稀有" : "基本"}）`;
    resourceSelect.append(option);
  });
  if (!state.strategyTradeResourceId && resourceSelect.options.length) {
    state.strategyTradeResourceId = resourceSelect.options[0].value;
  }
  resourceSelect.value = state.strategyTradeResourceId || "";
  resourceSelect.disabled = !canTrade || state.strategyBusy;
  form.append(createStrategyField("商品", resourceSelect));

  const amountInput = document.createElement("input");
  amountInput.type = "number";
  amountInput.min = "1";
  amountInput.step = "1";
  amountInput.value = String(Math.max(1, Number(state.strategyTradeAmount) || 10));
  amountInput.disabled = !canTrade || state.strategyBusy;
  form.append(createStrategyField("数量", amountInput));

  const moneyInput = document.createElement("input");
  moneyInput.type = "number";
  moneyInput.min = "1";
  moneyInput.step = "1";
  moneyInput.disabled = !canTrade || state.strategyBusy;
  form.append(createStrategyField("金钱（结算）", moneyInput));
  const quoteLine = document.createElement("p");
  quoteLine.className = "strategy-meta strategy-resource-quote";
  form.append(quoteLine);

  const refreshTradeQuote = () => {
    state.strategyTradeTargetId = targetSelect.value || "";
    state.strategyTradeDirection = directionSelect.value || "sell";
    state.strategyTradeResourceId = resourceSelect.value || "";
    state.strategyTradeAmount = Math.max(1, Number.parseInt(amountInput.value || "1", 10) || 1);
    const counterpart = counterparts.find((item) => item.faction_id === state.strategyTradeTargetId) || counterparts[0];
    const quote = (counterpart?.quotes || []).find((item) => item.id === state.strategyTradeResourceId);
    const suggested = Number(quote?.unit_price || 0) * Math.max(1, Number(state.strategyTradeAmount) || 10);
    if (state.strategyTradeAutoMoney || !Number(state.strategyTradeMoney)) {
      state.strategyTradeMoney = suggested || 1;
      moneyInput.value = String(state.strategyTradeMoney);
    }
    quoteLine.textContent = quote
      ? `${counterpart?.name || "对方"}当前库存 ${quote.stock}，建议单价 ${quote.unit_price}，建议总额 ${suggested}。库存越少越贵。`
      : "";
  };
  targetSelect.addEventListener("change", refreshTradeQuote);
  directionSelect.addEventListener("change", refreshTradeQuote);
  resourceSelect.addEventListener("change", () => {
    state.strategyTradeAutoMoney = true;
    state.strategyTradeMoney = 0;
    refreshTradeQuote();
  });
  amountInput.addEventListener("input", refreshTradeQuote);
  moneyInput.addEventListener("input", (event) => {
    state.strategyTradeAutoMoney = false;
    state.strategyTradeMoney = Math.max(1, Number.parseInt(event.target.value || "1", 10) || 1);
  });
  if (!state.strategyTradeMoney) state.strategyTradeAutoMoney = true;
  refreshTradeQuote();
  if (!canTrade) {
    appendTextLine(form, "strategy-command-lock", office?.office_type === "lord" ? "当前不能下达贸易请求。" : "只有主公可以向其他势力发起贸易。");
  } else {
    form.append(createButton({
      label: "发出贸易请求",
      variant: "primary",
      disabled: state.strategyBusy || !state.strategyTradeTargetId || !state.strategyTradeResourceId,
      onClick: () => queueStrategyAction("propose_resource_trade", {
        target_faction_id: state.strategyTradeTargetId,
        direction: state.strategyTradeDirection || "sell",
        resource_id: state.strategyTradeResourceId,
        amount: Math.max(1, Number(state.strategyTradeAmount) || 10),
        money: Math.max(1, Number(state.strategyTradeMoney) || 1),
      }),
    }));
  }
  host.append(form);
}

const STRATEGY_CREATE_STEPS = [
  { id: "scenario", label: "开局变体" },
  { id: "identity", label: "战役身份" },
  { id: "rules", label: "战役规则" },
  { id: "confirm", label: "确认开局" },
];

function strategyCreateVariants() {
  return state.strategyVariants.length ? state.strategyVariants : FALLBACK_STRATEGY_VARIANTS;
}

function strategyCreateCatalog() {
  return state.strategyWorldCatalog || {};
}

function strategyCreateScenario() {
  const scenarios = Array.isArray(strategyCreateCatalog().scenarios) ? strategyCreateCatalog().scenarios : [];
  return scenarios.find((item) => item.id === state.strategyScenarioId) || scenarios.find((item) => item.default) || scenarios[0] || null;
}

/**
 * 新建战役。
 *
 * 此前这是列表页顶上常驻的一整排输入框——名字、种子、变体、势力数、加入码挤在同
 * 一行网格里，其中一半还是禁用的。可是"开一局新的"是偶尔发生一次的事，没有理由
 * 每次打开战役列表都先看它一遍。所以它成了一条独立流程：先选这局要问什么问题，
 * 再给它起个名字，定规则，最后确认。每一步只回答一件事。
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
    const modeRow = document.createElement("div");
    modeRow.className = "strategy-wizard__variants";
    [
      ["true_campaign", "真实战役", "读世界目录。国家强弱不对称，同国城市连片，矿脉和武将都能改。"],
      ["random_campaign", "随机战役", "现有算法。各主要势力一城起家，矿脉随机撒。"],
    ].forEach(([id, name, blurb]) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = `strategy-wizard__variant${state.strategyCampaignMode === id ? " is-active" : ""}`;
      const titleNode = document.createElement("strong");
      titleNode.textContent = name;
      card.append(titleNode);
      appendTextLine(card, "strategy-wizard__variant-question", blurb);
      card.addEventListener("click", () => {
        state.strategyCampaignMode = id;
        renderStrategyPanel();
      });
      modeRow.append(card);
    });
    body.append(modeRow);
    const scenario = strategyCreateScenario();
    if (state.strategyCampaignMode === "true_campaign" && scenario) {
      state.strategyScenarioId = scenario.id;
      appendTextLine(body, "strategy-meta", `${scenario.name} · ${scenario.city_count} 城 · ${scenario.major_faction_count} 国 · ${scenario.neutral_city_state_count} 座独立城邦`);
      const nationList = document.createElement("div");
      nationList.className = "strategy-wizard__nations";
      (scenario.nations || []).forEach((nation) => {
        const line = document.createElement("p");
        line.className = "strategy-meta";
        line.textContent = `${nation.name}：${nation.city_count} 城 / ${nation.hero_count} 将 · ${(nation.roster || []).map((item) => item.name || item.code).join("、")}`;
        nationList.append(line);
      });
      body.append(nationList);
    }
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
      const cleaned = String(event.target.value || "").replace(/[^\d]/g, "").slice(0, 12);
      state.strategySeed = cleaned || "1";
    });
    seedRow.append(createStrategyField("地图种子", seedInput), createButton({
      label: "换一张",
      variant: "subtle",
      size: "sm",
      disabled: state.strategyBusy,
      onClick: () => {
        state.strategySeed = String(10000000 + Math.floor(Math.random() * 899999999));
        renderStrategyPanel();
      },
    }));
    body.append(seedRow);
  } else if (step === 2) {
    const factionCount = document.createElement("input");
    factionCount.type = "number";
    factionCount.min = "2";
    factionCount.max = "10";
    factionCount.step = "1";
    factionCount.value = String(Math.max(2, Math.min(10, Number(state.strategyMajorFactionCount) || 2)));
    factionCount.disabled = state.strategyBusy;
    factionCount.addEventListener("input", (event) => {
      state.strategyMajorFactionCount = Math.max(2, Math.min(10, Number.parseInt(event.target.value || "2", 10) || 2));
    });
    if (state.strategyCampaignMode !== "true_campaign") {
      body.append(createStrategyField("主要势力数量（2～10）", factionCount));
      appendTextLine(body, "strategy-meta", "其余城市会生成中立城邦。地图至少 20 座城；势力越多，中立城邦相对越少。");
    } else {
      const scenario = strategyCreateScenario();
      if (scenario) {
        appendTextLine(body, "strategy-meta", `${scenario.name} · ${scenario.city_count} 城 · ${scenario.major_faction_count} 国 · ${scenario.neutral_city_state_count} 座独立城邦`);
      }
    }

    const yearLimit = document.createElement("input");
    yearLimit.type = "number";
    yearLimit.min = "0";
    yearLimit.max = "100";
    yearLimit.step = "1";
    yearLimit.value = String(Number(state.strategyYearLimit) || 0);
    yearLimit.disabled = state.strategyBusy;
    yearLimit.addEventListener("input", (event) => {
      const next = Math.max(0, Math.min(100, Number.parseInt(event.target.value || "0", 10) || 0));
      state.strategyYearLimit = next;
    });
    body.append(createStrategyField("战役年限（0 为不限时）", yearLimit));

    const earliest = document.createElement("input");
    earliest.type = "number";
    earliest.min = "1";
    earliest.max = "100";
    earliest.step = "1";
    earliest.value = String(Math.max(1, Number(state.strategyCrisisEarliestYear) || 10));
    earliest.disabled = state.strategyBusy;
    earliest.addEventListener("input", (event) => {
      const next = Math.max(1, Math.min(100, Number.parseInt(event.target.value || "10", 10) || 10));
      state.strategyCrisisEarliestYear = next;
    });
    body.append(createStrategyField("危机最早出现年份", earliest));
    if (Number(state.strategyYearLimit) > 0 && Number(state.strategyYearLimit) < Number(state.strategyCrisisEarliestYear)) {
      appendTextLine(body, "strategy-wizard__warn", "战役年限早于危机最早年份：这局可能还没等到北境危机就先评议。");
    }
  } else {
    const summary = document.createElement("dl");
    summary.className = "strategy-wizard__summary";
    [
      ["战役名", state.strategyName || "英灵城邦"],
      ["开局变体", selectedVariant.name],
      ["核心问题", selectedVariant.core_question || "—"],
      ["地图种子", String(state.strategySeed || "1")],
      ["战役年限", Number(state.strategyYearLimit) > 0 ? `${state.strategyYearLimit} 年` : "不限时，靠胜利条件结束"],
      ["北境危机", `第 ${Math.max(1, Number(state.strategyCrisisEarliestYear) || 10)} 年起可能出现，之后逐年更容易`],
      ["世界", state.strategyCampaignMode === "true_campaign" ? "真实战役（配置表）" : "随机战役"],
      ["规模", (() => {
        if (state.strategyCampaignMode === "true_campaign") {
          const scenario = strategyCreateScenario();
          if (!scenario) return "读取世界目录";
          return `${scenario.city_count} 城 · ${scenario.major_faction_count} 国 · ${scenario.neutral_city_state_count} 座独立城邦`;
        }
        const majors = Math.max(2, Math.min(10, Number(state.strategyMajorFactionCount) || 2));
        const cities = Math.min(128, Math.max(20, majors * 2));
        return `${cities} 城 · ${majors} 个主要势力 · ${cities - majors} 个中立城邦`;
      })()],
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
  const existing = current.querySelector(":scope > .campaign-prep");
  if (existing) existing.replaceWith(screen);
  else current.replaceChildren(screen);
}

const TECH_CATEGORIES = [
  { id: "politics", label: "政治" },
  { id: "military", label: "军事" },
  { id: "economy", label: "经济" },
  { id: "construction", label: "建设" },
  { id: "agriculture", label: "农业" },
  { id: "diplomacy", label: "外交" },
  { id: "ritual", label: "祭祀" },
  { id: "heroes", label: "武将" },
  { id: "siege", label: "攻城" },
];

function strategyTechCategoryId(tech) {
  if (tech.category && TECH_CATEGORIES.some((item) => item.id === tech.category)) return tech.category;
  const branch = tech.branch || "";
  if (branch === "building" || branch.includes("建筑") || branch.includes("营造")) return "construction";
  if (branch === "office" || branch.includes("职位") || branch.includes("政务")) return "politics";
  return "military";
}

function strategyQueuedResearchId(campaign, faction) {
  const queued = (campaign?.queued_actions || []).find((item) => (
    item.action_type === "unlock_tactic_tech"
    && (!faction?.id || item.faction_id === faction.id)
  ));
  return String(queued?.payload?.tech_id || queued?.action_key || "");
}

function strategyActiveResearch(campaign, faction) {
  const researching = faction?.researching || {};
  const worldId = String(researching.tech_id || "");
  const queuedId = strategyQueuedResearchId(campaign, faction);
  const techId = worldId || queuedId;
  if (!techId) return null;
  return {
    techId,
    monthsDone: Number(researching.months_done || 0),
    monthsTotal: Number(researching.months_total || 0),
  };
}

function hideStrategyTechFloatTip() {
  const tip = document.getElementById("strategy-tech-float-tip");
  if (!tip) return;
  tip.hidden = true;
  if (typeof tip.replaceChildren === "function") tip.replaceChildren();
}

function strategyTechFloatTipNode() {
  let tip = document.getElementById("strategy-tech-float-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "strategy-tech-float-tip";
    document.body.append(tip);
  } else if (tip.parentNode !== document.body) {
    document.body.append(tip);
  }
  tip.className = "strategy-tech-float-tip";
  return tip;
}

function showStrategyTechFloatTip(anchor, tech, months, monthlyMoney) {
  const tip = strategyTechFloatTipNode();
  if (typeof tip.replaceChildren === "function") tip.replaceChildren();
  else tip.innerHTML = "";
  appendTextLine(tip, "strategy-meta", tech.description || "暂无描述");
  strategyTechBonusLines(tech).forEach((line) => appendTextLine(tip, "strategy-meta", line));
  appendTextLine(tip, "strategy-meta", `总花费约 钱 ${monthlyMoney * months}（每回合扣除）`);
  const rect = typeof anchor.getBoundingClientRect === "function" ? anchor.getBoundingClientRect() : null;
  if (rect && Number.isFinite(rect.left) && Number.isFinite(rect.top)) {
    const width = Number(tip.offsetWidth) || 240;
    const height = Number(tip.offsetHeight) || 72;
    const viewWidth = Number(globalThis.innerWidth) || 0;
    let left = rect.left;
    let top = rect.top - height - 8;
    if (top < 8) top = rect.bottom + 8;
    if (viewWidth && left + width > viewWidth - 8) left = Math.max(8, viewWidth - width - 8);
    if (left < 8) left = 8;
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(top)}px`;
  }
  tip.hidden = false;
}

function strategyTechResearchMonths(tech) {
  const specified = Number(tech.research_months || 0);
  if (specified > 0) return specified;
  const name = tech.name || "";
  if (name.includes("III")) return 5;
  if (name.includes("II")) return 3;
  if (/\bI\b/.test(name) || name.endsWith(" I")) return 2;
  return 2;
}

function strategyTechBonusLines(tech) {
  const lines = [];
  if (tech.special_ratio_bonus) lines.push(`特色士兵比例 +${tech.special_ratio_bonus}%`);
  if (tech.garrison_ratio_bonus) lines.push(`守备兵比例 +${tech.garrison_ratio_bonus}%`);
  if (tech.hero_deployment_limit_bonus) lines.push(`同时投入英灵 +${tech.hero_deployment_limit_bonus}`);
  Object.entries(tech.office_capacity_effects || {}).forEach(([key, value]) => {
    const labels = {
      general_per_grand_general: "每名大将军可辖将军",
      grand_general: "大将军职位容量",
    };
    lines.push(`${labels[key] || key} +${value}`);
  });
  (tech.unit_unlocks || []).forEach((unit) => {
    const labels = { archer: "弓兵", cavalry: "骑兵", infantry: "步兵" };
    lines.push(`解锁单位：${labels[unit] || unit}`);
  });
  Object.entries(tech.siege_effects || {}).forEach(([key, value]) => {
    const labels = {
      cannon_attack: "火炮攻击",
      cannon_range: "火炮射程",
      cannon_splash: "火炮溅射",
      tower_attack: "箭塔攻击",
      tower_range: "箭塔射程",
      tower_defense: "箭塔防御",
      can_forge_cannon: "解锁铸造火炮",
    };
    if (key === "can_forge_cannon") lines.push(labels[key]);
    else lines.push(`${labels[key] || key} +${value}`);
  });
  Object.entries(tech.building_level_effects || {}).forEach(([building, value]) => {
    const labels = {
      academy: "学院",
      fields: "农业区",
      barracks: "军营",
      stables: "马厩",
      archery_range: "靶场",
      ritual_site: "祭坛",
      market: "商业区",
      industrial: "工业区",
      walls: "城墙",
      castle: "城堡",
    };
    lines.push(`${labels[building] || building}等级上限 +${value}`);
  });
  return lines;
}

function strategyTechById(techs) {
  const map = {};
  (techs || []).forEach((tech) => {
    if (tech?.id) map[tech.id] = tech;
  });
  return map;
}

function strategyTechDepth(tech, byId, seen = new Set()) {
  const prereqs = Array.isArray(tech?.prerequisites) ? tech.prerequisites : [];
  if (!prereqs.length) return 0;
  if (seen.has(tech.id)) return 0;
  seen.add(tech.id);
  return 1 + Math.max(0, ...prereqs.map((id) => strategyTechDepth(byId[id] || {}, byId, seen)));
}

function hideStrategyTechTree() {
  const overlay = document.getElementById("strategy-tech-tree");
  if (!overlay) return;
  overlay.hidden = true;
  overlay.setAttribute("aria-hidden", "true");
}

function showStrategyTechTree(campaign, faction) {
  hideStrategyTechFloatTip();
  let overlay = document.getElementById("strategy-tech-tree");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "strategy-tech-tree";
    overlay.className = "strategy-tech-tree";
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) hideStrategyTechTree();
    });
    document.body.append(overlay);
  }
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  overlay.replaceChildren();
  const dialog = document.createElement("div");
  dialog.className = "strategy-tech-tree__dialog";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-labelledby", "strategy-tech-tree-title");
  const head = document.createElement("header");
  head.className = "strategy-tech-tree__head";
  const title = document.createElement("h3");
  title.id = "strategy-tech-tree-title";
  title.textContent = "科技树";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "ghost";
  close.textContent = "关闭";
  close.addEventListener("click", hideStrategyTechTree);
  head.append(title, close);
  dialog.append(head);
  appendTextLine(dialog, "strategy-meta", "只读查看：前置科技、所需建筑与城市等级。研究仍在科技页进行。");
  const techs = faction?.tactic_tech_tree || [];
  const byId = strategyTechById(techs);
  const board = document.createElement("div");
  board.className = "strategy-tech-tree__board";
  TECH_CATEGORIES.forEach((category) => {
    const items = techs.filter((tech) => strategyTechCategoryId(tech) === category.id);
    if (!items.length) return;
    const column = document.createElement("section");
    column.className = "strategy-tech-tree__category";
    const heading = document.createElement("strong");
    heading.textContent = category.label;
    column.append(heading);
    const layers = new Map();
    items.forEach((tech) => {
      const depth = strategyTechDepth(tech, byId);
      if (!layers.has(depth)) layers.set(depth, []);
      layers.get(depth).push(tech);
    });
    [...layers.keys()].sort((a, b) => a - b).forEach((depth) => {
      const row = document.createElement("div");
      row.className = "strategy-tech-tree__layer";
      layers.get(depth).forEach((tech) => {
        const node = document.createElement("article");
        node.className = `strategy-tech-tree__node${tech.unlocked ? " is-unlocked" : ""}${tech.available ? " is-available" : ""}${tech.researching ? " is-researching" : ""}`;
        const name = document.createElement("strong");
        name.textContent = tech.name;
        node.append(name);
        const prereqNames = (tech.prerequisites || [])
          .map((id) => byId[id]?.name || id)
          .filter(Boolean);
        if (prereqNames.length) appendTextLine(node, "strategy-meta", `前置：${prereqNames.join("、")}`);
        if (tech.required_building) {
          appendTextLine(
            node,
            "strategy-meta",
            `建筑：${tech.required_building_label || tech.required_building} ${tech.required_building_level || 1} 级`,
          );
        }
        if (tech.required_settlement) {
          appendTextLine(node, "strategy-meta", `城市：${tech.required_settlement_label || tech.required_settlement}`);
        }
        appendTextLine(node, "strategy-meta", tech.unlocked ? "已解锁" : tech.available ? "可研究" : "未开放");
        row.append(node);
      });
      column.append(row);
    });
    board.append(column);
  });
  dialog.append(board);
  overlay.append(dialog);
}

function renderStrategyTechPanel(current, campaign, faction, canResume, office = strategyActiveOffice(campaign)) {
  hideStrategyTechFloatTip();
  const techs = faction?.tactic_tech_tree || [];
  if (!techs.length) return;
  const canResearch = !office || office.office_type === "lord";
  const active = strategyActiveResearch(campaign, faction);
  const visible = techs.filter((tech) => tech.available || tech.researching || active?.techId === tech.id);
  const panel = document.createElement("div");
  panel.className = "strategy-tech-panel";
  const toolbar = document.createElement("div");
  toolbar.className = "strategy-tech-toolbar";
  const treeBtn = document.createElement("button");
  treeBtn.type = "button";
  treeBtn.className = "ghost";
  treeBtn.textContent = "查看科技树";
  treeBtn.addEventListener("click", () => showStrategyTechTree(campaign, faction));
  toolbar.append(treeBtn);
  panel.append(toolbar);

  TECH_CATEGORIES.forEach((category) => {
    const items = visible.filter((tech) => strategyTechCategoryId(tech) === category.id);
    if (!items.length) return;
    const collapsed = Boolean(state.strategyTechCollapsed?.[category.id]);
    const card = document.createElement("article");
    card.className = `strategy-tech-category is-${category.id}${collapsed ? " is-collapsed" : ""}`;

    const head = document.createElement("button");
    head.type = "button";
    head.className = "strategy-tech-category__head";
    const title = document.createElement("span");
    title.className = "strategy-tech-category__title";
    title.textContent = category.label;
    const count = document.createElement("span");
    count.className = "strategy-tech-category__count";
    count.textContent = `${items.length} 项`;
    const chevron = document.createElement("span");
    chevron.className = "strategy-tech-category__chevron";
    chevron.textContent = collapsed ? "▸" : "▾";
    head.append(title, count, chevron);
    head.addEventListener("click", () => {
      hideStrategyTechFloatTip();
      state.strategyTechCollapsed = { ...(state.strategyTechCollapsed || {}) };
      state.strategyTechCollapsed[category.id] = !collapsed;
      renderStrategyPanel();
    });
    card.append(head);

    if (!collapsed) {
      const list = document.createElement("div");
      list.className = "strategy-tech-list";
      items.forEach((tech) => {
        const months = strategyTechResearchMonths(tech);
        const monthlyMoney = Number(tech.money_cost || 0);
        const isActive = active?.techId === tech.id;
        const monthsTotal = Math.max(1, isActive && active.monthsTotal ? active.monthsTotal : months);
        const monthsDone = isActive ? Number(active.monthsDone || 0) : 0;
        const percent = !isActive
          ? 0
          : monthsDone > 0
            ? Math.max(6, Math.round((monthsDone / monthsTotal) * 100))
            : 12;
        const row = document.createElement("div");
        row.className = `strategy-tech-row${isActive ? " is-researching" : ""}`;
        row.tabIndex = 0;
        if (typeof row.style?.setProperty === "function") row.style.setProperty("--progress", `${percent}%`);
        else row.style["--progress"] = `${percent}%`;
        const copy = document.createElement("div");
        const name = document.createElement("strong");
        name.className = "strategy-tech-row__name";
        name.textContent = tech.name;
        const cost = document.createElement("div");
        cost.className = "strategy-tech-row__cost";
        cost.textContent = isActive
          ? `${monthsDone}/${monthsTotal} · 每回合 钱 ${monthlyMoney}`
          : `每回合 钱 ${monthlyMoney} · ${months} 回合`;
        copy.append(name, cost);
        row.append(copy);
        const showTip = () => showStrategyTechFloatTip(row, tech, months, monthlyMoney);
        row.addEventListener("mouseenter", showTip);
        row.addEventListener("focus", showTip);
        row.addEventListener("mouseleave", hideStrategyTechFloatTip);
        row.addEventListener("blur", hideStrategyTechFloatTip);

        const actions = document.createElement("div");
        actions.className = "strategy-tech-actions";
        const button = document.createElement("button");
        button.type = "button";
        button.className = isActive ? "ghost strategy-tech-row__btn" : "primary strategy-tech-row__btn";
        if (isActive) {
          button.textContent = "取消";
          button.disabled = state.strategyBusy || !canResume || !canResearch;
          button.addEventListener("click", () => {
            hideStrategyTechFloatTip();
            queueStrategyAction("cancel_tactic_research", {});
          });
        } else {
          button.textContent = "研究";
          button.disabled = state.strategyBusy
            || !canResume
            || !canResearch
            || Boolean(active)
            || tech.unlocked
            || !tech.available
            || !strategyCanAffordCommand(campaign, faction, "unlock_tactic_tech");
          button.addEventListener("click", () => {
            hideStrategyTechFloatTip();
            queueStrategyAction("unlock_tactic_tech", { tech_id: tech.id });
          });
        }
        actions.append(button);
        row.append(actions);
        list.append(row);
      });
      card.append(list);
    }
    panel.append(card);
  });
  if (!panel.children.length) {
    appendTextLine(panel, "strategy-meta", canResearch ? "当前没有可研究的科技。" : "只有主公能签发研究。");
  }
  current.append(panel);
}

function strategyEventBucket(event) {
  const category = String(event?.category || "");
  if (category === "explore") return "intel";
  if (category.includes("trade") || category === "rare_resource_income") return "trade";
  if (
    category.includes("battle")
    || category.includes("siege")
    || category.includes("rebellion")
    || category.startsWith("strategy_siege")
  ) return "battle";
  if (
    category.includes("building")
    || category.includes("city_")
    || category === "city_policy"
    || category === "tactic_tech"
    || category === "city_work"
    || category === "field_troops_levied"
  ) return "building";
  return "intel";
}

function renderStrategyEventLog(current, campaign, faction, canResume) {
  const filters = document.createElement("div");
  filters.className = "strategy-event-filters";
  [
    ["all", "全部"],
    ["trade", "贸易"],
    ["battle", "战场"],
    ["building", "建筑"],
    ["intel", "情报"],
  ].forEach(([id, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = state.strategyEventFilter === id ? "primary" : "ghost";
    button.textContent = label;
    button.addEventListener("click", () => {
      state.strategyEventFilter = id;
      renderStrategyPanel();
    });
    filters.append(button);
  });
  current.append(filters);

  const filter = state.strategyEventFilter || "all";
  if (filter === "all" || filter === "battle") {
    renderStrategyBattleRecords(current, campaign, faction, canResume);
  }

  const events = document.createElement("div");
  events.className = "strategy-event-list";
  const eventLog = (campaign?.world?.event_log || [])
    .filter((event) => filter === "all" || strategyEventBucket(event) === filter)
    .slice(-16)
    .reverse();
  eventLog.forEach((event) => {
    appendTextLine(events, "strategy-event", `${formatStrategyCalendar(event.month)} · ${event.message}`);
  });
  if (!eventLog.length) appendTextLine(events, "strategy-meta", "这一类目前还没有记录。");
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
    appendTextLine(card, "strategy-meta", `处理方式：${strategyBattleModeName(battle.resolution_mode)} · 状态：${statusNames[battle.status] || battle.status}`);
    strategyBattleResultLines(battle, campaign).forEach((line) => appendTextLine(card, "strategy-meta", line));
    if (battle.battle_room_id) {
      const roomInfo = currentStrategyBattleRoomForBattle(battle);
      appendTextLine(card, "strategy-meta", `战场房间：${battle.battle_room_id}`);
      const attackerSummary = strategyRosterManifestSummary(roomInfo.attacker_roster_manifest);
      const defenderSummary = strategyRosterManifestSummary(roomInfo.defender_roster_manifest);
      if (attackerSummary) appendTextLine(card, "strategy-meta", `攻方单位：${attackerSummary}`);
      if (defenderSummary) appendTextLine(card, "strategy-meta", `守方单位：${defenderSummary}`);
      const actions = document.createElement("div");
      actions.className = "strategy-campaign-actions";
      const open = document.createElement("button");
      open.type = "button";
      open.className = battle.status === "resolved" ? "ghost" : "primary";
      open.textContent = battle.status === "resolved" ? "查看真实战斗" : battle.resolution_mode === "watch_ai" ? "观看 AI 战斗" : "进入真实战斗";
      open.disabled = state.strategyBusy;
      open.addEventListener("click", () => openStrategyBattleRoom(roomInfo));
      actions.append(open);
      if (battle.status === "pending" && faction?.id === battle.attacker_faction_id) {
        const auto = document.createElement("button");
        auto.type = "button";
        auto.className = "ghost";
        auto.textContent = "AI 推演";
        auto.disabled = state.strategyBusy || !canResume;
        auto.addEventListener("click", () => runAiBattleSimulation(
          strategyBattleId(battle),
          defaultBattleComposition(battle.attacker_troops, campaign?.world?.battle_unit_costs),
        ));
        actions.append(auto);
      }
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
      appendTextLine(card, "strategy-meta", "战事待决：可手动开战、快速结算、AI 推演、围城或撤退。");
      if (battle.attacker_faction_id === faction?.id) {
        const actions = document.createElement("div");
        actions.className = "strategy-campaign-actions";
        const handle = document.createElement("button");
        handle.type = "button";
        handle.className = "primary";
        handle.textContent = "处理这场战斗";
        handle.disabled = state.strategyBusy || !canResume;
        handle.addEventListener("click", () => {
          state.strategyNoticeKind = "";
          presentStrategyBattleChoiceNotice(campaign, faction, battle);
        });
        actions.append(handle);
        card.append(actions);
      }
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
    } else if (!strategyBattleResultLines(battle, campaign).length && Array.isArray(battle.report) && battle.report.length) {
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
      `${formatStrategyCalendar(campaign.world.current_month)} · ${campaign.world.cities.length} 城 · ${humanMembers.length}/${campaign.world.factions.length} 真人 · ${campaignStatus}`
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
  hideStrategyTechFloatTip();
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
      || state.strategyEndTurnPending
      || !loggedIn
      || !selected
      || !canResume
      || !strategyHostCanRequestAdvance(selected)
      || selected?.world?.strategic_status?.can_advance_month === false
      || !selectedIsOwner;
  }
  if (message) message.textContent = state.strategyMessage || "";
  if (!list || !current) return;

  const controlledHero = selected ? strategyControlledHero(selected) : null;
  const workspaceMode = !selected ? ""
    : selected.status === "lobby" ? "lobby"
      : controlledHero?.status === "roaming" ? "roaming"
        : "war";
  const keepWorkspace = Boolean(
    selected
    && workspaceMode === "war"
    && current.dataset.workspaceMode === "war"
    && String(current.dataset.campaignId || "") === String(selected.id)
  );
  list.innerHTML = "";
  if (!keepWorkspace) current.innerHTML = "";
  current.dataset.workspaceMode = workspaceMode;
  current.dataset.campaignId = selected ? String(selected.id) : "";
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
