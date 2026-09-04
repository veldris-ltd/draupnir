/**
 * The typed face of the token layer.
 *
 * CSS custom properties are the runtime; this is the compile-time view of the
 * same thing. A screen that needs to know the run states, or to render a chart
 * in the ink and forge ramps (AC-V9), reads them from here and gets a type
 * error when a name is wrong, rather than a silently unresolved `var()`.
 *
 * Nothing here restates a colour. Every value is the *name* of a custom
 * property, so the two cannot disagree: TypeScript knows which tokens exist
 * and CSS knows what they are worth. A hex in this file would be the same
 * defect as a hex in a component.
 */

/** The two themes a user can choose, and the third choice: follow the system. */
export const THEMES = ['light', 'dark', 'system'] as const;
export type ThemeChoice = (typeof THEMES)[number];
/** What a choice resolves to once the system has been asked. */
export type ResolvedTheme = 'light' | 'dark';

/** Section 4.5. Comfortable is the default; compact is CON-A and CON-B. */
export const DENSITIES = ['comfortable', 'compact'] as const;
export type Density = (typeof DENSITIES)[number];

/**
 * The run lifecycle of SAD 6.1, in order.
 *
 * All fourteen, not the ten section 4.2 assigns a colour to: a type that
 * omitted CURATED would make a screen unable to name a state the API returns.
 * The four without a colour of their own take the inert treatment, which
 * `state.css` states once.
 */
export const RUN_STATES = [
  'DRAFT',
  'CORPUS_REGISTERED',
  'LICENCE_CLEARED',
  'CURATED',
  'QUEUED',
  'TRAINING',
  'TRAINED',
  'EVALUATING',
  'MERGED',
  'QUANTISED',
  'AWAITING_APPROVAL',
  'RELEASED',
  'FAILED',
  'QUARANTINED',
] as const;
export type RunState = (typeof RUN_STATES)[number];

/** The states section 4.2 gives a colour of its own. */
export const COLOURED_RUN_STATES: readonly RunState[] = [
  'QUEUED',
  'TRAINING',
  'TRAINED',
  'EVALUATING',
  'MERGED',
  'QUANTISED',
  'AWAITING_APPROVAL',
  'RELEASED',
  'FAILED',
  'QUARANTINED',
];

/** The custom property stem for one state. `AWAITING_APPROVAL` -> `awaiting-approval`. */
export function runStateSlug(state: RunState): string {
  return state.toLowerCase().replace(/_/g, '-');
}

export interface RunStateTokens {
  /** The colour section 4.2 assigns. The marker. */
  colour: string;
  /** The tint a pill sits on. */
  surface: string;
  /** The pill's label. */
  on: string;
  /** The pill's edge. */
  border: string;
}

/**
 * The four token names for one state.
 *
 * Exposed for a chart or a canvas that cannot use a stylesheet, and for tests.
 * A component rendering markup writes `data-jg-run-state` and lets `state.css`
 * do this; calling it in a component to build an inline style would be the
 * per-component choice AC-V4 forbids, one indirection later.
 */
export function runStateTokens(state: RunState): RunStateTokens {
  const slug = runStateSlug(state);
  return {
    colour: `--jg-state-${slug}`,
    surface: `--jg-state-${slug}-surface`,
    on: `--jg-state-${slug}-on`,
    border: `--jg-state-${slug}-border`,
  };
}

/** The seven ramps of section 4.1, and the steps each one has. */
export const RAMPS = {
  ink: [900, 800, 700, 600, 400, 300, 100, 50],
  forge: [800, 700, 500, 300, 100, 50],
  success: [800, 500, 300, 50],
  warning: [800, 500, 300, 50],
  danger: [800, 500, 300, 50],
  info: [800, 500, 300, 50],
  merge: [500, 100, 50],
} as const satisfies Record<string, readonly number[]>;

export type RampName = keyof typeof RAMPS;

/**
 * The custom property for one ramp step.
 *
 * The step is typed against the ramp, so `rampToken('merge', 300)` does not
 * compile: merge has no 300, which is one of the four things
 * `docs/design/tokens.md` raises.
 */
export function rampToken<R extends RampName>(ramp: R, step: (typeof RAMPS)[R][number]): string {
  return `--jg-${ramp}-${String(step)}`;
}

/** The eight typography roles of section 4.3. */
export const TYPE_ROLES = [
  'display',
  'h1',
  'h2',
  'h3',
  'body',
  'small',
  'caption',
  'mono',
] as const;
export type TypeRole = (typeof TYPE_ROLES)[number];

export interface TypeTokens {
  size: string;
  line: string;
  weight: string;
}

export function typeTokens(role: TypeRole): TypeTokens {
  return {
    size: `--jg-text-${role}`,
    line: `--jg-text-${role}-line`,
    weight: `--jg-text-${role}-weight`,
  };
}

/** The space scale of section 4.4, in the step numbers the tokens use. */
export const SPACE_STEPS = [0, 1, 2, 3, 4, 6, 8, 12, 16] as const;
export type SpaceStep = (typeof SPACE_STEPS)[number];

/** Radius, named by use as section 4.4 names it. */
export const RADII = ['input', 'card', 'panel', 'dialog', 'badge'] as const;
export type Radius = (typeof RADII)[number];

/** Elevation. Section 4.4: three levels only. */
export const ELEVATIONS = ['flat', 'card', 'overlay'] as const;
export type Elevation = (typeof ELEVATIONS)[number];

/** Motion. Section 4.4: three durations and one easing. */
export const DURATIONS = ['micro', 'standard', 'large'] as const;
export type Duration = (typeof DURATIONS)[number];

/**
 * The ramps a chart may use. AC-V9: "charts use the ink and forge ramps. No
 * categorical rainbow palette appears anywhere."
 *
 * A list rather than a comment, so a chart component can be given the series
 * colours instead of choosing them, and so the rule is checkable.
 */
export const CHART_RAMPS: readonly RampName[] = ['ink', 'forge'];

/** The attribute the theme is applied with. One name, used by the provider and the tests. */
export const THEME_ATTRIBUTE = 'data-jg-theme';
/** The attribute the density is applied with. */
export const DENSITY_ATTRIBUTE = 'data-jg-density';
/** The attribute a component marks a run state with. */
export const RUN_STATE_ATTRIBUTE = 'data-jg-run-state';

export {
  ALL_PAIRS,
  PAIRS,
  RUN_STATE_PAIRS,
  THRESHOLD,
  UnresolvableTokenError,
  contrastRatio,
  describe as describeFailure,
  evaluate,
  luminance,
  resolve,
} from './contrast';
export type { Failure, Pair, Threshold } from './contrast';
