#!/usr/bin/env node
/**
 * The token linter (AC-U3).
 *
 * "Tokens are the only source of visual values. A hard-coded colour, spacing
 * or radius in any component fails the token linter."
 *
 * The rule is enforced rather than documented because a design system whose
 * only defence is a contribution guideline drifts within a release: someone
 * needs #2d6cdf today, the ramp gains an undocumented eleventh blue, and the
 * dark theme silently stops being a theme. Failing the build is the cheapest
 * moment to catch that.
 *
 * Three categories, exactly the three the prompt names:
 *
 *   colour   any literal colour in a colour-bearing property
 *   spacing  any non-zero length in a margin, padding, gap or inset
 *   radius   any literal length in a border-radius
 *
 * Deliberately NOT flagged: intrinsic sizing (`height`, `max-width`,
 * `grid-template-columns`). A log row is 1.25rem tall because that is what one
 * line of monospace occupies at the token font size, not because a designer
 * chose it, and tokenising it would invent a token nobody reasons about. The
 * README says so, so the omission is a decision rather than a gap.
 *
 * TSX is scanned for literal colours only. Inline geometry in a component is
 * data -- a gauge fill is `width: 63%` because the pool is 63% used -- and a
 * linter that rejected it would be rejecting the data, not a hard-coded value.
 */

import { readFileSync } from 'node:fs';
import { readdir } from 'node:fs/promises';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * The tree to scan. Defaults to the whole web workspace; the first argument
 * overrides it so that `token-lint.test.ts` can point the linter at fixtures
 * that are supposed to fail, and prove the linter actually catches them. A
 * linter nobody has watched fail is a linter nobody knows works.
 */
const WEB = process.argv[2] ?? fileURLToPath(new URL('..', import.meta.url));

/**
 * The one directory allowed to state raw visual values: the token layer.
 *
 * It was one file. It is now four -- the ramp of section 4.1, the semantic
 * roles, the run states of section 4.2 and the density modes of section 4.5 --
 * because a single file holding all four made the ramp hard to compare against
 * the specification line by line, which is the thing a reader most needs to do.
 */
const TOKEN_SOURCE = join('packages', 'jarngreipr', 'src', 'tokens') + sep;

const SKIP_DIRECTORIES = new Set([
  'node_modules',
  'dist',
  'storybook-static',
  'test-results',
  'playwright-report',
  // The token linter's own fixtures state hard-coded values on purpose.
  '__fixtures__',
  '.turbo',
  '.vite',
]);

const COLOUR_PROPERTIES = [
  'color',
  'background',
  'background-color',
  'background-image',
  'border',
  'border-top',
  'border-right',
  'border-bottom',
  'border-left',
  'border-color',
  'border-top-color',
  'border-right-color',
  'border-bottom-color',
  'border-left-color',
  'border-inline-start-color',
  'border-inline-end-color',
  'outline',
  'outline-color',
  'box-shadow',
  'text-shadow',
  'fill',
  'stroke',
  'caret-color',
  'accent-color',
  'text-decoration-color',
  'column-rule-color',
];

const SPACING_PROPERTIES = [
  'margin',
  'margin-top',
  'margin-right',
  'margin-bottom',
  'margin-left',
  'margin-block',
  'margin-inline',
  'margin-block-start',
  'margin-block-end',
  'margin-inline-start',
  'margin-inline-end',
  'padding',
  'padding-top',
  'padding-right',
  'padding-bottom',
  'padding-left',
  'padding-block',
  'padding-inline',
  'padding-block-start',
  'padding-block-end',
  'padding-inline-start',
  'padding-inline-end',
  'gap',
  'row-gap',
  'column-gap',
  'inset',
  'inset-block',
  'inset-inline',
];

const RADIUS_PROPERTIES = [
  'border-radius',
  'border-top-left-radius',
  'border-top-right-radius',
  'border-bottom-left-radius',
  'border-bottom-right-radius',
  'border-start-start-radius',
  'border-start-end-radius',
  'border-end-start-radius',
  'border-end-end-radius',
];

/**
 * Colour keywords that carry no design decision.
 *
 * `transparent` and `currentColor` inherit or erase rather than choose, and
 * `none`/`inherit`/`unset` are not colours at all. Every other CSS named
 * colour is a hard-coded colour wearing a word.
 */
const COLOURLESS_KEYWORDS = new Set([
  'transparent',
  'currentcolor',
  'inherit',
  'initial',
  'unset',
  'revert',
  'none',
  'auto',
]);

const HEX = /#[0-9a-f]{3,8}\b/i;
const COLOUR_FUNCTION = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\s*\(/i;
/** The CSS named colours, minus the keywords above. Enough to catch a slip. */
const NAMED_COLOUR =
  /\b(?:aliceblue|antiquewhite|aqua|aquamarine|azure|beige|bisque|black|blanchedalmond|blue|blueviolet|brown|burlywood|cadetblue|chartreuse|chocolate|coral|cornflowerblue|cornsilk|crimson|cyan|darkblue|darkcyan|darkgoldenrod|darkgray|darkgreen|darkgrey|darkkhaki|darkmagenta|darkolivegreen|darkorange|darkorchid|darkred|darksalmon|darkseagreen|darkslateblue|darkslategray|darkslategrey|darkturquoise|darkviolet|deeppink|deepskyblue|dimgray|dimgrey|dodgerblue|firebrick|floralwhite|forestgreen|fuchsia|gainsboro|ghostwhite|gold|goldenrod|gray|green|greenyellow|grey|honeydew|hotpink|indianred|indigo|ivory|khaki|lavender|lavenderblush|lawngreen|lemonchiffon|lightblue|lightcoral|lightcyan|lightgoldenrodyellow|lightgray|lightgreen|lightgrey|lightpink|lightsalmon|lightseagreen|lightskyblue|lightslategray|lightslategrey|lightsteelblue|lightyellow|lime|limegreen|linen|magenta|maroon|mediumaquamarine|mediumblue|mediumorchid|mediumpurple|mediumseagreen|mediumslateblue|mediumspringgreen|mediumturquoise|mediumvioletred|midnightblue|mintcream|mistyrose|moccasin|navajowhite|navy|oldlace|olive|olivedrab|orange|orangered|orchid|palegoldenrod|palegreen|paleturquoise|palevioletred|papayawhip|peachpuff|peru|pink|plum|powderblue|purple|rebeccapurple|red|rosybrown|royalblue|saddlebrown|salmon|sandybrown|seagreen|seashell|sienna|silver|skyblue|slateblue|slategray|slategrey|snow|springgreen|steelblue|tan|teal|thistle|tomato|turquoise|violet|wheat|white|whitesmoke|yellow|yellowgreen)\b/i;

/** A non-zero length. `0` and `0px` are not design decisions. */
const NON_ZERO_LENGTH = /(?<![\w.-])(?!0(?:\.0+)?(?:px|rem|em|%|ch|vh|vw)?(?![\w.]))\d*\.?\d+(?:px|rem|em|ch|vh|vw|vmin|vmax)\b/i;

/**
 * Whether a file is component source, for the colour-in-code rule.
 *
 * Component source and stories only. A test that asserts the linter catches
 * `rgba(` has to contain `rgba(` to do so, and a script that lists colour
 * function names has to list them; flagging those would make the rule
 * self-defeating rather than strict. The rule as written is about components,
 * and stories are in scope because a colour hard-coded in a story lands in the
 * visual baseline.
 */
function isComponentSource(rel) {
  const path = rel.split(sep).join('/');
  if (!/\.tsx?$/.test(path)) return false;
  if (/\.(test|spec)\.tsx?$/.test(path)) return false;
  return /^(packages|apps)\/[^/]+\/src\//.test(path);
}

/** Recursively list files under `directory`, skipping build output. */
async function walk(directory) {
  const found = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name.startsWith('.') && entry.name !== '.storybook') continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      if (SKIP_DIRECTORIES.has(entry.name)) continue;
      found.push(...(await walk(path)));
    } else {
      found.push(path);
    }
  }
  return found;
}

/**
 * Split a stylesheet into declarations, with the line each one starts on.
 *
 * Structural rather than line-based. A line-based reader is fooled by
 * `.probe { color: #2d6cdf; }` written on one line -- it sees the property as
 * `.probe { color` and waves it through -- and a linter that depends on the
 * formatter having run first is a linter with a hole in it. So this walks the
 * braces: text before `{` is a selector or an at-rule prelude and is
 * discarded, text before `;` or `}` is a declaration and is checked.
 */
function declarations(css) {
  const source = css.replace(/\/\*[\s\S]*?\*\//g, (match) => match.replace(/[^\n]/g, ' '));
  const result = [];
  let buffer = '';
  let line = 1;
  let start = 1;
  let quote = null;

  const flush = (isPrelude) => {
    const text = buffer.trim();
    buffer = '';
    const from = start;
    start = line;
    if (isPrelude || text === '' || text.startsWith('@')) return;
    const colon = text.indexOf(':');
    if (colon <= 0) return;
    const property = text.slice(0, colon).trim().toLowerCase();
    const value = text.slice(colon + 1).trim();
    if (value === '' || /[{}]/.test(property)) return;
    result.push({ property, value, line: from });
  };

  for (const character of source) {
    if (character === '\n') line += 1;
    if (quote !== null) {
      buffer += character;
      if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      buffer += character;
      continue;
    }
    if (character === '{') flush(true);
    else if (character === ';' || character === '}') flush(false);
    else buffer += character;
    if (buffer.trim() === '') start = line;
  }
  flush(false);
  return result;
}

/** Everything outside a `var(...)` call: what the author actually wrote. */
function outsideVar(value) {
  return value.replace(/var\(\s*--[a-z0-9-]+\s*(?:,[^)]*)?\)/gi, ' ');
}

function checkCss(path, source) {
  const failures = [];
  for (const { property, value, line } of declarations(source)) {
    // A custom property declared outside the ramp is a token being minted in
    // the wrong place, which is the same failure by another route.
    if (property.startsWith('--')) {
      failures.push({
        path,
        line,
        category: 'token',
        detail: `\`${property}\` is declared outside the ramp. Tokens live in tokens.css.`,
      });
      continue;
    }
    const bare = outsideVar(value);
    if (COLOUR_PROPERTIES.includes(property)) {
      const words = bare.split(/[\s,()/]+/).filter(Boolean);
      const named = words.find(
        (word) => NAMED_COLOUR.test(word) && !COLOURLESS_KEYWORDS.has(word.toLowerCase()),
      );
      if (HEX.test(bare) || COLOUR_FUNCTION.test(bare) || named !== undefined) {
        failures.push({
          path,
          line,
          category: 'colour',
          detail: `\`${property}: ${value}\` states a colour. Use a var(--jg-…) token.`,
        });
      }
    }
    if (SPACING_PROPERTIES.includes(property) && NON_ZERO_LENGTH.test(bare)) {
      failures.push({
        path,
        line,
        category: 'spacing',
        detail: `\`${property}: ${value}\` states a length. Use a var(--jg-space-…) token.`,
      });
    }
    if (RADIUS_PROPERTIES.includes(property) && NON_ZERO_LENGTH.test(bare)) {
      failures.push({
        path,
        line,
        category: 'radius',
        detail: `\`${property}: ${value}\` states a radius. Use a var(--jg-radius-…) token.`,
      });
    }
  }
  return failures;
}

function checkSource(path, source) {
  const failures = [];
  for (const [index, line] of source.split('\n').entries()) {
    const code = line.replace(/\/\/.*$/, '');
    if (/^\s*\*/.test(code)) continue;
    if (HEX.test(code) || COLOUR_FUNCTION.test(code)) {
      failures.push({
        path,
        line: index + 1,
        category: 'colour',
        detail: `\`${line.trim()}\` states a colour in component source. Use a token.`,
      });
    }
  }
  return failures;
}

const files = await walk(WEB);
const failures = [];

for (const file of files) {
  const rel = relative(WEB, file);
  if (rel.startsWith(TOKEN_SOURCE)) {
    continue;
  }
  if (file.endsWith('.css')) {
    failures.push(...checkCss(rel, readFileSync(file, 'utf8')));
  } else if (isComponentSource(rel)) {
    failures.push(...checkSource(rel, readFileSync(file, 'utf8')));
  }
}

const scanned = files.filter(
  (file) => file.endsWith('.css') || file.endsWith('.tsx') || file.endsWith('.ts'),
).length;

if (failures.length === 0) {
  process.stdout.write(
    `token-lint: ${String(scanned)} files, no hard-coded colour, spacing or radius.\n`,
  );
  process.exit(0);
}

for (const failure of failures) {
  process.stderr.write(
    `${failure.path}:${String(failure.line)}  ${failure.category}: ${failure.detail}\n`,
  );
}
process.stderr.write(
  `\ntoken-lint: ${String(failures.length)} violation${failures.length === 1 ? '' : 's'}. ` +
    'Tokens are the only source of visual values (AC-U3).\n',
);
process.exit(1);
