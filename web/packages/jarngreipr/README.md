# JARNGREIPR

The DRAUPNIR design system: tokens, sixteen primitives, eight composites, and
the six states every one of them ships.

Named for the iron gloves without which Mjölnir cannot be held. The console is
the hammer; this is what makes it safe to pick up.

---

## The four rules

Everything else in this document follows from these, and each one is enforced
by something that fails rather than by a reviewer who remembers.

| Rule                                          | Enforced by                                                      |
| --------------------------------------------- | ---------------------------------------------------------------- |
| Tokens are the only source of visual values   | `web/scripts/token-lint.mjs`, run by `inv lint-web`              |
| Every component ships six states plus `ready` | `src/state/stories.test.ts`, `src/components.test.tsx`           |
| Every state has a Storybook story             | `src/state/stories.test.ts`, and the story index in `e2e/visual` |
| WCAG 2.2 AA at the component level            | `src/tokens/tokens.test.ts`, `e2e/a11y/components.spec.ts`       |

---

## Using it

```tsx
import { Button, GateCard, RunCard } from '@draupnir/jarngreipr';

<RunCard
  runId="01a06244-ad82-7b67-af4d-8df67b2095e8"
  model="CIM-014 Gaelic"
  runState="RUNNING"
  step={42_000}
  totalSteps={120_000}
  state={loading ? 'loading' : denied ? 'denied' : 'ready'}
  problem={problem}
/>;
```

Importing the package pulls in the stylesheet. A consumer that wants the CSS on
its own — a page-level stylesheet, for instance — imports
`@draupnir/jarngreipr/styles.css`.

Wrap the application in `.jg-root`. It carries the surface, the type scale and
the single `:focus-visible` ring the whole system uses; a component rendered
outside it has no focus indicator.

```html
<body class="jg-root" data-jg-theme="dark"></body>
```

`data-jg-theme` is optional. Without it the ramp follows
`prefers-color-scheme`. With it, the explicit choice wins in both directions —
a user who picks light on a dark system gets light, because a theme toggle that
only works one way is a toggle half the users report as broken.

---

## The six states

```ts
type ComponentState =
  'ready' | 'loading' | 'empty' | 'error' | 'denied' | 'readOnly' | 'partitioned';
```

Every component takes `state`, `stateMessage` and `problem` through the shared
`StateProps`, so `state="denied"` means the same thing on a button as on a
lineage tree.

The five non-`ready` states are not decoration. Collapsing any two of them
loses something an operator acts on:

- **loading** — the answer is coming. Wait.
- **empty** — there is genuinely nothing. Not an error; do not retry.
- **error** — something failed. The problem document says what, and carries a
  correlation identifier to quote.
- **denied** — your role does not permit this. Distinct from `empty`, and the
  wording says so: an operator who reads a denial as "nothing here" draws the
  one wrong conclusion available.
- **readOnly** — you may look and not touch. Controls stay visible and
  disabled rather than hidden, so the shape of what exists is still legible.
- **partitioned** — the site is cut off from the federation. Training and
  evaluation continue; release is blocked (Decision S8). Emphatically not an
  error: rendering it as one sends an operator to investigate a network they
  already know about and cannot fix.

Two rendering patterns, chosen per component by what the state implies:

- a control that **is** the thing (button, input, toggle) renders itself inert
  and keeps its shape, because replacing a disabled button with a panel loses
  the fact that the action exists at all;
- a container that **shows** things (table, tabs, card) hands its content to
  `StateSurface`, because a table with headers, no rows and no explanation is
  the exact ambiguity the six states exist to remove.

One exception, and it is deliberate: a **dismissal** stays operable in every
state. Disabling the Cancel of a denied dialog traps a keyboard user inside a
modal that has just told them there is nothing they can do. Mark such a control
`<Button dismiss>`; the attribute exists so the rule can be tested with its one
exception named rather than assumed.

---

## Tokens

`src/tokens/tokens.css` is the only file in the workspace allowed to state a
raw visual value. Everything else spends tokens.

| Group      | Prefix                                                                                                                                | Notes                                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Typography | `--jg-font-*`, `--jg-text-*`, `--jg-leading-*`, `--jg-weight-*`                                                                       | A scale, so two headings chosen independently are the same size                         |
| Spacing    | `--jg-space-0` … `--jg-space-16`                                                                                                      | 4px base: a dense console needs steps smaller than 8                                    |
| Radius     | `--jg-radius-*`                                                                                                                       |                                                                                         |
| Motion     | `--jg-duration-*`, `--jg-ease-*`                                                                                                      | Every duration is a token so `prefers-reduced-motion` can zero all of them in one place |
| Colour     | `--jg-surface-*`, `--jg-text-*`, `--jg-border-*`, `--jg-accent-*`, `--jg-danger-*`, `--jg-warning-*`, `--jg-success-*`, `--jg-info-*` | Light and dark ramps, both measured                                                     |
| Focus      | `--jg-focus-ring`, `--jg-focus-width`, `--jg-focus-offset`                                                                            | One ring, everywhere                                                                    |
| Layering   | `--jg-z-*`                                                                                                                            |                                                                                         |

### What the token linter checks

Three categories, exactly the three the brief names:

- **colour** — any literal colour (hex, `rgb()`, a named colour) in a
  colour-bearing property, in CSS or in component source;
- **spacing** — any non-zero length in a `margin`, `padding`, `gap` or inset;
- **radius** — any literal length in a `border-radius`.

It also refuses a `--jg-*` custom property declared outside `tokens.css`: a
token minted in a component stylesheet is the same failure by another route.

### What it deliberately does not check

Intrinsic sizing — `height`, `max-width`, `grid-template-columns`. A log row is
`1.25rem` tall because that is what one line of monospace occupies at the token
font size, not because a designer chose it, and tokenising it would invent a
token nobody reasons about. Inline geometry in a component is also exempt: a
gauge fill is `width: 63%` because the pool is 63% used, and rejecting that
would be rejecting the data.

The linter's own fixtures live in `web/tests/__fixtures__/token-lint/` and
`web/tests/token-lint.test.ts` proves it fails on each category. The first
version of it passed a file containing `color: #2d6cdf`, because its parser
read one declaration per line and the fixture wrote the rule on one. A gate
nobody has watched fail is a gate nobody knows works.

---

## Contributing a component

1. **Write the component in `src/primitives/` or `src/composites/`.** Take
   `StateProps`. Render your content only in `ready` (and `readOnly`); hand the
   rest to `StateSurface`, or go inert and say why.
2. **Style it in the layer's stylesheet**, in tokens only.
3. **Export it from `src/index.ts`**, in the `export {}` block for its layer and
   its types in the matching `export type {}` block. `stories.test.ts` reads
   those blocks to decide what needs stories, so an export that is not there is
   a component nothing checks.
4. **Add `<Name>.stories.tsx` beside it**, using the factory:

   ```tsx
   import type { Meta } from '@storybook/react';
   import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
   import { Thing } from '.';

   export default {
     title: 'Primitives/Thing',
     ...COMPONENT_META,
   } satisfies Meta;

   const stories = stateStories((state) => <Thing state={state} problem={SAMPLE_PROBLEM} />);

   export const Ready = stories.Ready;
   export const Loading = stories.Loading;
   export const Empty = stories.Empty;
   export const ErrorState = stories.ErrorState;
   export const Denied = stories.Denied;
   export const ReadOnly = stories.ReadOnly;
   export const Partitioned = stories.Partitioned;
   ```

   The default export must be an **object literal**. Storybook's story indexer
   is static: a default export it cannot read at parse time is a component that
   never appears in the sidebar and never gets a snapshot. `ErrorState` rather
   than `Error` because `Error` is a reserved global; the factory corrects the
   displayed name.

5. **Add a row to `COMPONENTS` in `src/components.test.tsx`.** That is where
   every component is rendered in all seven states and checked for a live
   region, for inert controls, and for an operable dismissal.
6. **If you introduce a colour pairing, add it to `TEXT_PAIRS` or
   `BOUNDARY_PAIRS` in `src/tokens/tokens.test.ts`.** The list is explicit
   rather than a cartesian product of the ramp, because the contract is "every
   pairing a component actually makes", and an unmeasured pairing is how a ramp
   ships at 2.5:1.

### The checks, locally

```bash
pnpm --dir web run lint:tokens && pnpm --dir web run lint && pnpm --dir web run typecheck && pnpm --dir web test
```

and, for the visual and accessibility gates:

```bash
pnpm --dir web run build-storybook && pnpm --dir web run test:a11y && pnpm --dir web run test:visual
```

---

## Accessibility

WCAG 2.2 AA is a merge gate, not later remediation (Decision S13).

- **Contrast.** `tokens.test.ts` recomputes every declared pairing in both
  ramps from the stylesheet: 4.5:1 for text (1.4.3), 3:1 for control boundaries
  and the focus ring (1.4.11). It found `--jg-border` at 1.5:1 on the day it
  was written; the ramp changed, not the threshold.
- **Focus.** One `:focus-visible` ring, defined once on `.jg-root`.
  `outline: none` without a replacement is banned and the test checks for it.
- **Keyboard.** Tabs and the lineage tree implement the ARIA authoring
  practices — roving tabindex, arrow keys to move, Home and End to jump. The
  log viewer, the ledger payload and the diff body are focusable because they
  scroll, and WCAG 2.1.1 requires a keyboard user to be able to reach them.
- **Colour is never the only carrier.** A gate decision has a coloured border,
  a badge, _and_ a sentence. A diff has a background wash, a `+`/`-` character,
  _and_ the words "Added"/"Removed" for a screen reader. The best cell of a
  sweep is marked with a check glyph and the word "Best", not only a green
  wash.
- **Live regions.** `StateSurface` announces a state change `polite`, not
  `assertive`: a run board with fifty-six panels settling at once would
  otherwise interrupt continuously. An error toast is the exception and uses
  `role="alert"`.
- **Reduced motion.** Every duration is a token, and
  `prefers-reduced-motion: reduce` zeroes all of them in one place. The loading
  spinner stops rotating and stays visible as a static ring — the affordance is
  the ring, not the spin.

### Virtualisation has an accessibility cost, paid explicitly

The log viewer renders only the visible window: a training log runs to millions
of lines and a browser asked to lay all of them out stops responding, which in
practice means an operator cannot read the log of the run that is going wrong.
A screen reader cannot read rows that do not exist, so the region is labelled
with the total line count and the visible range, and only the tail is
announced, only while streaming.

---

## Layout

```
src/
  tokens/tokens.css       the ramp; the only raw visual values in the workspace
  state/states.tsx        ComponentState, StateProps, StateSurface
  state/stories.tsx       stateStories(), COMPONENT_META, SAMPLE_PROBLEM
  primitives/index.tsx    the sixteen controls
  composites/index.tsx    the eight DRAUPNIR-shaped assemblies
  styles.css              the whole system as one stylesheet
  index.ts                the public surface
```

The package resolves to `src/` under the `development` export condition, so the
console and the tests never need it built first and never read a stale bundle.
`dist/` is the library build; `dist-types/` is the declaration output, kept
separate so that running the typechecker cannot overwrite the bundle.
