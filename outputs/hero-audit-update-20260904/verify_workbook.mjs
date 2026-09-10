import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = "C:/Users/jiz14/TeamGH/wujiang_game/docs/武将实现问题清单.xlsx";
const outputDir = "C:/Users/jiz14/TeamGH/wujiang_game/outputs/hero-audit-update-20260904";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));

const sheets = await workbook.inspect({
  kind: "sheet,table",
  include: "id,name",
  maxChars: 10000,
  tableMaxRows: 8,
  tableMaxCols: 8,
});
const changed = await workbook.inspect({
  kind: "table",
  range: "武将设计思想!Z1:AB7",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 3,
  tableMaxCellChars: 500,
  maxChars: 16000,
});
const conformity = await workbook.inspect({
  kind: "table",
  range: "武将符合性审查!A1:M55",
  include: "values,formulas",
  tableMaxRows: 55,
  tableMaxCols: 13,
  tableMaxCellChars: 260,
  maxChars: 50000,
});
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 200 },
  summary: "post-export formula error scan",
  maxChars: 12000,
});
const preview = await workbook.render({
  sheetName: "武将设计思想",
  range: "Z1:AB7",
  scale: 1,
  format: "png",
});
const conformityPreview = await workbook.render({
  sheetName: "武将符合性审查",
  range: "A39:M55",
  scale: 1,
  format: "png",
});

await fs.writeFile(`${outputDir}/verified_sheets.ndjson`, sheets.ndjson, "utf8");
await fs.writeFile(`${outputDir}/verified_design.ndjson`, changed.ndjson, "utf8");
await fs.writeFile(`${outputDir}/verified_conformity.ndjson`, conformity.ndjson, "utf8");
await fs.writeFile(`${outputDir}/verified_formula_errors.ndjson`, formulaErrors.ndjson, "utf8");
await fs.writeFile(`${outputDir}/verified_design.png`, new Uint8Array(await preview.arrayBuffer()));
await fs.writeFile(
  `${outputDir}/verified_conformity.png`,
  new Uint8Array(await conformityPreview.arrayBuffer()),
);

console.log(sheets.ndjson);
console.log(changed.ndjson);
console.log(conformity.ndjson);
console.log(formulaErrors.ndjson);
