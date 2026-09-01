# Sindri Forge and DRAUPNIR  ·  Figure set

**CONFIDENTIAL — RECIPIENT EYES ONLY**  ·  Veldris Ltd, company no. 17366869

Twenty three figures supporting three documents. All are rendered at print resolution
(160 to 185 dpi) on a white background and are reproducible from the sources in this
archive.

## Contents

| Path | Contents |
|---|---|
| `png/` | The 23 rendered figures |
| `sources/graphviz/` | 14 Graphviz `.dot` sources |
| `sources/python/` | 3 Python renderers (matplotlib) |
| `sources/*.sh` | Shell drivers that render the Graphviz sources |

## VLD-INF-SINDRI-001 Rev 3.3  ·  Sindri Forge build manual

| File | Figure | Subject |
|---|---|---|
| `01_rack_elevations.png` | 1 | Rack elevations for STEDI, BELGR and TANGIR, front view, U1 at floor |
| `02_topology.png` | 3 | Network topology: BAUGR, BRAUT and TAUMR fabrics |
| `03_cx7_ring.png` | 5 | ConnectX-7 200 GbE ring, port and address map |
| `04_model_factory.png` | 6 | CIM-56 model factory pipeline |
| `05_software_stack.png` | 4 | Software stack, layers L0 to L7 |
| `06_power_thermal.png` | 2 | Power and thermal budget |

## VLD-SAD-DRAUPNIR-001 Rev 1.4  ·  Solutions architecture

| File | Figure | Subject |
|---|---|---|
| `07_draupnir_c4_context.png` | 1 | C4 level 1, system context |
| `08_draupnir_c4_container.png` | 2 | C4 level 2, containers and modules |
| `09_draupnir_state.png` | 3 | Model lifecycle state machine |
| `10_draupnir_threat.png` | 4 | STRIDE threat model dataflow with trust boundaries |
| `11_federation.png` | 5 | Multi-forge federation, MEGINGJORD and GULLINBURSTI |
| `13_c4_l3_core.png` | 6 | C4 level 3, core component decomposition |
| `12_er_model.png` | 7 | Entity relationship model |
| `16_sequence.png` | 8 | Sequence, adapter run from submission to release |
| `14_ui_ia.png` | 9 | Console information architecture and user journeys |
| `15_cicd.png` | 10 | Continuous integration and delivery pipeline |

## VLD-UX-DRAUPNIR-001 Rev 1.0  ·  Experience and interface design

| File | Figure | Subject |
|---|---|---|
| `20_tokens.png` | 1 | JARNGREIPR design tokens |
| `21_states.png` | 2 | Universal component states and content rules |
| `14_ui_ia.png` | 3 | Console information architecture (shared with the SAD) |
| `25_screens.png` | 4 | Screen inventory and navigation model, 31 screens |
| `22_wire_runs.png` | 5 | Wireframes 1: run board, run detail, failure diagnosis |
| `23_wire_gates.png` | 6 | Wireframes 2: gate queue, approval, publish, partitioned state |
| `24_wire_lineage.png` | 7 | Wireframes 3: lineage, sweep comparison, corpus registration, CON-A |
| `26_journeys.png` | 8 | The four primary journeys |

`14_ui_ia.png` appears in both the SAD and the UX document. It is stored once.

## Reproducing the figures

Requires Graphviz, Python 3 and matplotlib. The shell drivers write into a sibling
`diagrams/` directory, so run them from the directory that contains `dot/`.

```
dot -Tpng -Gdpi=165 sources/graphviz/11_federation.dot -o png/11_federation.png

python3 sources/python/gen_racks.py    # 01, 05, 06
python3 sources/python/gen_seq.py      # 16
python3 sources/python/gen_ux.py       # 20 to 24
```

## Style

All figures share one palette, defined as the JARNGREIPR token set in
VLD-UX-DRAUPNIR-001 section 4. Ink `#0E1A2B` to `#F5F8FB`, forge accent `#E0A030`,
success `#2E8B57`, danger `#B3402F`, info `#177E89`, merge `#6C5B9E`. No figure
conveys meaning by colour alone; every coloured element carries a text label.

Em dashes are not used except in the classification marker.
