#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p diagrams dot

# ---------------------------------------------------------------- topology ---
cat > dot/02_topology.dot <<'EOF'
digraph forge {
  rankdir=TB;
  bgcolor="white";
  fontname="DejaVu Sans";
  labelloc="t";
  labeljust="l";
  label=<<b>SINDRI FORGE &#183; NETWORK TOPOLOGY</b><br align="left"/><font point-size="11" color="#1F3350">Veldris Ltd &#183; three isolated fabrics: BAUGR, BRAUT, TAUMR &#183; CONFIDENTIAL — RECIPIENT EYES ONLY</font><br align="left"/> >;
  fontsize=22; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=box, style="filled,rounded", penwidth=1.4, color="#0E1A2B", fontsize=10];
  edge [fontname="DejaVu Sans", fontsize=8, color="#3E5C82", penwidth=1.3];
  nodesep=0.45; ranksep=0.55;

  subgraph cluster_wan {
    label=<<b>SITE / WAN</b>>; fontsize=11; fontcolor="#1F3350";
    style="rounded,dashed"; color="#7E8FA3";
    RTR [label="Site router / firewall\n10.10.0.1\nNAT + egress allow-list only", fillcolor="#E9F0F7"];
    WG  [label="WireGuard endpoint\nVeldris_NXT VPS (UK)\nremote operator access", fillcolor="#E9F0F7"];
  }

  subgraph cluster_mgmt {
    label=<<b>FABRIC 3 &#183; TAUMR &#183; MANAGEMENT  :  1 GbE  :  10.10.0.0/24</b>>; fontsize=11; fontcolor="#177E89";
    style="rounded"; color="#177E89"; bgcolor="#F2FAFB";
    SWM [label="NORI  (SW-MGMT-01)\nTP-Link TL-SG108S\n8 x 1 GbE", fillcolor="#177E89", fontcolor="white"];
    PI  [label="REGIN  10.10.0.5\nRaspberry Pi 5 16 GB\nslurmctld + slurmdbd + MariaDB\nProm + Grafana + Loki + Alertmanager\ndnsmasq (sindri.veldris.internal) + chrony", fillcolor="#2E8B57", fontcolor="white"];
    LCD [label="CON-B operations console\n9\" 1280x720 touch\ndriven by REGIN", fillcolor="#6C5B9E", fontcolor="white"];
  }

  subgraph cluster_data {
    label=<<b>FABRIC 2 &#183; BRAUT &#183; DATA / STORAGE  :  10 GbE &#38; 2.5 GbE  :  10.20.0.0/24</b>>; fontsize=11; fontcolor="#3E5C82";
    style="rounded"; color="#3E5C82"; bgcolor="#F5F8FB";
    SWD [label="NAIN  (SW-DATA-01)\n6-port unmanaged\n2 x 10G RJ45 + 4 x 2.5G\n60 Gbps switching capacity", fillcolor="#3E5C82", fontcolor="white"];
    M1  [label="ANDVARI  10.20.0.21\nMac mini M4 Pro\nHODD VAULT + MLflow + MinIO\nTB5 -> SN850X 4 TB", fillcolor="#B9C6D4"];
    M2  [label="ALVISS  10.20.0.22\nMac mini M4 Pro\nRAUN eval / CI runner /\nAnsible control node", fillcolor="#B9C6D4"];
  }

  subgraph cluster_ring {
    label=<<b>FABRIC 1 &#183; BAUGR &#183; CX-7 RoCEv2 RING  :  200 GbE  :  192.168.0-5.0/24</b>>; fontsize=11; fontcolor="#E0A030";
    style="rounded"; color="#E0A030"; bgcolor="#FDF7EC";
    S1 [label="DVALIN   (dgx-spark-1)\nGB10 &#183; 128 GB &#183; 4 TB\n10.20.0.11", fillcolor="#E0A030"];
    S2 [label="DURIN   (dgx-spark-2)\nGB10 &#183; 128 GB &#183; 4 TB\n10.20.0.12", fillcolor="#E0A030"];
    S3 [label="DAIN   (dgx-spark-3)\nGB10 &#183; 128 GB &#183; 4 TB\n10.20.0.13", fillcolor="#E0A030"];

    S1 -> S2 [label="  DAC-1  200 GbE\n  P0 -> P1", dir=both, color="#C6851C", penwidth=3.2, fontcolor="#8A5A08"];
    S2 -> S3 [label="  DAC-2  200 GbE\n  P0 -> P1", dir=both, color="#C6851C", penwidth=3.2, fontcolor="#8A5A08"];
    S3 -> S1 [label="  DAC-3  200 GbE\n  P0 -> P1", dir=both, color="#C6851C", penwidth=3.2, fontcolor="#8A5A08"];
  }

  RTR -> SWM [label=" uplink 1 GbE ", dir=both];
  WG  -> RTR [style=dashed, dir=both, label=" encrypted "];
  SWM -> PI  [dir=both, label=" P2 "];
  PI  -> LCD [style=dotted, arrowhead=none, label=" HDMI+USB "];
  SWM -> SWD [label=" P1 <-> P6 uplink 1 GbE\n (2.5G port, 1G negotiated) ", dir=both, penwidth=2.0];

  SWD -> M1 [label=" P1  10 GbE ", dir=both, penwidth=2.4];
  SWD -> S1 [label=" P2  10 GbE ", dir=both, penwidth=2.4];
  SWD -> S2 [label=" P3  2.5 GbE ", dir=both, penwidth=1.8];
  SWD -> S3 [label=" P4  2.5 GbE ", dir=both, penwidth=1.8];
  SWD -> M2 [label=" P5  2.5 GbE ", dir=both, penwidth=1.8];

  { rank=same; M1; M2; }
}
EOF

# ------------------------------------------------------------- ring detail ---
cat > dot/03_cx7_ring.dot <<'EOF'
digraph ring {
  rankdir=LR;
  bgcolor="white";
  fontname="DejaVu Sans";
  labelloc="t"; labeljust="l";
  label=<<b>BAUGR &#183; CX-7 200 GbE RING &#183; PORT AND ADDRESS MAP</b><br align="left"/><font point-size="10" color="#1F3350">Port0 = QSFP cage adjacent to the RJ45 jack &#183; Port1 = outer cage. Each physical port presents TWO logical interfaces (enp1... and enP2p...); both must be addressed.</font><br align="left"/> >;
  fontsize=20; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=plaintext, fontsize=9];
  edge [fontname="DejaVu Sans", fontsize=9, penwidth=3.4, color="#C6851C", fontcolor="#8A5A08"];
  nodesep=1.0; ranksep=1.9;

  N1 [label=<
   <table border="0" cellborder="1" cellspacing="0" cellpadding="5" bgcolor="#FDF7EC" color="#0E1A2B">
    <tr><td colspan="2" bgcolor="#E0A030"><b>DVALIN &#183; dgx-spark-1</b><br/><font point-size="8">RING NODE 1 &#183; RANK 0</font></td></tr>
    <tr><td align="left"><b>P0</b> enp1s0f0np0</td><td align="left">192.168.0.1/24</td></tr>
    <tr><td align="left"><b>P0</b> enP2p1s0f0np0</td><td align="left">192.168.1.1/24</td></tr>
    <tr><td align="left"><b>P1</b> enp1s0f1np1</td><td align="left">192.168.2.1/24</td></tr>
    <tr><td align="left"><b>P1</b> enP2p1s0f1np1</td><td align="left">192.168.3.1/24</td></tr>
    <tr><td align="left" bgcolor="#F5F8FB">RJ45 10 GbE</td><td align="left" bgcolor="#F5F8FB">10.20.0.11/24</td></tr>
   </table>>];

  N2 [label=<
   <table border="0" cellborder="1" cellspacing="0" cellpadding="5" bgcolor="#FDF7EC" color="#0E1A2B">
    <tr><td colspan="2" bgcolor="#E0A030"><b>DURIN &#183; dgx-spark-2</b><br/><font point-size="8">RING NODE 2 &#183; RANK 1</font></td></tr>
    <tr><td align="left"><b>P0</b> enp1s0f0np0</td><td align="left">192.168.4.1/24</td></tr>
    <tr><td align="left"><b>P0</b> enP2p1s0f0np0</td><td align="left">192.168.5.1/24</td></tr>
    <tr><td align="left"><b>P1</b> enp1s0f1np1</td><td align="left">192.168.0.2/24</td></tr>
    <tr><td align="left"><b>P1</b> enP2p1s0f1np1</td><td align="left">192.168.1.2/24</td></tr>
    <tr><td align="left" bgcolor="#F5F8FB">RJ45 10 GbE</td><td align="left" bgcolor="#F5F8FB">10.20.0.12/24</td></tr>
   </table>>];

  N3 [label=<
   <table border="0" cellborder="1" cellspacing="0" cellpadding="5" bgcolor="#FDF7EC" color="#0E1A2B">
    <tr><td colspan="2" bgcolor="#E0A030"><b>DAIN &#183; dgx-spark-3</b><br/><font point-size="8">RING NODE 3 &#183; RANK 2</font></td></tr>
    <tr><td align="left"><b>P0</b> enp1s0f0np0</td><td align="left">192.168.2.2/24</td></tr>
    <tr><td align="left"><b>P0</b> enP2p1s0f0np0</td><td align="left">192.168.3.2/24</td></tr>
    <tr><td align="left"><b>P1</b> enp1s0f1np1</td><td align="left">192.168.4.2/24</td></tr>
    <tr><td align="left"><b>P1</b> enP2p1s0f1np1</td><td align="left">192.168.5.2/24</td></tr>
    <tr><td align="left" bgcolor="#F5F8FB">RJ45 10 GbE</td><td align="left" bgcolor="#F5F8FB">10.20.0.13/24</td></tr>
   </table>>];

  N1 -> N2 [label="  DAC-1\n  N1:P0  ->  N2:P1  ", dir=both];
  N2 -> N3 [label="  DAC-2\n  N2:P0  ->  N3:P1  ", dir=both];
  N3 -> N1 [label="  DAC-3\n  N3:P0  ->  N1:P1  ", dir=both, constraint=false];
}
EOF

# ----------------------------------------------------------- model factory ---
cat > dot/04_model_factory.dot <<'EOF'
digraph factory {
  rankdir=TB;
  bgcolor="white";
  fontname="DejaVu Sans";
  labelloc="t"; labeljust="l";
  label=<<b>DRAUPNIR &#183; CIM-56 MODEL FACTORY PIPELINE</b><br align="left"/><font point-size="11" color="#1F3350">Veldris Ltd &#183; one shared substrate, fifty-six jurisdiction adapters, hot-swappable or merged at release</font><br align="left"/> >;
  fontsize=22; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=box, style="filled,rounded", penwidth=1.3, color="#0E1A2B", fontsize=9.5];
  edge [fontname="DejaVu Sans", fontsize=8, color="#3E5C82", penwidth=1.4];
  nodesep=0.38; ranksep=0.52;

  subgraph cluster_t0 {
    label=<<b>T0 &#183; INGEST &#38; PROVENANCE</b>  (HODD on ANDVARI)>; fontsize=10.5; fontcolor="#1F3350";
    style=rounded; color="#7E8FA3"; bgcolor="#F5F8FB";
    A1 [label="National corpora acquisition\nlegislation, gazettes, hansard,\nstatistics, standards, open data", fillcolor="#E9F0F7"];
    A2 [label="Licence & provenance register\nper-source SPDX + terms +\nSHA-256 + retrieval date", fillcolor="#E9F0F7"];
    A3 [label="Curation\nnemo-curator / datatrove:\ndedup, PII strip, quality filter,\nlanguage ID, toxicity screen", fillcolor="#E9F0F7"];
    A4 [label="DPIA gate\nUK GDPR Art.35 where\npersonal data is present", fillcolor="#F6E3E0"];
    A1 -> A2 -> A3 -> A4;
  }

  subgraph cluster_t1 {
    label=<<b>T1 &#183; SHARED SUBSTRATE</b>  (3-node ring, FSDP2 / ZeRO-3)>; fontsize=10.5; fontcolor="#E0A030";
    style=rounded; color="#E0A030"; bgcolor="#FDF7EC";
    B1 [label="Base selection & licence gate\nQwen3 (Apache-2.0)\nGLM-4.5/4.6 (MIT)", fillcolor="#FBEFD6"];
    B2 [label="MIDGARD-CORE\ncontinued pre-train / SFT\nCommonwealth-wide corpus:\ncharter, common-law structure,\nshared institutions, register", fillcolor="#E0A030"];
    B3 [label="Alignment pass\nDPO / KTO on curated\npreference set", fillcolor="#FBEFD6"];
    B1 -> B2 -> B3;
  }

  subgraph cluster_t2 {
    label=<<b>T2 &#183; ADAPTER FARM</b>  (3 x independent single-node jobs, Slurm queue)>; fontsize=10.5; fontcolor="#177E89";
    style=rounded; color="#177E89"; bgcolor="#F2FAFB";
    C1 [label="LoRA / QLoRA r=32-64\nISO3 = ABW ... ZMB\n56 jurisdiction adapters", fillcolor="#177E89", fontcolor="white"];
    C2 [label="Per-jurisdiction eval gate\nlegal citation accuracy,\nlanguage coverage, refusal", fillcolor="#DCF0F2"];
    C1 -> C2;
    C2 -> C1 [label=" fail: requeue ", style=dashed, color="#B3402F", fontcolor="#B3402F"];
  }

  subgraph cluster_t3 {
    label=<<b>T3 &#183; REWEIGHT &#38; MERGE</b>>; fontsize=10.5; fontcolor="#6C5B9E";
    style=rounded; color="#6C5B9E"; bgcolor="#F4F1FA";
    D1 [label="mergekit\nTIES / DARE-TIES /\ntask arithmetic / SLERP", fillcolor="#6C5B9E", fontcolor="white"];
    D2 [label="Route A: merged dense weights\none artefact per jurisdiction", fillcolor="#E8E2F5"];
    D3 [label="Route B: hot-swap adapters\nvLLM multi-LoRA, one base\nin memory, 56 adapters on disk", fillcolor="#E8E2F5"];
    D1 -> D2; D1 -> D3;
  }

  subgraph cluster_t4 {
    label=<<b>T4 &#183; QUANTISE, PACKAGE, RELEASE</b>>; fontsize=10.5; fontcolor="#1F3350";
    style=rounded; color="#1F3350"; bgcolor="#EEF2F7";
    E1 [label="NVFP4 (TensorRT Model Optimizer)\nGGUF Q4_K_M / Q5_K_M\nMLX 4-bit for Apple estate", fillcolor="#D7E0EA"];
    E2 [label="Model card + SBOM (CycloneDX)\nSHA-256 manifest\ndata lineage attestation", fillcolor="#D7E0EA"];
    E3 [label="MIDGARD-CIM-<ISO3>-<base>-vX.Y\npushed to MinIO registry\n-> Midgard Suite / Mimir", fillcolor="#0E1A2B", fontcolor="white"];
    E1 -> E2 -> E3;
  }

  A4 -> B1 [lhead=cluster_t1, label=" curated corpora "];
  B3 -> C1 [lhead=cluster_t2, label=" frozen substrate "];
  C2 -> D1 [lhead=cluster_t3, label=" 56 validated adapters "];
  D2 -> E1; D3 -> E1;

  MLF [label="MLflow + MinIO + DVC\nevery run, dataset hash,\nhyper-parameter set and\nartefact tracked end to end", fillcolor="#2E8B57", fontcolor="white", shape=box, style="filled,rounded"];
  MLF -> A3 [style=dotted, arrowhead=none, color="#2E8B57"];
  MLF -> B2 [style=dotted, arrowhead=none, color="#2E8B57"];
  MLF -> C1 [style=dotted, arrowhead=none, color="#2E8B57"];
  MLF -> E2 [style=dotted, arrowhead=none, color="#2E8B57"];
}
EOF

for f in 02_topology 03_cx7_ring 04_model_factory; do
  dot -Tpng -Gdpi=170 "dot/$f.dot" -o "diagrams/$f.png"
  echo "wrote diagrams/$f.png"
done
