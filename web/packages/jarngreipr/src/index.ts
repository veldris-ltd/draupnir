/**
 * JARNGREIPR, the DRAUPNIR design system.
 *
 * Three layers, and the order matters:
 *
 *   tokens      the only source of visual values (AC-U3, enforced by
 *               `scripts/token-lint.mjs`, not by convention)
 *   state       the six states every component ships (AC-U2)
 *   primitives  the sixteen controls of SAD 11F.1
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

import './tokens/tokens.css';

export const JARNGREIPR_VERSION = '0.1.0';

export {
  ALL_STATES,
  REPLACING_STATES,
  StateSurface,
  isInert,
  replacesContent,
} from './state/states';
export type { ComponentState, ProblemSummary, StateProps, StateSurfaceProps } from './state/states';

export {
  Badge,
  Breadcrumb,
  Button,
  Checkbox,
  Dialog,
  Drawer,
  Input,
  Pagination,
  Radio,
  Select,
  Table,
  Tabs,
  Tag,
  Toast,
  Toggle,
  Tooltip,
} from './primitives';
export type {
  BadgeProps,
  BreadcrumbProps,
  ButtonProps,
  ButtonVariant,
  CheckboxProps,
  Column,
  Crumb,
  DialogProps,
  DrawerProps,
  InputProps,
  PaginationProps,
  RadioProps,
  SelectOption,
  SelectProps,
  TabItem,
  TableProps,
  TabsProps,
  TagProps,
  ToastProps,
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
  RunState,
  SweepArm,
  SweepMatrixProps,
  SweepMetric,
} from './composites';
