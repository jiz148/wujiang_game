import fs from "node:fs/promises";
import assert from "node:assert/strict";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = "C:/Users/jiz14/TeamGH/wujiang_game";
const outputDir = `${root}/outputs/hero-review-followup-20260904`;
const inputPath = `${root}/docs/武将实现问题清单.xlsx`;
const outputPath = `${outputDir}/武将实现问题清单.xlsx`;
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const inventory = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 12000 });
const sheets = inventory.ndjson.split("\n").filter(Boolean).map(JSON.parse).filter(row => row.kind === "sheet");
await fs.writeFile(`${outputDir}/sheets.ndjson`, inventory.ndjson);
const before = new Map(sheets.map(({ name }) => {
  const range = workbook.worksheets.getItem(name).getUsedRange();
  return [name, { values: structuredClone(range.values), formulas: structuredClone(range.formulas) }];
}));

async function inspectAndRender(book, suffix) {
  for (const [name, range, label] of [
    ["武将设计思想", "Z1:AA7", "design"],
    ["武将符合性审查", "D1:J4", "conformity"],
  ]) {
    const result = await book.inspect({ kind: "table", range: `${name}!${range}`, include: "values,formulas", tableMaxRows: 7, tableMaxCols: 7, maxChars: 12000 });
    await fs.writeFile(`${outputDir}/${label}_${suffix}.ndjson`, result.ndjson);
    const preview = await book.render({ sheetName: name, range, scale: 1, format: "png" });
    await fs.writeFile(`${outputDir}/${label}_${suffix}.png`, new Uint8Array(await preview.arrayBuffer()));
  }
}

if (process.argv.includes("--inspect")) {
  console.log(JSON.stringify({savedAnswers: before.get("问卷").values.filter(row => ["精兵", "吟游诗人", "元素猎人"].includes(row[0]))}));
  await inspectAndRender(workbook, "before");
  console.log(JSON.stringify({ sheets: sheets.map(row => row.name), beforePreviews: true }));
} else {
  const design = JSON.parse(await fs.readFile(`${root}/docs/武将设计思想.json`, "utf8"));
  const review = JSON.parse(await fs.readFile(`${root}/docs/武将符合性审查.json`, "utf8"));
  const designSheet = workbook.worksheets.getItem("武将设计思想");
  const originalDesign = before.get("武将设计思想").values;
  const designFields = ["batch", "source_row", "code", "name", "baseline_method", "baseline_locked_at", "player_fantasy", "battlefield_role", "design_intent", "core_decisions", "operation_sequence", "resource_cadence", "skill_synergy", "strengths", "weaknesses", "counterplay", "ai_policy", "ai_priorities", "ai_prohibited_behaviors", "general_ai_lesson", "key_rules", "interaction_boundaries", "unresolved_ambiguities", "expected_signals", "risk_patterns", "source_clarifications", "review_status", "inferred_rulings"];
  let designRowCount = originalDesign.length;
  for (const hero of design.heroes) {
    const existing = originalDesign.findIndex((row, index) => index > 0 && row[2] === hero.code);
    if (existing > 0) {
      designSheet.getRange(`AA${existing + 1}`).values = [[hero.review_status]];
      if (hero.batch === "R01") {
        designSheet.getRange(`A${existing + 1}:AB${existing + 1}`).values = [designFields.map(key => {
          const value = hero[key];
          return value == null ? null : typeof value === "object" ? JSON.stringify(value) : value;
        })];
        designSheet.getRange(`A${existing + 1}:AB${existing + 1}`).format.rowHeight = 260;
        designSheet.getRange(`A${existing + 1}:AB${existing + 1}`).format.wrapText = true;
      }
    } else {
      const values = designFields.map(key => {
        const value = hero[key];
        return value == null ? null : typeof value === "object" ? JSON.stringify(value) : value;
      });
      designSheet.tables.items[0].rows.add(null, [values]);
      designRowCount += 1;
      designSheet.getRange(`A${designRowCount}:AB${designRowCount}`).copyFrom(designSheet.getRange(`A${designRowCount - 1}:AB${designRowCount - 1}`), "all");
      designSheet.getRange(`A${designRowCount}:AB${designRowCount}`).values = [values];
      designSheet.getRange(`A${designRowCount}:AB${designRowCount}`).format.rowHeight = 260;
      designSheet.getRange(`A${designRowCount}:AB${designRowCount}`).format.wrapText = true;
    }
  }
  const conformity = workbook.worksheets.getItem("武将符合性审查");
  const rows = review.heroes.flatMap(hero => hero.items.map(item => [
    hero.code, hero.name, hero.review_status, item.layer, item.requirement,
    item.evidence, item.verdict, item.severity, item.planned_change, item.status,
    hero.design_baseline, hero.audit_evidence, review.review_process,
  ]));
  const existingRows = before.get("武将符合性审查").values.length - 1;
  assert.ok(rows.length >= existingRows);
  assert.equal(conformity.tables.items.length, 1);
  for (let index = existingRows; index < rows.length; index += 1) {
    conformity.tables.items[0].rows.add(null, [rows[index]]);
    conformity.getRange(`A${index + 2}:M${index + 2}`).copyFrom(conformity.getRange(`A${index + 1}:M${index + 1}`), "all");
  }
  conformity.getRange(`A2:M${rows.length + 1}`).values = rows;

  workbook.recalculate();
  const expected = new Map(sheets.map(({ name }) => [name, structuredClone(workbook.worksheets.getItem(name).getUsedRange().values)]));
  for (const { name } of sheets) {
    if (!["武将设计思想", "武将符合性审查"].includes(name)) {
      assert.deepEqual(expected.get(name), before.get(name).values, `Unexpected values changed: ${name}`);
      assert.deepEqual(workbook.worksheets.getItem(name).getUsedRange().formulas, before.get(name).formulas, `Unexpected formulas changed: ${name}`);
    }
  }
  const unchangedDesign = structuredClone(expected.get("武将设计思想").slice(0, originalDesign.length));
  for (let row = 1; row < originalDesign.length; row += 1) {
    if (originalDesign[row][0] === "R01") unchangedDesign[row] = originalDesign[row];
    else unchangedDesign[row][26] = originalDesign[row][26];
  }
  assert.deepEqual(unchangedDesign, before.get("武将设计思想").values);
  await inspectAndRender(workbook, "after");
  const currentPreview = await workbook.render({ sheetName: "武将设计思想", range: "H8:J10", scale: 1, format: "png" });
  await fs.writeFile(`${outputDir}/r01_designs.png`, new Uint8Array(await currentPreview.arrayBuffer()));
  if (designRowCount > originalDesign.length) {
    const preview = await workbook.render({ sheetName: "武将设计思想", range: `H${originalDesign.length + 1}:J${designRowCount}`, scale: 1, format: "png" });
    await fs.writeFile(`${outputDir}/new_designs.png`, new Uint8Array(await preview.arrayBuffer()));
  }
  const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 50 }, maxChars: 4000 });
  await fs.writeFile(`${outputDir}/formula_errors.ndjson`, errors.ndjson);
  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(outputPath);
  const saved = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  for (const { name } of sheets) {
    assert.deepEqual(saved.worksheets.getItem(name).getUsedRange().values, expected.get(name), `Export altered sheet: ${name}`);
    if (!["武将设计思想", "武将符合性审查"].includes(name)) {
      assert.deepEqual(saved.worksheets.getItem(name).getUsedRange().formulas, before.get(name).formulas, `Export altered formulas: ${name}`);
    }
  }
  await fs.copyFile(outputPath, inputPath);
  console.log(JSON.stringify({ outputPath, syncedRepositoryWorkbook: true, reviewItems: rows.length, preservedSheets: sheets.map(row => row.name), formulaScan: errors.ndjson }));
}
