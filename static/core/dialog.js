// 应用内确认弹窗。
//
// 删房间、离开房间、投降、退出登录这些不可撤销的动作此前都用 window.confirm。
// 浏览器原生弹窗有三个问题：它长得不像这个应用（浅色系统控件砸在暗色界面上）、
// 措辞和按钮文案改不了（只有"确定/取消"，说不出"删除房间"），而且它会阻塞整个
// 页面。既然要在关键处拦一道，这一道就该由我们自己画。
//
// 用法是 await 一个布尔值，替换 window.confirm 时调用点几乎不用改结构：
//   if (!(await confirmDialog({ ... }))) return;
import { createButton } from './components.js';

let activeDialog = null;

function teardown(result, resolve, returnFocus) {
  activeDialog?.remove();
  activeDialog = null;
  document.removeEventListener("keydown", onKeydown, true);
  // 焦点要回到那个触发它的按钮上，否则关掉之后 Tab 会从文档开头重新开始。
  window.requestAnimationFrame(() => returnFocus?.focus?.());
  resolve(result);
}

let onKeydown = () => {};

/**
 * 返回 Promise<boolean>：确认为 true，取消/Esc/点遮罩为 false。
 * tone 传 "danger" 时确认按钮显示为危险色，用于不可撤销的动作。
 */
export function confirmDialog({
  title = "确认操作",
  body = "",
  confirmLabel = "确定",
  cancelLabel = "取消",
  tone = "default",
} = {}) {
  // 同一时刻只允许一个：连点两次按钮不该叠出两层遮罩。
  if (activeDialog) return Promise.resolve(false);

  const returnFocus = document.activeElement;

  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "dialog-overlay";

    const card = document.createElement("div");
    card.className = "dialog";
    card.setAttribute("role", "alertdialog");
    card.setAttribute("aria-modal", "true");

    const heading = document.createElement("h2");
    heading.className = "dialog__title";
    heading.textContent = title;
    heading.id = "dialog-title";
    card.setAttribute("aria-labelledby", heading.id);
    card.append(heading);

    if (body) {
      const text = document.createElement("p");
      text.className = "dialog__body";
      text.textContent = body;
      text.id = "dialog-body";
      card.setAttribute("aria-describedby", text.id);
      card.append(text);
    }

    const actions = document.createElement("div");
    actions.className = "dialog__actions";
    const cancel = createButton({
      label: cancelLabel,
      variant: "subtle",
      onClick: () => teardown(false, resolve, returnFocus),
    });
    const accept = createButton({
      label: confirmLabel,
      variant: tone === "danger" ? "danger" : "primary",
      onClick: () => teardown(true, resolve, returnFocus),
    });
    actions.append(cancel, accept);
    card.append(actions);

    // 点遮罩等于取消；点卡片本身不该穿透到遮罩上。
    overlay.addEventListener("click", () => teardown(false, resolve, returnFocus));
    card.addEventListener("click", (event) => event.stopPropagation());

    onKeydown = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        teardown(false, resolve, returnFocus);
        return;
      }
      if (event.key !== "Tab") return;
      // 焦点困在弹窗里，否则 Tab 会走到背后那一屏上去。
      const focusable = [cancel, accept];
      const index = focusable.indexOf(document.activeElement);
      event.preventDefault();
      const next = event.shiftKey ? index - 1 : index + 1;
      focusable[(next + focusable.length) % focusable.length].focus();
    };
    // 捕获阶段：全局快捷键（空格暂停、方向键选格）不该在弹窗开着时还生效。
    document.addEventListener("keydown", onKeydown, true);

    overlay.append(card);
    document.body.append(overlay);
    activeDialog = overlay;
    // 默认落在取消上：危险动作不该被一次回车确认掉。
    cancel.focus();
  });
}

export function closeAppOverlay() {
  if (!activeDialog) return;
  if (typeof activeDialog.remove === "function") activeDialog.remove();
  activeDialog = null;
  if (typeof document.removeEventListener === "function") {
    document.removeEventListener("keydown", onKeydown, true);
  }
}

/**
 * 可承载自定义内容的应用内弹层，给战役结束、危机爆发这类必须看见的通知用。
 * actions.onClick 返回 false 时不关闭。
 */
export function openAppOverlay({
  title = "",
  body = null,
  className = "",
  actions = [],
  dismissible = false,
} = {}) {
  if (activeDialog) return false;
  if (typeof document === "undefined" || !document.body || typeof document.body.append !== "function") {
    return false;
  }

  const overlay = document.createElement("div");
  overlay.className = `dialog-overlay campaign-notice-overlay${className ? ` ${className}` : ""}`;

  const card = document.createElement("div");
  card.className = "dialog campaign-notice";
  if (typeof card.setAttribute === "function") {
    card.setAttribute("role", "alertdialog");
    card.setAttribute("aria-modal", "true");
  }

  if (title) {
    const heading = document.createElement("h2");
    heading.className = "dialog__title";
    heading.textContent = title;
    heading.id = "campaign-notice-title";
    if (typeof card.setAttribute === "function") card.setAttribute("aria-labelledby", heading.id);
    card.append(heading);
  }

  if (typeof body === "string" && body) {
    const text = document.createElement("p");
    text.className = "dialog__body";
    text.textContent = body;
    card.append(text);
  } else if (body) {
    const content = document.createElement("div");
    content.className = "campaign-notice__body";
    content.append(body);
    card.append(content);
  }

  const actionRow = document.createElement("div");
  actionRow.className = "dialog__actions";
  const buttons = [];
  actions.forEach((item) => {
    const button = createButton({
      label: item.label,
      variant: item.variant || (item.tone === "danger" ? "danger" : "primary"),
      onClick: async () => {
        const result = await item.onClick?.();
        if (result === false) return;
        closeAppOverlay();
      },
    });
    buttons.push(button);
    actionRow.append(button);
  });
  if (buttons.length) card.append(actionRow);

  overlay.addEventListener("click", () => {
    if (!dismissible) return;
    closeAppOverlay();
  });
  card.addEventListener("click", (event) => event.stopPropagation());

  onKeydown = (event) => {
    if (event.key === "Escape" && dismissible) {
      event.stopPropagation();
      closeAppOverlay();
      return;
    }
    if (event.key !== "Tab" || !buttons.length) return;
    const index = buttons.indexOf(document.activeElement);
    event.preventDefault();
    const next = event.shiftKey ? index - 1 : index + 1;
    buttons[(next + buttons.length) % buttons.length].focus();
  };
  document.addEventListener("keydown", onKeydown, true);

  overlay.append(card);
  document.body.append(overlay);
  activeDialog = overlay;
  buttons[0]?.focus();
  return true;
}
