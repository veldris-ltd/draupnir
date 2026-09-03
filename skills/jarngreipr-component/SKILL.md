---
name: jarngreipr-component
description: >
  Scaffold a JARNGREIPR component with all seven states, tokens only, and an
  accessible structure that passes axe — plus the story file Storybook's static
  indexer can see. Use when adding or changing a design-system component:
  "add a component", "new primitive", "the console needs a widget", "add a
  state to X".
---

# jarngreipr-component

Scaffold a component that passes the token linter, `stories.test.ts`, `tsc` and
an axe run without an edit.

## Why a scaffold rather than a description

Three rules, each of which a component can break while looking finished:

- **A component with only a happy path is not done** (AC-U2). Seven stories,
  and Storybook's indexer is static — a story it cannot read at parse time is a
  story that never renders and never gets a snapshot.
- **Tokens are the only source of visual values** (AC-U3). One `#2d6cdf`
  today, an undocumented eleventh blue in the ramp, and the dark theme quietly
  stops being a theme.
- **Accessibility is an acceptance criterion, not a review comment**
  (Decision S13). `aria-label` on a bare `<span>` is prohibited — a generic
  element has no role that supports a name — and that exact defect shipped in
  four console screens and in the sweep matrix, passing review for a whole
  prompt before axe caught it.

The scaffold makes the first unavoidable, the second unlikely, and the third
checkable in the frontend stage rather than eight browser shards later.

## Use it

```bash
python skills/jarngreipr-component/scripts/new_component.py --name PoolStatus --layer composites --summary "Allocation pools and how much of each is in use." --label "Allocation pools"
```

`--layer` is `primitives` for a generic control, `composites` for something
that knows what DRAUPNIR is — a run has a lifecycle state, a gate has evidence
with digests. Putting that knowledge in a composite rather than in a screen is
what makes the seven states enforceable: a screen that assembles its own gate
card out of divs ships a happy path and nothing else.

It writes:

```
<layer>/PoolStatus.tsx           the component, delegating five states to StateSurface
<layer>/PoolStatus.css           tokens only
<layer>/PoolStatus.stories.tsx   seven stories, through the shared factory
<layer>/index.tsx                export * from './PoolStatus'
../index.ts                      PoolStatus and PoolStatusProps, sorted into both blocks
```

The last one matters more than it looks. `stories.test.ts` reads `index.ts`'s
`export { … } from './composites'` block to decide which components must have
seven stories — a component missing from it ships whatever stories it happens
to have, and nothing fails.

Then:

```bash
pnpm lint:tokens
pnpm test
pnpm --filter @draupnir/jarngreipr typecheck
```

## What the generated component already gets right

| Rule | How |
|---|---|
| AC-U2, seven states | `stateStories()` produces all seven from one render function, and the seven `export const` lines are written out so the static indexer sees them. |
| One meaning per state | The five replacing states go to `StateSurface`. A component that renders its own "no permission" panel renders a slightly different one, and by the twentieth `denied` means five things. |
| `readOnly` is not a replacing state | The rows stay and the controls go inert, so the shape of what exists is still legible to someone who cannot change it. |
| AC-U3, tokens only | Every colour, space and radius in the stylesheet is a `var(--jg-*)`. There is no literal to copy. |
| WCAG 1.4.1 | Tone is carried by a word as well as by a border colour. |
| Accessible name | The list is labelled through `aria-label` on a `<ul>`, which has a role that supports a name. Nothing labels a bare `<span>`. |
| Storybook indexing | The default export is an object literal with `...COMPONENT_META` spread in, because `const meta = componentMeta(title)` parses as a call and the indexer refuses it. |

## What you then write

The component's actual content, in place of the generated rows. Keep the
`StateSurface` wrapper and the seven story exports; change everything inside.

Two rules while you do:

- **An empty array is not the `empty` state.** The caller says which state it
  is in, because "no rows" and "you are not allowed to see the rows" look
  identical from inside the component and mean opposite things.
- **Do not add a sixth non-ready state.** If you need one, it belongs in
  `state/states.ts` where every component gets it, not in this file where one
  does.

## Refusals

**Do not write a hex, an rem or a px in a stylesheet.** The token linter fails
the build, and it is right to. If the ramp lacks the value you need, add it to
`tokens/tokens.css` — with the contrast measured, since `tokens.test.ts`
measures it from the stylesheet in both ramps.

**Do not put `aria-label` on a `<span>` or a `<div>`.** Use a `jg-sr-only`
span for the text and `aria-hidden` on the glyph.

**Do not bypass `stateStories()`.** Seven hand-written stories drift into six
good ones and a `ready` under another name, and the shared meta — which is
what runs the a11y addon on every story — goes with them.

**Do not hide a control in `readOnly`.** Visible and disabled. Hiding it
removes the evidence that the action exists at all.

## References

- `references/conventions.md` — the seven states and what each means, the token
  names, the class naming, and what each existing gate asserts.

## Verified

`web/packages/jarngreipr/src/example/` holds this skill's worked example, which
is the scaffold's output with nothing edited.
`PoolStatus.a11y.test.tsx` renders all seven states and runs **real axe** over
each in jsdom, and `tests/contract/test_skills.py` regenerates the three files
and compares them byte for byte with what is committed. The token linter walks
the whole workspace, so it covers the example too, and the example's stories
join the Storybook a11y and visual sweeps.

If the conventions move and this skill does not, the byte comparison fails.
