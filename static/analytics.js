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
  if (key.endsWith("_rate")) return formatRate(value);
  return String(value ?? 0);
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
    const response = await fetch("/api/analytics/funnel");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "读取失败");
    renderAnalytics(payload);
  } catch (error) {
    status.textContent = `无法读取内测数据：${error.message || "请确认本地服务正在运行"}`;
  } finally {
    refresh.disabled = false;
  }
}

document.getElementById("refresh-analytics").addEventListener("click", loadAnalytics);
loadAnalytics();
