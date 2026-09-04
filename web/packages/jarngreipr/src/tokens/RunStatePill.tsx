import type { JSX } from 'react';

import { type RunState } from './index';

/**
 * A run's state, rendered as section 4.2 requires it.
 *
 * The colour is not decided here. The component writes the state name into
 * `data-jg-run-state` and `tokens/state.css` supplies the four properties the
 * pill paints with, so a state reads identically on this card, on a table row
 * and on a timeline (AC-V4). A `Record<RunState, Tone>` in this file was
 * exactly the per-component choice section 4.2 forbids, and it also disagreed
 * with SAD 6.1 about which states exist.
 *
 * The label is inside the pill, always: "colour never carries meaning alone".
 */
export interface RunStatePillProps {
  state: RunState;
}

export function RunStatePill({ state }: RunStatePillProps): JSX.Element {
  return (
    <span className="jg-state-pill" data-jg-run-state={state}>
      <span className="jg-state-pill__marker" aria-hidden="true" />
      {state.replace(/_/g, ' ')}
    </span>
  );
}
