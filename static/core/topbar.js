// 顶栏：全站共享的外壳，不属于任何一个游戏域。
//
// 这里原本是六个并排的按钮——主菜单、房间大厅、返回战场、复制邀请链接、昵称、
// 修改昵称——各自靠 .hidden 出没。并排摆开的代价是看不出主次：真正要紧的
// "回到你正在打的那一局"和"退出登录"长得一模一样，而多数时候大半个按钮条
// 都是隐藏的空位。
//
// 现在只留两样：当前上下文里唯一要紧的那个动作，以及一个收着其余操作的账号
// 菜单。次要操作藏进下拉不是为了省地方，而是为了让剩下露在外面的那个有分量。
import { createMenu } from './components.js';
import { confirmDialog } from './dialog.js';
import { $ } from './dom.js';
import { state } from './state.js';
import { setScreen } from './ui.js';
import { effectiveProfileName, openProfileModal } from '../platform/auth.js';
import { logoutAuth } from '../platform/home.js';

let accountMenu = null;
let primaryButton = null;

async function confirmLogout() {
  const ok = await confirmDialog({
    title: "退出登录",
    body: "退出后需要重新登录才能继续。进行中的战役与房间会保留，用同一账号登录即可恢复。",
    confirmLabel: "退出登录",
    tone: "danger",
  });
  if (ok) logoutAuth();
}

function build(host) {
  host.replaceChildren();

  primaryButton = document.createElement("button");
  primaryButton.type = "button";
  primaryButton.id = "topbar-primary";
  primaryButton.className = "btn btn--sm hidden";

  // 三项，从"我是谁"到"我要走"：账号 → 导航 → 登出。
  // 此前还有"房间大厅"和"复制邀请链接"两项。前者的显示条件与左边那个上下文
  // 主按钮完全相同（battle 屏且有房间），等于同一件事摆了两遍；后者在房间头部
  // 的"更多"里已经有了。账号菜单收的应该是账号相关的东西。
  accountMenu = createMenu({
    label: "",
    ariaLabel: "账号与导航",
    items: [
      { id: "profile", label: "用户信息", onClick: () => openProfileModal() },
      { id: "menu", label: "返回主菜单", onClick: () => {
        state.strategyCampaign = null;
        state.strategySelectedCityId = "";
        state.strategySelectedCampaignId = 0;
        state.strategyCreateOpen = false;
        state.strategyNoticeKind = "";
        setScreen("menu");
      } },
      { separator: true },
      { id: "logout", label: "退出登录", tone: "danger", onClick: () => confirmLogout() },
    ],
  });
  accountMenu.id = "account-menu";

  host.append(primaryButton, accountMenu);
}

/**
 * 右上角不再放房间/战场跳转。进战场在房间页，回大厅在战场「更多」里。
 * 这里留下钩子，以免以后要再挂一个全站级动作时又去改结构。
 */
function primaryAction() {
  return null;
}

export function renderTopbar() {
  const host = $("topbar-tools");
  if (!host) return;
  if (!accountMenu || !host.contains(accountMenu)) build(host);

  const action = primaryAction();
  primaryButton.classList.toggle("hidden", !action);
  if (action) {
    primaryButton.textContent = action.label;
    primaryButton.classList.toggle("btn--primary", action.variant === "primary");
    primaryButton.classList.toggle("btn--subtle", action.variant !== "primary");
    primaryButton.onclick = action.go;
  }

  accountMenu.setLabel(effectiveProfileName());
  // 菜单项按当前是否可用出没，而不是常驻一个点了没反应的入口。
  accountMenu.itemEls.get("menu")?.classList.toggle("hidden", state.screen === "menu");
}
