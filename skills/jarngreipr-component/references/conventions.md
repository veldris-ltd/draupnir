# JARNGREIPR conventions

What `web/packages/jarngreipr/` actually does. Where this differs from the SAD,
the code is right and the specification is amended.

## The layers

```
tokens      the only source of visual values (AC-U3)
state       the seven states every component ships (AC-U2)
primitives  sixteen generic controls
composites  eight DRAUPNIR-shaped assemblies
example     this skill's worked example; not exported from index.ts
```

The state layer sits *under* the components rather than beside them, because
"every component ships six states" has to be checkable. It is: `stateStories()`
produces the stories from one render function, and `stories.test.ts` fails the
build if a component's story file does not export all seven.

## The seven states

`ready` plus the six of AC-U2. Each is a distinct thing an operator needs to be
told, and collapsing any two loses information they act on.

| State | Means | Replaces content? |
|---|---|---|
| `ready` | here it is | — |
| `loading` | the answer is coming; wait | yes |
| `empty` | there is genuinely nothing; do not retry | yes |
| `error` | something failed; the problem document says what | yes |
| `denied` | your role does not permit this; do not read it as "nothing here" | yes |
| `readOnly` | you may see it and not change it | **no** |
| `partitioned` | the site is cut off; training continues, release is blocked | yes |

`partitioned` is emphatically not an error. It is a normal operating condition
with a consequence (Decision S8), and rendering it as a failure sends an
operator to investigate a network they cannot fix.

`readOnly` does not replace content, and controls stay **visible and disabled**
rather than hidden. Hiding them removes the evidence that the action exists.

`StateSurface` renders the five replacing states from one implementation, in a
`polite` live region — `assertive` would interrupt continuously on a board with
fifty-six panels settling at once. SAD 11F.4 requires live regions to announce
state changes, and a screen-reader user who is not told a panel became `denied`
reads an empty region and concludes there is nothing there.

## Story files

Seven `export const` lines, produced by the factory and written out
individually:

```tsx
export default {
  title: 'Composites/PoolStatus',
  ...COMPONENT_META,
} satisfies Meta;

const stories = stateStories((state) => <PoolStatus … state={state} />);

export const Ready = stories.Ready;
export const Loading = stories.Loading;
export const Empty = stories.Empty;
export const ErrorState = stories.ErrorState;
export const Denied = stories.Denied;
export const ReadOnly = stories.ReadOnly;
export const Partitioned = stories.Partitioned;
```

Two things here are not style:

- The default export is an **object literal**. Storybook's story indexer is
  static; `const meta = componentMeta(title)` parses as a call, the indexer
  refuses it ("default export must be an object"), and the component never
  appears in the sidebar.
- `ErrorState`, not `Error`. `Error` shadows the global, and the factory names
  the story `Error` for display while exporting under a safe symbol.

## Tokens

The full set is in `tokens/tokens.css`. The families:

```
colour     --jg-surface{,-raised,-sunken,-overlay,-inverse}
           --jg-text{,-muted,-subtle,-inverse,-on-accent}
           --jg-border{,-subtle,-strong}
           --jg-accent / --jg-info / --jg-success / --jg-warning / --jg-danger
             each with -subtle, -border, and accent also -hover, -active
space      --jg-space-0 1 2 3 4 5 6 8 10 12 16
radius     --jg-radius-none sm md lg xl full
type       --jg-font-sans, --jg-font-mono
           --jg-text-2xs xs sm md lg xl 2xl 3xl
           --jg-weight-regular medium semibold
           --jg-leading-tight normal loose
motion     --jg-duration-instant fast normal slow
           --jg-ease-standard enter exit
depth      --jg-elevation-1 2 3, --jg-scrim, --jg-z-*
focus      --jg-focus-ring, --jg-focus-width, --jg-focus-offset
border     --jg-border-width, --jg-border-width-thick
```

`--jg-border` is `#848b96` light and `#646b76` dark. Those values were changed
rather than the threshold when the ramp measured 1.5:1 against WCAG 1.4.11's
3:1 requirement — the criterion is not the thing that gives way.

**What the token linter does not flag**, deliberately: intrinsic sizing
(`height`, `max-width`, `grid-template-columns`), and inline geometry in TSX. A
log row is `1.25rem` tall because that is what one line of monospace occupies,
and a gauge fill is `width: 63%` because the pool is 63% used. Tokenising
either would invent a token nobody reasons about.

## Class naming

`jg-<component>` block, `jg-<component>__<part>` element, and state as a data
attribute rather than a modifier class:

```css
.jg-pool-status { … }
.jg-pool-status__row { … }
.jg-pool-status__row[data-jg-tone='warning'] { … }
.jg-pool-status[data-jg-state='denied'] { … }
```

Data attributes rather than modifier classes because the state is already on
the element as `data-jg-state`, set by the component from one prop — a modifier
class would be the same fact spelled twice.

`jg-sr-only` is the visually-hidden utility. Use it for the text that carries
what a glyph means.

## Accessibility

WCAG 2.2 AA, verified rather than asserted. Three things that have actually
gone wrong here:

- **`aria-label` on a bare `<span>` or `<div>` is prohibited.** A generic
  element has no role that supports an accessible name, so the label is
  discarded and axe reports it as serious. It shipped in four console screens
  and in `SweepMatrix`, passing review for a whole prompt. Use an
  `aria-hidden` glyph plus a `jg-sr-only` span.
- **`role="meter"`, not `progressbar`**, for a capacity gauge. A progress bar
  is a task advancing to completion, and a GPU pool at 100% has not finished
  anything.
- **Colour is never the only carrier.** A badge reading "FAILED" in red still
  reads "FAILED" in greyscale (WCAG 1.4.1).

Respect `prefers-reduced-motion`: every transition is behind it, and
`motion.test.ts` checks that.

## The gates a component passes

| Gate | Command | Asserts |
|---|---|---|
| Token linter | `pnpm lint:tokens` | no literal colour, spacing or radius outside `tokens.css` |
| Story shape | `pnpm test` | every exported component has a story file with the seven exports, built through `stateStories()` with `...COMPONENT_META` |
| Contrast | `pnpm test` | measured from the stylesheet, in both ramps |
| Motion | `pnpm test` | every transition is behind `prefers-reduced-motion` |
| axe, jsdom | `pnpm test` | zero serious or critical on the worked example, in all seven states |
| Types | `pnpm --filter @draupnir/jarngreipr typecheck` | `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess` |
| axe, browser | `make test-a11y` | every story, eight shards, zero serious or critical |
| Visual | `make test-visual` | every story renders — `body.sb-show-main`, and no `pageerror` |

Strict TypeScript here means an optional prop is declared `?: T | undefined`,
not `?: T`, and narrowing has to happen inline rather than through a helper
function.
