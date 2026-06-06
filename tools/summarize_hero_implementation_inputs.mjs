import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, "..");
const sourcePath = path.join(rootDir, "data", "武将yoo.xlsx");
const questionnairePath = path.join(rootDir, "docs", "武将实现问题清单.xlsx");
const outputPath = path.join(rootDir, "tools", "hero_implementation_inputs.summary.json");

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

const commonTraitNames = new Set([
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

function normalize(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function numberOrText(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "number") return value;
  const trimmed = String(value).trim();
  const n = Number(trimmed);
  return Number.isFinite(n) && trimmed !== "" ? n : trimmed;
}

function splitTopLevel(text, delimiters) {
  const fragments = [];
  let current = "";
  let depth = 0;
  for (const ch of normalize(text)) {
    if (ch === "（" || ch === "(") depth += 1;
    if ((ch === "）" || ch === ")") && depth > 0) depth -= 1;
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
  const value = normalize(text)
    .replace(/\s+/g, " ")
    .replace(/）(?=[^\s￥¥】）])/g, "） ")
    .replace(/\)(?=[^\s￥¥\]\)])/g, ") ")
    .replace(/([￥¥])/g, " $1")
    .replace(/(【\s*\d+(?:\.\d+)?\s*(?=[\p{Script=Han}A-Za-z]))/gu, " $1");
  return splitTopLevel(value, new Set([" "])).filter(Boolean);
}

function splitTraitFragments(text) {
  const value = normalize(text);
  if (!value || value === "无") return [];
  return splitTopLevel(value, new Set(["；", ";"])).filter(Boolean);
}

function extractSkillName(fragment) {
  let value = normalize(fragment)
    .replace(/^[￥¥]\s*/, "")
    .replace(/^【\s*[\d.]+\s*/, "")
    .replace(/^【\s*/, "");
  const paren = value.search(/[（(]/);
  if (paren >= 0) value = value.slice(0, paren);
  value = value.replace(/【.*$/, "");
  value = value.replace(/[：:，,；;。].*$/, "").trim();
  return value || "未命名片段";
}

function compact(text) {
  return normalize(text).replace(/\s+/g, "").replace(/[。；;，,]/g, "");
}

function stripSkillRulePrefix(text) {
  return normalize(text)
    .replace(/^[￥¥]\s*/, "")
    .replace(/^【\s*\d+(?:\.\d+)?\s*/, "")
    .replace(/^【\s*/, "")
    .trim();
}

function isPlainCommonSkill(fragment) {
  const stripped = stripSkillRulePrefix(fragment);
  if (!stripped) return true;
  const skillName = extractSkillName(fragment);
  const compacted = compact(stripped);
  const compactedName = compact(skillName);
  const isCommonName = commonSkillNames.has(skillName) || commonSkillNames.has(stripped) || commonSkillNames.has(compacted);
  if (!isCommonName) return false;
  if (compacted === compactedName) return true;
  return !/[（(]|\d+\s*\*\s*\d+|额外|特殊|每|直到|持续|不能|破魔|半破魔|无视|召唤|制造|魔力点|计数点|标记|复制|夺走|随机|硬币|天气|场|范围|选择|移动|位移|交换|上限|重置|冷却|次数|一回合|一轮|下回合|本回合/.test(stripped);
}

function isPlainCommonTrait(fragment) {
  const compacted = compact(fragment);
  return !compacted || commonTraitNames.has(compacted);
}

async function importWorkbook(filePath) {
  const blob = await FileBlob.load(filePath);
  return SpreadsheetFile.importXlsx(blob);
}

async function readSourceRows() {
  const workbook = await importWorkbook(sourcePath);
  const sheet = workbook.worksheets.getItem("最新武将");
  const values = sheet.getRange(sheet.getUsedRange().address).values;
  const rows = [];
  for (let index = 1; index < values.length; index += 1) {
    const row = values[index] || [];
    const name = normalize(row[4]);
    if (!name) continue;
    rows.push({
      sourceRow: index + 1,
      level: numberOrText(row[0]),
      role: normalize(row[1]),
      attribute: normalize(row[2]) === "炎" ? "火" : normalize(row[2]),
      race: normalize(row[3]),
      name,
      attack: numberOrText(row[5]),
      defense: numberOrText(row[6]),
      speed: numberOrText(row[7]),
      range: numberOrText(row[8]),
      mana: numberOrText(row[9]),
      skills: normalize(row[10]),
      traits: normalize(row[11]),
      skillFragments: splitSkillFragments(row[10]).map((fragment) => ({
        fragment,
        name: extractSkillName(fragment),
        common: isPlainCommonSkill(fragment),
      })),
      traitFragments: splitTraitFragments(row[11]).map((fragment) => ({
        fragment,
        common: isPlainCommonTrait(fragment),
      })),
    });
  }
  return rows;
}

async function readQuestionnaire() {
  const workbook = await importWorkbook(questionnairePath);
  const sheet = workbook.worksheets.getItem("问卷");
  const values = sheet.getRange(sheet.getUsedRange().address).values;
  const byHero = new Map();
  const questions = [];
  for (let index = 1; index < values.length; index += 1) {
    const row = values[index] || [];
    const question = {
      row: index + 1,
      hero: normalize(row[0]),
      module: normalize(row[1]),
      id: normalize(row[2]),
      question: normalize(row[3]),
      options: normalize(row[4]),
      answer: normalize(row[5]),
      notes: normalize(row[6]),
      sourceRow: numberOrText(row[7]),
    };
    if (!question.hero) continue;
    questions.push(question);
    if (!byHero.has(question.hero)) byHero.set(question.hero, []);
    byHero.get(question.hero).push(question);
  }
  return { questions, byHero };
}

function answerLetter(answer) {
  const value = normalize(answer).toLowerCase();
  const match = value.match(/^[a-z]/);
  return match ? match[0] : "";
}

function classifyHero(row, questions) {
  const specialSkills = row.skillFragments.filter((item) => !item.common);
  const specialTraits = row.traitFragments.filter((item) => !item.common);
  const blanks = questions.filter((question) => !question.answer);
  const freeform = questions.filter((question) => !answerLetter(question.answer) && question.answer);
  return {
    sourceRow: row.sourceRow,
    name: row.name,
    stats: {
      level: row.level,
      role: row.role,
      attribute: row.attribute,
      race: row.race,
      attack: row.attack,
      defense: row.defense,
      speed: row.speed,
      range: row.range,
      mana: row.mana,
    },
    rawSkillText: row.skills,
    rawTraitText: row.traits,
    skillFragments: row.skillFragments,
    traitFragments: row.traitFragments,
    specialSkillCount: specialSkills.length,
    specialTraitCount: specialTraits.length,
    questionnaireQuestionCount: questions.length,
    blankAnswerCount: blanks.length,
    freeformAnswerCount: freeform.length,
    blankQuestions: blanks.map(({ row: xlsxRow, id, module, question }) => ({ xlsxRow, id, module, question })),
    answers: questions.map(({ row: xlsxRow, id, module, question, options, answer, notes }) => ({
      xlsxRow,
      id,
      module,
      question,
      options,
      answer,
      letter: answerLetter(answer),
      notes,
    })),
  };
}

const sourceRows = await readSourceRows();
const { questions, byHero } = await readQuestionnaire();
const unimplementedRows = sourceRows.filter((row) => !implementedHeroNames.has(row.name));
const heroes = unimplementedRows.map((row) => classifyHero(row, byHero.get(row.name) || []));

const summary = {
  sourcePath,
  questionnairePath,
  totalSourceHeroes: sourceRows.length,
  implementedHeroCount: sourceRows.length - unimplementedRows.length,
  unimplementedHeroCount: unimplementedRows.length,
  questionnaireRows: questions.length,
  heroesWithBlankAnswers: heroes.filter((hero) => hero.blankAnswerCount > 0).map((hero) => ({
    sourceRow: hero.sourceRow,
    name: hero.name,
    blankAnswerCount: hero.blankAnswerCount,
    blankQuestions: hero.blankQuestions,
  })),
  implementationBuckets: {
    noQuestionnaireRows: heroes.filter((hero) => hero.questionnaireQuestionCount === 0).length,
    noSpecialTextByHeuristic: heroes.filter((hero) => hero.specialSkillCount === 0 && hero.specialTraitCount === 0).length,
    commonOnlyAnswered: heroes.filter((hero) => hero.specialSkillCount === 0 && hero.specialTraitCount === 0 && hero.blankAnswerCount === 0).length,
    hasSpecialRulesAnswered: heroes.filter((hero) => (hero.specialSkillCount > 0 || hero.specialTraitCount > 0) && hero.blankAnswerCount === 0).length,
    hasBlankAnswers: heroes.filter((hero) => hero.blankAnswerCount > 0).length,
  },
  firstTwenty: heroes.slice(0, 20).map((hero) => ({
    sourceRow: hero.sourceRow,
    name: hero.name,
    stats: hero.stats,
    specialSkillCount: hero.specialSkillCount,
    specialTraitCount: hero.specialTraitCount,
    questionnaireQuestionCount: hero.questionnaireQuestionCount,
    blankAnswerCount: hero.blankAnswerCount,
    rawSkillText: hero.rawSkillText,
    rawTraitText: hero.rawTraitText,
  })),
  heroes,
};

await fs.writeFile(outputPath, JSON.stringify(summary, null, 2), "utf8");
console.log(JSON.stringify({
  outputPath,
  totalSourceHeroes: summary.totalSourceHeroes,
  implementedHeroCount: summary.implementedHeroCount,
  unimplementedHeroCount: summary.unimplementedHeroCount,
  questionnaireRows: summary.questionnaireRows,
  heroesWithBlankAnswers: summary.heroesWithBlankAnswers,
  implementationBuckets: summary.implementationBuckets,
  firstTwenty: summary.firstTwenty,
}, null, 2));
