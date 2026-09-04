/**
 * The contrast checker. AC-V6.
 *
 * "Dark theme meets the same contrast thresholds as light. Verified by
 * automated contrast check on every token pair in use."
 *
 * Two halves. The maths is WCAG 2.2's, unmodified. The list of pairs is the
 * interesting half: it is every foreground-on-background combination the
 * components actually make, written down. A cartesian product of the ramp
 * would be a bigger number and a worse check, because most of those pairs are
 * combinations nobody renders and a failure among them tells you nothing.
 * Adding a colour combination to a stylesheet means adding it here. That is
 * the contract, and the reason the list is explicit.
 *
 * The resolver understands the three things a token value can be: a hex, a
 * `var()` reference to another token, and a `color-mix()` of two of them. It
 * has to, because the themes are written as references so that a component
 * naming a role gets whatever the theme put there, and because a hover state
 * is its ramp step mixed toward ink rather than a new hex nobody specified.
 */

/** What a pair is held to. WCAG 1.4.3 for text, 1.4.11 for everything else. */
export type Threshold = 'text' | 'boundary';

/** The ratios, as WCAG states them. */
export const THRESHOLD: Readonly<Record<Threshold, number>> = {
  text: 4.5,
  boundary: 3,
};

export interface Pair {
  /** The token drawn on top. */
  foreground: string;
  /** The token behind it. */
  background: string;
  kind: Threshold;
  /** What renders this pair. A failure names it, so the fix has a location. */
  where: string;
}

export interface Failure extends Pair {
  ratio: number;
  required: number;
  foregroundValue: string;
  backgroundValue: string;
}

/** sRGB relative luminance, WCAG 2.2 definition. */
export function luminance(hex: string): number {
  const value = hex.trim().replace('#', '');
  const full =
    value.length === 3
      ? value
          .split('')
          .map((character) => character + character)
          .join('')
      : value;
  const channels = [0, 2, 4].map((offset) => {
    const part = Number.parseInt(full.slice(offset, offset + 2), 16) / 255;
    return part <= 0.04045 ? part / 12.92 : ((part + 0.055) / 1.055) ** 2.4;
  });
  const [r, g, b] = channels as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The contrast ratio between two colours, either way round. */
export function contrastRatio(foreground: string, background: string): number {
  const a = luminance(foreground);
  const b = luminance(background);
  const [lighter, darker] = a > b ? [a, b] : [b, a];
  return (lighter + 0.05) / (darker + 0.05);
}

function channels(hex: string): [number, number, number] {
  const value = hex.trim().replace('#', '');
  const full =
    value.length === 3
      ? value
          .split('')
          .map((character) => character + character)
          .join('')
      : value;
  return [0, 2, 4].map((offset) => Number.parseInt(full.slice(offset, offset + 2), 16)) as [
    number,
    number,
    number,
  ];
}

function toHex(parts: readonly number[]): string {
  return `#${parts.map((part) => Math.round(part).toString(16).padStart(2, '0')).join('')}`;
}

/**
 * `color-mix(in srgb, <a> <p>%, <b>)`.
 *
 * `in srgb` mixes the gamma-encoded coordinates, so this is a weighted average
 * of the channel bytes and not of their linear values. Implemented rather than
 * approximated because the hover and active states are mixes, and a checker
 * that skipped them would be a checker that never looked at a hover state.
 */
const MIX = /^color-mix\(\s*in\s+srgb\s*,\s*(.+?)\s+([\d.]+)%\s*,\s*(.+?)\s*\)$/;

const VAR = /^var\(\s*(--[a-z0-9-]+)\s*\)$/;

export class UnresolvableTokenError extends Error {}

/**
 * Resolve a token to a hex colour, following references and evaluating mixes.
 *
 * `declarations` is one theme's custom properties, flattened. Resolution is
 * depth limited: a token that refers to itself is a mistake that should stop
 * the build rather than the process.
 */
export function resolve(
  token: string,
  declarations: Readonly<Record<string, string>>,
  depth = 0,
): string {
  if (depth > 12) {
    throw new UnresolvableTokenError(`${token} resolves in a cycle`);
  }

  const raw = token.startsWith('--') ? declarations[token] : token;
  if (raw === undefined) {
    throw new UnresolvableTokenError(`${token} is not declared in this theme`);
  }

  const value = raw.trim();

  if (value.startsWith('#')) {
    return value;
  }

  const reference = VAR.exec(value);
  if (reference?.[1] !== undefined) {
    return resolve(reference[1], declarations, depth + 1);
  }

  const mix = MIX.exec(value);
  if (mix?.[1] !== undefined && mix[2] !== undefined && mix[3] !== undefined) {
    const weight = Number.parseFloat(mix[2]) / 100;
    const [ar, ag, ab] = channels(resolve(mix[1].trim(), declarations, depth + 1));
    const [br, bg, bb] = channels(resolve(mix[3].trim(), declarations, depth + 1));
    const blend = (a: number, b: number): number => a * weight + b * (1 - weight);
    return toHex([blend(ar, br), blend(ag, bg), blend(ab, bb)]);
  }

  throw new UnresolvableTokenError(`${token} is ${value}, which is not a colour this can read`);
}

/**
 * Every pairing the components make.
 *
 * `where` is not decoration. A failure that says "--jg-text-subtle on
 * --jg-surface-raised is 4.1:1" leaves someone hunting; one that adds "a
 * placeholder inside a card" does not.
 */
export const PAIRS: readonly Pair[] = [
  // -- body text on the three surfaces ------------------------------------
  { foreground: '--jg-text', background: '--jg-surface', kind: 'text', where: 'body text' },
  {
    foreground: '--jg-text',
    background: '--jg-surface-raised',
    kind: 'text',
    where: 'text on a card',
  },
  {
    foreground: '--jg-text',
    background: '--jg-surface-sunken',
    kind: 'text',
    where: 'text in a well or a table header',
  },
  {
    foreground: '--jg-text',
    background: '--jg-surface-overlay',
    kind: 'text',
    where: 'a dialog title',
  },
  {
    foreground: '--jg-text-muted',
    background: '--jg-surface',
    kind: 'text',
    where: 'secondary text',
  },
  {
    foreground: '--jg-text-muted',
    background: '--jg-surface-raised',
    kind: 'text',
    where: 'secondary text on a card',
  },
  {
    foreground: '--jg-text-muted',
    background: '--jg-surface-sunken',
    kind: 'text',
    where: 'a column header',
  },
  {
    foreground: '--jg-text-muted',
    background: '--jg-surface-overlay',
    kind: 'text',
    where: 'a dialog body',
  },
  {
    foreground: '--jg-text-subtle',
    background: '--jg-surface',
    kind: 'text',
    where: 'a placeholder or a breadcrumb separator',
  },
  {
    foreground: '--jg-text-subtle',
    background: '--jg-surface-raised',
    kind: 'text',
    where: 'a placeholder inside a card',
  },
  {
    foreground: '--jg-text-subtle',
    background: '--jg-surface-sunken',
    kind: 'text',
    where: 'a selected tab label',
  },
  {
    foreground: '--jg-text-inverse',
    background: '--jg-surface-inverse',
    kind: 'text',
    where: 'a tooltip',
  },

  // -- text on tinted surfaces --------------------------------------------
  {
    foreground: '--jg-text',
    background: '--jg-accent-subtle',
    kind: 'text',
    where: 'a selected row',
  },
  {
    foreground: '--jg-text',
    background: '--jg-danger-subtle',
    kind: 'text',
    where: 'an error panel',
  },
  {
    foreground: '--jg-text',
    background: '--jg-warning-subtle',
    kind: 'text',
    where: 'the sole approver notice',
  },
  {
    foreground: '--jg-text',
    background: '--jg-success-subtle',
    kind: 'text',
    where: 'a passed gate panel',
  },
  {
    foreground: '--jg-text',
    background: '--jg-info-subtle',
    kind: 'text',
    where: 'an informational panel',
  },

  // -- text on filled controls --------------------------------------------
  {
    foreground: '--jg-text-on-accent',
    background: '--jg-accent',
    kind: 'text',
    where: 'a primary button',
  },
  {
    foreground: '--jg-text-on-accent',
    background: '--jg-accent-hover',
    kind: 'text',
    where: 'a primary button, hovered',
  },
  {
    foreground: '--jg-text-on-accent',
    background: '--jg-accent-active',
    kind: 'text',
    where: 'a primary button, pressed',
  },
  {
    foreground: '--jg-text-on-accent',
    background: '--jg-danger',
    kind: 'text',
    where: 'a destructive button',
  },
  {
    foreground: '--jg-text-on-accent',
    background: '--jg-danger-hover',
    kind: 'text',
    where: 'a destructive button, hovered',
  },

  // -- semantic text ------------------------------------------------------
  {
    foreground: '--jg-accent',
    background: '--jg-surface',
    kind: 'text',
    where: 'a link or a ghost button',
  },
  {
    foreground: '--jg-accent',
    background: '--jg-surface-raised',
    kind: 'text',
    where: 'a link on a card',
  },
  {
    foreground: '--jg-accent',
    background: '--jg-surface-sunken',
    kind: 'text',
    where: 'an active tab',
  },
  {
    foreground: '--jg-accent',
    background: '--jg-accent-subtle',
    kind: 'text',
    where: 'a ghost button, hovered',
  },
  { foreground: '--jg-danger', background: '--jg-surface', kind: 'text', where: 'a field error' },
  {
    foreground: '--jg-danger',
    background: '--jg-surface-raised',
    kind: 'text',
    where: 'a failed gate on a card',
  },
  {
    foreground: '--jg-danger',
    background: '--jg-danger-subtle',
    kind: 'text',
    where: 'a danger badge',
  },
  {
    foreground: '--jg-warning',
    background: '--jg-surface',
    kind: 'text',
    where: 'a margin breach',
  },
  {
    foreground: '--jg-warning',
    background: '--jg-surface-raised',
    kind: 'text',
    where: 'a warning on a card',
  },
  {
    foreground: '--jg-warning',
    background: '--jg-warning-subtle',
    kind: 'text',
    where: 'a warning badge',
  },
  { foreground: '--jg-success', background: '--jg-surface', kind: 'text', where: 'a passed gate' },
  {
    foreground: '--jg-success',
    background: '--jg-surface-raised',
    kind: 'text',
    where: 'a passed gate on a card',
  },
  {
    foreground: '--jg-success',
    background: '--jg-success-subtle',
    kind: 'text',
    where: 'a success badge',
  },
  { foreground: '--jg-info', background: '--jg-surface', kind: 'text', where: 'a fabric reading' },
  {
    foreground: '--jg-info',
    background: '--jg-info-subtle',
    kind: 'text',
    where: 'an info badge',
  },

  // -- boundaries, WCAG 1.4.11 --------------------------------------------
  {
    foreground: '--jg-border',
    background: '--jg-surface',
    kind: 'boundary',
    where: 'the edge of an input',
  },
  {
    foreground: '--jg-border',
    background: '--jg-surface-raised',
    kind: 'boundary',
    where: 'the edge of an input on a card',
  },
  {
    foreground: '--jg-border',
    background: '--jg-surface-sunken',
    kind: 'boundary',
    where: 'a table rule',
  },
  {
    foreground: '--jg-border-strong',
    background: '--jg-surface',
    kind: 'boundary',
    where: 'a focused field',
  },
  {
    foreground: '--jg-border-strong',
    background: '--jg-surface-raised',
    kind: 'boundary',
    where: 'a focused field on a card',
  },
  {
    foreground: '--jg-focus-ring',
    background: '--jg-surface',
    kind: 'boundary',
    where: 'the focus indicator',
  },
  {
    foreground: '--jg-focus-ring',
    background: '--jg-surface-raised',
    kind: 'boundary',
    where: 'the focus indicator on a card',
  },
  {
    foreground: '--jg-focus-ring',
    background: '--jg-surface-sunken',
    kind: 'boundary',
    where: 'the focus indicator in a well',
  },
  {
    foreground: '--jg-focus-ring',
    background: '--jg-surface-overlay',
    kind: 'boundary',
    where: 'the focus indicator in a dialog',
  },
  {
    foreground: '--jg-accent-border',
    background: '--jg-accent-subtle',
    kind: 'boundary',
    where: 'the edge of a selected row',
  },
  {
    foreground: '--jg-danger-border',
    background: '--jg-danger-subtle',
    kind: 'boundary',
    where: 'the edge of an error panel',
  },
  {
    foreground: '--jg-warning-border',
    background: '--jg-warning-subtle',
    kind: 'boundary',
    where: 'the edge of the sole approver notice',
  },
  {
    foreground: '--jg-success-border',
    background: '--jg-success-subtle',
    kind: 'boundary',
    where: 'the edge of a passed gate panel',
  },
  {
    foreground: '--jg-info-border',
    background: '--jg-info-subtle',
    kind: 'boundary',
    where: 'the edge of an informational panel',
  },
];

/**
 * Every run state pill, as three pairs each: the label on its tint, the edge
 * on its tint, and the label on the page behind it when the pill is bare.
 *
 * Generated from the state list rather than typed out, because a state added
 * to section 4.2 and not to this file would be a state nobody measured.
 */
export const RUN_STATE_PAIRS: readonly Pair[] = [
  'queued',
  'training',
  'trained',
  'evaluating',
  'merged',
  'quantised',
  'awaiting-approval',
  'released',
  'failed',
  'quarantined',
  'draft',
  'corpus-registered',
  'licence-cleared',
  'curated',
].flatMap((state): Pair[] => [
  {
    foreground: `--jg-state-${state}-on`,
    background: `--jg-state-${state}-surface`,
    kind: 'text',
    where: `the ${state.replace(/-/g, ' ')} pill's label`,
  },
  {
    foreground: `--jg-state-${state}-border`,
    background: `--jg-state-${state}-surface`,
    kind: 'boundary',
    where: `the ${state.replace(/-/g, ' ')} pill's edge`,
  },
  {
    // The pill's edge against the page, not its tint against the page. A tint
    // at 3:1 would be a tint nobody would call subtle, and WCAG 1.4.11 asks
    // that the *boundary* of a component be perceivable, which is the edge.
    foreground: `--jg-state-${state}-border`,
    background: '--jg-surface',
    kind: 'boundary',
    where: `the ${state.replace(/-/g, ' ')} pill's edge against the page`,
  },
]);

/** Every pair the system is held to. */
export const ALL_PAIRS: readonly Pair[] = [...PAIRS, ...RUN_STATE_PAIRS];

/**
 * Check one theme. Returns the failures; an empty array is a pass.
 *
 * Returning rather than throwing so a caller can report all of them at once.
 * A checker that stopped at the first failure would make fixing a ramp a
 * sequence of builds instead of one.
 */
export function evaluate(
  declarations: Readonly<Record<string, string>>,
  pairs: readonly Pair[] = ALL_PAIRS,
): Failure[] {
  const failures: Failure[] = [];
  for (const pair of pairs) {
    const foregroundValue = resolve(pair.foreground, declarations);
    const backgroundValue = resolve(pair.background, declarations);
    const ratio = contrastRatio(foregroundValue, backgroundValue);
    const required = THRESHOLD[pair.kind];
    if (Number(ratio.toFixed(2)) < required) {
      failures.push({ ...pair, ratio, required, foregroundValue, backgroundValue });
    }
  }
  return failures;
}

/** One failure as a line a person can act on. */
export function describe(failure: Failure): string {
  return (
    `${failure.foreground} (${failure.foregroundValue}) on ` +
    `${failure.background} (${failure.backgroundValue}) is ${failure.ratio.toFixed(2)}:1, ` +
    `below ${String(failure.required)}:1 -- ${failure.where}`
  );
}
