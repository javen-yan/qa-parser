'use strict';
const assert = require('node:assert/strict');
const display = require('../src/qa_parser/web/math-preview.js');
const katex = require('../src/qa_parser/web/vendor/katex/katex.min.js');

// The same grammar reaches KaTeX; source annotations and codepoints survive.
const specimens = [
  String.raw`\mathop{\mathop{ \rightarrow }\limits_{}}\limits^{点燃}`,
  String.raw`I_{甲}+I_{乙}=I_{总}`,
  String.raw`①＋②＝③，Ⅰ∶Ⅱ`,
  String.raw`\frac{U′}{R}﹣I`,
  'U_{\u200b2}',
  String.raw`\overset{催化剂}{\underset{高温、高压}{\rightarrow}}`,
  String.raw`（U_2=U_{总}-IR_1）`,
];
for (const source of specimens) {
  const report = display.assess(source);
  assert.equal(report.supported, true, JSON.stringify(report));
  assert.equal(report.source, source);
  assert(report.warnings.length > 0);
  const html = display.renderToString(source);
  assert(!html.includes('katex-error'));
  for (const glyph of Array.from(source).filter(c => c.codePointAt(0) > 127)) {
    assert(html.includes(glyph), 'Lost Unicode codepoint: ' + glyph);
  }
}
// Existing strict-compatible math does not need a different representation.
const plain = String.raw`\frac{U_x}{I_x}=R_x`;
assert.equal(display.assess(plain).warnings.length, 0);
assert.equal(display.renderToString(plain), katex.renderToString(plain, {
  throwOnError: true, trust: false, strict: 'error', maxExpand: 1000, maxSize: 20,
  output: 'htmlAndMathml', displayMode: false,
}));

// Do not globally suppress strict warnings, invent glyphs or accept bad TeX.
for (const source of [String.raw`\unknown{x}`, String.raw`\frac{1}`, '\ue123', '😀',
  String.raw`\mathbf{①}`, String.raw`\htmlStyle{color:red}{x}`,
  String.raw`\href{https://example.invalid}{x}`, String.raw`\includegraphics{x}`,
  String.raw`\def\stealth{\href{https://example.invalid}{x}}\stealth`,
  String.raw`x%`, String.raw`\def\loop{\loop}\loop`, 'x'.repeat(65537)]) {
  assert.equal(display.assess(source).supported, false, source);
}
assert.equal(display.assess(null).supported, false);
assert.equal(display.assess(String.raw`\gdef\testleak{x}\testleak`).supported, true);
assert.equal(display.assess(String.raw`\testleak`).supported, false, 'Macro leaked across formulas');
assert(!display.renderToString(String.raw`\text{<img>}`).includes('<img>'));

// Approved font warnings remain auditable rather than silently disappearing.
const circles = display.assess('①');
assert.deepEqual(circles.warnings.map(w => w.code), ['unknownSymbol', 'symbolNotInFont']);
assert(circles.warnings.every(w => w.codepoint === 'U+2460'));
console.log('math-preview: Unicode identity, strict preservation, negative cases and state isolation passed');

// Coverage is tied to the actual bundled cmap, not an entire Unicode block.
const fs = require('node:fs');
const vm = require('node:vm');
const fontManifest = require('../src/qa_parser/web/vendor/fonts/manifest.json');
const browser = { katex };
vm.createContext(browser);
vm.runInContext(fs.readFileSync(require.resolve('../src/qa_parser/web/font-coverage.js'), 'utf8'), browser);
assert.equal(JSON.stringify(browser.QaFormulaFont.unicode_ranges), JSON.stringify(fontManifest.unicode_ranges));
assert.equal(browser.QaFormulaFont.sha256, fontManifest.sha256);
vm.runInContext(fs.readFileSync(require.resolve('../src/qa_parser/web/math-preview.js'), 'utf8'), browser);
assert.equal(browser.QaMathPreview.assess('①甲').supported, true);
assert.equal(browser.QaMathPreview.assess('\u9fff').supported, false);
assert.equal(browser.QaMathPreview.assess(String.raw`\text{\char"9FFF}`).supported, false);
assert.equal(browser.QaMathPreview.assess('\\text{\u9fff}').supported, false);
const noFont = { katex };
vm.createContext(noFont);
vm.runInContext(fs.readFileSync(require.resolve('../src/qa_parser/web/math-preview.js'), 'utf8'), noFont);
assert.equal(noFont.QaMathPreview.assess('甲').supported, false);
assert.equal(noFont.QaMathPreview.assess('\\text{甲}').supported, false);
assert.equal(noFont.QaMathPreview.assess('x+1').supported, true);
assert.equal(noFont.QaMathPreview.assess('x\u200b').supported, true);
console.log('math-preview: pinned font coverage, browser parity, absent glyph and zero-width cases passed');

const crypto = require('node:crypto');
const path = require('node:path');
const fontBytes = fs.readFileSync(path.join(__dirname, '../src/qa_parser/web/vendor/fonts', fontManifest.path));
assert.equal(fontBytes.length, fontManifest.bytes);
assert.equal(crypto.createHash('sha256').update(fontBytes).digest('hex'), fontManifest.sha256);
console.log('math-preview: pinned font file size and SHA-256 passed');
