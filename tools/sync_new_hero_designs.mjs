import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve(import.meta.dirname, "..");
const jsonPath = path.join(root, "docs", "武将设计思想.json");
const reviewPath = path.join(root, "docs", "武将符合性审查.json");
const workbookPath = path.join(root, "docs", "武将实现问题清单.xlsx");
const targetCodes = new Set(process.argv.slice(2));
if (!targetCodes.size) throw new Error("Provide at least one hero code.");

const archive = JSON.parse(await fs.readFile(jsonPath, "utf8"));
const heroes = archive.heroes.filter((hero) => targetCodes.has(hero.code));
if (heroes.length !== targetCodes.size) throw new Error("A requested design baseline is missing.");

const source = await FileBlob.load(workbookPath);
const workbook = await SpreadsheetFile.importXlsx(source);
const sheet = workbook.worksheets.getItem("武将设计思想");
const existing = sheet.getRange("C2:C500").values.map((row) => String(row[0] ?? ""));
const columns = [
  "batch", "source_row", "code", "name", "baseline_method", "baseline_locked_at",
  "player_fantasy", "battlefield_role", "design_intent", "core_decisions",
  "operation_sequence", "resource_cadence", "skill_synergy", "strengths", "weaknesses",
  "counterplay", "ai_policy", "ai_priorities", "ai_prohibited_behaviors",
  "general_ai_lesson", "key_rules", "interaction_boundaries", "unresolved_ambiguities",
  "expected_signals", "risk_patterns", "source_clarifications", "review_status",
];
for (const hero of heroes) {
  const at = existing.indexOf(hero.code);
  const row = at >= 0 ? at + 2 : existing.findIndex((code) => !code) + 2;
  if (row < 2) throw new Error("No free design-sheet row found.");
  const inference = (hero.inferred_rulings ?? []).map((item, index) =>
    `${index + 1}. ${item.topic}｜结论：${item.decision}｜依据：${item.reasoning}｜放弃方案：${item.rejected_alternatives}｜置信度：${item.confidence}｜玩法后果：${item.gameplay_consequence}`
  ).join("\n");
  sheet.getRange(`A${row}:AB${row}`).values = [[...columns.map((key) => hero[key] ?? ""), inference]];
  sheet.getRange(`A${row}:AB${row}`).format.wrapText = true;
  sheet.getRange(`A${row}:AB${row}`).format.verticalAlignment = "top";
  existing[row - 2] = hero.code;
}
const questions = workbook.worksheets.getItem("问卷");
const existingQuestionIds = questions.getRange("C2:C1000").values.map((row) => String(row[0] ?? ""));
for (const hero of heroes) {
  for (const item of hero.questionnaire_items ?? []) {
    if (existingQuestionIds.includes(item.id)) continue;
    const row = existingQuestionIds.findIndex((code) => !code) + 2;
    if (row < 2) throw new Error("No free questionnaire row found.");
    questions.getRange(`A${row}:H${row}`).values = [[
      hero.name, item.area, item.id, item.question, item.options, "",
      `源表第${hero.source_row}行；设计基线推定${item.inferred}，非用户回答；依据见docs/武将设计思想.json#${hero.code}。`,
      hero.source_row,
    ]];
    questions.getRange(`A${row}:H${row}`).format.wrapText = true;
    questions.getRange(`A${row}:H${row}`).format.verticalAlignment = "top";
    existingQuestionIds[row - 2] = item.id;
  }
}
const review = JSON.parse(await fs.readFile(reviewPath, "utf8"));
const summary = workbook.worksheets.getItem("说明");
const sourceHeroCount = Number(summary.getRange("B5").values[0][0]);
const publicHeroCount = Number(review.developed_hero_inspection_progress?.developed_public_total);
if (Number.isFinite(sourceHeroCount) && Number.isFinite(publicHeroCount)) {
  summary.getRange("B6:B8").values = [
    [publicHeroCount],
    [Math.max(0, sourceHeroCount - publicHeroCount)],
    [existingQuestionIds.filter(Boolean).length],
  ];
}
const compliance = workbook.worksheets.getItem("武将符合性审查");
const complianceCodes = compliance.getRange("A2:A1000").values.map((row) => String(row[0] ?? ""));
for (const hero of heroes) {
  const record = review.heroes.find((item) => item.code === hero.code);
  if (!record) throw new Error(`No conformity review for ${hero.code}.`);
  const priorRows = complianceCodes.flatMap((code, index) => code === hero.code ? [index + 2] : []);
  for (const [index, item] of record.items.entries()) {
    const row = priorRows[index] ?? (complianceCodes.findIndex((code) => !code) + 2);
    if (row < 2) throw new Error("No free conformity-sheet row found.");
    compliance.getRange(`A${row}:M${row}`).values = [[
      record.code, record.name, record.review_status, item.layer, item.requirement,
      item.evidence, item.verdict, item.severity, item.planned_change, item.status,
      record.design_baseline, record.audit_evidence, review.review_process,
    ]];
    compliance.getRange(`A${row}:M${row}`).format.wrapText = true;
    compliance.getRange(`A${row}:M${row}`).format.verticalAlignment = "top";
    complianceCodes[row - 2] = hero.code;
  }
}
const output = await SpreadsheetFile.exportXlsx(workbook);
const temporary = `${workbookPath}.new`;
await output.save(temporary);
await fs.rename(temporary, workbookPath);
console.log(`Synchronized ${heroes.map((hero) => hero.code).join(", ")} to 武将设计思想.`);
