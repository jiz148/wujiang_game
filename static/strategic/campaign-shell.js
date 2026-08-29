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
import { STRATEGY_DUTY_LABELS, strategyFactionCommandPoints, strategyOfficeLabel } from './ui-base.js';

const STATUS_LABELS = {
  serving: "仕官",
  roaming: "在野",
  sleeping: "沉睡",
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
    return `沉睡 · 第 ${hero.sleeping_until_month || "?"} 月醒`;
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

  // 专长决定这名武将擅长什么差事，也是未来"可选动作由技能派生"的接口。
  if (hero.specialty?.name) {
    const specialty = document.createElement("span");
    specialty.className = "hero-slot__specialty";
    specialty.textContent = hero.specialty.name;
    specialty.title = hero.specialty.effect || "";
    card.append(specialty);
  }

  const loyalty = document.createElement("span");
  loyalty.className = "hero-slot__loyalty";
  loyalty.style.setProperty("--fill", `${Math.max(0, Math.min(100, Number(hero.loyalty ?? 50)))}%`);
  loyalty.title = `忠诚 ${hero.loyalty ?? 50}（${hero.loyalty_band?.label || "稳定"}）`;
  card.append(loyalty);

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
export function renderCampaignHeroList(host, campaign, faction, { onSelect, selectedCode = "" } = {}) {
  const heroes = campaignFactionHeroes(campaign, faction);
  const list = document.createElement("div");
  list.className = "campaign-hero-list";
  if (!heroes.length) {
    const empty = document.createElement("p");
    empty.className = "campaign-hero-list__empty";
    empty.textContent = "本势力还没有武将。";
    list.append(empty);
  } else {
    heroes.forEach((hero) => list.append(heroCard(campaign, hero, {
      onSelect,
      selected: Boolean(selectedCode) && hero.code === selectedCode,
    })));
  }
  host.append(list);
}

/**
 * 顶部一条状态栏：我在哪局、我是谁、我有什么、这个月还剩多少军令、怎么结束这
 * 个月、怎么出去。这些此前分散在共享页头、按钮条、资源条和底部推进条四层里，
 * 加起来近 200px 都还没进入游戏。
 *
 * 推进与刷新沿用页头那两个按钮的禁用判断（房主、能否推进、开局待决都写在
 * renderStrategyPanel 里），这里只转发点击，判断仍旧只有一处。
 */
function createCampaignHud(campaign, faction, office) {
  const hud = document.createElement("header");
  hud.className = "campaign-hud";

  const identity = document.createElement("div");
  identity.className = "campaign-hud__identity";
  const name = document.createElement("strong");
  name.className = "campaign-hud__name";
  name.textContent = campaign.name || "战役";
  const role = document.createElement("span");
  role.className = "campaign-hud__role";
  role.textContent = `${faction?.name || "未绑定势力"} · ${office ? strategyOfficeLabel(office, campaign) : "在野"}`;
  identity.append(name, role);
  hud.append(identity);

  const monthLimit = campaign?.world?.strategic_status?.month_limit;
  const points = strategyFactionCommandPoints(campaign, faction);
  const facts = document.createElement("div");
  facts.className = "campaign-hud__facts";
  [
    ["月份", monthLimit ? `${campaign.world.current_month} / ${monthLimit}` : `第 ${campaign.world.current_month} 月`],
    ["军令", `${points.remaining} / ${points.maximum}`],
    ["粮", faction ? String(faction.resources.food) : "—"],
    ["钱", faction ? String(faction.resources.money) : "—"],
    ["以太", faction ? String(faction.resources.ether) : "—"],
    ["兵", faction ? String(faction.resources.troops) : "—"],
  ].forEach(([label, value]) => {
    const fact = document.createElement("div");
    fact.className = "campaign-hud__fact";
    const caption = document.createElement("span");
    caption.className = "campaign-hud__label";
    caption.textContent = label;
    const strong = document.createElement("strong");
    strong.className = "campaign-hud__value";
    strong.textContent = value;
    fact.append(caption, strong);
    facts.append(fact);
  });
  hud.append(facts);

  const actions = document.createElement("div");
  actions.className = "campaign-hud__actions";
  const source = {
    refresh: $("strategy-refresh"),
    advance: $("strategy-advance-month"),
    exit: $("strategy-exit-campaign"),
  };
  actions.append(
    createButton({
      label: "刷新",
      variant: "subtle",
      size: "sm",
      disabled: Boolean(source.refresh?.disabled),
      onClick: () => source.refresh?.click(),
    }),
    createButton({
      label: "推进一月",
      variant: "primary",
      size: "sm",
      disabled: Boolean(source.advance?.disabled),
      onClick: () => source.advance?.click(),
    }),
    createButton({
      label: "战役列表",
      variant: "subtle",
      size: "sm",
      onClick: () => source.exit?.click(),
    }),
  );
  hud.append(actions);
  return hud;
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
  dock.className = `campaign-dock${state.strategyDockOpen ? " is-open" : " is-collapsed"}`;
  dock.setAttribute("aria-label", "战役操作面板");

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
  collapse.title = state.strategyDockOpen ? "收起操作面板" : "展开操作面板";
  collapse.textContent = state.strategyDockOpen ? "›" : "‹";
  collapse.addEventListener("click", () => {
    state.strategyDockOpen = !state.strategyDockOpen;
    applyDockState(dock, tabs);
  });
  tabs.append(collapse);

  const body = document.createElement("div");
  body.className = "campaign-dock__body";
  const active = available.find((item) => item.id === activeId);
  if (active) {
    const head = document.createElement("div");
    head.className = "campaign-dock__head";
    const title = document.createElement("h4");
    title.textContent = active.title || active.label;
    head.append(title);
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

  dock.append(tabs, body);
  return dock;
}

function applyDockState(dock, tabs) {
  dock.classList.toggle("is-open", state.strategyDockOpen);
  dock.classList.toggle("is-collapsed", !state.strategyDockOpen);
  const toggle = tabs.querySelector(".campaign-dock__toggle");
  if (toggle) {
    toggle.textContent = state.strategyDockOpen ? "›" : "‹";
    toggle.title = state.strategyDockOpen ? "收起操作面板" : "展开操作面板";
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
  const screen = document.createElement("section");
  screen.className = "campaign-screen";
  screen.append(createCampaignHud(campaign, faction, office));

  const stage = document.createElement("div");
  stage.className = "campaign-stage";
  renderMap(stage);
  const dock = createCampaignDock(modules);
  stage.append(dock);
  if (typeof onDockChange === "function") {
    dock.addEventListener("campaign-dock-change", onDockChange);
  }
  screen.append(stage);
  host.append(screen);
}
