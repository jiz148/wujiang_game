const METRIC_LABELS = {
  first_effective_action_median_ms: "首次有效行动中位时间",
  tutorial_completion_rate: "教学完成率",
  match_completion_rate: "对局完成率",
  invalid_action_rate: "非法操作率",
  tutorial_duration_median_ms: "教学时长中位数",
  match_duration_median_ms: "对局时长中位数",
  action_attempts: "动作尝试数",
  rematch_within_10m_rate: "10 分钟内再战率",
};

const EVENT_LABELS = {
  home_view: "进入首页",
  quick_start_click: "点击快速开始",
  tutorial_start: "开始教学",
  first_effective_action: "首次有效行动",
  tutorial_complete: "完成教学",
  quick_ai_start: "开始快速 AI 对战",
  match_start: "开始对局",
  match_end: "完成对局",
  rematch_start: "直接再战",
};

const STRATEGY_METRIC_LABELS = {
  campaigns: "战役样本",
  completed_campaigns: "已完成战役",
  completion_rate: "战役完成率",
  median_completion_seconds: "完成时长中位数",
  peaceful_integrations: "和平整合",
  resolved_battles: "已结算战斗",
  ai_city_share: "AI 主要势力城市占比",
  ai_battle_win_share: "AI 战斗胜利占比",
};

const VICTORY_ROUTE_LABELS = {
  unify_cities: "统一城邦",
  eliminate_enemy_factions: "消灭主要敌对势力",
  world_mainline: "世界主线",
  relic_altar: "圣物祭坛",
  time_limit_assessment: "十二月评议",
};

function formatDuration(value) {
  if (value === null || value === undefined) return "暂无样本";
  const seconds = Math.round(Number(value) / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function formatRate(value) {
  if (value === null || value === undefined) return "暂无样本";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatMetric(key, value) {
  if (key.endsWith("_ms")) return formatDuration(value);
  if (key.endsWith("_seconds")) return value == null ? "暂无样本" : formatDuration(Number(value) * 1000);
  if (key.endsWith("_rate")) return formatRate(value);
  return String(value ?? 0);
}

function replaceRows(targetId, rows, keyFormatter = (value) => value) {
  const target = document.getElementById(targetId);
  const source = rows && rows.length ? rows : [{ key: "暂无样本", campaigns: "—" }];
  target.replaceChildren(...source.map((item) => {
    const row = document.createElement("tr");
    [keyFormatter(item.key), item.campaigns].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    });
    return row;
  }));
}

function populateStrategyFilters(options = {}) {
  document.querySelectorAll("#strategy-analytics-filters select[data-filter]").forEach((select) => {
    const current = select.value;
    const key = select.dataset.filter;
    const values = options[key] || [];
    const label = select.options[0]?.textContent || "全部";
    select.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = label;
    select.append(empty);
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.append(option);
    });
    select.value = values.includes(current) ? current : "";
  });
}

function strategyFilterQuery() {
  const query = new URLSearchParams();
  document.querySelectorAll("#strategy-analytics-filters select[data-filter]").forEach((select) => {
    if (select.value) query.set(select.dataset.filter, select.value);
  });
  return query.toString();
}

function renderStrategyAnalytics(payload) {
  populateStrategyFilters(payload.filter_options || {});
  const summary = document.getElementById("strategy-analytics-summary");
  const metrics = payload.summary || {};
  summary.replaceChildren(...Object.entries(STRATEGY_METRIC_LABELS).map(([key, label]) => {
    const card = document.createElement("article");
    card.className = "analytics-card";
    const caption = document.createElement("span");
    caption.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = formatMetric(key, metrics[key]);
    card.append(caption, strong);
    return card;
  }));
  replaceRows("strategy-victory-routes", payload.victory_routes, (key) => VICTORY_ROUTE_LABELS[key] || key);
  replaceRows("strategy-dropoff", payload.incomplete_by_last_month, (key) => key === "暂无样本" ? key : `第 ${key} 月`);
  const monthly = document.getElementById("strategy-monthly");
  const monthlyRows = payload.monthly && payload.monthly.length ? payload.monthly : [];
  monthly.replaceChildren(...monthlyRows.map((item) => {
    const row = document.createElement("tr");
    [
      `第 ${item.month} 月`, item.campaigns, item.avg_leading_city_gap,
      `${item.avg_human_food} / ${item.avg_human_money} / ${item.avg_human_ether}`,
      `${item.avg_ai_food} / ${item.avg_ai_money} / ${item.avg_ai_ether}`,
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    });
    return row;
  }));
  if (!monthlyRows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "暂无符合筛选条件的战役快照。";
    row.append(cell);
    monthly.append(row);
  }
  document.getElementById("strategy-sample-quality").textContent =
    "样本资格：尚未评估。自动化、本地账号与未审核线上样本不能直接算作真实玩家指标通过。";
}

function renderAnalytics(payload) {
  const summary = document.getElementById("analytics-summary");
  const funnel = document.getElementById("analytics-funnel");
  const metrics = payload.metrics || {};
  const cards = [
    ["total_events", "累计事件", payload.total_events],
    ["unique_sessions", "匿名会话", payload.unique_sessions],
    ...Object.entries(METRIC_LABELS).map(([key, label]) => [key, label, metrics[key]]),
  ];
  summary.replaceChildren(...cards.map(([key, label, value]) => {
    const card = document.createElement("article");
    card.className = "analytics-card";
    const caption = document.createElement("span");
    caption.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = formatMetric(key, value);
    card.append(caption, strong);
    return card;
  }));

  funnel.replaceChildren(...(payload.steps || []).map((step) => {
    const row = document.createElement("tr");
    [
      EVENT_LABELS[step.event] || step.event,
      step.events,
      step.unique_sessions,
      formatRate(step.from_home_rate),
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    });
    return row;
  }));
  document.getElementById("analytics-generated").textContent = payload.generated_at
    ? `生成时间：${new Date(payload.generated_at * 1000).toLocaleString("zh-CN")}`
    : "";
  document.getElementById("analytics-status").textContent = payload.total_events
    ? "数据已更新。真实玩家样本仍需按内测清单判定有效性。"
    : "目前还没有事件样本。请从游戏首页开始一次测试。";
}

async function loadAnalytics() {
  const status = document.getElementById("analytics-status");
  const refresh = document.getElementById("refresh-analytics");
  refresh.disabled = true;
  status.textContent = "正在读取本地数据…";
  try {
    const query = strategyFilterQuery();
    const [response, strategyResponse] = await Promise.all([
      fetch("/api/analytics/funnel"),
      fetch(`/api/analytics/strategy${query ? `?${query}` : ""}`),
    ]);
    const [payload, strategyPayload] = await Promise.all([response.json(), strategyResponse.json()]);
    if (!response.ok) throw new Error(payload.error || "读取失败");
    if (!strategyResponse.ok) throw new Error(strategyPayload.error || "读取战役数据失败");
    renderAnalytics(payload);
    renderStrategyAnalytics(strategyPayload);
    status.textContent = strategyPayload.summary?.campaigns
      ? "战役数据已更新；真实玩家指标资格仍需单独审核。"
      : "当前筛选没有战役快照；真实玩家指标仍为尚未采样。";
  } catch (error) {
    status.textContent = `无法读取内测数据：${error.message || "请确认本地服务正在运行"}`;
  } finally {
    refresh.disabled = false;
  }
}

document.getElementById("refresh-analytics").addEventListener("click", loadAnalytics);
document.querySelectorAll("#strategy-analytics-filters select[data-filter]").forEach((select) => {
  select.addEventListener("change", loadAnalytics);
});
document.getElementById("reset-strategy-filters").addEventListener("click", () => {
  document.querySelectorAll("#strategy-analytics-filters select[data-filter]").forEach((select) => {
    select.value = "";
  });
  loadAnalytics();
});
loadAnalytics();
