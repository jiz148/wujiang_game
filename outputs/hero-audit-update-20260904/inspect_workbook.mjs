import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "C:/Users/jiz14/TeamGH/wujiang_game/docs/武将实现问题清单.xlsx";
const outputDir = "C:/Users/jiz14/TeamGH/wujiang_game/outputs/hero-audit-update-20260904";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));

const overview = await workbook.inspect({
  kind: "sheet,table",
  include: "id,name",
  maxChars: 6000,
  tableMaxRows: 8,
  tableMaxCols: 8,
});
await fs.writeFile(`${outputDir}/workbook_overview.ndjson`, overview.ndjson, "utf8");

const design = await workbook.inspect({
  kind: "table",
  range: "武将设计思想!A1:AB7",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 28,
  tableMaxCellChars: 240,
  maxChars: 30000,
});
await fs.writeFile(`${outputDir}/design_before.ndjson`, design.ndjson, "utf8");

const preview = await workbook.render({
  sheetName: "武将设计思想",
  range: "A1:AB7",
  scale: 1,
  format: "png",
});
await fs.writeFile(`${outputDir}/design_before.png`, new Uint8Array(await preview.arrayBuffer()));

console.log(overview.ndjson);
console.log(design.ndjson);
