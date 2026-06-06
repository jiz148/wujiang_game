import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, "..");
const sourcePath = path.join(rootDir, "data", "武将yoo.xlsx");
const outputPath = path.join(rootDir, "docs", "武将实现问题清单.xlsx");
const mode = process.argv[2] || "build";

const implementedHeroNames = new Set([
  "艾莉",
  "E。暗人",
  "火葬者",
  "精兵",
  "吟游诗人",
  "元素猎人",
  "不死王利娜",
  "岩神",
  "神龙。末日光",
  "天位骑士。政宗",
  "翡翠",
  "N",
  "噬血",
  "李",
  "咏唱者",
  "抹杀的使徒",
  "龙骑",
  "销魂的死灵",
]);

const patterns = {
  area: [/(\d+)\s*\*\s*(\d+)/, /周围|身前|前方|直线|横竖|全场|场上|一周|范围|范\s*\d|远程|斜线|打\d+格|伤害?\d*格|第\d+格/],
  movement: [/移动|瞬移|位移|拉|推|排列|交换|穿人|飞跃|冲撞|消失|出现|重合|靠近|尽量/],
  duration: [/\d+(\.\d+)?\s*轮|下个回合|本回合|直到|持续|回合结束|回合开始|下回合/],
  summon: [/召唤|制造|分身|召龙|士兵|兽|马|龙|炮|占\d|地形|攻\s*\d.*守|守\s*无限/],
  resource: [/魔力点|计数点|标记|编号|层|剩余|消耗|不费魔|费魔|魔[+-]|n\s*[=＝]|选择n|任意分配/],
  random: [/硬币|随机|1\/2|几率|概率|决定的数量/],
  field: [/天气|烈日|暴雨|沙尘|雾|领域|场|地形|范围内|区域|天空圣域/],
  stacking: [/叠加|不叠加|重置|编号|最多|每回合|每轮|同名|上限|次数|恢复/],
  copy: [/复制|模仿|夺走|得到.*技能|得到.*特性|上一次使用|选择.*技能|选择.*特性/],
  passive: [/被动技能|连锁|受到.*攻击|被.*影响|挡住|回避|反击|保护/],
  instant: [/随时使用|随时发动/],
  toggle: [/开关技能|开启|关闭/],
  hidden: [/隐身|分身真假|未公开|只能.*可见|无法被选中/],
  ai: [/强制|最近|失败|使对方|控制|随机|1\/2|不能.*使用|无效|复制|模仿|夺走/],
};

async function loadSourceWorkbook() {
  const source = await FileBlob.load(sourcePath);
  return SpreadsheetFile.importXlsx(source);
}

function normalizeCell(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function truncate(text, max = 32000) {
  const value = normalizeCell(text);
  return value.length > max ? `${value.slice(0, max - 20)}... [truncated]` : value;
}

function splitTopLevel(text, delimiters) {
  const fragments = [];
  let current = "";
  let depth = 0;
  const open = new Set(["（", "("]);
  const close = new Set(["）", ")"]);
  for (const ch of normalizeCell(text)) {
    if (open.has(ch)) depth += 1;
    if (close.has(ch) && depth > 0) depth -= 1;
    if (depth === 0 && delimiters.has(ch)) {
      if (current.trim()) fragments.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  if (current.trim()) fragments.push(current.trim());
  return fragments;
}

function splitSkillFragments(text) {
  let value = normalizeCell(text);
  value = value
    .replace(/\s+/g, " ")
    .replace(/）(?=[^\s￥¥】）])/g, "） ")
    .replace(/\)(?=[^\s￥¥\]\)])/g, ") ")
    .replace(/([￥¥])/g, " $1")
    .replace(/(【\s*\d+(?:\.\d+)?\s*(?=[\p{Script=Han}A-Za-z]))/gu, " $1");
  return splitTopLevel(value, new Set([" "])).filter(Boolean);
}

function splitTraitFragments(text) {
  const value = normalizeCell(text);
  if (!value || value === "无") return [];
  return splitTopLevel(value, new Set(["；", ";"])).filter(Boolean);
}

function extractSkillName(fragment) {
  let value = normalizeCell(fragment)
    .replace(/^[￥¥]\s*/, "")
    .replace(/^【\s*[\d.]+\s*/, "")
    .replace(/^【\s*/, "");
  const paren = value.search(/[（(]/);
  if (paren >= 0) value = value.slice(0, paren);
  value = value.replace(/【.*$/, "");
  value = value.replace(/[：:，,；;。].*$/, "").trim();
  return value || "未命名片段";
}

function hasAny(text, regexes) {
  return regexes.some((regex) => regex.test(text));
}

const commonSkillNames = new Set([
  "光墙",
  "魔墙",
  "石墙",
  "保护",
  "回避",
  "神速",
  "飞跃",
  "变硬",
  "硬化",
  "穿刺",
  "远程穿刺",
  "震开",
  "机枪",
  "爆头",
  "守*2",
  "回血",
  "治疗",
  "洗礼",
  "吟唱",
  "大圣光",
  "隐身",
  "分身",
  "吸魔",
  "回魔",
  "魔盾",
  "龙息",
  "远程龙息",
  "链条",
  "锁链",
  "格挡",
  "撤步射击",
]);

const commonTraitTexts = new Set([
  "无",
  "飞行",
  "自然回血",
  "自然回魔",
  "自然回复",
  "原地回复",
  "可格挡反击",
  "可格挡，反击",
  "格挡反击",
  "攻击吸血",
  "普攻吸血",
  "攻击吸魔",
  "攻击2次",
  "攻击两次",
  "攻击3次",
  "攻击三次",
  "攻2次",
  "攻3次",
  "魔免",
  "物免",
  "可穿人",
  "弧形攻击",
  "周围攻击",
  "可乘骑",
]);

function compactRuleText(text) {
  return normalizeCell(text).replace(/\s+/g, "").replace(/[。；;，,]/g, "");
}

function stripSkillRulePrefix(text) {
  return normalizeCell(text)
    .replace(/^[￥¥]\s*/, "")
    .replace(/^【\s*\d+(?:\.\d+)?\s*/, "")
    .replace(/^【\s*/, "")
    .trim();
}

function isPlainCommonSkill(fragment) {
  const stripped = stripSkillRulePrefix(fragment);
  if (!stripped) return true;
  const skillName = extractSkillName(fragment);
  const compacted = compactRuleText(stripped);
  const compactedName = compactRuleText(skillName);
  const isCommonName = commonSkillNames.has(skillName) || commonSkillNames.has(stripped) || commonSkillNames.has(compacted);
  if (!isCommonName) return false;
  if (compacted === compactedName) return true;
  const hasHeroSpecificExtra = /[（(]|\d+\s*\*\s*\d+|额外|特殊|每|直到|持续|不能|破魔|半破魔|无视|召唤|制造|魔力点|计数点|标记|复制|夺走|随机|硬币|天气|场|范围|选择|移动|位移|交换|上限|重置|冷却|次数|一回合|一轮|下回合|本回合/.test(stripped);
  return !hasHeroSpecificExtra;
}

function isPlainCommonTrait(fragment) {
  const compacted = compactRuleText(fragment);
  return !compacted || commonTraitTexts.has(compacted);
}

function isSpecialSkillFragment(fragment) {
  return !isPlainCommonSkill(fragment);
}

function isSpecialTraitFragment(fragment) {
  return !isPlainCommonTrait(fragment);
}

function makeQuestionId(row, nextNumber) {
  return `R${String(row.sourceRow).padStart(3, "0")}-Q${String(nextNumber).padStart(3, "0")}`;
}

function pushQuestion(questions, row, moduleName, questionId, question, options, notes = "") {
  questions.push({
    hero: row.name,
    module: moduleName,
    questionId,
    question: truncate(question),
    options: truncate(options),
    answer: "",
    notes: truncate(notes),
    sourceRow: row.sourceRow,
  });
}

async function inspectSource() {
  const workbook = await loadSourceWorkbook();
  const sheets = [];
  for (const sheet of workbook.worksheets.items) {
    const used = sheet.getUsedRange();
    const address = used?.address || "";
    const sample = used ? sheet.getRange(address).values.slice(0, 8).map((row) => row.slice(0, 12)) : [];
    sheets.push({ name: sheet.name, address, sample });
  }
  console.log(JSON.stringify({ sourcePath, sheets }, null, 2));
}

async function readHeroRows() {
  const workbook = await loadSourceWorkbook();
  const sheet = workbook.worksheets.getItem("最新武将");
  const values = sheet.getRange(sheet.getUsedRange().address).values;
  const rows = [];
  for (let index = 1; index < values.length; index += 1) {
    const row = values[index] || [];
    const name = normalizeCell(row[4]);
    if (!name) continue;
    rows.push({
      sourceRow: index + 1,
      level: row[0],
      className: normalizeCell(row[1]),
      element: normalizeCell(row[2]),
      race: normalizeCell(row[3]),
      name,
      attack: row[5],
      defense: row[6],
      speed: row[7],
      range: row[8],
      mana: row[9],
      skills: normalizeCell(row[10]),
      traits: normalizeCell(row[11]),
    });
  }
  return rows;
}

function addHeroQuestions(row) {
  const questions = [];
  let q = 1;
  const skillFragments = splitSkillFragments(row.skills);
  const traitFragments = splitTraitFragments(row.traits);
  const statsText = `等级${row.level}，职业${row.className}，属性${row.element}，种族${row.race}，攻${row.attack} 守${row.defense} 速${row.speed} 范${row.range} 魔${row.mana}`;

  pushQuestion(
    questions,
    row,
    "基础数据",
    makeQuestionId(row, q++),
    `请确认【${row.name}】的基础数据是否按源表直接实现：${statsText}。`,
    [
      "a. 按源表直接实现。",
      "b. 有数值或分类修正，请在回答列写明修正后的完整基础数据。",
      "c. 暂缓实现此武将，请在回答列写原因。",
    ].join("\n"),
    `源表技能：${row.skills}\n源表特性：${row.traits}`,
  );

  pushQuestion(
    questions,
    row,
    "技能切分",
    makeQuestionId(row, q++),
    `请确认【${row.name}】技能列是否按以下片段切分。若有技能名、费魔符号、￥/¥大招标记、括号范围归属错误，请在回答列写正确切分。`,
    [
      "a. 切分正确，按这些片段分别实现。",
      "b. 有片段应合并为同一技能/同一技能的多个阶段，请在回答列写明。",
      "c. 有片段应拆成多个技能，请在回答列写明。",
      "d. 技能名或大招/费魔标记归属有误，请在回答列写明。",
    ].join("\n"),
    skillFragments.map((fragment, index) => `${index + 1}. ${fragment}`).join("\n"),
  );

  pushQuestion(
    questions,
    row,
    "特性切分",
    makeQuestionId(row, q++),
    `请确认【${row.name}】特性列是否按以下片段切分。`,
    [
      "a. 切分正确，按这些特性分别实现。",
      "b. 有片段应合并或拆分，请在回答列写正确切分。",
      "c. 某些特性其实是技能效果或召唤物规则，请在回答列写归属。",
      "d. 此武将没有需要实现的特性。",
    ].join("\n"),
    traitFragments.length ? traitFragments.map((fragment, index) => `${index + 1}. ${fragment}`).join("\n") : "源表特性为“无”或空。",
  );

  for (const [index, fragment] of skillFragments.entries()) {
    const skillName = extractSkillName(fragment);
    const moduleName = `技能:${skillName}`;
    pushQuestion(
      questions,
      row,
      moduleName,
      makeQuestionId(row, q++),
      `【${row.name}】的技能片段「${fragment}」是否按技能名「${skillName}」实现？`,
      [
        "a. 是，技能名和整段规则归属正确。",
        "b. 技能名正确，但规则中有子阶段/子召唤/子技能，请在回答列拆分。",
        "c. 技能名不正确，请在回答列写正确技能名。",
        "d. 该片段不是独立技能，请在回答列写应归属的位置。",
      ].join("\n"),
      `源表行 ${row.sourceRow}，技能片段 ${index + 1}/${skillFragments.length}`,
    );

    if (hasAny(fragment, patterns.area) || hasAny(fragment, patterns.movement)) {
      pushQuestion(questions, row, `${moduleName}/目标与前端`, makeQuestionId(row, q++), `技能「${skillName}」的目标、范围、方向、位移或前端选择方式如何实现？源文本：${fragment}`, [
        "a. 按通用直接单位/格子规则：需要在范内，并按横/竖/斜直线或技能写明形状选择；前端只高亮合法格。",
        "b. 这是远程范围/矩形/多格技能：前端用范围格子选择，后端按所选格和边界截断验证。",
        "c. 这是身前/方向/移动后结算技能：前端需要先选方向或路径，再显示结算格。",
        "d. 这是多阶段选择，例如目标+方向、目标+落点、数值n+范围，请在回答列写完整交互顺序。",
        "e. 其他，请在回答列写精确目标、范围、方向和前端预览策略。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.duration)) {
      pushQuestion(questions, row, `${moduleName}/持续时间`, makeQuestionId(row, q++), `技能「${skillName}」涉及持续时间或“下回合/本回合/直到”文字。持续时间按谁的轮次结算？源文本：${fragment}`, [
        "a. 按受影响单位自己的轮次计数。",
        "b. 按施法者自己的轮次计数。",
        "c. 按每个全局武将回合计数。",
        "d. 这是半轮/阶段性效果，请在回答列写开始、结束和清理时点。",
        "e. 其他，请在回答列写具体计时规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.movement) && (hasAny(fragment, patterns.area) || /伤|效果|破坏|回复|吸魔|不能|无效/.test(fragment))) {
      pushQuestion(questions, row, `${moduleName}/结算顺序`, makeQuestionId(row, q++), `技能「${skillName}」同时涉及位移/位置变化和伤害或效果。连锁窗口与结算顺序如何切分？源文本：${fragment}`, [
        "a. 整段作为单一 effect，在一次连锁窗口后按文本顺序结算。",
        "b. 位移/召唤/天气等前段先结算，后续伤害或负面效果另开连锁窗口。",
        "c. 每个命中单位或每段伤害分别开连锁窗口。",
        "d. 先按现有站位锁定原声明格，连锁只影响后续是否命中。",
        "e. 其他，请在回答列写完整结算顺序与连锁窗口。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.summon)) {
      pushQuestion(questions, row, `${moduleName}/召唤物或占格`, makeQuestionId(row, q++), `技能「${skillName}」疑似涉及召唤物、分身、地形、占格或独立单位。请确认该对象的实现规则。源文本：${fragment}`, [
        "a. 作为普通召唤物：占格、可被攻击、不进全局回合环，按召唤者控制。",
        "b. 作为分身/复制体：需要隐藏真假或复制属性，回答列写可见性和行动限制。",
        "c. 作为地形/场地标记：不作为单位行动，回答列写是否可被攻击、阻挡移动和清除方式。",
        "d. 作为坐骑/可乘骑单位：回答列写骑乘、重叠、受击和重新召唤规则。",
        "e. 其他，请在回答列写占格、出生位置、行动回合、持续时间、破坏和上限。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.resource)) {
      pushQuestion(questions, row, `${moduleName}/资源与计数`, makeQuestionId(row, q++), `技能「${skillName}」涉及魔力点、计数点、标记、编号、n、剩余次数或资源变化。请确认资源规则。源文本：${fragment}`, [
        "a. 使用现有魔力点/计数点通用模型，回答列只写上限和初始值（若有）。",
        "b. 这是该武将专属资源，回答列写初始值、上限、获得、消耗、公开可见性和清除时机。",
        "c. n 或可分配点数由玩家在前端输入/选择，回答列写合法范围和是否可为0。",
        "d. 资源变化影响当前值和上限，回答列写是否同时改当前魔/血/能力上限。",
        "e. 其他，请在回答列写完整资源规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.random)) {
      pushQuestion(questions, row, `${moduleName}/随机与公开信息`, makeQuestionId(row, q++), `技能「${skillName}」涉及硬币、概率或随机结果。随机如何执行和展示？源文本：${fragment}`, [
        "a. 后端随机，结果公开写入战斗日志，并按结果继续结算。",
        "b. 后端随机，但对敌方隐藏部分信息，请在回答列写哪些视角可见。",
        "c. 玩家先选择结果数量/分支，随机只决定具体命中或顺序。",
        "d. 不是随机，应按确定性规则实现，请在回答列写规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.field) || hasAny(fragment, patterns.stacking)) {
      pushQuestion(questions, row, `${moduleName}/场地与叠加`, makeQuestionId(row, q++), `技能「${skillName}」涉及场地、天气、范围持续效果、同名效果、重置或次数。请确认叠加和清理规则。源文本：${fragment}`, [
        "a. 同名效果不叠加，重新施加只刷新持续时间或按已有通用规则处理。",
        "b. 同名效果可以叠加，回答列写叠加上限、每层效果和清除时机。",
        "c. 这是场地/天气/地形效果，回答列写覆盖范围、是否显示在前端、进入/离开时是否动态生效。",
        "d. 这是技能使用次数/冷却/重置问题，回答列写精确重置条件。",
        "e. 其他，请在回答列写完整规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.copy)) {
      pushQuestion(questions, row, `${moduleName}/复制或夺取`, makeQuestionId(row, q++), `技能「${skillName}」涉及复制、模仿、夺走、得到技能或特性。请确认复制对象、限制和前端选择方式。源文本：${fragment}`, [
        "a. 只能复制/夺取当前公开可见的技能或特性，前端列出合法选项。",
        "b. 可复制隐藏或未公开信息，请在回答列写可见性例外。",
        "c. 复制后保留原技能费用、冷却、次数和目标方式。",
        "d. 复制后使用新的费用/次数/附加效果，请在回答列写完整覆盖规则。",
        "e. 其他，请在回答列写复制来源、持续时间、可叠加性和失效时机。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.passive) || hasAny(fragment, patterns.instant) || hasAny(fragment, patterns.toggle)) {
      pushQuestion(questions, row, `${moduleName}/时机`, makeQuestionId(row, q++), `技能「${skillName}」有被动、连锁、随时使用或开关时机文字。请确认实际使用时机。源文本：${fragment}`, [
        "a. 主动技能，只能在自己的行动回合使用。",
        "b. 被动连锁技能，按速度2进入连锁窗口。",
        "c. 随时使用技能，自己的回合可主动用，对方回合按速度3使用。",
        "d. 开关技能，只能在自己回合开始阶段开/关。",
        "e. 大招，每场一次；若还有特殊时机请在回答列写明。",
      ].join("\n"), fragment);
    }
  }

  for (const [index, fragment] of traitFragments.entries()) {
    const moduleName = `特性:${index + 1}`;
    pushQuestion(questions, row, moduleName, makeQuestionId(row, q++), `【${row.name}】的特性片段如何触发和结算？源文本：${fragment}`, [
      "a. 作为常驻特性实现，持续影响自身相关数值或规则。",
      "b. 作为触发式特性实现，回答列写触发时机、每回合/每轮限制和结算顺序。",
      "c. 作为光环/范围特性实现，回答列写范围、进入/离开时是否动态生效、是否影响召唤物/分身。",
      "d. 作为隐藏信息相关特性实现，回答列写各视角可见内容。",
      "e. 其他，请在回答列写完整特性规则。",
    ].join("\n"), fragment);

    if (hasAny(fragment, [...patterns.duration, ...patterns.stacking, ...patterns.resource, ...patterns.summon, ...patterns.field])) {
      pushQuestion(questions, row, `${moduleName}/状态管理`, makeQuestionId(row, q++), `特性片段涉及持续、叠加、资源、召唤物或范围状态。请确认状态管理。源文本：${fragment}`, [
        "a. 状态绑定在该武将身上，按该武将自己的轮次清理或触发。",
        "b. 状态绑定在被影响单位身上，按被影响单位自己的轮次清理或触发。",
        "c. 状态绑定在场地/天气/召唤物上，回答列写生命周期和可见性。",
        "d. 可叠加或有计数，上限、层数效果和清除时机请写在回答列。",
      ].join("\n"), fragment);
    }
  }

  if (hasAny(`${row.skills}\n${row.traits}`, [...patterns.hidden, ...patterns.ai, ...patterns.copy, ...patterns.random])) {
    pushQuestion(questions, row, "AI与可见性", makeQuestionId(row, q++), `【${row.name}】包含隐藏信息、随机、复制/控制或强制行动等复杂规则。AI 和公开信息应如何处理？`, [
      "a. AI 只能使用合法公开信息；隐藏真假、未公开状态、随机未揭示结果都不可偷看。",
      "b. 该武将有特殊公开例外，请在回答列写明哪些信息对哪些玩家可见。",
      "c. AI 需要特殊候选生成或评分策略，请在回答列写优先级和禁止动作。",
      "d. 暂不实现 AI 特化，只保证合法随机/基础动作，回答列可补充限制。",
    ].join("\n"), `源表技能：${row.skills}\n源表特性：${row.traits}`);
  }

  return questions;
}

function addHeroQuestionsV2(row) {
  const questions = [];
  let q = 1;
  const skillFragments = splitSkillFragments(row.skills).filter(isSpecialSkillFragment);
  const traitFragments = splitTraitFragments(row.traits).filter(isSpecialTraitFragment);
  const specialText = `${skillFragments.join("\n")}\n${traitFragments.join("\n")}`;

  for (const [index, fragment] of skillFragments.entries()) {
    const skillName = extractSkillName(fragment);
    const moduleName = `技能:${skillName}`;
    let targeted = false;

    if (hasAny(fragment, patterns.area) || hasAny(fragment, patterns.movement)) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/目标与前端`, makeQuestionId(row, q++), `技能「${skillName}」写了专属目标、范围、方向、位移或前端选择内容。请确认玩家交互和后端合法性边界。源文本：${fragment}`, [
        "a. 直接选择一个或多个单位；回答列写敌我限制、数量上限、是否必须命中和可选距离。",
        "b. 选择中心格或固定形状区域；回答列写区域尺寸、朝向、边界截断和是否会命中双方。",
        "c. 选择方向或路径后结算；回答列写方向集合、移动/牵引/推开的合法落点和阻挡规则。",
        "d. 多阶段选择；回答列写完整顺序，例如目标+方向、目标+落点、数值n+区域。",
        "e. 其他；回答列写精确目标、范围、方向、预览和点击完成策略。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.duration)) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/持续时间`, makeQuestionId(row, q++), `技能「${skillName}」涉及持续时间或“下回合/本回合/直到”等文字。请确认计时边界。源文本：${fragment}`, [
        "a. 按受影响单位自己的轮次计数或清理。",
        "b. 按施法者自己的轮次计数或清理。",
        "c. 按每个全局武将回合计数或清理。",
        "d. 半轮或阶段性效果；回答列写开始、结束和清理时点。",
        "e. 其他；回答列写具体计时规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.movement) && (hasAny(fragment, patterns.area) || /伤|效果|破坏|回复|吸魔|不能|无效/.test(fragment))) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/结算顺序`, makeQuestionId(row, q++), `技能「${skillName}」同时涉及位置变化和伤害/效果。请确认连锁窗口和结算切分。源文本：${fragment}`, [
        "a. 整段作为单一效果，在一次连锁窗口后按文本顺序结算。",
        "b. 位移/召唤/场地等前段先结算，后续伤害或负面效果另开连锁窗口。",
        "c. 每个命中单位或每段伤害分别开连锁窗口。",
        "d. 先锁定原声明格；连锁只影响后续是否命中或是否继续结算。",
        "e. 其他；回答列写完整结算顺序与连锁窗口。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.summon)) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/召唤物或占格`, makeQuestionId(row, q++), `技能「${skillName}」疑似涉及召唤物、分身、地形、占格、坐骑或独立单位。请确认该对象的实现规则。源文本：${fragment}`, [
        "a. 作为可被攻击的召唤物；回答列写占格、出生位置、行动时机、控制方、生命和上限。",
        "b. 作为分身或复制体；回答列写真假可见性、复制属性、行动限制和破坏条件。",
        "c. 作为地形/场地标记；回答列写是否占格、是否阻挡移动、是否可被攻击和清除方式。",
        "d. 作为坐骑/可乘骑单位；回答列写骑乘、重叠、受击、移动携带和重新召唤规则。",
        "e. 其他；回答列写对象类型、生命周期和前端显示。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.resource)) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/资源与计数`, makeQuestionId(row, q++), `技能「${skillName}」涉及魔力点、计数点、标记、编号、n、剩余次数或资源变化。请确认资源规则。源文本：${fragment}`, [
        "a. 资源显示为魔力点或计数点字段；回答列写初始值、上限、公开可见性和清除时机。",
        "b. 这是该武将专属资源；回答列写获得、消耗、上限、是否公开和跨回合保留规则。",
        "c. n 或可分配点数由玩家输入/选择；回答列写合法范围、是否可为 0、以及 UI 控件类型。",
        "d. 资源变化同时影响当前值和上限；回答列写具体哪些属性被同步修改和如何钳制。",
        "e. 其他；回答列写完整资源规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.random)) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/随机与公开信息`, makeQuestionId(row, q++), `技能「${skillName}」涉及硬币、概率或随机结果。请确认随机执行和展示方式。源文本：${fragment}`, [
        "a. 后端随机，结果公开写入战斗日志，并按结果继续结算。",
        "b. 后端随机，但部分结果对敌方隐藏；回答列写各视角可见内容。",
        "c. 玩家先选择分支或数量，随机只决定具体命中、顺序或对象。",
        "d. 这不是随机；回答列写确定性规则。",
        "e. 其他；回答列写完整随机和可见性规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.field) || hasAny(fragment, patterns.stacking)) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/场地与叠加`, makeQuestionId(row, q++), `技能「${skillName}」涉及场地、天气、范围持续效果、同名效果、重置、冷却或次数。请确认叠加和清理规则。源文本：${fragment}`, [
        "a. 同名效果刷新持续时间；回答列写刷新条件、清理时点和已有效果如何替换。",
        "b. 同名效果可以叠加；回答列写叠加上限、每层效果和清除时机。",
        "c. 场地/天气/地形动态生效；回答列写覆盖范围、进入/离开触发、前端标记和覆盖关系。",
        "d. 这是技能次数/冷却/重置问题；回答列写精确重置条件和跨回合保留规则。",
        "e. 其他；回答列写完整规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.copy)) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/复制或夺取`, makeQuestionId(row, q++), `技能「${skillName}」涉及复制、模仿、夺走、得到技能或特性。请确认复制对象、限制和前端选择方式。源文本：${fragment}`, [
        "a. 只能复制/夺取当前公开可见的技能或特性；前端列出合法选项。",
        "b. 可复制隐藏或未公开信息；回答列写哪些信息对哪些玩家可见。",
        "c. 复制后保留原技能费用、冷却、次数和目标方式。",
        "d. 复制后使用新的费用、次数或附加效果；回答列写完整覆盖规则。",
        "e. 其他；回答列写复制来源、持续时间、可叠加性和失效时机。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, patterns.passive) || hasAny(fragment, patterns.instant) || hasAny(fragment, patterns.toggle) || /^[￥¥]/.test(normalizeCell(fragment))) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/时机`, makeQuestionId(row, q++), `技能「${skillName}」写了被动、连锁、随时使用、开关或大招时机。请确认实际使用窗口。源文本：${fragment}`, [
        "a. 主动技能；回答列写可使用阶段、费用、次数和是否结束行动。",
        "b. 被动连锁技能；回答列写触发条件、连锁速度、是否可替队友响应和次数限制。",
        "c. 随时使用技能；回答列写己方回合/对方回合的可用窗口、速度和是否消耗行动。",
        "d. 开关技能；回答列写开启/关闭阶段、持续状态、结算时点和是否可连续切换。",
        "e. 大招；回答列写次数、重置条件、特殊时机和是否与其他限制共存。",
      ].join("\n"), fragment);
    }

    if (!targeted) {
      pushQuestion(questions, row, `${moduleName}/专属规则`, makeQuestionId(row, q++), `技能片段「${fragment}」不是纯通用技能，且源表没有足够信息被现有通用规则完全覆盖。请补齐该技能的核心实现边界。`, [
        "a. 作为一个主动技能实现；回答列写费用、次数、目标、效果和持续时间。",
        "b. 作为已有技能的变体实现；回答列写只覆盖哪些差异。",
        "c. 作为多阶段/子技能/召唤物组合实现；回答列写拆分、顺序和前端交互。",
        "d. 作为被动、随时或开关技能实现；回答列写触发/使用窗口和结算顺序。",
        "e. 暂不实现或其他；回答列写原因和后续处理。",
      ].join("\n"), `源表行 ${row.sourceRow}，特殊技能片段 ${index + 1}/${skillFragments.length}`);
    }
  }

  for (const [index, fragment] of traitFragments.entries()) {
    const moduleName = `特性:${index + 1}`;
    let targeted = false;

    if (hasAny(fragment, patterns.area) || hasAny(fragment, patterns.movement) || /占\s*\d|占\d|周围|范围|光环|可穿人|弧形/.test(fragment)) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/占格与前端`, makeQuestionId(row, q++), `特性片段涉及占格、范围、移动、普攻形状或前端显示。请确认表现和后端边界。源文本：${fragment}`, [
        "a. 改变入场占格/身体形状；回答列写宽高、出生规则、受击格和渲染方式。",
        "b. 改变移动或穿越规则；回答列写路径、终点、阻挡和与位移技能的关系。",
        "c. 改变普通攻击形状；回答列写方向/区域选择、命中双方或敌方、攻击次数消耗。",
        "d. 范围/光环动态生效；回答列写覆盖范围、进入/离开触发和前端标记。",
        "e. 其他；回答列写完整后端与前端规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, [...patterns.duration, ...patterns.stacking, ...patterns.resource, ...patterns.summon, ...patterns.field])) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/状态管理`, makeQuestionId(row, q++), `特性片段涉及持续、叠加、资源、召唤物或范围状态。请确认状态管理。源文本：${fragment}`, [
        "a. 状态绑定在该武将身上；回答列写触发时机、清理时机和次数限制。",
        "b. 状态绑定在被影响单位身上；回答列写受影响对象、计时边界和清理条件。",
        "c. 状态绑定在场地/天气/召唤物上；回答列写生命周期、覆盖关系和可见性。",
        "d. 有计数或叠加；回答列写上限、每层效果、获得/消耗和清除时机。",
        "e. 其他；回答列写完整状态规则。",
      ].join("\n"), fragment);
    }

    if (hasAny(fragment, [...patterns.hidden, ...patterns.ai, ...patterns.copy, ...patterns.random])) {
      targeted = true;
      pushQuestion(questions, row, `${moduleName}/可见性与AI`, makeQuestionId(row, q++), `特性片段涉及隐藏信息、随机、复制/控制或强制行动。请确认公开信息和 AI 边界。源文本：${fragment}`, [
        "a. 所有相关状态公开；回答列写前端显示位置和日志内容。",
        "b. 部分信息隐藏；回答列写队友、敌方、观战和回放视角分别可见什么。",
        "c. AI 需要特殊候选生成或评分；回答列写优先级、禁止动作和合法信息限制。",
        "d. 随机或强制行动需要特殊展示；回答列写随机来源、日志和玩家可控程度。",
        "e. 其他；回答列写完整可见性与 AI 规则。",
      ].join("\n"), fragment);
    }

    if (!targeted) {
      pushQuestion(questions, row, `${moduleName}/专属规则`, makeQuestionId(row, q++), `特性片段「${fragment}」不是纯通用特性，且源表没有足够信息被现有通用规则完全覆盖。请补齐该特性的核心实现边界。`, [
        "a. 常驻特性；回答列写影响的属性/规则、适用对象和前端显示。",
        "b. 触发式特性；回答列写触发条件、次数限制、连锁关系和结算顺序。",
        "c. 范围/光环特性；回答列写范围、动态生效、影响敌我和召唤物/分身关系。",
        "d. 隐藏/随机/复制相关特性；回答列写可见性、随机和 AI 限制。",
        "e. 暂不实现或其他；回答列写原因和后续处理。",
      ].join("\n"), `源表行 ${row.sourceRow}，特殊特性片段 ${index + 1}/${traitFragments.length}`);
    }
  }

  if (hasAny(specialText, [...patterns.hidden, ...patterns.ai, ...patterns.copy, ...patterns.random])) {
    pushQuestion(questions, row, "AI与可见性", makeQuestionId(row, q++), `该武将的专属规则包含隐藏信息、随机、复制/控制或强制行动。请确认 AI 和公开信息如何处理。`, [
      "a. AI 只能使用合法公开信息；隐藏真假、未公开状态、随机未揭示结果都不可偷看。",
      "b. 有特殊公开例外；回答列写哪些信息对哪些玩家可见。",
      "c. AI 需要特殊候选生成或评分策略；回答列写优先级和禁止动作。",
      "d. 暂不实现 AI 特化；只保证合法随机/基础动作，回答列可补充限制。",
      "e. 其他；回答列写完整 AI 与可见性规则。",
    ].join("\n"), `源表技能：${row.skills}\n源表特性：${row.traits}`);
  }

  return questions;
}

function addHeroQuestionsV3(row) {
  const questions = [];
  let q = 1;
  const text = `${row.skills}\n${row.traits}`;
  const asked = new Set();

  const ask = (key, moduleName, question, options, notes = "") => {
    if (asked.has(key)) return;
    asked.add(key);
    pushQuestion(questions, row, moduleName, makeQuestionId(row, q++), question, options.join("\n"), notes || `源表技能：${row.skills}\n源表特性：${row.traits}`);
  };

  if (/？|\?/.test(text)) {
    ask("unknown-values", "未知数值", `源表里有「？」或未给出的数值。请确认这些未知值如何落地，避免实现时自行猜数值。`, [
      "a. 按源文本里的点数分配/计算规则由玩家在使用时选择，回答列写每个未知项的合法范围。",
      "b. 这些「？」有固定数值，回答列写完整数值。",
      "c. 暂不实现含未知数值的这部分，回答列写跳过范围。",
    ]);
  }

  if (/（大）|\(大\)|最大|大吸魔|穿刺大|飞跃[（(]?大|神速[（(]?大|远程穿刺大/.test(row.skills)) {
    ask("big-common-variant", "技能变体", `源表写了已有技能的「大」或「最大」变体。请确认它和基础版的差异，避免实现时套成普通版。`, [
      "a. 只扩大范围/格数，回答列写扩大后的精确范围或格数。",
      "b. 费用、次数、伤害或破魔也变化，回答列写完整差异。",
      "c. 这是独立专属技能，回答列写完整规则。",
    ], row.skills);
  }

  if (/位置重合|重合/.test(text)) {
    ask("overlap-position", "重合与占位", `源表写到单位「位置重合」。当前移动/占位规则通常不允许落点重叠；请确认这里的例外。`, [
      "a. 允许该技能/单位临时与目标重叠，触发效果后立即破坏或离场，不保留重叠状态。",
      "b. 允许持续重叠；回答列写谁可被攻击、谁阻挡移动、点击时如何选中。",
      "c. 不允许真正重叠；只要移动路径经过目标格就触发。",
    ]);
  }

  if (/被视为地形|视作地形|视为地形/.test(text)) {
    ask("terrain-object", "地形对象", `源表把某个召唤/生成物写成「地形」。请确认它作为地形时和单位的边界。`, [
      "a. 占格并阻挡移动/出生，但不是单位，不进回合环，也不能被普通攻击选为单位目标。",
      "b. 既是地形也是可被攻击对象；回答列写能被哪些攻击/技能破坏。",
      "c. 不阻挡移动，只是前端标记和范围来源。",
    ]);
  }

  if (/不受到技能效果影响|不会受到技能效果影响|不受.*技能效果影响|无视.*技能效果/.test(text)) {
    ask("ignore-skill-effects", "技能效果免疫", `源表写「不受到技能效果影响」。请确认这里是否包含技能伤害。`, [
      "a. 不受技能伤害和技能附带效果影响。",
      "b. 只不受非伤害技能效果影响，技能伤害仍正常结算。",
      "c. 只不能被敌方技能影响，己方技能仍可影响。",
    ]);
  }

  if (/不受到伤害以外的效果影响/.test(text)) {
    ask("ignore-nondamage-effects", "非伤害效果免疫", `源表写「不受到伤害以外的效果影响」。请确认这个免疫是否连己方增益/治疗也排除。`, [
      "a. 只免疫敌方非伤害效果；己方增益、治疗和自身效果仍可生效。",
      "b. 免疫所有非伤害效果，包括己方增益和治疗。",
      "c. 只免疫控制/减益类效果，回答列写哪些效果仍可生效。",
    ]);
  }

  if (/编号/.test(text)) {
    ask("numbered-objects", "编号对象", `源表规则依赖「编号」。请确认编号如何产生和变化。`, [
      "a. 按召唤/生成顺序固定编号，之后不因位置或对象破坏重排。",
      "b. 每次规则检查时按当前位置重新编号，回答列写排序方式。",
      "c. 玩家在生成时选择编号，前端需要编号选择/显示。",
    ]);
  }

  if (/世界时钟/.test(text) && /世界之种/.test(text)) {
    ask("world-seed-name", "文本指代", `同一段里同时出现「世界之种」和「世界时钟」。请确认「世界时钟」是否就是「世界之种」。`, [
      "a. 是，同一个对象，按「世界之种」实现。",
      "b. 不是，是另一个对象；回答列写世界时钟的生成和属性。",
      "c. 源表笔误，回答列写正确名称。",
    ]);
  }

  if (/第二个对方回合|下个对方回合|防守回合|进攻回合/.test(text)) {
    ask("opponent-turn-wording", "回合指代", `源表使用「第二个对方回合 / 防守回合 / 进攻回合」这类尚未固定的回合表述。请确认在 n-v-n 回合环里的计数方式。`, [
      "a. 按该武将所属玩家/队伍面对的敌方英雄行动次数计数。",
      "b. 按被影响单位自己的敌我轮次计数。",
      "c. 按全局英雄回合顺序计数，回答列写触发的具体节点。",
    ]);
  }

  if (/结束对方.*回合|只有此单位可以行动|跳过.*回合|额外.*回合/.test(text)) {
    ask("turn-ring-control", "回合环控制", `源表会结束、跳过或独占后续回合。请确认它如何改动固定英雄回合环。`, [
      "a. 立即结束当前行动英雄的回合，回合环继续到下一个原本槽位。",
      "b. 立即跳到该武将自己的下一个行动槽位，期间其他英雄槽位跳过。",
      "c. 创建一个额外行动，不改变原本回合环；回答列写额外行动后回到哪里。",
    ]);
  }

  if (/挡开/.test(text)) {
    ask("block-counter", "挡开次数", `源表写「挡开攻击或技能」。请确认挡开具体阻止什么。`, [
      "a. 挡开一次敌方普攻或主动技能的伤害和附带效果。",
      "b. 只挡伤害，不挡非伤害附带效果。",
      "c. 像护盾一样只抵消一层伤害/效果；回答列写和破魔、无视魔免的关系。",
    ]);
  }

  if (/模仿|复制|夺走|夺取|得到.*技能|得到.*特性|获得.*技能|获得.*特性|特性增加那个单位|增加那个单位.*特性/.test(text)) {
    ask("copy-steal", "复制/夺取", `源表涉及复制、模仿、夺走或临时得到技能/特性。请确认复制对象和使用限制。`, [
      "a. 只能选择当前公开可见且可合法使用的技能/特性；复制后保留原费用、次数、目标方式。",
      "b. 可复制隐藏或暂不可用技能/特性；回答列写可见性例外和不可复制项。",
      "c. 复制后使用新的费用/次数/附加效果；回答列写完整覆盖规则。",
    ]);
  }

  if (/每次受到伤害最多|伤害上限|最多-/.test(text)) {
    ask("damage-cap", "伤害上限", `源表限制单次受到的伤害上限。请确认它对不同伤害类型的适用范围。`, [
      "a. 每个伤害实例分别封顶，普攻、技能、天气、场地和固定失血都适用。",
      "b. 只对普攻和技能伤害封顶，不影响天气/场地/固定失血。",
      "c. 只对敌方造成的伤害封顶，己方或自身代价不封顶。",
    ]);
  }

  if (/攻击己方加血|普攻击中己方.*血|攻击己方.*回血/.test(text)) {
    ask("attack-ally-heal", "攻击己方加血", `源表写「攻击己方加血」。请确认普攻己方单位时是治疗还是先造成伤害。`, [
      "a. 普攻己方不会造成伤害，改为治疗；回答列写治疗量。",
      "b. 普攻己方先造成正常伤害，再触发治疗。",
      "c. 只能把己方作为特殊技能目标，不允许普通攻击己方。",
    ]);
  }

  if (/攻击己方加魔|普攻击中己方.*魔/.test(text)) {
    ask("attack-ally-mana", "攻击己方加魔", `源表写「攻击己方加魔」。请确认普攻己方单位时是否造成伤害以及加魔量。`, [
      "a. 普攻己方不会造成伤害，改为给目标加魔；回答列写加魔量。",
      "b. 普攻己方先造成正常伤害，再给目标加魔。",
      "c. 只能作为特殊效果在命中己方时触发；回答列写触发条件和加魔量。",
    ]);
  }

  if (/能力值加成效果|拥有此单位的特性/.test(text)) {
    ask("share-traits-buffs", "特性/加成共享", `源表让其他单位拥有此单位的特性或能力值加成。请确认共享范围和动态更新。`, [
      "a. 只共享源表写明的常驻特性和当前数值加成，动态进入/离开范围时立即增减。",
      "b. 共享该武将全部当前特性，包括临时状态给出的特性。",
      "c. 只在回合开始或召唤时快照一次，之后不动态更新。",
    ]);
  }

  if (/附着/.test(text)) {
    ask("attach-state", "附着状态", `源表写「附着」到单位。请确认附着期间本体的占位和可交互状态。`, [
      "a. 本体仍在原格，可被攻击和行动，只是获得附着目标的一项能力/技能。",
      "b. 本体不占格或不可被选中，直到取消附着后返回。",
      "c. 本体移动到目标身上形成重叠；回答列写受击和移动规则。",
    ]);
  }

  if (/无法被吸魔|不能被吸魔/.test(text)) {
    ask("drain-immunity", "吸魔免疫", `源表写「无法被吸魔」。请确认它挡哪些魔力变化。`, [
      "a. 只免疫敌方吸魔/夺魔导致的当前魔减少。",
      "b. 免疫所有敌方导致的当前魔和魔上限减少。",
      "c. 免疫任何魔力减少，包括自身技能代价；回答列写例外。",
    ]);
  }

  if (/天气|叠加天气/.test(text) && /1\/2|随机|硬币|几率/.test(text)) {
    ask("random-weather", "随机天气", `源表的天气/场地包含随机失败或硬币结果。请确认随机结果的公开和结算时机。`, [
      "a. 每次单位声明攻击/主动技能时后端随机，结果公开进日志。",
      "b. 随机结果只对行动方公开；回答列写敌方和观战视角能看到什么。",
      "c. 天气生成时预先随机一组结果；回答列写保存和展示方式。",
    ]);
  }

  if (/不受到破魔效果影响|不受破魔效果影响|不受到破魔影响|不受破魔影响/.test(text)) {
    ask("ignore-pierce", "不受破魔", `源表写不受破魔效果影响。请确认破魔命中它时如何降级。`, [
      "a. 破魔只对该单位失效，护盾/魔免仍可正常阻挡。",
      "b. 只有破魔附带效果失效，破魔伤害仍穿盾。",
      "c. 该单位完全免疫带破魔标签的伤害和效果。",
    ]);
  }

  if (/同样的攻击|继承伤害和攻击效果/.test(text)) {
    ask("propagated-basic-attack", "普攻扩散", `源表让被攻击单位周围其他单位受到「同样的攻击」。请确认多目标普攻的结算方式。`, [
      "a. 原普攻只开一次连锁，结算时对扩散目标分别造成同等普攻伤害和普攻附带效果。",
      "b. 每个扩散目标分别开连锁窗口，可分别反应。",
      "c. 只复制伤害，不复制吸血、吸魔、链条等普攻附带效果。",
    ]);
  }

  if (/攻击一周|攻击3格|攻击4格|攻击5格|普攻距离|普攻.*全场|攻击全场/.test(text)) {
    ask("basic-attack-shape", "普攻形状", `源表改变普通攻击形状或距离。请确认声明方式和多目标命中。`, [
      "a. 玩家声明一个方向/目标格，按该形状命中范围内所有敌方单位。",
      "b. 玩家直接选择一个单位，只是射程/距离改变，不产生范围命中。",
      "c. 玩家选择一片区域；回答列写区域大小、是否命中双方和攻击次数消耗。",
    ]);
  }

  if (/无敌/.test(text)) {
    ask("invincible-scope", "无敌", `源表写「无敌」。请确认无敌阻止哪些内容。`, [
      "a. 不受伤害，但仍可受到非伤害效果影响。",
      "b. 不受伤害和敌方非伤害效果影响。",
      "c. 完全不可被选中；回答列写己方技能是否也不能选。",
    ]);
  }

  if (/特性计算为“无”|特性计算为无/.test(text)) {
    ask("trait-as-none", "特性视为无", `源表让目标在连锁计算时特性计算为「无」。请确认它具体影响哪些判定。`, [
      "a. 只在这次攻击的连锁窗口中忽略目标自身特性，不影响队友支援技能。",
      "b. 忽略目标及其队友因特性获得的所有反应/防御能力。",
      "c. 从声明攻击到结算结束都视为无特性，包括伤害后触发。",
    ]);
  }

  if (/随机一项能力/.test(text)) {
    ask("random-stat", "随机能力", `源表写「随机一项能力」。请确认随机池包含哪些能力。`, [
      "a. 只在攻、守、速、范中随机。",
      "b. 在攻、守、速、范、魔上限中随机。",
      "c. 包含当前魔/血等其他数值；回答列写完整随机池。",
    ]);
  }

  if (/撕裂(?![（(【])/.test(row.skills)) {
    ask("bare-rip", "缺少技能文本", `源表出现「撕裂」但没有括号规则文本。请确认它的技能规则。`, [
      "a. 这是已有技能的别名，回答列写对应技能名。",
      "b. 这是专属主动技能，回答列写费用、次数、目标和效果。",
      "c. 暂不实现此技能。",
    ], row.skills);
  }

  if (/范围一格以上/.test(text)) {
    ask("area-more-than-one", "范围一格以上", `源表写「范围一格以上」。请确认什么算范围伤害。`, [
      "a. 只要技能/攻击声明了多个格子，就算范围一格以上，即使最终只命中一个格。",
      "b. 只有同一单位被多个格子命中时才算。",
      "c. 只有实际影响多个单位时才算。",
    ]);
  }

  if (/占四格|占4格/.test(text)) {
    ask("occupy-four-cells", "占4格", `源表只写「占4格」。请确认入场形状。`, [
      "a. 固定 2*2。",
      "b. 直线 1*4 / 4*1，回答列写朝向。",
      "c. 非固定形状，回答列写形状和出生朝向规则。",
    ]);
  }

  if (/满状态复活|复活|重新召唤/.test(text) && /破坏|被破坏/.test(text)) {
    ask("revive-placement", "复活/重召唤", `源表写破坏后复活或重新召唤。请确认返回位置和被占用时处理。`, [
      "a. 优先原地返回；原位置不可用时在周围选择合法格。",
      "b. 由玩家在召唤者范内选择合法格。",
      "c. 固定在召唤者周围；回答列写若无合法格是否延后。",
    ]);
  }

  if (/记录那个技能|不再受到那个技能/.test(text)) {
    ask("record-killing-skill", "记录致命技能", `源表会记录造成破坏的技能并免疫之后的致命伤害。请确认记录粒度。`, [
      "a. 按技能名记录；同名技能都不能再对其造成致命伤害。",
      "b. 按具体施法者+技能记录；其他单位同名技能仍有效。",
      "c. 按伤害标签/类型记录；回答列写记录哪些标签。",
    ]);
  }

  if (/使对方移动或攻击或放出一个技能/.test(text)) {
    ask("control-enemy-action", "控制敌方行动", `源表让对方移动、攻击或放出技能。请确认谁选择动作和目标。`, [
      "a. 施法者选择动作、路径/目标和技能，但必须满足被控制单位的合法行动。",
      "b. 被控制单位的拥有者选择具体行动，施法者只指定类别。",
      "c. AI/随机选择合法行动；回答列写优先级。",
    ]);
  }

  if (/消失.*出现|出现前/.test(text)) {
    ask("banish-return", "消失返回", `源表让单位消失后原地出现。请确认返回格被占用或越界时如何处理。`, [
      "a. 原地可用则返回；不可用则由拥有者选择最近合法格。",
      "b. 原地不可用就延后到下个可用时点。",
      "c. 原地不可用则该单位被破坏。",
    ]);
  }

  if (/带有伤害的技能/.test(text)) {
    ask("damaging-skill-definition", "带有伤害的技能", `源表限制「带有伤害的技能」。请确认判断依据。`, [
      "a. 技能文本或当前结算会造成任意伤害就算，固定失血也算。",
      "b. 只有使用攻击公式的技能伤害算，固定失血/天气不算。",
      "c. 只看技能原始文本，不看临时附加伤害。",
    ]);
  }

  if (/穿人造成|穿人有伤害/.test(text)) {
    ask("pass-through-damage", "穿人伤害", `源表写穿人造成伤害。请确认触发次数。`, [
      "a. 每次移动路径经过一个单位，各对该单位结算一次伤害。",
      "b. 每个被穿过单位每回合最多受一次该伤害。",
      "c. 只有移动结束时重叠/相邻的单位受伤。",
    ]);
  }

  if (/受到2倍.*伤害|2倍的技能伤害|伤害\*2|伤害加倍|受到的.*伤害\+/.test(text)) {
    ask("damage-multiplier", "伤害倍率", `源表写受到伤害加倍或额外增加。请确认倍率作用在哪些伤害上。`, [
      "a. 只影响敌方普攻和技能造成的血量伤害。",
      "b. 影响所有血量伤害，包括天气、场地和固定失血。",
      "c. 只影响技能伤害；回答列写普攻是否例外。",
    ]);
  }

  if (/回复效果/.test(text) && /2倍|加倍/.test(text)) {
    ask("healing-multiplier", "回复倍率", `源表同时提到回复效果加倍/受影响。请确认它影响哪些回复。`, [
      "a. 只影响被技能治疗的回血/回魔。",
      "b. 影响所有回复，包括自然回血、自然回魔和吸血/吸魔。",
      "c. 只影响回血，不影响回魔。",
    ]);
  }

  if (/选择对方.*技能不能使用|选择.*一技能不能使用|不能使用.*技能/.test(text) && /选择/.test(text)) {
    ask("disable-chosen-skill", "禁用指定技能", `源表让玩家选择对方一个技能不能使用。请确认可选技能范围。`, [
      "a. 只能选择当前公开可见的主动技能。",
      "b. 可选择被动/随时/大招等任何技能；回答列写不可选类型。",
      "c. 随机或由目标方选择被禁技能；回答列写选择方。",
    ]);
  }

  if (/夺取对方一个主动技能|使用场上使用过的一个技能|上一次使用过的主动技能/.test(text)) {
    ask("used-skill-source", "已使用技能来源", `源表要得到或使用场上/对方已经使用过的技能。请确认候选池。`, [
      "a. 只看公开日志中已经成功使用过的主动技能。",
      "b. 包括使用失败、被无效或被连锁挡住的技能。",
      "c. 包括隐藏信息中的技能；回答列写各视角可见性。",
    ]);
  }

  if (/天堂之门|空间/.test(text)) {
    ask("space-marker", "空间/门标记", `源表让攻击或技能针对「空间」或声明「门」区域。请确认空间标记如何存在。`, [
      "a. 空间/门是不占格场地标记，可与单位重叠并显示在前端。",
      "b. 空间/门占格并限制进入/离开；回答列写是否阻挡移动和出生。",
      "c. 空间只记录被影响过的格子，不作为持续场地显示。",
    ]);
  }

  if (/依附/.test(text)) {
    ask("attached-unit", "依附单位", `源表写召唤物依附在单位上。请确认依附期间是否仍可被攻击或占格。`, [
      "a. 依附后不占格，作为目标身上的状态，可被指定清除但不能被普通攻击。",
      "b. 依附后仍占格并跟随目标移动，可被攻击。",
      "c. 依附只表示放置计数/状态，不保留召唤物实体。",
    ]);
  }

  if (/所有进攻单位|进攻单位/.test(text)) {
    ask("attacking-units", "进攻单位", `源表按「那个回合的所有进攻单位」结算。请确认哪些单位算进攻单位。`, [
      "a. 该回合中声明过普攻或主动技能的单位都算。",
      "b. 只有实际造成过伤害的单位算。",
      "c. 只有当前行动英雄算，召唤物/分身不算。",
    ]);
  }

  if (/对攻击或技能被.*单位的单位给予|攻击或技能被.*单位/.test(text)) {
    ask("retaliate-marked-ally", "被保护目标反伤", `源表让攻击或技能某个被标记/被保护单位的单位受到伤害。请确认触发窗口。`, [
      "a. 敌方声明攻击/技能并进入连锁时即可触发。",
      "b. 只有原动作实际命中或造成伤害后触发。",
      "c. 每个攻击者/施法者每回合最多触发一次；回答列写限制。",
    ]);
  }

  if (/不费魔使用.*次数|不费魔使用.*技能|加一次.*不费魔使用/.test(text)) {
    ask("free-skill-use", "免费技能次数", `源表增加不费魔使用某技能或任意技能的次数。请确认这次免费使用和普通次数的关系。`, [
      "a. 免费次数额外增加，不消耗该技能原本每回合次数。",
      "b. 只是把下一次原本可用的使用改为不费魔，仍消耗次数。",
      "c. 可突破冷却/大招限制；回答列写可突破哪些限制。",
    ]);
  }

  if (/掷硬币|扔硬币|硬币|投骰子|骰子|1\/2几率|有1\/2|1\/3几率/.test(text) && !/天气|叠加天气/.test(text)) {
    ask("random-result", "随机结果", `源表包含硬币或 1/2 几率。请确认随机发生时机和公开方式。`, [
      "a. 每次触发时后端随机，结果公开进日志。",
      "b. 随机结果只向相关玩家公开；回答列写各视角可见性。",
      "c. 玩家先选择是否触发，再随机；回答列写触发窗口。",
    ]);
  }

  if (/投骰子|骰子/.test(text)) {
    ask("dice-roll", "骰子", `源表使用投骰子结果。请确认骰子的面数和结果公开方式。`, [
      "a. 使用普通 6 面骰，结果公开进日志。",
      "b. 使用自定义面数；回答列写面数和每个结果。",
      "c. 改为后端等概率随机整数；回答列写范围。",
    ]);
  }

  if (/不能使用带有位移效果的技能|位移效果的技能/.test(text)) {
    ask("movement-skill-definition", "位移技能", `源表限制「带有位移效果的技能」。请确认哪些技能属于位移技能。`, [
      "a. 任何会移动自身、目标、召唤物或位置交换的技能都算。",
      "b. 只有移动自身的技能算，牵引/推开/交换不算。",
      "c. 只看技能原始文本，不看临时附加位移效果。",
    ]);
  }

  if (/受到技能伤害.*血全满|不受到技能伤害.*血全满|技能伤害.*血全满/.test(text)) {
    ask("skill-damage-heals", "技能伤害回血", `源表同时写不受技能伤害并在受到技能伤害时血全满。请确认触发顺序。`, [
      "a. 敌方技能伤害被防止，然后该单位回满血。",
      "b. 只有原本会造成技能伤害时才回满；无伤害技能不触发。",
      "c. 技能伤害正常结算后再回满；回答列写是否可能被破坏。",
    ]);
  }

  if (/保持距离|超过3格|随着此单位移动/.test(text)) {
    ask("tether-distance", "距离牵制", `源表要求两个单位保持距离或跟随移动。请确认强制移动规则。`, [
      "a. 当距离超过限制时，被影响单位沿最短路径尽量移动到合法格。",
      "b. 施法者移动时同步尝试拖动目标；无法合法放置则停在原地。",
      "c. 距离限制只禁止主动远离，不产生自动跟随移动。",
    ]);
  }

  if (/反击时的效果|反击.*周围/.test(text)) {
    ask("counter-effect", "反击效果", `源表改变反击效果。请确认反击是否替代普通攻击结算。`, [
      "a. 反击不再按普通攻击选单体，改为对写明范围内敌方单位造成伤害。",
      "b. 先对原攻击者反击，再追加范围伤害。",
      "c. 只有格挡成功后的反击改变；其他反击不变。",
    ]);
  }

  if (/持续效果不给予致命伤害|不给予致命伤害/.test(text)) {
    ask("nonlethal-dot", "持续效果不致命", `源表写持续效果不给予致命伤害。请确认血量下限。`, [
      "a. 该持续伤害最低把目标留在 1/4 血。",
      "b. 最低留在最小正血量；回答列写具体值。",
      "c. 只是不触发破坏奖励，但目标仍可到 0。",
    ]);
  }

  if (/不能封死/.test(text)) {
    ask("no-seal-area", "不能封死", `源表召唤地形/墙时写不能封死范围。请确认合法性判定。`, [
      "a. 不能让任意现存单位完全没有合法普通移动出口。",
      "b. 不能切断两方出生区或关键通路；回答列写检查范围。",
      "c. 只做局部检查：新召唤物不能围住任何单个单位的周围 8 格。",
    ]);
  }

  if (/无法被连锁|不能被连锁|所有技能.*无法被连锁/.test(text)) {
    ask("unchainable", "无法被连锁", `源表写技能或动作无法被连锁。请确认它阻止哪些反应。`, [
      "a. 敌方不能对这些动作使用任何被动/连锁技能。",
      "b. 只阻止目标自己的反应，队友支援仍可连锁。",
      "c. 只阻止防御连锁，不阻止随时技能或其他插入动作。",
    ]);
  }

  if (/牺牲.*魔以外.*能力|牺牲一点魔以外的能力/.test(text)) {
    ask("sacrifice-stat", "牺牲能力值", `源表写牺牲魔以外能力值。请确认可牺牲的能力和下限。`, [
      "a. 可选择攻/守/速/范之一 -1，到当前规则下限。",
      "b. 系统自动选择最高的一项；回答列写平局规则。",
      "c. 可扣到 0 或扣临时增益；回答列写持续时长。",
    ]);
  }

  if (/双方向攻击|4方向攻击|四方向攻击|攻击双方向/.test(text)) {
    ask("multi-direction-attack", "多方向攻击", `源表写双方向或四方向攻击。请确认一次普攻如何声明。`, [
      "a. 一次普攻选择一个方向组合，同时命中对应方向上的单位。",
      "b. 每个方向分别消耗一次普攻次数。",
      "c. 自动攻击所有合法方向；回答列写是否命中友军。",
    ]);
  }

  if (/自动.*使用|自动在周围召唤|每个己方回合开始时.*自动/.test(text)) {
    ask("automatic-skill", "自动使用", `源表写自动使用技能或自动召唤。请确认自动效果失败时如何处理。`, [
      "a. 自动效果在触发时尽量执行；没有合法目标/位置则跳过。",
      "b. 玩家在触发时选择目标/位置；若不选则视为放弃。",
      "c. 自动效果必须成功；若无合法位置则延后到下一次触发。",
    ]);
  }

  if (/一回合只能被攻击或技能一次|只能被攻击或技能一次/.test(text)) {
    ask("once-targeted-per-turn", "每回合只能被影响一次", `源表限制每回合只能被攻击或技能一次。请确认计数触发点。`, [
      "a. 被声明为目标或处在范围内就计数，即使最终无伤。",
      "b. 只有实际受到伤害或效果才计数。",
      "c. 普攻和技能分别各可一次；回答列写计数重置时机。",
    ]);
  }

  if (/体积增加|体积单独计算|每一格体积|占的1格破坏/.test(text)) {
    ask("multi-cell-body", "多格身体", `源表把身体体积逐格增长/破坏，并让每格单独计算。请确认身体格规则。`, [
      "a. 身体格必须保持正交连通；玩家选择增长/破坏格。",
      "b. 按固定顺序自动增长/破坏；回答列写顺序。",
      "c. 每格可独立受伤和攻击；回答列写整体死亡条件。",
    ]);
  }

  if (/从以下效果中选择|选择一个应用/.test(text)) {
    ask("target-chooses-effect", "选择效果", `源表让单位从多个效果中选择一个。请确认选择方。`, [
      "a. 被击中的单位拥有者选择承受哪个效果。",
      "b. 施法者选择给目标哪个效果。",
      "c. 后端随机选择；回答列写概率。",
    ]);
  }

  if (/代替受到伤害|代替.*伤害|代替破坏/.test(text)) {
    ask("take-damage-instead", "代替受伤/破坏", `源表写一个单位可代替另一个单位受伤或破坏。请确认代替后的结算。`, [
      "a. 代替者承受原本全部伤害/破坏，原目标不受该次伤害和效果。",
      "b. 只代替伤害，不代替非伤害附带效果。",
      "c. 两者都受到部分效果；回答列写分配规则。",
    ]);
  }

  if (/受到伤害后，当回合结束|受到伤害后.*回合结束/.test(text)) {
    ask("end-turn-on-damage", "受伤结束回合", `源表写受到伤害后当回合结束。请确认结束谁的回合。`, [
      "a. 结束当前行动英雄的回合，不论伤害来源是谁。",
      "b. 只在该武将自己的行动回合受伤时结束自己的回合。",
      "c. 结束造成伤害单位的回合；回答列写多段伤害时机。",
    ]);
  }

  if (/名字里带有|场上名为/.test(text)) {
    ask("name-matching", "名字匹配", `源表按名字包含某些字或场上同名单位结算。请确认匹配范围。`, [
      "a. 只匹配英雄本体的武将名，不匹配召唤物/分身显示名。",
      "b. 英雄、召唤物、分身的显示名都匹配。",
      "c. 只匹配己方单位；回答列写是否包含敌方同名。",
    ]);
  }

  if (/回到位置.*血.*魔|血，魔回到|回到.*回合.*开始/.test(text)) {
    ask("rewind-state", "状态回溯", `源表让单位回到本回合开始时的位置/血/魔。请确认回溯快照包含哪些内容。`, [
      "a. 只回溯位置、当前血和当前魔。",
      "b. 同时回溯状态、护盾、技能次数/冷却和临时增益。",
      "c. 只回溯己方可见状态；隐藏信息另按回答列说明。",
    ]);
  }

  if (/攻击2\*2|攻击\d+\*\d+|普攻.*\d+\*\d+/.test(text)) {
    ask("basic-attack-area", "普攻区域", `源表把普通攻击写成一个矩形/区域。请确认普攻区域声明方式。`, [
      "a. 玩家选择区域中心或角点，命中区域内所有敌方单位。",
      "b. 玩家选择一个单位，区域以目标占格为中心展开。",
      "c. 玩家选择方向，区域从自身身前展开。",
    ]);
  }

  if (/选择此单位一个特性|取消.*特性|特性无效/.test(text)) {
    ask("disable-trait", "取消/无效特性", `源表会取消或无效化特性。请确认可选特性和持续范围。`, [
      "a. 只能选择源表列出的常驻特性，临时状态给的特性不可选。",
      "b. 可选择当前拥有的任何特性，包括临时获得的特性。",
      "c. 无效化全部特性；回答列写是否影响已触发的持续效果。",
    ]);
  }

  if (/攻在\d+以上|攻.*以上的单位/.test(text)) {
    ask("attack-threshold-source", "攻击阈值", `源表按攻击值阈值判断单位是否能造成伤害或被限制。请确认使用哪个攻击值。`, [
      "a. 使用行动声明时的当前攻击值，包含临时增减。",
      "b. 使用单位基础攻击值，不看临时状态。",
      "c. 使用伤害结算时的最终攻击/威力；回答列写技能固定伤害怎么算。",
    ]);
  }

  if (/形成区域|声明.*区域|区域内|区域中/.test(text) && /持续|轮|回合|移动.*区域|不能离开/.test(text)) {
    ask("persistent-area", "持续区域", `源表形成一个持续区域并让区域内单位持续受影响。请确认区域移动、进入和离开时的动态规则。`, [
      "a. 区域是前端可见场地，单位进入/离开时动态获得或失去效果。",
      "b. 只影响生成时已经在区域内的单位，之后进入的不受影响。",
      "c. 区域可移动；回答列写移动时是否携带其中单位和效果。",
    ]);
  }

  if (/放置一个点|重置点|移动到那个点|声明.*门|位置重置/.test(text)) {
    ask("anchor-marker", "锚点/位置标记", `源表会放置、重置或移动到一个点/门/位置标记。请确认标记是否占格和被占用时处理。`, [
      "a. 标记不占格，可与单位重叠；移动到标记时若被占用则选择周围合法格。",
      "b. 标记占格并阻挡移动；被占用时不能移动过去。",
      "c. 标记只保存坐标，不显示在前端；回答列写可见性。",
    ]);
  }

  if (/交换位置|换位置/.test(text)) {
    ask("swap-position", "交换位置", `源表写两个单位交换位置。请确认多格单位或目标位置不合法时如何处理。`, [
      "a. 只有双方占格尺寸能互相合法放置时才能交换。",
      "b. 允许锚点交换，交换后尽量寻找合法落点。",
      "c. 只交换单格单位；多格单位不可作为目标。",
    ]);
  }

  if (/随意分配自己能力值|任意分配自己能力值|形状为1-6个任意摆放|任意摆放/.test(text)) {
    ask("custom-stats-shape", "自定义能力/形状", `源表允许每回合重新分配能力值或任意摆放身体形状。请确认前端输入和合法性。`, [
      "a. 回合开始时弹出配置，玩家分配攻守速范魔并选择连通身体格。",
      "b. 只允许选择预设形状/数值方案；回答列写预设列表。",
      "c. 自动按当前最优/默认分配；回答列写默认规则。",
    ]);
  }

  if (/额外使用此单位的技能|获得此单位.*一个技能|使用.*此单位的技能/.test(text)) {
    ask("grant-skill-use", "获得技能使用", `源表让另一个单位额外使用此单位的技能。请确认技能的费用、次数和归属。`, [
      "a. 使用者支付费用并消耗使用者的行动/次数，技能来源仍按原单位规则。",
      "b. 原单位支付费用和次数，使用者只作为位置/目标来源。",
      "c. 复制成使用者自己的临时技能；回答列写持续时长和可见性。",
    ]);
  }

  if (/所有持续效果结束|持续效果结束/.test(text)) {
    ask("clear-ongoing-effects", "清除持续效果", `源表会让所有持续效果结束。请确认清除范围。`, [
      "a. 清除目标身上的全部持续状态，包括增益和减益。",
      "b. 只清除敌方造成的负面持续状态。",
      "c. 也清除场地/天气/召唤物持续效果；回答列写范围。",
    ]);
  }

  if (/伤害.*日轮承受|由.*承受|让.*承受/.test(text)) {
    ask("damage-recipient-redirect", "伤害承受转移", `源表让另一个对象承受本应由该单位承受的伤害。请确认转移范围。`, [
      "a. 只转移血量伤害，非伤害效果仍作用于原目标。",
      "b. 伤害和附带效果都转移到承受者。",
      "c. 原目标和承受者都参与连锁；回答列写谁可反应。",
    ]);
  }

  if (/3\*无限|范围无限|范.*无限|速无限/.test(text)) {
    ask("infinite-range", "无限范围/速度", `源表写无限范围、无限速度或无限尺寸区域。请确认在有限棋盘上的落地方式。`, [
      "a. 按当前棋盘边界截断，覆盖所有符合方向/形状的格子。",
      "b. 只表示没有距离限制，仍需选择一个普通形状/目标。",
      "c. 前端显示为全场可选；回答列写是否需要方向。",
    ]);
  }

  if (/宣告一个主动技能|宣言一个.*技能|宣告.*指令/.test(text)) {
    ask("declare-disabled-action", "宣告禁用", `源表让玩家宣告一个技能或指令并使其无效/禁止。请确认宣告对象。`, [
      "a. 只能宣告公开可见的主动技能或基础指令。",
      "b. 可以宣告任意技能名，包括隐藏/未公开技能；回答列写可见性。",
      "c. 宣告后影响双方所有同名技能/指令；回答列写是否只影响目标单位。",
    ]);
  }

  if (/自由放\d+次墙|放\d+次墙|放4次墙/.test(text)) {
    ask("place-multiple-walls", "多次放墙", `源表让一次被动发动后自由放多次墙。请确认墙的目标和费用。`, [
      "a. 每次墙都选择一个正受影响目标，但本技能一次发动统一支付费用。",
      "b. 可在全场任意单位/格子放墙，不要求目标正受影响。",
      "c. 每放一次墙都独立检查费用、范围和连锁目标。",
    ]);
  }

  if (/加血；加魔|加血;加魔/.test(row.skills)) {
    ask("unspecified-heal-mana", "未写数值的回复", `源表写「加血；加魔」但没有数值。请确认回复量。`, [
      "a. 血 +1/4，魔 +1。",
      "b. 血回满，魔回满。",
      "c. 使用既有治疗技能的回血量，魔量另在回答列写明。",
    ]);
  }

  if (/下次攻击|下一次攻击/.test(text) && /移动/.test(text)) {
    ask("next-attack-move", "下一次攻击移动", `源表让下一次攻击变为先移动再伤害。请确认攻击声明和落点。`, [
      "a. 声明攻击目标后，攻击者必须先移动 1 格到合法格，再按新伤害结算原目标。",
      "b. 先选择移动落点，再从新位置重新选择攻击目标。",
      "c. 如果没有合法移动格，强化攻击不能使用。",
    ]);
  }

  if (/装备|解除装备/.test(text)) {
    ask("equip-object", "装备/解除装备", `源表写到召唤物或技能可以「装备 / 解除装备」。请确认装备期间对象是否仍作为单位存在。`, [
      "a. 装备后不再占格，作为装备状态挂在目标身上；解除时再召唤到周围。",
      "b. 装备后仍占格并跟随目标移动；回答列写受击和阻挡规则。",
      "c. 装备只是给目标加状态，原召唤物立即消失；解除时重新生成一个新对象。",
    ]);
  }

  if (/减1魔以外的能力|减去.*能力.*伤害不计算/.test(text)) {
    ask("stat-payment-prevent-damage", "能力值抵消伤害", `源表允许降低魔以外的能力值来让一次伤害不计算。请确认可支付的能力和时机。`, [
      "a. 可在受伤前选择攻/守/速/范之一 -1 到下限，阻止该次伤害。",
      "b. 系统按固定优先级自动扣能力；回答列写优先级。",
      "c. 可以扣临时加成或基础值；回答列写下限、是否可扣到 0、持续多久。",
    ]);
  }

  if (/强制选择|最近的对方|最近的敌|离此单位最近/.test(text)) {
    ask("forced-nearest-target", "强制最近目标", `源表要求强制选择最近的单位或自动攻击最近目标。请确认同距离和不可达时处理。`, [
      "a. 同距离由拥有者选择；若没有合法路径/目标则本次强制行动失败。",
      "b. 同距离随机；若不可达则选择下一个最近合法目标。",
      "c. 后端按固定排序自动选择；回答列写排序规则。",
    ]);
  }

  if (/挡住一个攻击或技能|无效下一次伤害|使一次技能无效|技能无效化|攻击或技能或移动无效|无效连锁|无效连锁中/.test(text)) {
    ask("negate-action", "无效化动作", `源表写到挡住/无效化攻击、技能、伤害或移动。请确认无效化范围。`, [
      "a. 只取消本次动作对被保护目标的伤害和效果，不取消动作对其他目标的结算。",
      "b. 取消整个攻击/技能/移动动作，所有目标都不再结算。",
      "c. 只取消伤害，非伤害附带效果仍结算。",
    ]);
  }

  if (/体型扩大|扩大一圈|体型.*扩大/.test(text)) {
    ask("body-growth", "体型扩大", `源表写体型扩大或扩大一圈。请确认新增身体格如何选择。`, [
      "a. 以当前身体外圈尽量扩张，保持连通；被占用/越界格跳过。",
      "b. 由玩家选择新增格，必须保持身体连通。",
      "c. 固定变成指定矩形；回答列写尺寸和锚点。",
    ]);
  }

  if (/满血受到致命伤害|血量为0时不会被破坏|血量为0时不.*破坏|剩1\/2的血|剩1\/4/.test(text)) {
    ask("survive-lethal", "致命伤害保留", `源表写到致命伤害后不破坏并保留血量。请确认触发限制和同一结算多段伤害。`, [
      "a. 每次伤害实例可触发一次，只要满足条件就把血设为写明数值。",
      "b. 每回合最多触发一次；同一动作后续伤害仍可能破坏。",
      "c. 每场最多触发一次；回答列写重置条件。",
    ]);
  }

  if (/位置试图造成伤害|试图造成伤害/.test(text)) {
    ask("attempted-damage-position", "试图造成伤害", `源表按「试图造成伤害」给资源。请确认护盾/免疫导致无伤时是否仍计入。`, [
      "a. 只要动作声明会影响对应位置就计入，即使最终被护盾/免疫挡住。",
      "b. 只有实际造成血量伤害才计入。",
      "c. 只要消耗护盾或产生有效效果也计入；完全无影响不计入。",
    ]);
  }

  if (/平分到/.test(text)) {
    ask("split-damage", "伤害平分", `源表把一次伤害减少后平分给双方。请确认小数和来源归属。`, [
      "a. 减免后的伤害平均分成两个伤害实例，来源仍是原攻击者。",
      "b. 反弹给攻击者的部分来源视为受击者。",
      "c. 小数按游戏当前小数血量保留；若有取整规则请在回答列写明。",
    ]);
  }

  if (/结伴而行/.test(text)) {
    ask("companion-travel", "结伴而行", `源表让任意己方单位可对该单位使用「结伴而行」。请确认这是共享技能还是特殊移动动作。`, [
      "a. 作为这些己方单位临时获得的主动技能，消耗其自己的行动/次数。",
      "b. 作为该武将的支援能力，由该武将消耗次数把友军拉到周围。",
      "c. 作为免费移动前置动作；回答列写每回合次数和是否结束行动。",
    ]);
  }

  if (/击中没有单位的区域/.test(text)) {
    ask("empty-area-hit", "空区域命中", `源表对「击中没有单位的区域」有特殊处理。请确认“没有单位”的判断范围。`, [
      "a. 所选区域内没有任何单位时触发。",
      "b. 只有中心格没有单位时触发。",
      "c. 每个空格分别触发；回答列写最多生成数量。",
    ]);
  }

  if (/交替变为|变为光属性|变为暗属性/.test(text)) {
    ask("alternating-element", "属性交替", `源表写属性交替变化。请确认切换时机。`, [
      "a. 每个自己的回合开始时切换一次。",
      "b. 每个全局英雄回合开始时切换一次。",
      "c. 每次行动或使用技能后切换；回答列写初始属性。",
    ]);
  }

  if (/分身\*\d+|召唤.*分身.*\d+/.test(text)) {
    ask("multi-clone-count", "多个分身", `源表写一次生成多个分身。请确认分身位置和真假处理。`, [
      "a. 玩家逐个选择合法分身格；真假按既有分身规则随机处理。",
      "b. 系统在周围/范围内随机生成；回答列写范围和失败时数量。",
      "c. 这些分身不隐藏真假；回答列写行动和破坏限制。",
    ]);
  }

  if (/乘骑|骑乘|可被.*乘/.test(text)) {
    ask("mount-specific", "乘骑对象", `源表写到特定单位可被乘骑或处于乘骑关系。请确认该坐骑的入场和骑乘边界。`, [
      "a. 入场时已存在并已被骑乘；回答列写坐骑相对骑手的位置和朝向。",
      "b. 不是骑士开局坐骑，只能通过技能后续骑乘；回答列写骑乘动作和限制。",
      "c. 乘骑时有额外受击/移动/技能差异，回答列写完整差异。",
    ]);
  }

  if (/有一次保护/.test(text)) {
    ask("one-protection", "一次保护", `源表写召唤物或单位「有一次保护」。请确认这一次保护是什么。`, [
      "a. 自带一次保护反应，可为自己抵挡一次。",
      "b. 自带一层护盾，不需要连锁。",
      "c. 可替队友保护一次；回答列写范围、费用和是否消耗行动。",
    ]);
  }

  if (/重置.*移动次数.*攻击次数|重置移动和攻击次数|重置次单位的移动次数和攻击次数/.test(text)) {
    ask("reset-actions", "重置行动次数", `源表会重置移动次数和攻击次数。请确认是否也允许继续使用技能。`, [
      "a. 只恢复普通移动和普攻次数，不重置技能使用次数，也不清除已使用主动技能标记。",
      "b. 恢复移动、普攻，并允许继续使用主动技能。",
      "c. 只在造成伤害后重置；回答列写触发条件。",
    ]);
  }

  if (/控制敌方|控制权|得到该单位控制权|将此单位的控制权/.test(text)) {
    ask("control-owner", "控制权", `源表会改变或获得单位控制权。请确认队伍、可见性和回合归属。`, [
      "a. 只改变操作者/所属方，单位留在原回合槽位。",
      "b. 改变队伍并移动到新队伍回合环；回答列写插入位置。",
      "c. 只是临时代控行动，不改变队伍和隐藏信息归属。",
    ]);
  }

  if (/技能费两倍|费两倍/.test(text)) {
    ask("double-skill-cost", "技能费用翻倍", `源表让针对该单位的技能费用翻倍。请确认费用不足和多目标时处理。`, [
      "a. 只要技能目标包含该单位，整个技能费用翻倍。",
      "b. 只有单体指定该单位时翻倍；范围技能不翻倍。",
      "c. 多目标按该单位对应的目标费用翻倍；回答列写墙/保护等多目标费用怎么算。",
    ]);
  }

  if (/威力减半/.test(text)) {
    ask("power-halved", "威力减半", `源表写技能效果无效或威力减半。请确认「威力」具体指什么。`, [
      "a. 只把技能伤害减半，非伤害效果仍正常。",
      "b. 伤害和数值型效果都减半，例如攻/守/速变化。",
      "c. 先判断技能是否无效；若不无效才减半，回答列写触发条件。",
    ]);
  }

  if (/写下0或1|猜|猜对|猜错/.test(text)) {
    ask("secret-guess", "秘密猜测", `源表需要一方秘密写下 0/1 并由另一方猜。请确认线上实现方式。`, [
      "a. 后端生成秘密选择，双方只看到结果；不要求玩家手动隐藏输入。",
      "b. 被猜方在前端提交隐藏选择，猜方再选择 0/1。",
      "c. 改为公开随机硬币；回答列写概率和日志显示。",
    ]);
  }

  if (/相同状态召唤一个相同单位|召唤一个相同单位|继承其技能使用状态/.test(text)) {
    ask("copy-unit-state", "复制单位状态", `源表会召唤一个相同单位并继承状态。请确认复制哪些状态。`, [
      "a. 复制基础属性、当前血魔、技能冷却/次数和公开状态。",
      "b. 只复制基础属性和技能列表，不复制血魔、冷却、隐藏状态。",
      "c. 复制全部状态包括隐藏信息；回答列写敌我可见性。",
    ]);
  }

  if (/魔上限|魔无上限|魔无限/.test(text)) {
    ask("mana-cap-special", "魔上限", `源表写魔上限、魔无限或特殊魔上限。请确认当前魔和上限如何显示/变化。`, [
      "a. 同时修改当前魔和魔上限，当前魔可增长到新上限。",
      "b. 只修改上限，当前魔不立即变化。",
      "c. 当前魔无上限但基础魔不变；回答列写显示方式。",
    ]);
  }

  if (/重置大招|重置“大招”|重置â€œ大招/.test(text)) {
    ask("reset-ultimate", "重置大招", `源表写「重置大招」。请确认多大招或已用/未用状态如何处理。`, [
      "a. 重置该武将所有已使用过的大招。",
      "b. 只重置最近使用或源表指定的大招；回答列写是哪一个。",
      "c. 只把大招冷却/次数恢复一次，不影响其他相关状态。",
    ]);
  }

  if (/破坏地形/.test(text)) {
    ask("destroy-terrain", "破坏地形", `源表写可以破坏地形。请确认哪些对象算地形。`, [
      "a. 只包括被标记为地形/场地的非单位对象。",
      "b. 包括墙、门、树根、牌等所有非英雄占格物。",
      "c. 包括召唤物；回答列写是否也能破坏坐骑/分身。",
    ]);
  }

  if (/原始能力值/.test(text)) {
    ask("restore-base-stats", "回到原始能力值", `源表写让单位回到原始能力值。请确认会清除哪些临时变化。`, [
      "a. 清除攻/守/速/范的临时增减，回到入场基础值；不改当前血魔。",
      "b. 连魔上限和当前魔也回到基础值。",
      "c. 只清除负面变化，不清除正面增益。",
    ]);
  }

  return questions;
}

function buildQuestions(rows) {
  const unimplemented = rows.filter((row) => !implementedHeroNames.has(row.name));
  return unimplemented.flatMap(addHeroQuestionsV3);
}

async function summarizeSource() {
  const rows = await readHeroRows();
  const unimplemented = rows.filter((row) => !implementedHeroNames.has(row.name));
  const questions = buildQuestions(rows);
  console.log(JSON.stringify({
    totalHeroes: rows.length,
    implementedHeroes: rows.length - unimplemented.length,
    unimplementedHeroes: unimplemented.length,
    questionCount: questions.length,
    firstUnimplemented: unimplemented.slice(0, 25).map((row) => ({
      row: row.sourceRow,
      name: row.name,
      questions: addHeroQuestionsV3(row).length,
      skills: row.skills.slice(0, 120),
      traits: row.traits.slice(0, 120),
    })),
  }, null, 2));
}

async function buildWorkbook() {
  const rows = await readHeroRows();
  const unimplemented = rows.filter((row) => !implementedHeroNames.has(row.name));
  const questions = buildQuestions(rows);

  const workbook = Workbook.create();
  const summary = workbook.worksheets.getOrAdd("说明", { renameFirstIfOnlyNewSpreadsheet: true });
  const sheet = workbook.worksheets.add("问卷");

  summary.showGridLines = false;
  summary.getRange("A1:G1").values = [["武将实现问题清单"]];
  summary.mergeCells("A1:G1");
  summary.getRange("A1").format = { font: { bold: true, size: 16, color: "#FFFFFF" }, fill: "#1F4E79", horizontalAlignment: "center" };
  summary.getRange("A3:B12").values = [
    ["源 Excel", sourcePath],
    ["输出文件", outputPath],
    ["源表武将数", rows.length],
    ["已实现武将数", rows.length - unimplemented.length],
    ["未实现武将数", unimplemented.length],
    ["问题总数", questions.length],
    ["回答方式", "在“你的回答”列填写 a/b/c/d/e 等选项字母；如果都不合适，可以直接手写答案。"],
    ["实现流程", "回答完本问卷后，再按问卷答案一次性实现未实现武将。"],
    ["共享规则", "问卷不包含共享/默认规则确认题；纯既有技能、纯既有特性和默认规则自动适用。"],
    ["质量要求", "不要因为问卷格式减少歧义数量；每个武将回答后应无剩余规则或前端实现歧义。"],
  ];
  summary.getRange("A3:A12").format = { font: { bold: true }, fill: "#D9EAF7" };
  summary.getRange("A3:B12").format.borders = { preset: "all", style: "thin", color: "#A6A6A6" };
  summary.getRange("A:A").format.columnWidthPx = 150;
  summary.getRange("B:B").format.columnWidthPx = 760;
  summary.getRange("B3:B12").format.wrapText = true;

  const headers = ["武将名字", "模块/技能/特性/前端", "问题ID", "问题", "选项", "你的回答", "备注/源文本", "源表行"];
  const data = questions.map((question) => [
    question.hero,
    question.module,
    question.questionId,
    question.question,
    question.options,
    question.answer,
    question.notes,
    question.sourceRow,
  ]);
  sheet.getRangeByIndexes(0, 0, data.length + 1, headers.length).values = [headers, ...data];
  sheet.freezePanes.freezeRows(1);
  sheet.getRange("A1:H1").format = { font: { bold: true, color: "#FFFFFF" }, fill: "#1F4E79" };
  sheet.getRange(`A1:H${data.length + 1}`).format.borders = { preset: "all", style: "thin", color: "#D9D9D9" };
  sheet.getRange(`A1:H${data.length + 1}`).format.wrapText = true;
  sheet.getRange("A:A").format.columnWidthPx = 150;
  sheet.getRange("B:B").format.columnWidthPx = 180;
  sheet.getRange("C:C").format.columnWidthPx = 110;
  sheet.getRange("D:D").format.columnWidthPx = 520;
  sheet.getRange("E:E").format.columnWidthPx = 500;
  sheet.getRange("F:F").format.columnWidthPx = 180;
  sheet.getRange("G:G").format.columnWidthPx = 430;
  sheet.getRange("H:H").format.columnWidthPx = 80;
  sheet.getRange(`F2:F${data.length + 1}`).format.fill = "#FFF2CC";
  sheet.getRange(`A2:H${data.length + 1}`).format.verticalAlignment = "top";
  sheet.tables.add(`A1:H${data.length + 1}`, true, "HeroQuestionnaire");

  await fs.mkdir(path.dirname(outputPath), { recursive: true });

  const summaryPreview = await workbook.render({ sheetName: "说明", autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(path.dirname(outputPath), "武将实现问题清单_说明预览.png"), new Uint8Array(await summaryPreview.arrayBuffer()));
  const questionnairePreview = await workbook.render({ sheetName: "问卷", range: "A1:H40", scale: 1, format: "png" });
  await fs.writeFile(path.join(path.dirname(outputPath), "武将实现问题清单_问卷预览.png"), new Uint8Array(await questionnairePreview.arrayBuffer()));

  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 20 },
    summary: "formula error scan",
  });
  const preview = await workbook.inspect({
    kind: "table",
    range: "问卷!A1:H8",
    include: "values",
    tableMaxRows: 8,
    tableMaxCols: 8,
  });

  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(outputPath);
  console.log(JSON.stringify({
    outputPath,
    sourcePath,
    totalHeroes: rows.length,
    implementedHeroes: rows.length - unimplemented.length,
    unimplementedHeroes: unimplemented.length,
    questionCount: questions.length,
    errorScan: errors.ndjson,
    preview: preview.ndjson,
  }, null, 2));
}

if (mode === "inspect") {
  await inspectSource();
} else if (mode === "summary") {
  await summarizeSource();
} else if (mode === "build") {
  await buildWorkbook();
}
