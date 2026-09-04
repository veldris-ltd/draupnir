# The token layer, and what it could not take as issued

Evidence for **AC-V1**, **AC-V4**, **AC-V6** and **AC-V7**, and the place prompt
UX-1's instruction lands:

> Values are exactly those in VLD-UX-DRAUPNIR-001 section 4. Do not improve
> them. If a value is wrong, raise it rather than silently changing it.

Nothing in `ramp.css` has been adjusted. Every hex is the hex in section 4.1
and `tokens.test.ts` compares them against a transcription of the
specification's own table, so a future edit that improves a colour fails the
build rather than shipping.

What follows is everything the specification could not settle on its own, with
the reading taken and the reason. Ten items. Four are gaps that need a decision
from whoever owns the specification; the rest are choices the specification
left open and the token layer had to make.

## How the layer is arranged

Four files, because they answer four different questions.

| File | Holds | Section |
|---|---|---|
| `ramp.css` | the palette as issued, seven ramps | 4.1 |
| `tokens.css` | which step fills which role, per theme; type, space, radius, motion | 4.1, 4.3, 4.4 |
| `state.css` | the run state mapping and the pill it paints | 4.2 |
| `density.css` | comfortable and compact, and the touch target floor | 4.5 |

A component names a role and never a ramp step. That is what makes the exit
condition true: switching theme or density changes no component code, because
there is nothing in a component for either to change.

---

## Raised: four things the specification should settle

### R1. Three of the ten state colours cannot be the label they sit behind

Section 4.2 assigns a colour per state and section 4.2's last paragraph
requires every state to render "as a text label inside its pill". Three of the
ten colours cannot be that text on their own ramp's lightest tint:

| State | Colour | On its own 50 tint | Needs |
|---|---|---:|---:|
| TRAINING | forge 500 `#E0A030` | 2.13:1 | 4.5:1 |
| TRAINED | forge 300 `#EFC272` | 1.56:1 | 4.5:1 |
| AWAITING_APPROVAL | warning 500 `#D98E04` | 2.43:1 | 4.5:1 |

RELEASED (success 500) reaches 3.77:1, which clears large text and not body.

**Taken:** the state colour is unchanged and remains what the pill's marker
paints. The label takes the 800 step of the same ramp, which clears 4.5:1 on
the same tint. So TRAINING is still forge 500 and reads as forge 500; the words
"TRAINING" beside the dot are forge 800.

**For the specification:** section 4.2 assigns one colour per state where a
pill needs three. Either say that the assigned colour is the marker, or add a
label colour per state.

### R2. The merge ramp has no 300 step, and dark theme needs one

Section 4.1: "accent and semantic ramps shift one step lighter in dark theme to
hold contrast." Every ramp has a 300 except merge, which goes 500, 100, 50.
merge 500 on the dark raised surface is 2.84:1.

**Taken:** MERGED uses merge 100 in dark, which is 13:1 and reads as a light
lilac rather than the mid purple the light theme uses.

**For the specification:** merge needs a 300, or a stated dark value.

### R3. ink has no step between 400 and 600, and the border needs one

`--jg-border` draws the edge of every input, select and table, so WCAG 1.4.11
holds it to 3:1 against what is behind it. ink 400 `#7E8FA3` is the weight a
border wants and is 3.31:1 on white, 3.11:1 on ink 50 and **2.88:1 on ink 100**,
which is the sunken surface a table header sits on.

**Taken:** the border is ink 600, which is 5.98:1 at worst. It is heavier than a
border normally is.

**For the specification:** ink wants a 500 step, around 3.2:1 on white, or the sunken
surface wants to be ink 50 rather than ink 100.

### R4. ink 400 is the specified dark secondary text and misses on the overlay surface

Section 4.1 names dark "secondary text `#7E8FA3`", which is ink 400. Against
the three specified dark surfaces it is 5.59:1 on base, 4.97:1 on raised and
**4.28:1 on overlay**.

**Taken:** ink 400 is `--jg-text-subtle`, which is used for placeholders,
disabled states and breadcrumb separators, none of which render on the overlay
surface. `--jg-text-muted` is ink 300 and is what a dialog body uses. The pair
list holds `--jg-text-muted` on the overlay and not `--jg-text-subtle`, because
the latter is not a pairing anything makes.

**For the specification:** either the overlay surface is a step darker, or the
secondary text is ink 300.

---

## Decided: six things the specification left open

### D1. The light theme's surfaces

Section 4.1 specifies the dark surfaces and not the light ones. Taken: white is
the page, ink 50 is a card, ink 100 is a well or a table header. ink's stated
purpose is "text, surfaces, chrome, borders" and 50 and 100 are its two surface
steps.

### D2. The dark theme has three surfaces and the system uses four

Base, raised and overlay are specified; the system also has a sunken well. In
dark, sunken is the base value, so a well and the page share a colour and are
told apart by the border between them.

### D3. Hover and active are mixes, not new colours

The forge ramp has exactly one step that carries white text at 4.5:1, so a
hover state one step along would be a hover state whose label cannot be read.
Hover and active are the rest colour mixed toward ink 900 with `color-mix()`,
which composes two specified values rather than introducing a third. The
contrast checker evaluates the mix rather than skipping it, so a hover state is
measured like everything else.

### D4. warning, success and info take their 800 step for text

warning 500 is 2.68:1 on white and success 500 is 4.25:1. Both 800 steps clear
4.5:1. The 500 steps keep their job as state markers, where section 4.2 puts
them.

### D5. The four corpus states section 4.2 does not colour

SAD 6.1 has fourteen states; section 4.2 assigns colours to ten. DRAFT,
CORPUS_REGISTERED, LICENCE_CLEARED and CURATED are stages of a corpus before a
run exists and never appear on a run board. They take QUEUED's inert treatment
rather than an invented colour of their own.

### D6. Two space steps and one type step were off the scale

`--jg-space-5` (20px), `--jg-space-10` (40px) and `--jg-text-2xs` (11px) are not
in section 4.4 or 4.3. The scale is now exactly the specification's; the three
names remain as references to the nearest step that is on it, so nothing
written against them breaks.

---

## What is checked, and where

| Criterion | Checked by |
|---|---|
| AC-V1 | `tokens.test.ts`: every value of section 4.1 compared against a transcription of the table, the eight type roles of 4.3, the space scale, the five radii, three elevations, three durations and the easing of 4.4 |
| AC-V4 | the mapping is fourteen rules in `state.css`; a test asserts no state name meets a ramp token in any component file |
| AC-V6 | `contrast.ts` holds 93 pairs, evaluated in both themes at 4.5:1 for text and 3:1 for a boundary. `pnpm lint:contrast`, and `make lint-web` in CI |
| AC-V7 | `--jg-touch-target` is declared once, outside both density blocks; a test fails if either mode declares it, and checks that compact reduces row height, body size and gutter |

The marker a pill paints with the section 4.2 colour is deliberately not held to
a threshold. It is redundant with the label beside it, and WCAG 1.4.11 exempts a
graphic that conveys nothing the text does not. The label and the pill's edge
are held, because those are what a low-vision reader actually needs.
