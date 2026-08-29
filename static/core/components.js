// 通用 UI 组件工厂。
//
// 界面几乎全部是命令式拼 DOM，所以光有 CSS 类推不动统一：每个新面板还是会各写
// 各的 createElement。这里把组件的结构、类名和无障碍属性收在一处，调用方只描述
// 意图。样式定义在 theme.css，两边的类名必须保持一致。
//
// 本模块不依赖任何其他模块，可以被任意层级安全引入。

function applyCommon(node, { id, title, className, dataset } = {}) {
  if (id) node.id = id;
  if (title) node.title = title;
  if (className) node.classList.add(...className.split(" ").filter(Boolean));
  if (dataset) {
    for (const [key, value] of Object.entries(dataset)) node.dataset[key] = value;
  }
  return node;
}

/**
 * 按钮。variant 决定语义，size 决定高度，两者正交。
 * accent 可覆盖强调色，避免为个别按钮再写一套背景。
 */
export function createButton({
  label = "",
  variant = "default",
  size = "md",
  onClick,
  type = "button",
  block = false,
  icon = false,
  disabled = false,
  accent,
  ariaLabel,
  ...rest
} = {}) {
  const button = document.createElement("button");
  button.type = type;
  button.className = "btn";
  if (variant && variant !== "default") button.classList.add(`btn--${variant}`);
  if (size && size !== "md") button.classList.add(`btn--${size}`);
  if (block) button.classList.add("btn--block");
  if (icon) button.classList.add("btn--icon");
  if (accent) button.style.setProperty("--btn-accent", accent);
  if (label) button.textContent = label;
  if (ariaLabel) button.setAttribute("aria-label", ariaLabel);
  button.disabled = Boolean(disabled);
  if (onClick) button.addEventListener("click", onClick);
  return applyCommon(button, rest);
}

/**
 * 提示气泡：一个小叹号，悬浮或聚焦才展开说明。
 * 用它替代界面上常驻的说明段落。
 */
export function createHint(text, { align = "center", label = "说明" } = {}) {
  const hint = document.createElement("span");
  hint.className = "hint";
  if (align === "start") hint.classList.add("hint--start");
  if (align === "end") hint.classList.add("hint--end");
  hint.tabIndex = 0;
  hint.setAttribute("role", "note");
  hint.setAttribute("aria-label", `${label}：${text}`);
  hint.textContent = "!";

  const bubble = document.createElement("span");
  bubble.className = "hint__bubble";
  bubble.textContent = text;
  hint.append(bubble);
  return hint;
}

/**
 * 带标签的表单控件。传 options 得到 select，否则得到 input。
 * hint 会渲染成标签旁的小叹号，而不是控件下方常驻的一行说明。
 */
export function createField({
  label = "",
  id,
  type = "text",
  value = "",
  placeholder = "",
  hint = "",
  options,
  onInput,
  onChange,
  disabled = false,
  ...rest
} = {}) {
  const field = document.createElement("label");
  field.className = "field";
  if (id) field.htmlFor = id;

  if (label) {
    const caption = document.createElement("span");
    caption.className = "field__label";
    caption.append(label);
    if (hint) caption.append(createHint(hint, { align: "start" }));
    field.append(caption);
  }

  let control;
  if (Array.isArray(options)) {
    control = document.createElement("select");
    control.className = "select";
    for (const option of options) {
      const node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      control.append(node);
    }
    control.value = value;
  } else {
    control = document.createElement("input");
    control.className = "input";
    control.type = type;
    control.value = value;
    if (placeholder) control.placeholder = placeholder;
  }
  if (id) control.id = id;
  control.disabled = Boolean(disabled);
  if (onInput) control.addEventListener("input", onInput);
  if (onChange) control.addEventListener("change", onChange);

  field.append(control);
  applyCommon(field, rest);
  // 用自定义属性名交出控件，不要碰 field.control：它是 <label> 的只读原生属性，
  // 赋值会在严格模式下抛错；而读取它又依赖 for 指向的 ID 能在文档里查到，
  // 此时这棵树还没挂载，只会得到 null。
  field.fieldControl = control;
  return field;
}

/** 面板：标题栏 + 操作区 + 内容区。 */
export function createPanel({ title = "", hint = "", actions = [], children = [], ...rest } = {}) {
  const panel = document.createElement("section");
  panel.className = "panel";

  if (title || actions.length) {
    const head = document.createElement("div");
    head.className = "panel__head";

    const caption = document.createElement("div");
    caption.className = "panel__title";
    caption.append(title);
    if (hint) caption.append(createHint(hint, { align: "start" }));
    head.append(caption);

    if (actions.length) {
      const tools = document.createElement("div");
      tools.className = "panel__actions";
      tools.append(...actions);
      head.append(tools);
    }
    panel.append(head);
  }

  const body = document.createElement("div");
  body.className = "panel__body";
  body.append(...children);
  panel.append(body);

  applyCommon(panel, rest);
  panel.bodyEl = body;
  return panel;
}

/** 状态标记。 */
export function createChip(text, tone = "") {
  const chip = document.createElement("span");
  chip.className = tone ? `chip chip--${tone}` : "chip";
  chip.textContent = text;
  return chip;
}

/** 键值对，用来替代成段的说明文字。 */
export function createStat(label, value, { hint = "" } = {}) {
  const stat = document.createElement("div");
  stat.className = "stat";

  const caption = document.createElement("span");
  caption.className = "stat__label";
  caption.append(label);
  if (hint) caption.append(createHint(hint, { align: "start" }));

  const body = document.createElement("span");
  body.className = "stat__value";
  body.textContent = value;

  stat.append(caption, body);
  return stat;
}

/**
 * 标签页。真正切换内容，不是滚动定位。
 * onChange 收到被选中的 id。
 */
export function createTabs({ tabs = [], active = "", onChange } = {}) {
  const bar = document.createElement("div");
  bar.className = "tabs";
  bar.setAttribute("role", "tablist");

  const buttons = new Map();
  const select = (id) => {
    for (const [key, button] of buttons) {
      button.setAttribute("aria-selected", key === id ? "true" : "false");
      button.tabIndex = key === id ? 0 : -1;
    }
    onChange?.(id);
  };

  for (const tab of tabs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tab";
    button.textContent = tab.label;
    button.setAttribute("role", "tab");
    button.dataset.tabId = tab.id;
    button.addEventListener("click", () => select(tab.id));
    buttons.set(tab.id, button);
    bar.append(button);
  }

  if (tabs.length) {
    const initial = buttons.has(active) ? active : tabs[0].id;
    for (const [key, button] of buttons) {
      button.setAttribute("aria-selected", key === initial ? "true" : "false");
      button.tabIndex = key === initial ? 0 : -1;
    }
  }

  bar.selectTab = select;
  return bar;
}

// 同一时刻只允许一个下拉展开，否则点开第二个时第一个会留在屏幕上。
const openMenus = new Set();

function closeAllMenus(except) {
  for (const close of [...openMenus]) {
    if (close !== except) close();
  }
}

if (typeof document !== "undefined") {
  document.addEventListener("click", () => closeAllMenus());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAllMenus();
  });
}

/**
 * 下拉菜单：一个触发按钮 + 一层浮层。
 *
 * 用它收纳次要操作。把所有操作都摊在表面会让人看不出哪个重要，而次要操作
 * 往往只在特定时刻才有意义——收进下拉既省地方，也表明了主次。
 *
 * items 里 { label, onClick, disabled, tone, separator } 逐条渲染。
 */
export function createMenu({
  label = "",
  items = [],
  children = [],
  align = "end",
  variant = "subtle",
  size = "sm",
  ariaLabel,
  ...rest
} = {}) {
  const wrap = document.createElement("div");
  wrap.className = "menu";
  if (align === "start") wrap.classList.add("menu--start");

  const trigger = createButton({ variant, size, ariaLabel });
  trigger.classList.add("menu__trigger");
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");

  const caption = document.createElement("span");
  caption.className = "menu__label";
  caption.textContent = label;

  const caret = document.createElement("span");
  caret.className = "menu__caret";
  caret.setAttribute("aria-hidden", "true");
  trigger.append(caption, caret);

  const list = document.createElement("div");
  list.className = "menu__list";
  list.setAttribute("role", "menu");
  list.hidden = true;

  const close = () => {
    if (list.hidden) return;
    list.hidden = true;
    wrap.classList.remove("is-open");
    trigger.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    closeAllMenus(close);
    list.hidden = false;
    wrap.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
  };
  openMenus.add(close);

  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    if (list.hidden) open();
    else close();
  });
  // 浮层内的点击不该被文档级的"点外面就关"当成外部点击。
  list.addEventListener("click", (event) => event.stopPropagation());

  const entries = new Map();
  for (const item of items) {
    if (item.separator) {
      const rule = document.createElement("div");
      rule.className = "menu__separator";
      list.append(rule);
      continue;
    }
    const entry = document.createElement("button");
    entry.type = "button";
    entry.className = "menu__item";
    if (item.tone) entry.classList.add(`menu__item--${item.tone}`);
    entry.setAttribute("role", "menuitem");
    entry.textContent = item.label;
    entry.disabled = Boolean(item.disabled);
    entry.addEventListener("click", () => {
      close();
      item.onClick?.();
    });
    list.append(entry);
    if (item.id) entries.set(item.id, entry);
  }

  // 收纳已经存在的按钮。它们的 id、监听和 .hidden 开关都保持原样，只是换了个
  // 容身之处——这样调用方不必为了搬进下拉而重写一套渲染逻辑。
  for (const node of children) {
    node.classList.add("menu__item");
    node.addEventListener("click", close);
    list.append(node);
  }

  wrap.append(trigger, list);
  applyCommon(wrap, rest);
  wrap.triggerEl = trigger;
  wrap.itemEls = entries;
  wrap.setLabel = (text) => {
    caption.textContent = text;
  };
  wrap.closeMenu = close;
  return wrap;
}

/** 空态。 */
export function createEmpty(text, { action } = {}) {
  const empty = document.createElement("div");
  empty.className = "empty";
  const caption = document.createElement("p");
  caption.textContent = text;
  empty.append(caption);
  if (action) empty.append(action);
  return empty;
}
