#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

cat > dot/12_er_model.dot <<'EOF'
digraph er {
  rankdir=LR; bgcolor="white"; fontname="DejaVu Sans"; labelloc="t"; labeljust="l";
  label=<<b>DRAUPNIR &#183; DATA MODEL</b><br align="left"/><font point-size="10" color="#1F3350">PK in bold. site_id present on every scoped entity from the first migration. CONFIDENTIAL — RECIPIENT EYES ONLY</font><br align="left"/> >;
  fontsize=19; fontcolor="#0E1A2B";
  node [shape=plaintext, fontname="DejaVu Sans", fontsize=8.5];
  edge [fontname="DejaVu Sans", fontsize=7.5, color="#3E5C82"];
  nodesep=0.4; ranksep=1.0;

  site [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#FDF7EC">
    <tr><td colspan="2" bgcolor="#E0A030"><b>site</b></td></tr>
    <tr><td align="left"><b>id</b></td><td align="left">uuid</td></tr>
    <tr><td align="left">name, location</td><td align="left">text</td></tr>
    <tr><td align="left">control_plane_uri</td><td align="left">text</td></tr>
    <tr><td align="left">anchor_state</td><td align="left">enum</td></tr>
    <tr><td align="left">last_anchored_at</td><td align="left">tstz</td></tr></table>>];

  ledger [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#EAF4EE">
    <tr><td colspan="2" bgcolor="#2E8B57"><font color="white"><b>ledger_entry</b></font></td></tr>
    <tr><td align="left"><b>id</b></td><td align="left">uuid</td></tr>
    <tr><td align="left">site_id, seq</td><td align="left">fk, bigint</td></tr>
    <tr><td align="left">prev_hash, entry_hash</td><td align="left">bytea</td></tr>
    <tr><td align="left">ts, actor</td><td align="left">tstz, text</td></tr>
    <tr><td align="left">subject_type, subject_id</td><td align="left">enum, uuid</td></tr>
    <tr><td align="left">transition, payload</td><td align="left">text, jsonb</td></tr></table>>];

  source [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#F5F8FB">
    <tr><td colspan="2" bgcolor="#3E5C82"><font color="white"><b>source</b></font></td></tr>
    <tr><td align="left"><b>id</b></td><td align="left">uuid</td></tr>
    <tr><td align="left">jurisdiction, url</td><td align="left">char(3), text</td></tr>
    <tr><td align="left">licence_spdx</td><td align="left">text</td></tr>
    <tr><td align="left">attribution_required</td><td align="left">bool</td></tr>
    <tr><td align="left">sha256, retrieved_at</td><td align="left">bytea, tstz</td></tr>
    <tr><td align="left">personal_data, dpia_ref</td><td align="left">bool, text</td></tr>
    <tr><td align="left">residency_constraint</td><td align="left">text[]</td></tr></table>>];

  artefact [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#F4F1FA">
    <tr><td colspan="2" bgcolor="#6C5B9E"><font color="white"><b>artefact</b></font></td></tr>
    <tr><td align="left"><b>id</b></td><td align="left">uuid</td></tr>
    <tr><td align="left">site_id, locality</td><td align="left">fk, uuid[]</td></tr>
    <tr><td align="left">kind, uri</td><td align="left">enum, text</td></tr>
    <tr><td align="left">sha256_manifest, size</td><td align="left">bytea, bigint</td></tr>
    <tr><td align="left">created_from_run</td><td align="left">fk</td></tr>
    <tr><td align="left">immutable_at</td><td align="left">tstz</td></tr></table>>];

  run [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#FDF7EC">
    <tr><td colspan="2" bgcolor="#E0A030"><b>run</b></td></tr>
    <tr><td align="left"><b>id</b></td><td align="left">uuid</td></tr>
    <tr><td align="left">site_id, spec_hash</td><td align="left">fk, bytea</td></tr>
    <tr><td align="left">kind, state</td><td align="left">enum, enum</td></tr>
    <tr><td align="left">scheduler_job_id, node</td><td align="left">text, text</td></tr>
    <tr><td align="left">retry_count</td><td align="left">int</td></tr>
    <tr><td align="left">started_at, ended_at</td><td align="left">tstz</td></tr></table>>];

  gate [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#F5F8FB">
    <tr><td colspan="2" bgcolor="#3E5C82"><font color="white"><b>gate_result</b></font></td></tr>
    <tr><td align="left"><b>id</b></td><td align="left">uuid</td></tr>
    <tr><td align="left">run_id, artefact_sha256</td><td align="left">fk, bytea</td></tr>
    <tr><td align="left">gate, suite_version</td><td align="left">enum, text</td></tr>
    <tr><td align="left">value, baseline_value</td><td align="left">numeric</td></tr>
    <tr><td align="left">margin, passed</td><td align="left">numeric, bool</td></tr></table>>];

  approval [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#FBF2F0">
    <tr><td colspan="2" bgcolor="#B3402F"><font color="white"><b>approval</b></font></td></tr>
    <tr><td align="left"><b>id</b></td><td align="left">uuid</td></tr>
    <tr><td align="left">subject_id, approver</td><td align="left">uuid, text</td></tr>
    <tr><td align="left">decision, reason</td><td align="left">enum, text</td></tr>
    <tr><td align="left">sole_approver_exception</td><td align="left">bool</td></tr>
    <tr><td align="left">signature, decided_at</td><td align="left">bytea, tstz</td></tr></table>>];

  release [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#EEF2F7">
    <tr><td colspan="2" bgcolor="#0E1A2B"><font color="white"><b>release</b></font></td></tr>
    <tr><td align="left"><b>id</b></td><td align="left">uuid</td></tr>
    <tr><td align="left">artefact_id, approval_id</td><td align="left">fk, fk</td></tr>
    <tr><td align="left">model_card_uri, sbom_uri</td><td align="left">text</td></tr>
    <tr><td align="left">lineage_uri</td><td align="left">text</td></tr>
    <tr><td align="left">training_summary_uri</td><td align="left">text</td></tr>
    <tr><td align="left">copyright_policy_uri</td><td align="left">text</td></tr>
    <tr><td align="left">signature, anchored_at</td><td align="left">bytea, tstz</td></tr></table>>];

  plugin [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#F2FAFB">
    <tr><td colspan="2" bgcolor="#177E89"><font color="white"><b>plugin</b></font></td></tr>
    <tr><td align="left"><b>name, version</b></td><td align="left">text</td></tr>
    <tr><td align="left">interface, capabilities</td><td align="left">text, text[]</td></tr>
    <tr><td align="left">signature_verified</td><td align="left">bool</td></tr></table>>];

  retention [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4" bgcolor="#FBF2F0">
    <tr><td colspan="2" bgcolor="#B3402F"><font color="white"><b>retention_action</b></font></td></tr>
    <tr><td align="left"><b>id</b></td><td align="left">uuid</td></tr>
    <tr><td align="left">subject_id, policy</td><td align="left">uuid, text</td></tr>
    <tr><td align="left">due_at, approved_by</td><td align="left">tstz, text</td></tr>
    <tr><td align="left">manifests_retained</td><td align="left">bool</td></tr></table>>];

  site -> ledger   [label=" 1 : N "];
  site -> run      [label=" 1 : N "];
  site -> artefact [label=" 1 : N "];
  source -> artefact [label=" N : M\n corpus lineage "];
  run -> artefact  [label=" 1 : N produces "];
  artefact -> run  [label=" N : M consumes ", style=dashed];
  run -> gate      [label=" 1 : N "];
  artefact -> approval [label=" 1 : N "];
  approval -> release  [label=" 1 : 1 required "];
  artefact -> release  [label=" 1 : 1 "];
  run -> plugin    [label=" driver version pinned ", style=dashed];
  artefact -> retention [label=" 1 : N "];
}
EOF

cat > dot/13_c4_l3_core.dot <<'EOF'
digraph l3 {
  rankdir=TB; bgcolor="white"; fontname="DejaVu Sans"; labelloc="t"; labeljust="l";
  label=<<b>DRAUPNIR CORE &#183; C4 LEVEL 3 &#183; COMPONENTS</b><br align="left"/><font point-size="10" color="#1F3350">Internal decomposition of the control plane container. Arrows are call direction.</font><br align="left"/> >;
  fontsize=19; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=box, style="filled,rounded", penwidth=1.2, color="#0E1A2B", fontsize=9];
  edge [fontname="DejaVu Sans", fontsize=7.5, color="#3E5C82"];
  nodesep=0.3; ranksep=0.45;

  subgraph cluster_edge {
    label=<<b>EDGE</b>>; fontsize=9.5; fontcolor="#1F3350"; style=rounded; color="#7E8FA3"; bgcolor="#F5F8FB";
    RT  [label="Router\nOpenAPI 3.1 route table\nrole declaration required\nat registration", fillcolor="#E9F0F7"];
    AUTHN [label="Authn middleware\nOIDC token validation", fillcolor="#E9F0F7"];
    AUTHZ [label="Authz guard\nfail-closed decorator", fillcolor="#E9F0F7"];
    VAL [label="Request validation\nPydantic + JSON Schema", fillcolor="#E9F0F7"];
    SSE [label="Event stream\nserver-sent events\nfor the run board", fillcolor="#E9F0F7"];
    ERR [label="Error mapper\nRFC 9457 problem+json", fillcolor="#E9F0F7"];
  }

  subgraph cluster_app {
    label=<<b>APPLICATION</b>>; fontsize=9.5; fontcolor="#E0A030"; style=rounded; color="#E0A030"; bgcolor="#FDF7EC";
    SPEC [label="Spec compiler\nvalidate, hash, resolve\nartefact references", fillcolor="#FBEFD6"];
    SM  [label="State machine\ndeclarative transitions\nwith guards", fillcolor="#E0A030"];
    ORCH [label="Transition orchestrator\nguard, act, ledger, project\nsingle transaction", fillcolor="#E0A030"];
    BUS [label="Event bus\nin-process pub/sub\nfeeds SSE and workers", fillcolor="#FBEFD6"];
    WRK [label="Worker pool\npolls actionable\ntransitions, backoff", fillcolor="#FBEFD6"];
  }

  subgraph cluster_dom {
    label=<<b>DOMAIN</b>>; fontsize=9.5; fontcolor="#2E8B57"; style=rounded; color="#2E8B57"; bgcolor="#EAF4EE";
    LED [label="Ledger writer\nhash chain, append-only", fillcolor="#2E8B57", fontcolor="white"];
    PROJ [label="Projector\nledger to relational\nrebuildable from zero", fillcolor="#EAF4EE"];
    SITE [label="Site resolver\nscope enforcement,\nunscoped query raises", fillcolor="#EAF4EE"];
    REG [label="Run registry\nidentity = H(spec + inputs)\nduplicate detection", fillcolor="#EAF4EE"];
  }

  subgraph cluster_port {
    label=<<b>PORTS (plug-in loader)</b>>; fontsize=9.5; fontcolor="#177E89"; style=rounded; color="#177E89"; bgcolor="#F2FAFB";
    LOAD [label="Entry-point loader\nversion negotiation,\ncapability check,\nsignature verification", fillcolor="#177E89", fontcolor="white"];
    CONF [label="Conformance harness\npurity and determinism\nassertions", fillcolor="#DCF0F2"];
  }

  subgraph cluster_inf {
    label=<<b>INFRASTRUCTURE</b>>; fontsize=9.5; fontcolor="#1F3350"; style=rounded; color="#7E8FA3"; bgcolor="#F5F8FB";
    REPO [label="Repositories\nSQLAlchemy, unit of work", fillcolor="#D7E0EA"];
    OBJ [label="Object store client\nMinIO / S3", fillcolor="#D7E0EA"];
    TEL [label="Telemetry\nOpenTelemetry traces,\nProm metrics, structlog", fillcolor="#D7E0EA"];
  }

  RT -> AUTHN -> AUTHZ -> VAL -> SPEC;
  RT -> ERR [style=dashed];
  SPEC -> REG -> ORCH;
  ORCH -> SM; ORCH -> LED -> PROJ -> REPO;
  ORCH -> BUS -> SSE;
  BUS -> WRK -> LOAD;
  LOAD -> CONF [style=dashed];
  ORCH -> SITE [style=dashed, label=" scope "];
  PROJ -> OBJ; ORCH -> TEL [style=dotted];
}
EOF

cat > dot/14_ui_ia.dot <<'EOF'
digraph ia {
  rankdir=LR; bgcolor="white"; fontname="DejaVu Sans"; labelloc="t"; labeljust="l";
  label=<<b>DRAUPNIR CONSOLE &#183; INFORMATION ARCHITECTURE AND USER JOURNEYS</b><br align="left"/><font point-size="10" color="#1F3350">Built on JARNGREIPR, the Veldris design system. Coloured paths are the four primary journeys.</font><br align="left"/> >;
  fontsize=19; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=box, style="filled,rounded", penwidth=1.2, color="#0E1A2B", fontsize=9];
  edge [fontname="DejaVu Sans", fontsize=7.5, color="#7E8FA3"];
  nodesep=0.25; ranksep=0.7;

  ROOT [label="Console shell\nsite switcher &#183; global search\ncommand palette &#183; alerts", fillcolor="#0E1A2B", fontcolor="white"];

  subgraph cluster_nav {
    label=<<b>PRIMARY NAVIGATION</b>>; fontsize=9.5; fontcolor="#1F3350"; style=rounded; color="#7E8FA3"; bgcolor="#F5F8FB";
    OVR [label="Overview\ncapacity, queue depth,\nthermal, anchor freshness", fillcolor="#E9F0F7"];
    COR [label="Corpora\nsources, licences,\ncuration status", fillcolor="#E9F0F7"];
    RUN [label="Runs\nboard, detail, logs,\nlive progress", fillcolor="#E9F0F7"];
    MOD [label="Models\nregistry, lineage,\nsweep comparison", fillcolor="#E9F0F7"];
    GAT [label="Gates\napproval queue\nwith evidence", fillcolor="#FBEFD6"];
    ADM [label="Admin\nplug-ins, policy,\nsites, users", fillcolor="#E9F0F7"];
    AUD [label="Audit\nledger explorer,\nchain verification", fillcolor="#EAF4EE"];
  }

  ROOT -> OVR; ROOT -> COR; ROOT -> RUN; ROOT -> MOD; ROOT -> GAT; ROOT -> ADM; ROOT -> AUD;

  J1 [label="J1 CURATOR\nregister source &#8594; declare licence\n&#8594; ingest &#8594; curate &#8594; review retention", fillcolor="#DCF0F2", shape=note];
  J2 [label="J2 OPERATOR\ncompose spec &#8594; dry run &#8594; submit\n&#8594; watch board &#8594; diagnose failure &#8594; retry", fillcolor="#FBEFD6", shape=note];
  J3 [label="J3 APPROVER\nopen gate queue &#8594; read evidence\n&#8594; see sole-approver notice &#8594; sign &#8594; publish", fillcolor="#FBF2F0", shape=note];
  J4 [label="J4 AUDITOR\npick release &#8594; walk lineage to base\nlicence and corpus hash &#8594; verify chain &#8594; export", fillcolor="#EAF4EE", shape=note];

  COR -> J1 [color="#177E89", penwidth=2];
  RUN -> J2 [color="#E0A030", penwidth=2];
  GAT -> J3 [color="#B3402F", penwidth=2];
  AUD -> J4 [color="#2E8B57", penwidth=2];

  DS [label="JARNGREIPR design system\n\n&#183; tokens: colour, type, space, motion\n&#183; primitives: button, field, table, badge\n&#183; composites: run card, gate card,\n  lineage tree, sweep matrix, log viewer\n&#183; states: loading, empty, error, denied,\n  partitioned, read-only\n&#183; WCAG 2.2 AA, keyboard-complete,\n  dark and light, 200 per cent zoom",
      fillcolor="#F4F1FA", color="#6C5B9E", shape=note, fontsize=8.5];
  ROOT -> DS [style=dotted, color="#6C5B9E", penwidth=2, arrowhead=none];
}
EOF

cat > dot/15_cicd.dot <<'EOF'
digraph ci {
  rankdir=LR; bgcolor="white"; fontname="DejaVu Sans"; labelloc="t"; labeljust="l";
  label=<<b>DRAUPNIR &#183; CONTINUOUS INTEGRATION AND DELIVERY</b><br align="left"/><font point-size="10" color="#1F3350">Self-hosted runner on ALVISS. No stage may be skipped on the main branch.</font><br align="left"/> >;
  fontsize=19; fontcolor="#0E1A2B";
  node [fontname="DejaVu Sans", shape=box, style="filled,rounded", penwidth=1.2, color="#0E1A2B", fontsize=8.5];
  edge [fontname="DejaVu Sans", fontsize=7.5, color="#3E5C82"];
  nodesep=0.25; ranksep=0.45;

  PR [label="Pull request", fillcolor="#E9F0F7"];

  subgraph cluster_static {
    label=<<b>1 STATIC</b>>; fontsize=9; fontcolor="#1F3350"; style=rounded; color="#7E8FA3"; bgcolor="#F5F8FB";
    L1 [label="ruff format + lint", fillcolor="#E9F0F7"];
    L2 [label="mypy --strict", fillcolor="#E9F0F7"];
    L3 [label="eslint + tsc --noEmit", fillcolor="#E9F0F7"];
    L4 [label="secret scan\n(gitleaks)", fillcolor="#FBF2F0"];
    L5 [label="dependency audit\n+ SBOM diff", fillcolor="#FBF2F0"];
  }

  subgraph cluster_test {
    label=<<b>2 TEST</b>>; fontsize=9; fontcolor="#E0A030"; style=rounded; color="#E0A030"; bgcolor="#FDF7EC";
    T1 [label="unit\npytest, 90% core", fillcolor="#FBEFD6"];
    T2 [label="property\nhypothesis on ledger\nand spec hashing", fillcolor="#FBEFD6"];
    T3 [label="contract\ndriver conformance\nharness", fillcolor="#FBEFD6"];
    T4 [label="integration\nephemeral PG + MinIO", fillcolor="#FBEFD6"];
    T5 [label="API contract\nOpenAPI diff, breaking\nchange gate", fillcolor="#FBEFD6"];
    T6 [label="frontend\nvitest + testing-library", fillcolor="#FBEFD6"];
    T7 [label="e2e\nPlaywright, 4 journeys", fillcolor="#FBEFD6"];
    T8 [label="a11y\naxe on every route", fillcolor="#FBEFD6"];
    T9 [label="visual regression\nstorybook snapshots", fillcolor="#FBEFD6"];
  }

  subgraph cluster_build {
    label=<<b>3 BUILD</b>>; fontsize=9; fontcolor="#177E89"; style=rounded; color="#177E89"; bgcolor="#F2FAFB";
    B1 [label="aarch64 images\nrootless, distroless base", fillcolor="#DCF0F2"];
    B2 [label="client regeneration\nCLI + TS from OpenAPI\nfails if drifted", fillcolor="#DCF0F2"];
    B3 [label="CycloneDX SBOM", fillcolor="#DCF0F2"];
    B4 [label="sign artefacts\ninternal PKI", fillcolor="#DCF0F2"];
  }

  subgraph cluster_dep {
    label=<<b>4 DEPLOY</b>>; fontsize=9; fontcolor="#1F3350"; style=rounded; color="#1F3350"; bgcolor="#EEF2F7";
    D1 [label="migrate\nforward-only, dry-run first", fillcolor="#D7E0EA"];
    D2 [label="deploy to ALVISS", fillcolor="#D7E0EA"];
    D3 [label="smoke: healthz, readyz,\nledger chain verify", fillcolor="#D7E0EA"];
    D4 [label="rollback on failure", fillcolor="#FBF2F0"];
  }

  PR -> L1 -> L2 -> L3 -> L4 -> L5 -> T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9 -> B1 -> B2 -> B3 -> B4 -> D1 -> D2 -> D3;
  D3 -> D4 [label=" fail ", style=dashed, color="#B3402F", fontcolor="#B3402F"];
}
EOF

for f in 12_er_model 13_c4_l3_core 14_ui_ia 15_cicd; do
  dot -Tpng -Gdpi=160 "dot/$f.dot" -o "diagrams/$f.png"; echo "wrote $f"
done
