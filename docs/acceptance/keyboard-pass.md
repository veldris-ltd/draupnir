# Manual keyboard pass

Evidence for **AC-U5**: "Every function is reachable and operable by keyboard,
with a visible focus indicator. Verified by a manual keyboard pass recorded in
the evidence pack."

## How it was performed

Eleven console routes were walked using **key events only**. There is no
`click()` and no `focus()` anywhere in `web/e2e/a11y/keyboard.spec.ts` — a walk
that focused an element to prove it was reachable would be proving the
opposite. Each Tab press was followed by a read of `document.activeElement`,
recording the element's tag, its accessible name computed the way a screen
reader computes it (`aria-label`, then `aria-labelledby`, then a `<label for>`,
then a wrapping `<label>`, then `title`, then text), whether a focus indicator
was painted, and whether the control was disabled.

The record is `docs/acceptance/evidence/keyboard-pass.json`, written by the
walk itself. The findings below are read off that record; the assertions in the
spec cover what can be asserted, and the findings cover what cannot.

**What this is and is not.** It is a keyboard-only traversal, driven and
recorded rather than performed by a person at a keyboard, and reviewed by hand
afterwards. It is not a screen-reader pass: nothing here was listened to with
NVDA or VoiceOver, and a screen-reader pass is a separate exercise this does
not stand in for. That is recorded rather than glossed, because a pass that
claimed more than it did would be worse than none.

```bash
make test-a11y     # includes the walk
```

## What was walked

| Route | Focus stops | Tab escaped the page | Notes |
|---|---:|---|---|
| `/` | 14 | yes, to browser chrome | |
| `/corpora` | 15 | yes | |
| `/corpora/register` | 19 | yes | see K-1 |
| `/runs` | 24 | yes | |
| `/runs/compose` | 17 | yes | |
| `/models` | 16 | yes | |
| `/gates` | 12 | yes | see K-1 |
| `/audit` | 60 (walk limit) | not within 60 | see K-2 |
| `/sites` | 15 | yes | |
| `/admin/policy` | 16 | yes | |
| `/signin` | 12 | yes | |

## What passed

- **Every focus stop has an accessible name.** Not one control on any route
  announces as its tag. The site switcher's links carry the anchor state in
  their name — `Sindri / Federation anchor state: anchored` — so a keyboard
  user learns which site they are switching to and whether it is anchored
  without leaving the control.
- **Every focus stop paints a visible indicator.** Read from the computed
  style — an outline or a box shadow — rather than from a class name, because
  a class that is supposed to paint a ring and does not is exactly the defect
  worth catching.
- **No control is named only by its placeholder.** A placeholder disappears the
  moment the user types, which is the moment they most need it (WCAG 2.4.6,
  3.3.2).
- **No keyboard trap.** Tab escapes every route to the browser chrome. Nothing
  cycles back into a region it cannot leave (WCAG 2.1.2).
- **A skip link is the first stop on every route**, and it is the first thing
  a keyboard user meets rather than eleven navigation links.
- **The command palette is fully operable on the keyboard.** `Ctrl+K` opens it,
  typing filters, `Enter` navigates, `Ctrl+K` reopens and `Escape` closes.
  That is AC-U17's claim, exercised without a mouse.

## Findings

### K-1 — An unavailable control leaves the tab ring, so a keyboard user cannot discover it

**Open. Recommendation recorded; not changed in this build.**

On `/corpora/register` the walk reaches the five fields of step 1 and then
leaves the page. The wizard's **Back** and **Next** buttons are never focus
stops: at step 1 Back goes nowhere and Next is unavailable until a valid
digest is entered, so both render in JARNGREIPR's `readOnly` state, which sets
the `disabled` attribute — and a `disabled` control is not focusable.

The same shape appears on `/gates`: the decision controls stay unavailable
until the gate evidence has been in the viewport (AC-U13), so they are absent
from the tab ring until then.

**Why this is not an AC-U5 failure.** AC-U5 asks that every *function* is
reachable and operable. In both cases the function is genuinely unavailable —
the digest is invalid, or the evidence has not been read — and becomes
reachable the moment it becomes available. A keyboard user can scroll the gate
evidence into view with Space or Page Down, because it is in the page flow and
not inside a scroll container, and the controls then enter the ring.

**Why it is still a finding.** `Button` already carries the explanation of why
it is inert, in a `title` and a visually hidden span. A `disabled` control is
not focusable, so neither ever reaches a keyboard or screen-reader user — the
"dead end" the component's own comment says it is avoiding. The ARIA authoring
practice for a control whose unavailability the user must be able to discover
is `aria-disabled="true"` **without** `disabled`, keeping the control focusable
while the handler stays a no-op.

**Why it was not changed here.** JARNGREIPR asserts the current behaviour
across all twenty-four components — `disables every acting control in the %s
state` — and two journey specs assert `toBeDisabled()`. Changing it is a
design-system decision with a cross-cutting test change behind it, and this is
the integration prompt. It is recorded rather than done, and the record names
the fix.

### K-2 — `/audit` does not close its tab ring within sixty presses

**Not a defect. Recorded so the number is not read as one.**

The ledger table has a focusable control per row, and the seeded chain has more
rows than the walk has presses. The walk was still finding new stops when it
stopped, which is the opposite of a trap: a trap is Tab *returning* to
somewhere it has been while the ring has not closed, and that is what the
assertion tests. It is worth knowing that a keyboard user reaching the footer
of the audit screen tabs through every row to get there; a paginated table
already limits that to one page.

### K-3 — The site switcher's accessible name contains line breaks

**Cosmetic. Not changed.**

`Sindri\nFederation anchor state:\nanchored` reads correctly to a screen reader
— the name is announced as one string with the newlines collapsed — but it is
awkward in a record like this one, and a future `aria-label` would read better
than the concatenated text. The information is right, which is what AC-U12
asks for; only the phrasing is not.

## What is not covered

- **Screen readers.** Nothing here was listened to. The accessible names are
  computed, not announced, and announcement order, live-region politeness and
  table navigation are unverified by this pass.
- **Voice control and switch access.** Both depend on accessible names, which
  this checks, and on target size, which it does not.
- **The Storybook component surface.** Covered by the axe sweep over 175
  stories, which is a different check: it reads markup rather than walking it.
