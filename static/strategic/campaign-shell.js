// 战役屏的外壳：整屏地图 + 浮在地图上的操作面板。
//
// 这一屏此前是三栏并排——左边武将、中间地图、右边城市军令。三栏的代价是地图只
// 分到屏幕中间一条，而地图才是战役里唯一的主对象：城市、军队、路线、危机全都
// 长在它上面，其余面板讲的都是"你在地图上选中的那个东西"。
//
// 所以现在地图铺满整屏并且可以拖拽缩放，操作收进一层浮在地图上、可以整体收起
// 的面板；面板内部按模块分页，一次只回答一个问题：这座城怎么办、我有哪些人、
// 这个月排了什么、势力家底如何、最近发生了什么。
import { createButton } from '../core/components.js';
import { $ } from '../core/dom.js';
import { state } from '../core/state.js';
import { STRATEGY_DUTY_LABELS, appendStrategySkillTags, formatStrategyCalendar, strategyFactionCommandPoints, strategyOfficeLabel } from './ui-base.js';

const STATUS_LABELS = {
  serving: "仕官",
  roaming: "在野",
  sleeping: "负伤",
};

function cityName(campaign, cityId) {
  if (!cityId) return "";
  return (campaign?.world?.cities || []).find((city) => city.id === cityId)?.name || cityId;
}

/**
 * 武将此刻在做什么，一句话。
 * 返回 null 表示闲置——那正是名单上要高亮的东西。
 */
function heroDuty(campaign, hero) {
  if (hero.status === "sleeping") {
    return `负伤 · 第 ${hero.sleeping_until_month || "?"} 月复原`;
  }
  if (hero.office_id) {
    const office = (campaign?.world?.offices || []).find((item) => item.id === hero.office_id);
    const label = strategyOfficeLabel(office, campaign);
    const city = cityName(campaign, office?.city_id);
    return city ? `${label} · ${city}` : label;
  }
  if (hero.assignment_type && hero.assignment_type !== "reserve") {
    const duty = STRATEGY_DUTY_LABELS[hero.assignment_type] || hero.assignment_type;
    const target = cityName(campaign, hero.assignment_target_id);
    return target ? `${duty} · ${target}` : duty;
  }
  if (hero.ritual_city_id) {
    return `祭祀 · ${cityName(campaign, hero.ritual_city_id)}`;
  }
  return null;
}

export function campaignFactionHeroes(campaign, faction) {
  return Array.isArray(faction?.strategic_heroes)
    ? faction.strategic_heroes
    : (campaign?.world?.strategic_hero_pool || []).filter((hero) => hero.home_faction_id === faction?.id);
}

export function campaignIdleHeroCount(campaign, faction) {
  return campaignFactionHeroes(campaign, faction)
    .filter((hero) => hero.status === "serving" && heroDuty(campaign, hero) === null).length;
}

function heroCard(campaign, hero, { onSelect, selected } = {}) {
  const duty = heroDuty(campaign, hero);
  const idle = duty === null && hero.status === "serving";

  const card = document.createElement("article");
  card.className = "hero-slot";
  card.dataset.heroCode = hero.code;
  if (idle) card.classList.add("is-idle");
  if (hero.status === "sleeping") card.classList.add("is-wounded");
  if (hero.defender_assigned) card.classList.add("is-defending");
  if (typeof onSelect === "function") {
    card.classList.add("is-selectable");
    if (selected) card.classList.add("is-selected");
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.setAttribute("aria-pressed", selected ? "true" : "false");
    card.title = selected ? "收起武将详情" : "查看武将详情";
    card.addEventListener("click", () => onSelect(hero.code));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onSelect(hero.code);
      }
    });
  }

  const head = document.createElement("div");
  head.className = "hero-slot__head";
  const name = document.createElement("strong");
  name.className = "hero-slot__name";
  name.textContent = hero.name || hero.code;
  head.append(name);
  if (hero.status === "sleeping") {
    const mark = document.createElement("span");
    mark.className = "hero-slot__mark is-wounded";
    mark.textContent = "伤";
    mark.title = `负伤中 · 第 ${hero.sleeping_until_month || "?"} 月复原`;
    head.append(mark);
  }
  if (hero.defender_assigned) {
    const mark = document.createElement("span");
    mark.className = "hero-slot__mark";
    mark.textContent = "守";
    mark.title = "默认出战防守";
    head.append(mark);
  }

  const duties = document.createElement("span");
  duties.className = "hero-slot__duty";
  duties.textContent = duty || (hero.status === "serving" ? "闲置" : STATUS_LABELS[hero.status] || hero.status);

  card.append(head, duties);

  appendStrategySkillTags(card, hero, { compact: true });

  const loyalty = document.createElement("span");
  loyalty.className = "hero-slot__loyalty";
  loyalty.style.setProperty("--fill", `${Math.max(0, Math.min(100, Number(hero.loyalty ?? 50)))}%`);
  loyalty.title = `忠诚 ${hero.loyalty ?? 50}（${hero.loyalty_band?.label || "稳定"}）`;
  card.append(loyalty);

  if (hero.personal_mission) {
    const statusLabels = { active: "进行中", completed: "已完成", failed: "已逾期" };
    const mission = document.createElement("span");
    mission.className = "hero-slot__mission";
    const progress = `${hero.personal_mission.progress ?? 0}/${hero.personal_mission.required ?? 0}`;
    const due = hero.personal_mission.due_month ? ` · 截止第 ${hero.personal_mission.due_month} 月` : "";
    mission.textContent = `${hero.personal_mission.name} · ${statusLabels[hero.personal_mission.status] || hero.personal_mission.status} · ${progress}${due}`;
    card.append(mission);
  }

  return card;
}

/**
 * 武将名单。
 *
 * 这不只是一份花名册：可执行的动作最终由武将技能派生，所以"还有几个人没派活"
 * 就是"还剩几个动作"。预算画在屏幕上，而不是写成一行数字。
 *
 * 名单只给一行状态；档案要点开某个人才展开，一屏之内不会同时铺开十几份履历。
 */
export function renderCampaignHeroList(host, campaign, faction, { onSelect, selectedCode = "", renderDetail } = {}) {
  const heroes = campaignFactionHeroes(campaign, faction);
  const list = document.createElement("div");
  list.className = "campaign-hero-list";
  if (!heroes.length) {
    const empty = document.createElement("p");
    empty.className = "campaign-hero-list__empty";
    empty.textContent = "本势力还没有武将。";
    list.append(empty);
  } else {
    heroes.forEach((hero) => {
      const selected = Boolean(selectedCode) && hero.code === selectedCode;
      list.append(heroCard(campaign, hero, { onSelect, selected }));
      if (selected && typeof renderDetail === "function") {
        const detail = document.createElement("div");
        detail.className = "hero-slot__detail strategy-hero-detail";
        renderDetail(detail, hero);
        list.append(detail);
      }
    });
  }
  host.append(list);
}

const HUD_ICONS = {
  month: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="3" width="12" height="11" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M2 6.5h12M5 1.8v2.6M11 1.8v2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  command: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 13.2 8 2.6l5 10.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M5.2 9.6h5.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  food: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 13.4V7.2M4.6 13.2c0-3.4 1.5-5.2 3.4-6M11.4 13.2c0-3.4-1.5-5.2-3.4-6M8 7.2c1.6-1.7 1.6-3.8 0-5.2C6.4 3.4 6.4 5.5 8 7.2Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  money: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.2" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 5.1v5.8M6.3 6.4c.5-.7 1.3-1 1.7-1 .9 0 1.6.5 1.6 1.3 0 1.8-3.3 1.1-3.3 2.7 0 .8.8 1.4 1.7 1.4.6 0 1.2-.3 1.6-.8" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
  ether: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.4 12.6 8 8 13.6 3.4 8Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 5.2 10.4 8 8 10.8 5.6 8Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>',
  troops: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.6 13.2 5v3.2c0 3.2-2.1 5-5.2 5.8C4.9 13.2 2.8 11.4 2.8 8.2V5Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.1v3.4M6.4 7.8h3.2" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
};

/**
 * 资源条浮在战略地图左上角：只报月份、军令和四项资源，不再占掉一整行顶栏。
 */
function createCampaignHud(campaign, faction) {
  const hud = document.createElement("header");
  hud.className = "campaign-hud";

  const points = strategyFactionCommandPoints(campaign, faction);
  const facts = document.createElement("div");
  facts.className = "campaign-hud__facts";
  [
    ["month", "年月", formatStrategyCalendar(campaign.world.current_month)],
    ["command", "军令", `${points.remaining} / ${points.maximum}`],
    ["money", "钱", faction ? String(faction.resources.money) : "—"],
    ["food", "粮", faction ? String(faction.resources.food) : "—"],
    ["troops", "兵", faction ? String(faction.resources.troops) : "—"],
    ["ether", "以太", faction ? String(faction.resources.ether) : "—"],
  ].forEach(([icon, label, value]) => {
    const fact = document.createElement("div");
    fact.className = "campaign-hud__fact";
    const mark = document.createElement("span");
    mark.className = `campaign-hud__icon campaign-hud__icon--${icon}`;
    mark.innerHTML = HUD_ICONS[icon];
    mark.setAttribute("aria-hidden", "true");
    const caption = document.createElement("span");
    caption.className = "campaign-hud__label";
    caption.textContent = label;
    const strong = document.createElement("strong");
    strong.className = "campaign-hud__value";
    strong.textContent = value;
    fact.append(mark, caption, strong);
    facts.append(fact);
  });
  hud.append(facts);

  const source = campaignChromeButtons();
  const endTurn = document.createElement("button");
  endTurn.type = "button";
  const endingTurn = Boolean(state.strategyEndTurnPending || state.strategyBusy);
  endTurn.className = endingTurn ? "campaign-end-turn is-loading" : "campaign-end-turn";
  endTurn.disabled = Boolean(source.advance?.disabled) || endingTurn;
  endTurn.setAttribute("aria-label", endingTurn ? "正在结算回合" : "回合结束");
  const line1 = document.createElement("span");
  line1.textContent = endingTurn ? "结算" : "回合";
  const line2 = document.createElement("span");
  line2.textContent = endingTurn ? "中" : "结束";
  endTurn.append(line1, line2);
  endTurn.addEventListener("click", () => {
    if (endTurn.disabled || state.strategyEndTurnPending || state.strategyBusy) return;
    source.advance?.click();
  });
  hud.append(endTurn);
  return hud;
}

function createCampaignNotice() {
  const text = String(state.strategyMessage || "").trim();
  if (!text) return null;
  const notice = document.createElement("div");
  notice.className = "campaign-stage-notice";
  notice.setAttribute("role", "status");
  const body = document.createElement("p");
  body.className = "campaign-stage-notice__text";
  body.textContent = text;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "campaign-stage-notice__close";
  close.setAttribute("aria-label", "关闭提示");
  close.textContent = "×";
  close.addEventListener("click", () => {
    state.strategyMessage = "";
    notice.remove();
  });
  notice.append(body, close);
  return notice;
}

function syncCampaignNotice(stage) {
  stage.querySelector(".campaign-stage-notice")?.remove();
  const notice = createCampaignNotice();
  if (notice) stage.append(notice);
}

function createCampaignTurnToast() {
  const toastState = state.strategyTurnToast;
  if (!toastState) return null;
  const toast = document.createElement("div");
  toast.className = "campaign-turn-toast";
  const title = document.createElement("strong");
  title.textContent = `第${toastState.year}年 · ${toastState.monthName}`;
  const detail = document.createElement("span");
  detail.textContent = `第 ${toastState.turn} 回合`;
  toast.append(title, detail);
  return toast;
}

function campaignChromeButtons() {
  return {
    refresh: $("strategy-refresh"),
    advance: $("strategy-advance-month"),
    exit: $("strategy-exit-campaign"),
  };
}

function moreToggleLabel(kind, prefs) {
  if (kind === "sound") return `声音：${prefs.sound ? "开" : "关"}`;
  if (kind === "colorblind") return `色弱高对比：${prefs.colorblind ? "开" : "关"}`;
  if (kind === "combatFeed") return `行动记录：${prefs.combatFeed === false ? "关" : "开"}`;
  const motion = prefs.motion || "system";
  const motionLabel = motion === "system" ? "跟随系统" : (motion === "reduce" ? "减少" : "完整");
  return `动态：${motionLabel}`;
}

/**
 * 战役「更多」与战场同一套系统设置，再加返回大厅。
 */
export function renderCampaignMorePanel(host) {
  const prefs = globalThis.WujiangBattleFeedback?.preferences?.() || {};
  const toolbar = document.createElement("div");
  toolbar.className = "campaign-more-toolbar";
  [
    ["sound", "sound"],
    ["colorblind", "colorblind"],
    ["combatFeed", "combatFeed"],
    ["motion", "motion"],
  ].forEach(([kind]) => {
    toolbar.append(createButton({
      label: moreToggleLabel(kind, prefs),
      variant: "subtle",
      size: "sm",
      onClick: () => {
        globalThis.WujiangBattleFeedback?.toggle(kind);
        const next = globalThis.WujiangBattleFeedback?.preferences?.() || prefs;
        const button = toolbar.querySelector(`[data-more-toggle="${kind}"]`);
        if (button) button.textContent = moreToggleLabel(kind, next);
      },
      dataset: { moreToggle: kind },
    }));
  });
  toolbar.append(createButton({
    label: "键盘帮助",
    variant: "subtle",
    size: "sm",
    onClick: () => $("open-keyboard-help")?.click(),
  }));
  host.append(toolbar);

  const divider = document.createElement("hr");
  divider.className = "campaign-more-divider";
  host.append(divider, createButton({
    label: "返回战役大厅",
    variant: "subtle",
    block: true,
    className: "campaign-more-exit",
    onClick: () => campaignChromeButtons().exit?.click(),
  }));
}

/**
 * 操作面板。
 *
 * 它浮在地图上而不是占掉一栏，因为它讲的每一件事都指向地图上的某个东西；收起
 * 之后整张图都在，展开之后也不必离开地图去别处找按钮。
 */
function createCampaignDock(modules) {
  const available = modules.filter(Boolean);
  const activeId = available.some((item) => item.id === state.strategyDockTab)
    ? state.strategyDockTab
    : available[0]?.id || "";
  state.strategyDockTab = activeId;

  const dock = document.createElement("aside");
  dock.className = [
    "campaign-dock",
    state.strategyDockOpen ? "is-open" : "is-collapsed",
    state.strategyDockOpen && state.strategyDockWide ? "is-wide" : "",
  ].filter(Boolean).join(" ");
  dock.setAttribute("aria-label", "战役操作面板");

  const rail = document.createElement("div");
  rail.className = "campaign-dock__rail";

  const tabs = document.createElement("nav");
  tabs.className = "campaign-dock__tabs";
  tabs.setAttribute("role", "tablist");
  available.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `campaign-dock__tab${item.id === activeId && state.strategyDockOpen ? " is-active" : ""}`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", item.id === activeId ? "true" : "false");
    const label = document.createElement("span");
    label.className = "campaign-dock__tab-label";
    label.textContent = item.label;
    button.append(label);
    if (item.badge) {
      const badge = document.createElement("span");
      badge.className = "campaign-dock__tab-badge";
      badge.textContent = String(item.badge);
      button.append(badge);
    }
    button.addEventListener("click", () => {
      // 点当前这一页等于把面板收起来——同一个按钮，开与关。
      const shouldClose = state.strategyDockOpen && state.strategyDockTab === item.id;
      state.strategyDockOpen = !shouldClose;
      state.strategyDockTab = item.id;
      applyDockState(dock, tabs);
    });
    tabs.append(button);
  });

  const collapse = document.createElement("button");
  collapse.type = "button";
  collapse.className = "campaign-dock__toggle";
  collapse.setAttribute("aria-expanded", state.strategyDockOpen ? "true" : "false");
  collapse.title = !state.strategyDockOpen ? "展开操作面板" : state.strategyDockWide ? "收起操作面板" : "更宽模式";
  collapse.textContent = !state.strategyDockOpen ? "‹" : state.strategyDockWide ? "›" : "»";
  collapse.addEventListener("click", () => {
    if (!state.strategyDockOpen) {
      state.strategyDockOpen = true;
      state.strategyDockWide = false;
    } else if (!state.strategyDockWide) {
      state.strategyDockWide = true;
    } else {
      state.strategyDockOpen = false;
      state.strategyDockWide = false;
    }
    applyDockState(dock, tabs);
  });
  tabs.append(collapse);

  const body = document.createElement("div");
  body.className = "campaign-dock__body";
  const active = available.find((item) => item.id === activeId);
  if (active) {
    const head = document.createElement("div");
    head.className = "campaign-dock__head";
    const titleRow = document.createElement("div");
    titleRow.className = "campaign-dock__title-row";
    if (active.titleTag?.label) {
      const tag = document.createElement("em");
      tag.className = "strategy-city-nation-tag";
      const color = active.titleTag.color || "#9d9681";
      if (typeof tag.style?.setProperty === "function") tag.style.setProperty("--faction-color", color);
      else tag.style["--faction-color"] = color;
      tag.textContent = active.titleTag.label;
      titleRow.append(tag);
    }
    const title = document.createElement("h4");
    title.textContent = active.title || active.label;
    titleRow.append(title);
    head.append(titleRow);
    if (active.caption) {
      const caption = document.createElement("span");
      caption.className = "campaign-dock__caption";
      caption.textContent = active.caption;
      head.append(caption);
    }
    body.append(head);
    // 也带上 strategy-command-panel：面板内部那些工作台组件（职位命令行、军团
    // 名册、圣物操作…）的样式都挂在这个类下，换个容器名会把它们全都撕掉。
    const page = document.createElement("div");
    page.className = "campaign-dock__page strategy-command-panel";
    active.render(page);
    if (!page.children.length) {
      const empty = document.createElement("p");
      empty.className = "campaign-dock__empty";
      empty.textContent = "这里当前没有可处理的内容。";
      page.append(empty);
    }
    body.append(page);
  }

  rail.append(tabs);
  dock.append(rail, body);
  return dock;
}

function applyDockState(dock, tabs) {
  dock.classList.toggle("is-open", state.strategyDockOpen);
  dock.classList.toggle("is-collapsed", !state.strategyDockOpen);
  dock.classList.toggle("is-wide", Boolean(state.strategyDockOpen && state.strategyDockWide));
  const toggle = tabs.querySelector(".campaign-dock__toggle");
  if (toggle) {
    toggle.textContent = !state.strategyDockOpen ? "‹" : state.strategyDockWide ? "›" : "»";
    toggle.title = !state.strategyDockOpen ? "展开操作面板" : state.strategyDockWide ? "收起操作面板" : "更宽模式";
    toggle.setAttribute("aria-expanded", state.strategyDockOpen ? "true" : "false");
  }
  // 内容随页签变，交给整屏重绘；这里只负责开关立刻有反应。
  if (typeof dock.dispatchEvent === "function") {
    dock.dispatchEvent(new CustomEvent("campaign-dock-change", { bubbles: true }));
  }
}

/**
 * 一整屏战役：状态栏 + 地图 + 浮层面板。
 *
 * renderMap 由战略域提供，modules 是浮层里的分页；外壳本身不知道战役规则。
 */
export function renderCampaignScreen(host, { campaign, faction, office, renderMap, modules, onDockChange }) {
  const campaignId = String(campaign?.id || "");
  let screen = host.querySelector(":scope > .campaign-screen");
  const reuse = Boolean(screen && screen.dataset.campaignId === campaignId && screen.querySelector(".campaign-stage"));
  if (!reuse) {
    host.replaceChildren();
    screen = document.createElement("section");
    screen.className = "campaign-screen";
    screen.dataset.campaignId = campaignId;
    const stage = document.createElement("div");
    stage.className = "campaign-stage";
    renderMap(stage);
    stage.append(createCampaignHud(campaign, faction));
    syncCampaignNotice(stage);
    const toast = createCampaignTurnToast();
    if (toast) stage.append(toast);
    const dock = createCampaignDock(modules);
    stage.append(dock);
    if (typeof onDockChange === "function") {
      dock.addEventListener("campaign-dock-change", onDockChange);
    }
    screen.append(stage);
    host.append(screen);
    return;
  }

  const stage = screen.querySelector(".campaign-stage");
  renderMap(stage);
  const hud = createCampaignHud(campaign, faction);
  const oldHud = stage.querySelector(".campaign-hud");
  if (oldHud) oldHud.replaceWith(hud);
  else stage.append(hud);
  syncCampaignNotice(stage);
  stage.querySelector(".campaign-turn-toast")?.remove();
  const toast = createCampaignTurnToast();
  if (toast) stage.append(toast);
  const dock = createCampaignDock(modules);
  const oldDock = stage.querySelector(".campaign-dock");
  if (oldDock) oldDock.replaceWith(dock);
  else stage.append(dock);
  if (typeof onDockChange === "function") {
    dock.addEventListener("campaign-dock-change", onDockChange);
  }
}
