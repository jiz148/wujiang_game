// 登录门。
//
// 后端对所有开局接口一律要求账号，未登录必然 401。既然如此，让玩家先浏览一圈
// 再撞墙是不诚实的，所以这里做成硬门禁：未登录时它是唯一可见的屏幕。
//
// render() 每几百毫秒就会跑一遍，因此 DOM 只在首次构建，之后只更新会变的部分，
// 否则输入框每次重绘都会丢焦点。
import { createButton, createField } from '../core/components.js';
import { $ } from '../core/dom.js';
import { state } from '../core/state.js';
import { submitAuth } from './home.js';

let form = null;
let usernameInput = null;
let passwordInput = null;
let messageNode = null;
let loginButton = null;
let registerButton = null;

function build(root) {
  root.replaceChildren();

  const card = document.createElement("section");
  card.className = "gate-card";

  const brand = document.createElement("div");
  brand.className = "gate-brand";
  const title = document.createElement("h1");
  title.id = "gate-title";
  title.className = "gate-title";
  title.textContent = "武将";
  const tagline = document.createElement("p");
  tagline.className = "gate-tagline";
  tagline.textContent = "登录后进入战役";
  brand.append(title, tagline);

  form = document.createElement("form");
  form.className = "gate-form";
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth("login");
  });

  const username = createField({
    label: "用户名",
    id: "gate-username",
    value: state.authUsername || "",
    onInput: (event) => {
      state.authUsername = event.target.value;
    },
  });
  usernameInput = username.fieldControl;
  usernameInput.autocomplete = "username";
  usernameInput.maxLength = 32;

  const password = createField({
    label: "密码",
    id: "gate-password",
    type: "password",
    onInput: (event) => {
      state.authPassword = event.target.value;
    },
  });
  passwordInput = password.fieldControl;
  passwordInput.autocomplete = "current-password";
  passwordInput.maxLength = 128;

  const actions = document.createElement("div");
  actions.className = "gate-actions";
  loginButton = createButton({
    label: "登录",
    variant: "primary",
    size: "lg",
    block: true,
    type: "submit",
    id: "gate-login",
  });
  registerButton = createButton({
    label: "注册新账号",
    variant: "ghost",
    size: "lg",
    block: true,
    id: "gate-register",
    onClick: () => submitAuth("register"),
  });
  actions.append(loginButton, registerButton);

  messageNode = document.createElement("p");
  messageNode.className = "gate-message";
  messageNode.setAttribute("role", "status");

  form.append(username, password, actions, messageNode);
  card.append(brand, form);
  root.append(card);
}

export function renderGate() {
  const root = $("gate-screen");
  if (!root) return;
  if (!form || !root.contains(form)) build(root);

  if (document.activeElement !== usernameInput) usernameInput.value = state.authUsername || "";
  if (document.activeElement !== passwordInput) passwordInput.value = state.authPassword || "";

  const busy = Boolean(state.authBusy);
  loginButton.disabled = busy;
  registerButton.disabled = busy;
  messageNode.textContent = state.authMessage || "";
  messageNode.classList.toggle("is-error", Boolean(state.authMessage) && !busy);
}
