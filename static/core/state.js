// Shared client state, storage keys and cross-cutting mutable UI handles.

export const state = {
  heroes: [],
  rooms: [],
  room: null,
  battle: null,
  liveBattle: null,
  selectedUnitId: "",
  inspectedUnitId: "",
  selectedActionCode: "",
  selectedActionSnapshot: null,
  hoveredActionCode: "",
  hoveredUnitId: "",
  hoverPointer: null,
  hoveredBoardCell: null,
  stagedPayload: null,
  screen: "draft",
  sidebarExpanded: "command",
  roomForm: {
    createName: "",
    joinName: "",
    joinRoomCode: "",
  },
  profileName: "",
  profileDraftName: "",
  profileReady: false,
  profileModalOpen: false,
  authToken: "",
  authUser: null,
  authUsername: "",
  authPassword: "",
  authMessage: "",
  authBusy: false,
  strategyCampaigns: [],
  strategyCampaign: null,
  strategyBattleRoom: null,
  strategyBattleRecovery: null,
  strategyName: "英灵城邦",
  strategySeed: "1",
  strategyVariantId: "classic_frontier",
  strategyVariants: [],
  // 新建战役是一条独立流程，不是列表页上常驻的一排设置框。
  strategyCreateOpen: false,
  strategyCreateStep: 0,
  strategyJoinCode: "",
  strategyJoinHostFaction: false,
  strategyMessage: "",
  strategyBusy: false,
  strategySelectedCityId: "",
  strategySelectedCampaignId: 0,
  strategySelectedCityByContext: {},
  strategyActiveOfficeId: "",
  strategyCommandDrafts: {},
  playerToken: "",
  lastSyncAt: 0,
  boardZoom: 1,
  boardPanX: 0,
  boardPanY: 0,
  lastSeenVisualEventId: 0,
  activeBattleVfx: [],
  replayMode: false,
  replayStepIndex: 0,
  replayOmniscient: false,
  roomEditSeatId: null,
  // 大厅里唯一会写字的那一行：只有操作失败时才有内容。
  roomError: "",
  rightRailCollapsed: true,
  battleConsoleCollapsed: true,
  battleDockTab: "info",
  // 战役屏：地图占满整屏，操作面板是浮在地图上、可收起的一层。
  strategyDockOpen: true,
  strategyDockTab: "city",
  // 武将页先给名单；详情要点开某个人才展开，避免一页塞下所有人的档案。
  strategyDockHeroCode: "",
  strategyMapView: { x: 0, y: 0, scale: 1 },
  aiPreview: null,
  homeFlow: "",
  // 首页数据加载失败时的提示。置位后，页头文案不再按流程覆盖它。
  homeLoadError: "",
  quickStartBusy: false,
  resumableTutorial: null,
  tutorialResumeError: "",
  onboarding: {beginner_heroes: [], recommended_rosters: [], hero_discovery: []},
  // 大厅上的设置与选将都住在弹窗里，所以这几项记的是"哪个弹窗开着、开给谁"。
  roomSetupOpen: false,
  roomSetupDraft: null,
  gameOverShowDetails: false,
  heroPickerSeatId: null,
  heroDetailCode: "",
  heroSearchQuery: "",
  heroSortKey: "name",
  heroSortDesc: false,
  connectionLostAt: 0,
  reconnectedAt: 0,
  lastTurnTimeoutAt: 0,
  tutorialGuideCollapsed: false,
  tutorialHistoryOffset: 0,
  tutorialCompletionRecorded: false,
  lastCompletedMatch: null,
  recentMatches: [],
  recentMatchesBusy: false,
  recentMatchesError: "",
  progression: null,
  progressionBusy: false,
  progressionError: "",
  historicalMatchId: "",
  lastHistorySyncMatchId: "",
};

export const ROOM_TOKEN_PREFIX = "wujiang-room-token:";

export const ROOM_NAME_PREFIX = "wujiang-room-name:";

export const PROFILE_NAME_KEY = "wujiang-profile-name";

export const PROFILE_READY_KEY = "wujiang-profile-ready";

export const AUTH_TOKEN_KEY = "wujiang-auth-token";

export const ANALYTICS_SESSION_KEY = "wujiang-analytics-session";

export const LAST_TUTORIAL_ROOM_KEY = "wujiang-last-tutorial-room";

export const RECORDED_MATCH_ENDS_KEY = "wujiang-recorded-match-ends";

export const LAST_COMPLETED_MATCH_KEY = "wujiang-last-completed-match";

export const FALLBACK_STRATEGY_VARIANTS = [
  { id: "classic_frontier", name: "经典边境", core_question: "在雪鬼危机到来前，如何平衡城邦外交、战争准备与圣物经营？", modifiers: ["使用标准钱粮、城防、兵员与以太开局。"] },
  { id: "hungry_frontier", name: "粮荒前线", core_question: "当全境粮食储备骤减时，是优先保城、贸易求援，还是冒险扩张？", modifiers: ["所有城市开局粮食降至 70%。", "主要势力开局粮食降至 75%。"] },
  { id: "fortified_leagues", name: "坚城联盟", core_question: "中立城邦更难武力吞并时，能否用外交、影响力和长期围城打开局面？", modifiers: ["中立城邦城防 +2。", "中立城邦守军 +120。", "当地自治支持 +15。"] },
  { id: "ether_tide", name: "以太潮汐", core_question: "以太充裕但主要势力资金紧张时，是否围绕英灵与圣物路线竞速？", modifiers: ["所有城市开局以太 +60。", "主要势力开局以太 +30、金钱 -80。"] },
];

export const ui = {
  pollHandle: null,
  nextHomePollAt: 0,
  nextRoomPollAt: 0,
  lastHomeRenderSignature: "",
  lastLobbyRenderSignature: "",
  pollBackoffMs: 0,
  refreshInFlight: false,
  boardOverlayRenderHandle: 0,
  battleVfxCleanupHandle: 0,
  boardDragState: null,
  boardDragSuppressUntil: 0,
  tooltipHideHandle: 0,
  keyboardHelpReturnFocus: null,
  profileModalReturnFocus: null,
};
