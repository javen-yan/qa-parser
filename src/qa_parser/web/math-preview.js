/* Display-only Unicode compatibility for the pinned KaTeX runtime.
 * Keeps the original TeX bytes and symbol codepoints. It is not a converter.
 * KaTeX docs: /docs/options#strict and /docs/supported#letters-and-unicode.
 * Font QA and representative visual comparison are separate release checks.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("./vendor/katex/katex.min.js"), require("./vendor/fonts/manifest.json"));
  } else {
    root.QaMathPreview = factory(root.katex, root.QaFormulaFont);
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (katex, fontManifest) {
  "use strict";
  const VERSION = "katex-unicode-display/2";
  const MAX_LENGTH = 65536;

  function scalar(value) {
    return typeof value === "string" && Array.from(value).length === 1 ? value.codePointAt(0) : -1;
  }
  function cjkText(value) {
    const cp = scalar(value);
    return (cp >= 0x3400 && cp <= 0x9fff) || (cp >= 0xf900 && cp <= 0xfaff) ||
      (cp >= 0x3000 && cp <= 0x303f) || (cp >= 0xff01 && cp <= 0xff60);
  }
  function supplementaryText(value) {
    const cp = scalar(value);
    // Named Unicode characters, not private glyph indices. The literal glyph
    // is retained: circled digits stay circled and fullwidth signs stay wide.
    return cp === 0x200b || cp === 0x2032 || cp === 0x2103 || cp === 0x2236 || cp === 0xfe63 ||
      (cp >= 0x2160 && cp <= 0x216b) || (cp >= 0x2460 && cp <= 0x2473);
  }
  function coveredGlyph(value) {
    const cp = scalar(value);
    // U+200B is a default-ignorable zero-width separator, not a visible glyph.
    // Keep it in source and markup; a cmap entry is neither needed nor invented.
    if (cp === 0x200b) return true;
    return Boolean(fontManifest && Array.isArray(fontManifest.unicode_ranges) &&
      fontManifest.unicode_ranges.some(range => cp >= range[0] && cp <= range[1]));
  }
  function policy(warnings) {
    return function (code, message, token) {
      let glyph = token && token.text;
      let permitted = code === "unicodeTextInMathMode" && cjkText(glyph);
      if (code === "unknownSymbol") permitted = supplementaryText(glyph);
      if (code === "symbolNotInFont") {
        // This diagnostic has no token in KaTeX 0.19.1. Match its whole stable
        // shape and permit only plain-text glyphs covered by the bundled font.
        const match = /^No character metrics for '([^']+)' in style 'Main-Regular' and mode 'text'$/.exec(message);
        glyph = match && match[1];
        permitted = Boolean(match && supplementaryText(glyph));
      }
      if (permitted && coveredGlyph(glyph)) {
        warnings.push({ code: code, glyph: glyph, codepoint: "U+" + scalar(glyph).toString(16).toUpperCase().padStart(4, "0"), message: message });
        return "ignore";
      }
      return "error";
    };
  }
  function auditFallbackFonts(tree, restricted) {
    if (Array.isArray(tree)) { tree.forEach(node => auditFallbackFonts(node, restricted)); return; }
    if (!tree || typeof tree !== "object") return;
    // Unknown Unicode is rendered as plain text by KaTeX. Do not silently
    // erase a deliberate bold/vector/blackboard/italic style around it. This
    // audit uses the pinned parser AST, including expanded macro bodies.
    const changesFont = (tree.type === "font" && tree.font !== "mathrm") ||
      (tree.type === "text" && tree.font && !["\\text", "\\textrm"].includes(tree.font));
    restricted = restricted || changesFont;
    if (typeof tree.text === "string" && (cjkText(tree.text) || supplementaryText(tree.text)) && !coveredGlyph(tree.text)) {
      throw new Error("随包公式字体未覆盖字符：U+" + scalar(tree.text).toString(16).toUpperCase());
    }
    if (restricted && typeof tree.text === "string" && (cjkText(tree.text) || supplementaryText(tree.text))) {
      throw new Error("Unicode 字符的显式字体样式尚未验证，未使用普通字体替代");
    }
    Object.keys(tree).forEach(key => { if (key !== "loc") auditFallbackFonts(tree[key], restricted); });
  }
  function run(latex, element) {
    if (!katex || typeof katex.renderToString !== "function") throw new Error("离线公式组件不可用");
    if (typeof latex !== "string" || latex.length > MAX_LENGTH) throw new Error("公式内容不是字符串或超出显示长度限制");
    const warnings = [], denied = [];
    const options = {
      throwOnError: true, strict: policy(warnings),
      trust: function (context) { denied.push(context.command); return false; },
      maxExpand: 1000, maxSize: 20, macros: {}, globalGroup: false,
      output: "htmlAndMathml", displayMode: false
    };
    // Construct detached markup first so a denied macro cannot leave partial
    // HTML in the visible element. KaTeX escapes source/annotation contents.
    const markup = katex.renderToString(latex, options);
    if (warnings.length || /[^\x00-\x7f]/.test(latex)) {
      if (katex.version !== "0.19.1" || typeof katex.__parse !== "function") {
        throw new Error("Unicode 显示适配需要经过验证的 KaTeX 0.19.1");
      }
      auditFallbackFonts(katex.__parse(latex, Object.assign({}, options, { macros: {}, strict: policy([]) })), false);
    }
    if (denied.length) throw new Error("公式含不允许的外部资源或 HTML 命令：" + denied.join(", "));
    if (element) {
      element.innerHTML = markup;
      element.classList.add("qa-math-preview");
      element.dataset.mathDisplay = warnings.length ? "unicode-compatible" : "strict";
      element.dataset.mathEngine = VERSION;
      element.title = "公式源码：" + latex;
    }
    return { markup: markup, warnings: warnings, source: latex, engine: VERSION };
  }
  return Object.freeze({
    version: VERSION,
    render: function (latex, element) { return run(latex, element); },
    renderToString: function (latex) { return run(latex).markup; },
    assess: function (latex) {
      try {
        const result = run(latex);
        return { supported: true, source: result.source, warnings: result.warnings, engine: VERSION };
      } catch (error) {
        return { supported: false, source: latex, warnings: [], engine: VERSION, error: String(error.message || error) };
      }
    }
  });
});
