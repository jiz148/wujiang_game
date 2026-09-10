import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "C:/Users/jiz14/TeamGH/wujiang_game/docs/武将实现问题清单.xlsx";
const outputDir = "C:/Users/jiz14/TeamGH/wujiang_game/outputs/hero-audit-update-20260904";
const outputPath = `${outputDir}/武将实现问题清单.xlsx`;

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheet = workbook.worksheets.getItem("武将设计思想");
const review = JSON.parse(
  await fs.readFile("C:/Users/jiz14/TeamGH/wujiang_game/docs/武将符合性审查.json", "utf8"),
);

sheet.getRange("AA2").values = [[
  "2026-09-03当前批次10场审计：自动高信号0；人工设计对照发现一场审判在敌方可预见回避后形成敌我各阵亡1名，违反“己方损失同等或更高则保留”。已修通用区域脱离反应识别与审判可靠交换评分，并补行为测试；待用户复跑当前批次。",
]];
sheet.getRange("AA6").values = [[
  "2026-09-03当前批次10场审计：自动高信号0；44条公开随机日志对应45次攻击/技能声明，定位为隐身目标导致普通日志抑制吞掉1次铁锁随机结果。已改为公开事件；两条回归失败分别为致死样本血量设置错误及落点断言过度限定，均已修正测试；待用户复跑当前批次。",
]];
sheet.getRange("AA7").values = [[
  "2026-09-03当前批次10场审计：自动高信号0；神速8次、普攻24次，连续三拳节奏符合设计。龙息8个可用节点均无有效命中候选，不使用正确；随机局未形成满3轮且有兽人锚的回归机会。已补到期无锚/无格的等待回归日志，并修审计工具的有效机会分类与跨武将日志串扰；待用户复跑当前批次。",
]];

const conformity = workbook.worksheets.getItem("武将符合性审查");
const conformityRows = review.heroes.flatMap((hero) =>
  hero.items.map((item) => [
    hero.code,
    hero.name,
    hero.review_status,
    item.layer,
    item.requirement,
    item.evidence,
    item.verdict,
    item.severity,
    item.planned_change,
    item.status,
    hero.design_baseline,
    hero.audit_evidence,
    review.review_process,
  ]),
);
if (conformityRows.length !== 54) {
  throw new Error(`Expected 54 conformity rows, got ${conformityRows.length}`);
}
conformity.getRange("A2:M55").values = conformityRows;

const changed = await workbook.inspect({
  kind: "table",
  range: "武将设计思想!Z1:AB7",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 3,
  tableMaxCellChars: 500,
  maxChars: 16000,
});
await fs.writeFile(`${outputDir}/design_after.ndjson`, changed.ndjson, "utf8");

const conformityChanged = await workbook.inspect({
  kind: "table",
  range: "武将符合性审查!A1:M55",
  include: "values,formulas",
  tableMaxRows: 55,
  tableMaxCols: 13,
  tableMaxCellChars: 260,
  maxChars: 50000,
});
await fs.writeFile(`${outputDir}/conformity_after.ndjson`, conformityChanged.ndjson, "utf8");

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 200 },
  summary: "final formula error scan",
  maxChars: 12000,
});
await fs.writeFile(`${outputDir}/formula_errors.ndjson`, formulaErrors.ndjson, "utf8");

const preview = await workbook.render({
  sheetName: "武将设计思想",
  range: "Z1:AB7",
  scale: 1,
  format: "png",
});
await fs.writeFile(`${outputDir}/design_after.png`, new Uint8Array(await preview.arrayBuffer()));

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

console.log(changed.ndjson);
console.log(conformityChanged.ndjson);
console.log(formulaErrors.ndjson);
