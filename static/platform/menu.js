// 主菜单。
//
// 这里刻意不区分单人与多人：一局战役天然带槽位，AI 只是默认占位者，真人可以
// 顶替、中途加入、离开或纯观战。「有没有真人」是对局内部的状态，不是玩家在
// 菜单上要做的选择。
//
// render() 高频重跑，因此结构只建一次，之后只更新会变的文案与可用性。
import { createButton } from '../core/components.js';
import { $ } from '../core/dom.js';
import { state } from '../core/state.js';
import { setScreen } from '../core/ui.js';
import { leaveRoomView } from '../tactical/session.js';
import { logoutAuth } from './home.js';

let root = null;
let accountName = null;

// 从主菜单选一个去处，就意味着离开你正在看的东西。不摘掉房间的话，地址栏上
// 残留的 ?room= 会让房间大厅把目标流程盖掉——点战役却落进遭遇战的房间。
// 房间和席位都还在，可以从遭遇战的房间列表或邀请链接回去。
function leaveSelectedCampaign() {
  // 主菜单的「战役」必须落在大厅/列表，不能带着上一局里选中的存档直接 resume。
  state.strategyCampaign = null;
  state.strategySelectedCityId = "";
  state.strategySelectedCampaignId = 0;
  state.strategyCreateOpen = false;
  state.strategyNoticeKind = "";
}

function goto(flow) {
  state.homeFlow = flow;
  leaveRoomView();
  if (flow === "campaign") leaveSelectedCampaign();
  setScreen("draft");
}

// 战役是大地图。房间大厅是一场一场的对战：模式在创建时选定，而不是进房后再改。
const ENTRY_DEFS = [
  {
    id: "campaign",
    title: "战役",
    desc: "大地图经营",
    onClick: () => goto("campaign"),
  },
  {
    id: "skirmish",
    title: "房间大厅",
    desc: "创建或加入一局",
    onClick: () => goto("skirmish"),
  },
  {
    id: "library",
    title: "数据库",
    desc: "武将与回放",
    onClick: () => {
      state.libraryTab = "heroes";
      goto("library");
    },
  },
  {
    id: "settings",
    title: "设置",
    desc: "",
    onClick: () => goto("settings"),
  },
];

function build(host) {
  host.replaceChildren();

  const layout = document.createElement("div");
  layout.className = "menu-layout";

  const head = document.createElement("div");
  head.className = "menu-head";
  const title = document.createElement("h1");
  title.id = "menu-title";
  title.className = "menu-title";
  title.textContent = "武将";
  head.append(title);

  const list = document.createElement("div");
  list.className = "menu-list";

  for (const def of ENTRY_DEFS) {
    const entry = document.createElement("button");
    entry.type = "button";
    entry.className = "menu-entry";
    if (def.variant === "primary") entry.classList.add("menu-entry--primary");
    entry.addEventListener("click", def.onClick);

    const caption = document.createElement("span");
    caption.className = "menu-entry__title";
    caption.textContent = def.title;

    entry.append(caption);
    if (def.desc) {
      const desc = document.createElement("span");
      desc.className = "menu-entry__desc";
      desc.textContent = def.desc;
      entry.append(desc);
    }
    list.append(entry);
  }

  const account = document.createElement("div");
  account.className = "menu-account";
  accountName = document.createElement("span");
  accountName.className = "menu-account__name";
  account.append(
    accountName,
    createButton({ label: "退出登录", variant: "subtle", size: "sm", onClick: () => logoutAuth() }),
  );

  layout.append(head, list, account);
  host.append(layout);
}

export function renderMenu() {
  const host = $("menu-screen");
  if (!host) return;
  if (!root || !host.contains(root)) {
    build(host);
    root = host.firstElementChild;
  }

  if (accountName) accountName.textContent = state.authUser?.username || "";
}
