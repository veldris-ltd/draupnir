/**
 * JARNGREIPR, the DRAUPNIR design system.
 *
 * Three layers, and the order matters:
 *
 *   tokens      the only source of visual values (AC-U3, enforced by
 *               `scripts/token-lint.mjs`, not by convention)
 *   state       the six states every component ships (AC-U2)
 *   primitives  the eighteen controls of VLD-UX-DRAUPNIR-001 section 5.1
 *   composites  the eight DRAUPNIR-shaped assemblies of SAD 11F.1
 *
 * The state layer sits under the components rather than beside them because
 * "every component ships six states" is a claim that has to be checkable. It
 * is: `stateStories()` produces the seven stories from one render function,
 * and `stories.test.tsx` fails the build if a component's story file does not
 * cover them.
 *
 * Importing this module pulls in the stylesheet, so a consumer gets the ramp
 * without a second import. The `./styles.css` export exists for consumers that
 * want the CSS alone -- the console's page stylesheet, for one.
 */

import './tokens/ramp.css';
import './tokens/tokens.css';
import './tokens/state.css';
import './tokens/density.css';

export const JARNGREIPR_VERSION = '0.1.0';

export {
  ALL_PAIRS,
  CHART_RAMPS,
  COLOURED_RUN_STATES,
  DENSITIES,
  DENSITY_ATTRIBUTE,
  DURATIONS,
  ELEVATIONS,
  PAIRS,
  RADII,
  RAMPS,
  RUN_STATES,
  RUN_STATE_ATTRIBUTE,
  RUN_STATE_PAIRS,
  SPACE_STEPS,
  THEMES,
  THEME_ATTRIBUTE,
  THRESHOLD,
  TYPE_ROLES,
  UnresolvableTokenError,
  contrastRatio,
  describeFailure,
  evaluate,
  luminance,
  rampToken,
  resolve,
  runStateSlug,
  runStateTokens,
  typeTokens,
} from './tokens';
export type {
  Density,
  Duration,
  Elevation,
  Failure,
  Pair,
  Radius,
  RampName,
  ResolvedTheme,
  RunStateTokens,
  SpaceStep,
  RunState,
  ThemeChoice,
  Threshold,
  TypeRole,
  TypeTokens,
} from './tokens';

export { ThemeProvider, readStoredChoice, resolveTheme, useTheme } from './theme/ThemeProvider';
export type { ThemeContextValue, ThemeProviderProps } from './theme/ThemeProvider';
export { DensityProvider, readStoredDensity, useDensity } from './theme/DensityProvider';
export type { DensityContextValue, DensityProviderProps } from './theme/DensityProvider';

export {
  ALL_STATES,
  REPLACING_STATES,
  StateSurface,
  isInert,
  replacesContent,
} from './state/states';
export type {
  ComponentState,
  ProblemSummary,
  Reserve,
  StateProps,
  StateSurfaceProps,
} from './state/states';

export {
  Badge,
  Breadcrumb,
  Button,
  COMBOBOX_THRESHOLD,
  Checkbox,
  Combobox,
  Dialog,
  Drawer,
  Input,
  Pagination,
  Pill,
  Radio,
  Select,
  Table,
  Tabs,
  Tag,
  TextArea,
  Toast,
  Toggle,
  Tooltip,
  wantsCombobox,
} from './primitives';
export type {
  BadgeProps,
  BreadcrumbProps,
  ButtonProps,
  ButtonVariant,
  CheckboxProps,
  Column,
  ComboboxProps,
  Crumb,
  DialogProps,
  DrawerProps,
  InputProps,
  PaginationProps,
  PillProps,
  RadioProps,
  SelectOption,
  SelectProps,
  TabItem,
  Sort,
  SortDirection,
  TableProps,
  TabsProps,
  TagProps,
  TextAreaProps,
  ToastProps,
  ToastTone,
  ToggleProps,
  Tone,
  TooltipProps,
} from './primitives';

export {
  CapacityGauge,
  DiffViewer,
  GateCard,
  LedgerEntryViewer,
  LineageTree,
  LogViewer,
  RunCard,
  SweepMatrix,
} from './composites';
export type {
  CapacityGaugeProps,
  DiffLine,
  DiffOp,
  DiffViewerProps,
  GateCardProps,
  GateDecision,
  GateEvidence,
  LedgerEntry,
  LedgerEntryViewerProps,
  LineageNode,
  LineageTreeProps,
  LogLine,
  LogViewerProps,
  RunAction,
  RunCardProps,
  SweepArm,
  SweepMatrixProps,
  SweepMetric,
} from './composites';
