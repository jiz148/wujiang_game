// Home screen: entry points, recent matches and onboarding.
import { $ } from '../core/dom.js';
import { fetchJson, recordProductEvent } from '../core/net.js';
import { render } from '../core/render.js';
import { AUTH_TOKEN_KEY, LAST_TUTORIAL_ROOM_KEY, state } from '../core/state.js';
import { saveProfileName, userLoggedIn } from '../platform/auth.js';
import { clearStrategyState, refreshStrategyCampaigns } from '../strategic/api.js';
import { renderRecentMatches } from '../strategic/ui-base.js';
import { renderPostgameSummary } from '../tactical/battle-ui.js';
import { loadStoredIdentity } from '../tactical/session.js';

// 每个流程一屏：面板、页头文案都跟着流程走，而不是把四套内容一起摊在页面上
// 靠 .hidden 互相遮挡。流程名同时就是地址栏的 hash，两边只有一套词汇。
const FLOW_PANELS = {
  campaign: "strategy-panel",
  skirmish: "skirmish-panel",
  tutorial: "quick-start-panel",
  archive: "recent-matches-panel",
};

// caption 为 null 表示该流程自己写页头说明，或者干脆不需要说明。
// 遭遇战进了房间之后标题是房间码，那比一句玩法介绍有用；没进房间时也只留「遭遇战」。
const FLOW_HEADINGS = {
  campaign: ["战役", null],
  skirmish: ["遭遇战", null],
  tutorial: ["新手教学", "用固定阵容和地图走一遍战场操作。"],
  archive: ["战绩与回放", "最近 10 场已结束对局，以及武将熟练度。"],
};

export function renderHomeFlow() {
  const flow = state.homeFlow;
  for (const [name, id] of Object.entries(FLOW_PANELS)) {
    const panel = $(id);
    if (panel) panel.classList.toggle("hidden", flow !== name);
  }
  // 房间列表只在遭遇战下有意义，战役有自己的战役列表。
  const directory = $("room-directory");
  if (directory) directory.classList.toggle("hidden", flow !== "skirmish");

  const tutorialButton = $("start-tutorial");
  const resumeButton = $("resume-tutorial");
  const resumeNote = $("tutorial-resume-note");
  const canResume = Boolean(state.resumableTutorial);

  // 进了房间之后页头归 renderRoomPanels：标题是房间码，说明只在失败时出现。
  // 操作席位下拉时 renderRoomPanels 会被跳过以免拆掉正在展开的控件；如果这里
  // 仍按流程文案重写，左上角就会在「房间 AB12CD」和「遭遇战」之间来回跳。
  const lobbyTitle = $("lobby-title");
  const lobbyCaption = $("lobby-caption");
  if (!state.room) {
    const [heading, caption] = FLOW_HEADINGS[flow] || ["", ""];
    if (lobbyTitle && heading) lobbyTitle.textContent = heading;
    // 加载失败时这里会被写成错误信息，不要用流程文案盖掉它。
    if (lobbyCaption && !state.homeLoadError) {
      lobbyCaption.textContent = state.roomError || caption || "";
      lobbyCaption.classList.toggle("is-error", Boolean(state.roomError));
    }
  }
  if (tutorialButton) {
    tutorialButton.disabled = state.quickStartBusy || !userLoggedIn();
    tutorialButton.textContent = state.quickStartBusy
      ? "正在准备教学..."
      : (canResume ? "重新开始教学" : "进入新手教学");
    // 有未完成进度时，「继续」才是主按钮，重新开始退居其次。
    tutorialButton.classList.toggle("primary", !canResume);
    tutorialButton.classList.toggle("ghost", canResume);
  }
  if (resumeButton) {
    resumeButton.classList.toggle("hidden", !canResume);
    resumeButton.disabled = state.quickStartBusy || !userLoggedIn();
    resumeButton.textContent = state.quickStartBusy ? "正在恢复教学..." : "继续未完成教学";
  }
  if (resumeNote) {
    if (canResume) {
      const stepTitle = state.resumableTutorial.step_title || "上次步骤";
      resumeNote.textContent = `发现未完成教学：${stepTitle}。你可以继续当前进度，也可以重新开始。`;
    } else if (state.tutorialResumeError) {
      resumeNote.textContent = state.tutorialResumeError;
    } else {
      resumeNote.textContent = "固定阵容和地图，依次练习选中、移动、普攻、技能、连锁响应和结束回合。";
    }
  }
}

export function clearResumableTutorial() {
  state.resumableTutorial = null;
  state.tutorialResumeError = "";
  localStorage.removeItem(LAST_TUTORIAL_ROOM_KEY);
}

export async function refreshResumableTutorial() {
  const roomId = String(localStorage.getItem(LAST_TUTORIAL_ROOM_KEY) || "").trim();
  if (!roomId) {
    state.resumableTutorial = null;
    state.tutorialResumeError = "";
    return;
  }
  const identity = loadStoredIdentity(roomId);
  if (!identity.token) {
    clearResumableTutorial();
    return;
  }
  try {
    const query = new URLSearchParams({room_id: roomId, player_token: identity.token});
    const payload = await fetchJson(`/api/rooms/state?${query.toString()}`);
    const room = payload.room || {};
    const tutorial = room.tutorial || null;
    const resumable = room.experience_kind === "tutorial"
      && Boolean(tutorial)
      && !tutorial.completed_at
      && !payload.battle?.winner
      && room.viewer_player_id !== null
      && room.viewer_player_id !== undefined;
    if (!resumable) {
      clearResumableTutorial();
      return;
    }
    state.resumableTutorial = {
      room_id: roomId,
      player_token: identity.token,
      step_id: tutorial.step_id || "",
      step_title: tutorial.step?.title || tutorial.step_title || "上次步骤",
    };
    state.tutorialResumeError = "";
  } catch (_error) {
    state.resumableTutorial = null;
    state.tutorialResumeError = "暂时无法检查上次教学进度；你仍可重新开始，稍后也可以再次检查。";
  }
}

export function normalizeAuthUsername(username) {
  return String(username || "").trim().replace(/\s+/g, " ").slice(0, 32);
}

export function initializeAuthState() {
  state.authToken = localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

function clearAuthSession(message = "") {
  state.authToken = "";
  state.authUser = null;
  state.authPassword = "";
  state.authMessage = message;
  state.recentMatches = [];
  state.recentMatchesError = "";
  state.progression = null;
  state.progressionError = "";
  clearStrategyState();
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

function saveAuthSession(sessionToken, user) {
  state.authToken = sessionToken || "";
  state.authUser = user || null;
  state.authPassword = "";
  if (state.authToken) {
    localStorage.setItem(AUTH_TOKEN_KEY, state.authToken);
  }
  if (user?.username) {
    saveProfileName(user.username);
  }
}

export async function refreshAuthSession() {
  if (!state.authToken) return;
  try {
    const payload = await fetchJson("/api/auth/me");
    if (payload.user) {
      saveAuthSession(state.authToken, payload.user);
    } else {
      clearAuthSession();
    }
  } catch (error) {
    clearAuthSession(error.error || "登录状态已失效。");
  }
}

export async function refreshRecentMatches({renderAfter = true} = {}) {
  if (!userLoggedIn()) {
    state.recentMatches = [];
    state.recentMatchesError = "";
    if (renderAfter) renderRecentMatches();
    return;
  }
  if (state.recentMatchesBusy) return;
  state.recentMatchesBusy = true;
  state.recentMatchesError = "";
  if (renderAfter) renderRecentMatches();
  try {
    const payload = await fetchJson("/api/matches/recent");
    state.recentMatches = payload.matches || [];
  } catch (error) {
    state.recentMatchesError = error.error || "读取最近战绩失败。";
  } finally {
    state.recentMatchesBusy = false;
    await refreshProgression({renderAfter: false});
    if (renderAfter) renderRecentMatches();
  }
}

async function refreshProgression({renderAfter = true} = {}) {
  if (!userLoggedIn()) {
    state.progression = null;
    state.progressionError = "";
    if (renderAfter) renderRecentMatches();
    return;
  }
  if (state.progressionBusy) return;
  state.progressionBusy = true;
  state.progressionError = "";
  if (renderAfter) renderRecentMatches();
  try {
    const payload = await fetchJson("/api/progression/overview");
    state.progression = payload.progression || null;
    recordProductEvent("progression_view", {
      source: state.screen === "battle" ? "postgame" : "home",
      empty_state: !Number(state.progression?.total_matches || 0),
    });
  } catch (error) {
    state.progressionError = error.error || "读取武将熟练度失败。";
  } finally {
    state.progressionBusy = false;
    if (renderAfter) {
      if (state.screen === "battle") renderPostgameSummary();
      else renderRecentMatches();
    }
  }
}

export async function submitAuth(mode) {
  if (state.authBusy) return;
  const username = normalizeAuthUsername(state.authUsername);
  const password = state.authPassword;
  if (!username || !password) {
    state.authMessage = "请输入用户名和密码。";
    render();
    return;
  }
  state.authBusy = true;
  state.authMessage = mode === "register" ? "正在注册..." : "正在登录...";
  render();
  try {
    const payload = await fetchJson(`/api/auth/${mode}`, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    saveAuthSession(payload.session_token, payload.user);
    state.authUsername = "";
    state.authMessage = mode === "register" ? "注册成功，已登录。" : "登录成功。";
    await refreshRecentMatches({renderAfter: false});
    await refreshStrategyCampaigns({ renderAfter: false });
  } catch (error) {
    state.authMessage = error.error || "账号操作失败。";
  } finally {
    state.authBusy = false;
    render();
  }
}

export async function logoutAuth() {
  if (state.authBusy) return;
  state.authBusy = true;
  render();
  try {
    await fetchJson("/api/auth/logout", {
      method: "POST",
      body: JSON.stringify({ session_token: state.authToken }),
    });
  } catch (error) {
    state.authMessage = error.error || "退出登录时出现问题。";
  } finally {
    clearAuthSession("已退出登录。");
    state.authBusy = false;
    render();
  }
}
