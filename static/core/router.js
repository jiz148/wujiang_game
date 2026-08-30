// 屏幕路由。
//
// 这个应用原本只有 draft 和 battle 两屏，首页、登录、战役、房间大厅全部堆在
// draft 里靠 .hidden 互相遮挡，所以「什么时候显示什么」无从表达。这里把屏幕
// 提升为一等概念：同一时刻只有一个屏幕存在于视口内，切换是切换，不是滚动。
//
// 每个屏幕对应 DOM 里的 `#<name>-screen`。body 上会带 `screen-<name>`，
// 供样式按屏幕收敛 chrome（例如登录门不显示顶栏）。
import { state } from './state.js';

export const SCREENS = ["gate", "menu", "draft", "battle"];

const DEFAULT_SCREEN = "draft";

// draft 屏是个容器，同一个 `#draft` 可能是战役、遭遇战或档案中的任意一个，
// 重开时无从还原是哪一个——地址栏因此必须写玩家实际所在的去处，而不是容器名。
// 这也是先前那个 bug 的根源：旧的 screenHash() 把 menu 也写成 `#draft`，
// 于是主菜单会在地址栏留下 `#draft`，下次打开就直接跳进内层，退不回菜单。
export const FLOWS = ["campaign", "skirmish", "archive"];

// 左上角写的是"你在哪"。此前它恒为「武将」，于是战役和遭遇战顶着同一个标题，
// 而这两条路线除了共用一个外壳之外没有任何关系。
const ROUTE_TITLES = {
  campaign: "战役",
  skirmish: "战场对战",
  archive: "战绩与回放",
};

export function routeTitle(screen = state.screen, flow = state.homeFlow) {
  if (screen === "battle") return "战场";
  if (screen === "draft") return ROUTE_TITLES[flow] || "武将";
  return "武将";
}

/** 地址栏的 hash（不含 #）。draft 屏交由当前流程命名。 */
export function screenRoute(screen = state.screen, flow = state.homeFlow) {
  if (screen === "battle") return "battle";
  if (screen === "menu") return "menu";
  if (screen === "gate") return "";
  return FLOWS.includes(flow) ? flow : "menu";
}

/** hash -> {screen, flow}；无法识别的一律回主菜单，而不是掉进某个内层。 */
export function routeTarget(route) {
  const name = String(route || "").replace("#", "");
  if (name === "battle") return { screen: "battle", flow: "" };
  if (FLOWS.includes(name)) return { screen: "draft", flow: name };
  return { screen: "menu", flow: "" };
}

export function activeScreen() {
  return SCREENS.includes(state.screen) ? state.screen : DEFAULT_SCREEN;
}

/** 把 state.screen 反映到 DOM。只做显示，不做导航决策。 */
export function applyScreen() {
  const active = activeScreen();
  for (const name of SCREENS) {
    const node = document.getElementById(`${name}-screen`);
    if (node) node.classList.toggle("hidden", name !== active);
    document.body.classList.toggle(`screen-${name}`, name === active);
  }
  // 登录门与主菜单是全屏接管的，不带房间/战斗那套顶栏工具。
  document.body.classList.toggle("battle-mode", active === "battle");
  document.body.classList.toggle("chrome-hidden", active === "gate" || active === "menu");
  // 战役进行中会把外壳锁成一屏（见 body.campaign-mode）。开关由 renderStrategyPanel
  // 打开，但它只在 draft 屏跑得到；离开这一屏必须在这里关掉，否则主菜单会继承
  // 一个 height: 100vh 的外壳。
  if (active !== "draft") document.body.classList.remove("campaign-mode");
  // draft 是四条路线共用的容器，样式却常常只对其中一条成立（战役那条自带页头，
  // 共享的 section-head 于是成了同一个词的第二遍）。把路线也写到 body 上。
  for (const name of FLOWS) {
    document.body.classList.toggle(`flow-${name}`, active === "draft" && state.homeFlow === name);
  }
  const title = routeTitle(active, state.homeFlow);
  const brand = document.getElementById("brand-title");
  if (brand) brand.textContent = title;
  document.title = title === "武将" ? "武将" : `武将 · ${title}`;
  // 到这里屏幕归属已经确定，可以让外壳露面了。
  document.body.classList.remove("is-booting");
}
