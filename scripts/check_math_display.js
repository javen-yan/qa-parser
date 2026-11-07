#!/usr/bin/env node
/* Developer-only replay of the exact display module used by UI/offline HTML.
 * Usage: node scripts/check_math_display.js input.json report.json
 * Input is an array of {latex, ...source metadata}. No browser/network needed.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const display = require('../src/qa_parser/web/math-preview.js');
const inputPath = process.argv[2], outputPath = process.argv[3];
if (!inputPath || !outputPath) throw new Error('Expected input JSON array and new report path');
if (fs.existsSync(outputPath)) throw new Error('Report exists; choose a new path');
const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
if (!Array.isArray(input)) throw new Error('Input must be a JSON array');
const compatibility = [], failures = [], warningCounts = {};
for (const row of input) {
  if (typeof row.latex !== 'string') throw new Error('Missing latex field');
  const checked = display.assess(row.latex);
  if (checked.source !== row.latex) throw new Error('Display adapter changed source');
  if (!checked.supported) failures.push({ ...row, error: checked.error });
  else if (checked.warnings.length) {
    compatibility.push({ ...row, warnings: checked.warnings });
    for (const warning of checked.warnings) warningCounts[warning.code] = (warningCounts[warning.code] || 0) + 1;
  }
}
const result = {
  schema_version: 1, engine: display.version, total: input.length,
  markup_constructed: input.length - failures.length, strict_unchanged: input.length - failures.length - compatibility.length,
  unicode_compatibility: compatibility.length, source_bytes_unchanged: true,
  warning_counts: warningCounts, failures, compatibility,
  not_proven_by_this_check: ['Font availability and glyph coverage', 'Browser layout and absence of clipping',
    'Fidelity against original Word display', 'Target customer APP rendering'],
};
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify({ ...result, compatibility: undefined, failures: failures.length }, null, 2));
if (failures.length) process.exitCode = 2;
