// 主菜单。
//
// 这里刻意不区分单人与多人：一局战役天然带槽位，AI 只是默认占位者，真人可以
// 顶替、中途加入、离开或纯观战。所以入口只有「继续 / 新建 / 用邀请码加入」，
// 「有没有真人」是对局内部的状态，不是玩家在菜单上要做的选择。
//
// render() 高频重跑，因此结构只建一次，之后只更新会变的文案与可用性。
import { createButton } from '../core/components.js';
import { $ } from '../core/dom.js';
import { state } from '../core/state.js';
import { setScreen } from '../core/ui.js';
import { leaveRoomView } from '../tactical/session.js';
import { logoutAuth } from './home.js';

let root = null;
let entries = new Map();
let accountName = null;
let resumeMeta = null;

// 从主菜单选一个去处，就意味着离开你正在看的东西。不摘掉房间的话，地址栏上
// 残留的 ?room= 会让房间大厅把目标流程盖掉——点战役却落进遭遇战的房间。
// 房间和席位都还在，可以从遭遇战的房间列表或邀请链接回去。
function goto(flow) {
  state.homeFlow = flow;
  leaveRoomView();
  setScreen("draft");
}

// 两种玩法并列：战役是大地图经营，遭遇战是单独一场战斗。后者不是前者的简化版，
// 它有自己的入口、自己的房间，同样能让真人顶替 AI 席位——只是不经营地图。
const ENTRY_DEFS = [
  {
    id: "resume",
    title: "继续战役",
    desc: "回到上一场未完成的战役",
    variant: "primary",
    onClick: () => goto("campaign"),
  },
  {
    id: "campaign",
    title: "战役",
    desc: "在大地图上经营、外交与开战",
    onClick: () => goto("campaign"),
  },
  {
    id: "skirmish",
    title: "遭遇战",
    desc: "跳过经营，直接打一场；可独自对 AI，也可开房邀人",
    onClick: () => goto("skirmish"),
  },
  {
    id: "tutorial",
    title: "新手教学",
    desc: "用固定阵容走一遍战场操作",
    onClick: () => goto("tutorial"),
  },
  {
    id: "archive",
    title: "战绩与回放",
    desc: "查看历史对局、回放与武将熟练度",
    onClick: () => goto("archive"),
  },
];

function build(host) {
  host.replaceChildren();
  entries = new Map();

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

    const desc = document.createElement("span");
    desc.className = "menu-entry__desc";
    desc.textContent = def.desc;

    entry.append(caption, desc);
    if (def.id === "resume") {
      resumeMeta = document.createElement("span");
      resumeMeta.className = "menu-entry__meta";
      entry.append(resumeMeta);
    }
    list.append(entry);
    entries.set(def.id, entry);
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

  const campaigns = state.strategyCampaigns || [];
  const resumable = campaigns.find((campaign) => campaign.status !== "archived") || campaigns[0];
  const resume = entries.get("resume");
  if (resume) {
    resume.disabled = !resumable;
    resume.classList.toggle("is-unavailable", !resumable);
  }
  if (resumeMeta) {
    resumeMeta.textContent = resumable
      ? `${resumable.name || "未命名战役"} · 第 ${resumable.world?.current_month ?? 1} 月`
      : "暂无进行中的战役";
  }

  const tutorial = entries.get("tutorial");
  if (tutorial) {
    tutorial.classList.toggle("is-unavailable", false);
  }

  if (accountName) accountName.textContent = state.authUser?.username || "";
}
