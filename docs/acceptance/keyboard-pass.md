# Manual keyboard pass

Evidence for **AC-U5**: "Every function is reachable and operable by keyboard,
with a visible focus indicator. Verified by a manual keyboard pass recorded in
the evidence pack."

## How it was performed

Twelve console routes were walked using **key events only**. There is no
`click()` and no `focus()` anywhere in `web/e2e/a11y/keyboard.spec.ts` — a walk
that focused an element to prove it was reachable would be proving the
opposite. Each Tab press was followed by a read of `document.activeElement`,
recording:
- the element's tag;
- its accessible name, computed the way a screen reader computes it:
  `aria-label`, then `aria-labelledby`, then a `<label for>`, then a wrapping
  `<label>`, then its text, then `title`;
- whether a focus indicator was painted;
- whether the control was unavailable;
- what it says beyond its name — its title, the text it is described by, and
  a wrapping label's title — which is where an unavailable control gives its
  reason.

Until RF-29 the name read `title` before text. That named an unavailable
button by its reason, so the record could not show which control it was.

The record is `docs/acceptance/evidence/keyboard-pass.json`, written by the
walk itself. The findings below are read off that record; the assertions in the
spec cover what can be asserted, and the findings cover what cannot.

The record is not committed (RF-31). It holds the wall-clock time and whatever
the seeded database held on the day, so every run rewrote it, and a committed
file whose diff changes on every run is one nobody reads. The pipeline writes a
fresh record on every build and uploads it in the workflow artefact
`acceptance-evidence-<commit>`. `make test-a11y` writes one locally. The
figures below are from the walk of 14 September 2026.

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
| `/` | 15 | yes, to browser chrome | |
| `/corpora` | 17 | yes | |
| `/corpora/register` | 22 | yes | reaches Back and Continue, both unavailable; see K-1 |
| `/runs` | 26 | yes | |
| `/runs/compose` | 18 | yes | |
| `/models` | 18 | yes | |
| `/gates` | 14 | yes | the queue |
| `/gates/:id` | 20 | yes | the first pending approval; reaches Sign and approve and Reject; see K-1 |
| `/audit` | 60 (walk limit) | not within 60 | see K-2 |
| `/sites` | 17 | yes | |
| `/admin/policy` | 18 | yes | |
| `/signin` | 13 | yes | |

Walked on 14 September 2026, after RF-29. Most counts are one or two higher
than the first walk's, because the console gained controls between the two.
Only `/corpora/register`'s increase is K-1's: its two unavailable buttons are
now stops.

## What passed

- **Every unavailable stop says why.** Checked for every stop marked
  `aria-disabled`, not read off by hand: the walk fails if one has no title
  and no description. On `/corpora/register` both Back and Continue carry
  "Read only: you can see this and not change it."
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

**Closed by RF-29.** The finding as first recorded is kept below, followed by
what changed and the evidence.

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
across every component — `disables every acting control in the %s
state` — and two journey specs assert `toBeDisabled()`. Changing it is a
design-system decision with a cross-cutting test change behind it, and this is
the integration prompt. It is recorded rather than done, and the record names
the fix.

**What changed.** The fix named above was made, across every acting control
in JARNGREIPR: Button, Toggle, the tag's remove button, tabs, text input, text
area, select, combobox, checkbox and radio. In any state but `ready` a control
is `aria-disabled="true"` and never `disabled`, so it stays in the tab ring.
Its explanation reaches assistive technology through the title and hidden
text Button already had, and through the field description for the inputs.

The browser no longer refuses activation, so the components do:
- a click is cancelled, which is also what stops a submit button submitting;
- a select refuses the pointer and every key but Tab;
- text fields are read-only;
- every change handler returns without acting.

A checkbox and a radio are held by their controlled value alone, because
cancelling their click as well let the browser restore a tick React had
already cleared. The component test found that.

**Evidence.**

- **The walk reaches them.** On `/corpora/register` Back and Continue are
  focus stops 21 and 22, both unavailable, each with its reason. The walk
  now also covers the approval screen, `/gates/:id`, and reaches Sign and
  approve and Reject. The walk fails if either screen stops reaching its
  controls. Whether they are available there depends on more than the
  evidence since RF-40 and RF-41: Sign and approve is unavailable when no
  signing agent answers with the approver's key, and both decisions are
  unavailable when the queue carries no recorded evidence. In each case the
  control stays a focus stop and carries the reason as its accessible
  description, which is the rule this finding set. That the unavailable state
  is reachable and does nothing is shown by the wizard and the component
  tests.
- **Activating one changes nothing — asserted.**
  - `components.test.tsx` reaches each control by Tab in all six states that
    are not `ready`. It then tries a click, Enter, Space, typing, arrow keys
    and a direct change event, as each control takes them, and asserts no
    handler ran and no value changed.
  - For a submit button it also asserts the enclosing form did not submit.
  - Journey J1 presses Enter and Space on the unavailable Continue and asserts
    the wizard did not advance.
- **The rule is enforced for every component.** The assertion "keeps every
  acting control reachable and unavailable" checks `aria-disabled` and the
  absence of `disabled` for every component in that test's table, in every
  replacing state.

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
- **The Storybook component surface.** Covered by the axe sweep over 220
  stories, which is a different check: it reads markup rather than walking it.
