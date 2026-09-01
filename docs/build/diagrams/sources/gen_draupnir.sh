#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p diagrams dot

# ---------------------------------------------- C4 L1 : system context ----
cat > dot/07_draupnir_c4_context.dot <<'EOF'
digraph ctx {
  rankdir=TB; bgcolor="white"; fontname="DejaVu Sans";
  labelloc="t"; labeljust="l";
  label=<<b>DRAUPNIR &#183; C4 LEVEL 1 &#183; SYSTEM CONTEXT</b><br align="left"/><font point-size="11" color="#1F3350">Veldris Ltd &#183; SPECIFIED architecture, not observed &#183; CONFIDENTIAL — RECIPIENT EYES ONLY</font><br align="left"/> >;
  fontsize=21; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=box, style="filled,rounded", penwidth=1.3, color="#0E1A2B", fontsize=10];
  edge [fontname="DejaVu Sans", fontsize=8.5, color="#3E5C82", penwidth=1.3];
  nodesep=0.5; ranksep=0.7;

  subgraph cluster_people {
    label=<<b>ACTORS</b>>; fontsize=10; fontcolor="#1F3350"; style="rounded,dashed"; color="#7E8FA3";
    OPS  [label="Forge Operator\nsubmits and monitors runs", fillcolor="#E9F0F7"];
    CUR  [label="Corpus Curator\ningests sources, sets licences", fillcolor="#E9F0F7"];
    APR  [label="Release Approver\nsigns off GLEIPNIR gates", fillcolor="#FBEFD6"];
    AUD  [label="Auditor / Counsel\nread-only ledger and lineage", fillcolor="#E9F0F7"];
  }

  DRA [label="DRAUPNIR\nCIM-56 model factory control plane\nOne application governing corpus,\ntraining, reweighting, evaluation,\nquantisation and release",
       fillcolor="#0E1A2B", fontcolor="white", penwidth=2.4, width=3.6];

  subgraph cluster_int {
    label=<<b>SINDRI FORGE (internal, trusted)</b>>; fontsize=10; fontcolor="#E0A030"; style=rounded; color="#E0A030"; bgcolor="#FDF7EC";
    SLU [label="Slurm on REGIN\njob scheduler", fillcolor="#FBEFD6"];
    NOD [label="DVALIN / DURIN / DAIN\nGB10 training appliances", fillcolor="#E0A030"];
    VLT [label="HODD vault on ANDVARI\nNFS + MinIO object store", fillcolor="#FBEFD6"];
    MLF [label="MLflow + PostgreSQL\nrun tracking", fillcolor="#FBEFD6"];
    OBS [label="Prometheus / Grafana / Loki\ntelemetry", fillcolor="#FBEFD6"];
    MLX [label="ALVISS\nMLX cross-platform eval", fillcolor="#FBEFD6"];
  }

  subgraph cluster_ext {
    label=<<b>EXTERNAL (egress allow-list only)</b>>; fontsize=10; fontcolor="#B3402F"; style="rounded,dashed"; color="#B3402F"; bgcolor="#FBF2F0";
    HF  [label="Hugging Face Hub\nbase weight acquisition\n(one-shot, hashed)", fillcolor="#F6E3E0"];
    PKG [label="PyPI / GitHub / NGC\ndependency and image pull", fillcolor="#F6E3E0"];
    TCH [label="Teacher model API\n(GLM-5.x, optional Phase 2)\nsynthetic data only", fillcolor="#F6E3E0"];
    SRC [label="National corpus sources\nlegislation, gazettes,\nstatistics, standards", fillcolor="#F6E3E0"];
  }

  OPS -> DRA [label=" submit, monitor, cancel "];
  CUR -> DRA [label=" register corpus + licence "];
  APR -> DRA [label=" approve or reject gate "];
  DRA -> AUD [label=" append-only ledger,\n lineage, model cards "];

  DRA -> SLU [label=" MOTSOGNIR dispatch "];
  SLU -> NOD;
  DRA -> VLT [label=" HODD read / write "];
  DRA -> MLF [label=" run metadata "];
  OBS -> DRA [label=" metrics, logs "];
  DRA -> MLX [label=" RAUN cross-platform check "];

  DRA -> HF  [label=" pull, hash, archive licence ", style=dashed];
  DRA -> PKG [style=dashed];
  DRA -> TCH [label=" distillation only ", style=dashed];
  SRC -> CUR [style=dashed, label=" out-of-band acquisition "];
}
EOF

# ------------------------------------------------ C4 L2 : containers -----
cat > dot/08_draupnir_c4_container.dot <<'EOF'
digraph cont {
  rankdir=TB; bgcolor="white"; fontname="DejaVu Sans";
  labelloc="t"; labeljust="l";
  label=<<b>DRAUPNIR &#183; C4 LEVEL 2 &#183; CONTAINERS AND MODULES</b><br align="left"/><font point-size="11" color="#1F3350">Every module is a replaceable plug-in behind a stable interface. Module names follow the Veldris naming standard: dwarf-made works.</font><br align="left"/> >;
  fontsize=21; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=box, style="filled,rounded", penwidth=1.3, color="#0E1A2B", fontsize=9.5];
  edge [fontname="DejaVu Sans", fontsize=8, color="#3E5C82", penwidth=1.2];
  nodesep=0.35; ranksep=0.55;

  subgraph cluster_ui {
    label=<<b>PRESENTATION</b>>; fontsize=10; fontcolor="#1F3350"; style=rounded; color="#7E8FA3"; bgcolor="#F5F8FB";
    WEB [label="Web console (React + TS)\nrun board, gate queue,\nlineage explorer", fillcolor="#E9F0F7"];
    CLI [label="draupnirctl (Python CLI)\nscriptable, CI-friendly", fillcolor="#E9F0F7"];
    TUI [label="CON-A local console view\nread-only status, kiosk\non DVALIN", fillcolor="#E9F0F7"];
  }

  API [label="DRAUPNIR CORE\nFastAPI + async workers\nWorkflow state machine &#183; Run registry\nAppend-only audit ledger &#183; Event bus\nPlug-in loader (entry-point contracts)",
       fillcolor="#0E1A2B", fontcolor="white", penwidth=2.2, width=4.4];

  subgraph cluster_mod {
    label=<<b>PIPELINE MODULES (plug-in, versioned interfaces)</b>>; fontsize=10; fontcolor="#E0A030"; style=rounded; color="#E0A030"; bgcolor="#FDF7EC";
    HODD [label="HODD\nCorpus and artefact store\nimmutable ingest, SHA-256,\nlicence register, retention", fillcolor="#2E8B57", fontcolor="white"];
    GLEI [label="GLEIPNIR\nPolicy and assurance gates\nlicence, DPIA, approval,\nrelease sign-off", fillcolor="#B3402F", fontcolor="white"];
    MOTS [label="MOTSOGNIR\nJob dispatch and placement\nSlurm / Ray drivers,\narray concurrency, retries", fillcolor="#177E89", fontcolor="white"];
    HAMA [label="HAMARR\nTraining executors\nLLaMA-Factory, Axolotl,\nNeMo, TRL drivers", fillcolor="#E0A030"];
    BRIS [label="BRISINGAMEN\nReweight and merge\nmergekit driver, weight\nsweeps, route A / B", fillcolor="#6C5B9E", fontcolor="white"];
    RAUN [label="RAUN\nEvaluation and assurance\nE1-E6 gate suite, baselines,\nquantisation regression", fillcolor="#3E5C82", fontcolor="white"];
    SKID [label="SKIDBLADNIR\nQuantise, package, release\nNVFP4 / GGUF / MLX,\nSBOM, model card, lineage", fillcolor="#1F3350", fontcolor="white"];
  }

  SVAL [label="SVALINN  (cross-cutting)\nOIDC identity &#183; RBAC &#183; mTLS &#183; secrets broker &#183; artefact signing (Sigstore)\nsandboxed plug-in execution &#183; egress policy enforcement",
        fillcolor="#8A5A08", fontcolor="white", width=5.4];

  subgraph cluster_data {
    label=<<b>PERSISTENCE</b>>; fontsize=10; fontcolor="#1F3350"; style=rounded; color="#7E8FA3"; bgcolor="#F5F8FB";
    PG  [label="PostgreSQL\nrun state, ledger,\nregisters, RBAC", fillcolor="#D7E0EA"];
    S3  [label="MinIO (S3)\nartefacts, weights,\nadapters, reports", fillcolor="#D7E0EA"];
    NFS [label="HODD vault (NFS)\ncorpora, checkpoints", fillcolor="#D7E0EA"];
  }

  WEB -> API; CLI -> API; TUI -> API [style=dashed, label=" read-only "];
  API -> HODD; API -> GLEI; API -> MOTS; API -> HAMA; API -> BRIS; API -> RAUN; API -> SKID;
  SVAL -> API [style=dotted, arrowhead=none, color="#8A5A08", penwidth=2.0];
  API -> PG; HODD -> S3; HODD -> NFS;
  MOTS -> HAMA [style=dashed, label=" schedules "];
  HAMA -> RAUN [style=dashed, label=" artefact "];
  RAUN -> GLEI [style=dashed, label=" gate result "];
  BRIS -> RAUN [style=dashed];
  SKID -> GLEI [style=dashed, label=" release gate "];
}
EOF

# ------------------------------------------- pipeline state machine ------
cat > dot/09_draupnir_state.dot <<'EOF'
digraph sm {
  rankdir=LR; bgcolor="white"; fontname="DejaVu Sans";
  labelloc="t"; labeljust="l";
  label=<<b>DRAUPNIR &#183; MODEL LIFECYCLE STATE MACHINE</b><br align="left"/><font point-size="11" color="#1F3350">One state machine per artefact. Every transition is an append-only ledger entry carrying actor, timestamp, input hashes and gate result.</font><br align="left"/> >;
  fontsize=20; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=box, style="filled,rounded", penwidth=1.3, color="#0E1A2B", fontsize=9.5];
  edge [fontname="DejaVu Sans", fontsize=8, color="#3E5C82", penwidth=1.2];
  nodesep=0.3; ranksep=0.5;

  DRAFT   [label="DRAFT", fillcolor="#E9F0F7"];
  CORPUS  [label="CORPUS_REGISTERED\nHODD", fillcolor="#2E8B57", fontcolor="white"];
  LICOK   [label="LICENCE_CLEARED\nGLEIPNIR", fillcolor="#B3402F", fontcolor="white"];
  CURATED [label="CURATED\nHODD", fillcolor="#2E8B57", fontcolor="white"];
  QUEUED  [label="QUEUED\nMOTSOGNIR", fillcolor="#177E89", fontcolor="white"];
  TRAIN   [label="TRAINING\nHAMARR", fillcolor="#E0A030"];
  TRAINED [label="TRAINED", fillcolor="#FBEFD6"];
  EVAL    [label="EVALUATING\nRAUN", fillcolor="#3E5C82", fontcolor="white"];
  MERGED  [label="MERGED\nBRISINGAMEN", fillcolor="#6C5B9E", fontcolor="white"];
  QUANT   [label="QUANTISED\nSKIDBLADNIR", fillcolor="#1F3350", fontcolor="white"];
  APPROVE [label="AWAITING_APPROVAL\nGLEIPNIR", fillcolor="#B3402F", fontcolor="white"];
  RELEASE [label="RELEASED", fillcolor="#0E1A2B", fontcolor="white", penwidth=2.2];
  FAILED  [label="FAILED", fillcolor="#F6E3E0"];
  QUAR    [label="QUARANTINED\nno delete, ledger retained", fillcolor="#F6E3E0"];

  DRAFT -> CORPUS -> LICOK -> CURATED -> QUEUED -> TRAIN -> TRAINED -> EVAL;
  EVAL -> MERGED [label=" E1-E6 pass "];
  EVAL -> QUEUED [label=" gate fail: requeue ", style=dashed, color="#B3402F", fontcolor="#B3402F"];
  MERGED -> EVAL [label=" re-gate merged ", style=dashed];
  MERGED -> QUANT;
  QUANT -> EVAL [label=" re-gate quantised ", style=dashed];
  QUANT -> APPROVE;
  APPROVE -> RELEASE [label=" human sign-off "];
  APPROVE -> QUAR [label=" rejected ", style=dashed, color="#B3402F", fontcolor="#B3402F"];
  LICOK -> QUAR [label=" licence fail ", style=dashed, color="#B3402F", fontcolor="#B3402F"];
  TRAIN -> FAILED [style=dashed, color="#B3402F"];
  FAILED -> QUEUED [label=" retry ", style=dashed];
}
EOF

# ------------------------------------------------ threat model DFD -------
cat > dot/10_draupnir_threat.dot <<'EOF'
digraph tm {
  rankdir=TB; bgcolor="white"; fontname="DejaVu Sans";
  labelloc="t"; labeljust="l";
  label=<<b>DRAUPNIR &#183; THREAT MODEL DATAFLOW (STRIDE-aligned)</b><br align="left"/><font point-size="11" color="#1F3350">Dashed red boundaries are trust boundaries. T-numbers reference the threat register in the SAD security section.</font><br align="left"/> >;
  fontsize=20; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=box, style="filled,rounded", penwidth=1.3, color="#0E1A2B", fontsize=9.5];
  edge [fontname="DejaVu Sans", fontsize=8, color="#3E5C82", penwidth=1.2];
  nodesep=0.35; ranksep=0.6;

  subgraph cluster_tb1 {
    label=<<b>TB1 &#183; INTERNET</b>  <font point-size="8">untrusted</font>>; fontsize=10; fontcolor="#B3402F";
    style="rounded,dashed"; color="#B3402F"; bgcolor="#FBF2F0";
    HF  [label="Hugging Face Hub", fillcolor="#F6E3E0"];
    SRC [label="Corpus source sites", fillcolor="#F6E3E0"];
    TCH [label="Teacher model API", fillcolor="#F6E3E0"];
  }

  subgraph cluster_tb2 {
    label=<<b>TB2 &#183; OPERATOR EDGE</b>  <font point-size="8">authenticated humans</font>>; fontsize=10; fontcolor="#E0A030";
    style="rounded,dashed"; color="#E0A030"; bgcolor="#FDF7EC";
    OPS [label="Operator via WireGuard\nor CON-A local console", fillcolor="#FBEFD6"];
    APR [label="Release approver", fillcolor="#FBEFD6"];
  }

  subgraph cluster_tb3 {
    label=<<b>TB3 &#183; DRAUPNIR CONTROL PLANE</b>  <font point-size="8">trusted, audited</font>>; fontsize=10; fontcolor="#1F3350";
    style="rounded,dashed"; color="#1F3350"; bgcolor="#F5F8FB";
    API [label="DRAUPNIR Core API\nOIDC + RBAC + mTLS", fillcolor="#0E1A2B", fontcolor="white"];
    LED [label="Append-only audit ledger\nhash-chained", fillcolor="#2E8B57", fontcolor="white"];
    SEC [label="SVALINN secrets broker\nshort-lived tokens", fillcolor="#8A5A08", fontcolor="white"];
  }

  subgraph cluster_tb4 {
    label=<<b>TB4 &#183; EXECUTION PLANE</b>  <font point-size="8">semi-trusted, sandboxed</font>>; fontsize=10; fontcolor="#177E89";
    style="rounded,dashed"; color="#177E89"; bgcolor="#F2FAFB";
    JOB [label="HAMARR training job\nrootless container,\nno outbound network", fillcolor="#E0A030"];
    PLG [label="Third-party plug-in\nsigned, capability-scoped", fillcolor="#DCF0F2"];
  }

  subgraph cluster_tb5 {
    label=<<b>TB5 &#183; DATA AT REST</b>  <font point-size="8">encrypted</font>>; fontsize=10; fontcolor="#6C5B9E";
    style="rounded,dashed"; color="#6C5B9E"; bgcolor="#F4F1FA";
    HODD [label="HODD vault\nAPFS encrypted, SED", fillcolor="#E8E2F5"];
    PG   [label="PostgreSQL\nrun state and registers", fillcolor="#E8E2F5"];
  }

  HF  -> API [label=" T1 supply-chain:\n poisoned weights ", color="#B3402F", fontcolor="#B3402F"];
  SRC -> API [label=" T2 corpus poisoning,\n unlicensed material ", color="#B3402F", fontcolor="#B3402F"];
  TCH -> API [label=" T3 exfiltration via\n prompt content ", color="#B3402F", fontcolor="#B3402F", style=dashed];
  OPS -> API [label=" T4 spoofed identity,\n privilege escalation "];
  APR -> API [label=" T5 approval bypass,\n repudiation "];
  API -> LED [label=" every transition "];
  API -> SEC;
  SEC -> JOB [label=" T6 credential leak\n into checkpoint ", color="#B3402F", fontcolor="#B3402F"];
  API -> JOB [label=" dispatch "];
  JOB -> PLG [label=" T7 malicious plug-in,\n arbitrary code ", color="#B3402F", fontcolor="#B3402F"];
  JOB -> HODD [label=" checkpoint write "];
  API -> PG;
  HODD -> API [label=" T8 tampered artefact\n between gate and release ", color="#B3402F", fontcolor="#B3402F", style=dashed];
}
EOF

for f in 07_draupnir_c4_context 08_draupnir_c4_container 09_draupnir_state 10_draupnir_threat; do
  dot -Tpng -Gdpi=165 "dot/$f.dot" -o "diagrams/$f.png"
  echo "wrote diagrams/$f.png"
done
