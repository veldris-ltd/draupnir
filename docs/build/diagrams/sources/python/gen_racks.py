#!/usr/bin/env python3
"""Render rack elevations, power tree and software stack PNGs for the
Veldris MIDGARD FORGE build plan."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
import matplotlib.patheffects as pe

NAVY   = "#0E1A2B"
SLATE  = "#1F3350"
STEEL  = "#3E5C82"
AMBER  = "#E0A030"
ICE    = "#E9F0F7"
GREEN  = "#2E8B57"
RED    = "#B3402F"
GREY   = "#7E8FA3"
LILAC  = "#6C5B9E"
TEAL   = "#177E89"
PAPER  = "#FFFFFF"

plt.rcParams["font.family"] = "DejaVu Sans"


def draw_rack(ax, x0, width, units, items, title, subtitle):
    """items: list of (start_u, height_u, label, sublabel, colour, textcolour)"""
    uh = 1.0
    # outer frame
    ax.add_patch(Rectangle((x0 - 0.34, -0.55), width + 0.68, units * uh + 1.5,
                           facecolor="#F4F7FA", edgecolor=NAVY, lw=2.2, zorder=0))
    # rails
    for rx in (x0 - 0.20, x0 + width + 0.20):
        ax.add_patch(Rectangle((rx - 0.06, -0.30), 0.12, units * uh + 0.60,
                               facecolor=NAVY, edgecolor="none", zorder=1))
    # U grid + labels
    for u in range(1, units + 1):
        y = (u - 1) * uh
        ax.plot([x0 - 0.20, x0 + width + 0.20], [y, y],
                color="#C3CEDA", lw=0.6, zorder=1)
        ax.text(x0 - 0.42, y + uh / 2, f"U{u}", ha="right", va="center",
                fontsize=7.5, color=GREY)

    for (su, hu, label, sub, colour, tc) in items:
        y = (su - 1) * uh
        ax.add_patch(FancyBboxPatch((x0, y + 0.06), width, hu * uh - 0.12,
                                    boxstyle="round,pad=0.005,rounding_size=0.06",
                                    facecolor=colour, edgecolor=NAVY,
                                    lw=1.1, zorder=3))
        ax.text(x0 + width / 2, y + hu * uh / 2 + (0.13 if sub else 0),
                label, ha="center", va="center", fontsize=8.3,
                fontweight="bold", color=tc, zorder=4)
        if sub:
            ax.text(x0 + width / 2, y + hu * uh / 2 - 0.19, sub,
                    ha="center", va="center", fontsize=6.6, color=tc,
                    zorder=4, alpha=0.92)

    ax.text(x0 + width / 2, units * uh + 0.66, title, ha="center", va="center",
            fontsize=11.5, fontweight="bold", color=NAVY)
    ax.text(x0 + width / 2, units * uh + 0.26, subtitle, ha="center", va="center",
            fontsize=7.8, color=STEEL)


def rack_elevations(path):
    fig, ax = plt.subplots(figsize=(15.5, 9.6))
    ax.set_xlim(-1.2, 25.2)
    ax.set_ylim(-1.6, 14.6)
    ax.axis("off")
    fig.patch.set_facecolor(PAPER)

    W = 5.6
    fan   = ("#274A6B", "white")
    spark = (AMBER, NAVY)
    mac   = ("#B9C6D4", NAVY)
    net   = (TEAL, "white")
    pdu   = (RED, "white")
    cbl   = ("#D8E2EC", NAVY)
    sbc   = (GREEN, "white")
    lcd   = (LILAC, "white")

    rack_a = [
        (10, 3, "CON-A  :  9\" 1280x720 TOUCH CONSOLE", "3U  ·  HDMI 2.1a + USB direct from DVALIN  ·  local console, no BMC", *lcd),
        (9,  1, "0.5U BRUSH PANEL (upper half) + OPEN", "DVALIN rear egress · 6 mm body overhang cleared", *cbl),
        (8,  1, "DVALIN   ·   dgx-spark-1 (alias)", "GB10 · 128 GB · 4 TB · BAUGR NODE 1 · RANK 0", *spark),
        (7,  1, "0.5U BRUSH PANEL (upper half) + OPEN", "DURIN rear egress", *cbl),
        (6,  1, "DURIN   ·   dgx-spark-2 (alias)", "GB10 · 128 GB · 4 TB · BAUGR NODE 2 · RANK 1", *spark),
        (5,  1, "0.5U BRUSH PANEL (upper half) + OPEN", "DAIN rear egress", *cbl),
        (4,  1, "DAIN   ·   dgx-spark-3 (alias)", "GB10 · 128 GB · 4 TB · BAUGR NODE 3 · RANK 2", *spark),
        (2,  2, "2U FAN PANEL  :  INTAKE", "GeeekPi 2U dual 70 mm + OLED  ·  FAN-A1", *fan),
        (1,  1, "PDU-A   ·   3 x UK 13 A", "3 x DGX Spark 240 W USB-C PSU", *pdu),
    ]

    rack_b = [
        (11, 2, "2U FAN PANEL  :  EXHAUST", "GeeekPi 2U dual 70 mm + OLED  ·  FAN-B2", *fan),
        (10, 1, "REGIN  +  HODD VAULT ENCLOSURE", "Pi 5 16 GB · ANYOYO TB5 + SN850X 4 TB", *sbc),
        (9,  1, "0.5U BRUSH PANEL (upper half) + OPEN", "ANDVARI rear egress", *cbl),
        (8,  1, "ANDVARI   ·   andvari", "Mac mini M4 Pro · HODD VAULT / MLflow / MinIO", *mac),
        (7,  1, "0.5U BRUSH PANEL (upper half) + OPEN", "ALVISS rear egress", *cbl),
        (6,  1, "ALVISS   ·   alviss", "Mac mini M4 Pro · RAUN eval / CI / Ansible", *mac),
        (5,  1, "NORI   ·   TL-SG108S", "8 x 1 GbE  ·  TAUMR management segment", *net),
        (4,  1, "NAIN   ·   6-port 10 G", "2 x 10 G RJ45 + 4 x 2.5 G  ·  BRAUT data segment", *net),
        (2,  2, "2U FAN PANEL  :  INTAKE", "GeeekPi 2U dual 70 mm + OLED  ·  FAN-B1", *fan),
        (1,  1, "PDU-B   ·   3 x UK 13 A", "ANDVARI · ALVISS · 200 W GaN hub", *pdu),
    ]

    rack_c = [
        (2, 3, "CON-B  ·  9\" 1280x720 TOUCH", "3U operations console · HDMI 2.1 from REGIN", *lcd),
        (1, 1, "PDU-C  ·  3 x UK 13 A", "CON-B · GDSTIME 80 mm · GDSTIME 120 mm", *pdu),
    ]

    draw_rack(ax, 0.6, W, 12, rack_a, "RACK A : STEDI  (DeskPi RackMate T2, 12U)",
              "COMPUTE PLANE + PRIMARY CONSOLE · 3 × DGX Spark")
    draw_rack(ax, 9.0, W, 12, rack_b, "RACK B : BELGR  (DeskPi RackMate T2, 12U)",
              "CONTROL · STORAGE · NETWORK PLANE")
    draw_rack(ax, 17.4, W, 4, rack_c, "RACK C : TANGIR  (T0 Plus, 4U)",
              "OPERATIONS CONSOLE · desk-side satellite")

    # spares panel
    spares = [
        "HELD AS SPARES / PHASE-2 EXPANSION",
        "",
        "6 x 2U vented blank panel   (reserved for 3rd T2)",
        "1 x 2U dual fan panel  (ex FAN-A2, displaced by CON-A)",
        "2 x 0.5U D-ring manager (both now spare, see note)",
        "3 x 0.5U brush cable panel  (8 supplied, 5 fitted)",
        "1 x PDU-D  ·  1 x ORI (TL-SG108S, bench)",
        "1 x 512 GB microSD (REGIN clone / recovery)",
        "~14 x 0.5 m Cat 6 patch leads",
        "",
        "NO SPARE CONSOLE. Both LCD panels are now in service.",
        "",
        "CHANGE vs Rev 2.0: CON-A occupies STEDI U10-U12, displacing",
        "the top exhaust fan panel and the D-ring manager. Rack A top",
        "extraction is now the GDSTIME 80 mm rear pair at full speed.",
        "The three BAUGR DAC cables are supported on the rear frame by",
        "hook-and-loop, not by a D-ring panel. Gated by acceptance",
        "test A4: 24 h soak, zero thermal throttle events.",
    ]
    ax.add_patch(FancyBboxPatch((17.15, 5.55), 7.7, 6.6,
                                boxstyle="round,pad=0.12,rounding_size=0.12",
                                facecolor=ICE, edgecolor=STEEL, lw=1.3))
    for i, line in enumerate(spares):
        ax.text(17.45, 11.85 - i * 0.38, line, fontsize=8.0 if i == 0 else 7.1,
                fontweight="bold" if i == 0 else "normal",
                color=NAVY if i == 0 else SLATE, va="center", family="DejaVu Sans")

    ax.text(-1.0, 14.15, "VELDRIS LTD  ·  SINDRI FORGE  ·  RACK ELEVATIONS  (front view, U1 at floor)  ·  Rev 3.0",
            fontsize=14, fontweight="bold", color=NAVY, va="center")
    ax.text(-1.0, 13.72, "Company no. 17366869  ·  CONFIDENTIAL — RECIPIENT EYES ONLY",
            fontsize=8.4, color=RED, va="center")
    ax.text(-1.0, -1.35,
            "10-inch (254 mm) rack, 260 mm usable depth, ~220 mm usable panel width. "
            "DGX Spark 150 × 150 × 50.5 mm; Mac mini M4 Pro 127 × 127 × 50 mm. "
            "Neither pair fits side-by-side: one appliance per shelf.",
            fontsize=7.6, color=GREY, va="center")

    fig.tight_layout()
    fig.savefig(path, dpi=185, facecolor=PAPER, bbox_inches="tight")
    plt.close(fig)
    print("wrote", path)


def software_stack(path):
    fig, ax = plt.subplots(figsize=(14.6, 9.2))
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")
    fig.patch.set_facecolor(PAPER)

    layers = [
        ("L7  GOVERNANCE & RELEASE", "#0E1A2B", "white",
         "Model cards · SHA-256 manifests · SBOM (CycloneDX) · corpus licence register · "
         "DPIA · Cyber Essentials asset record · Veldris release gate"),
        ("L6  MODEL FACTORY", "#1F3350", "white",
         "LLaMA-Factory · Axolotl · NVIDIA NeMo · Unsloth · TRL/PEFT · mergekit (TIES / DARE / SLERP) · "
         "TensorRT Model Optimizer (NVFP4) · llama.cpp quantise · MLX-LM-LoRA"),
        ("L5  EVALUATION & ASSURANCE", "#2A4A6B", "white",
         "lm-evaluation-harness · lighteval · promptfoo · ragas · custom CIM-56 jurisdiction suites · "
         "citation-hallucination probes · refusal & safety regression"),
        ("L4  ORCHESTRATION & TRACKING", TEAL, "white",
         "Slurm 23.11 (slurmctld on RPI5-OOB) · Ray 2.x · MLflow + PostgreSQL · MinIO (S3) · "
         "DVC / lakeFS dataset versioning · Ansible · GitLab-CI or Forgejo Actions"),
        ("L3  FRAMEWORK & RUNTIME", STEEL, "white",
         "PyTorch (sbsa aarch64, CUDA 13) · FSDP2 · DeepSpeed ZeRO-3 · transformers · datasets · "
         "accelerate · bitsandbytes · liger-kernel · vLLM · SGLang · TensorRT-LLM · MLX (Apple)"),
        ("L2  ACCELERATION & FABRIC", AMBER, NAVY,
         "CUDA 13.x · cuDNN · NCCL · DOCA-OFED / rdma-core · RoCEv2 · perftest · nccl-tests · "
         "NVIDIA Container Toolkit · Docker CE · DCGM"),
        ("L1  OPERATING SYSTEM", "#B9C6D4", NAVY,
         "DGX OS 7 (Ubuntu 24.04 LTS aarch64, 6.14-nvidia) × 3   |   macOS (Apple silicon) × 2   |   "
         "Raspberry Pi OS Bookworm 64-bit × 1"),
        ("L0  HARDWARE", GREY, "white",
         "3 × GB10 Grace Blackwell (128 GB unified, 4 TB NVMe, ConnectX-7 200 GbE) · "
         "2 × Apple M4 Pro · 1 × RPi 5 16 GB · 4 TB TB5 vault · 200 GbE ring + 10 GbE + 1 GbE"),
    ]

    y = 90
    for name, fc, tc, detail in layers:
        h = 9.6
        ax.add_patch(FancyBboxPatch((3, y - h), 94, h - 1.1,
                                    boxstyle="round,pad=0.2,rounding_size=0.7",
                                    facecolor=fc, edgecolor=NAVY, lw=1.2))
        ax.text(5.5, y - 2.9, name, fontsize=10.4, fontweight="bold", color=tc, va="center")
        # wrap detail
        words = detail.split(" ")
        lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 > 118:
                lines.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        lines.append(cur)
        for i, ln in enumerate(lines[:2]):
            ax.text(5.5, y - 5.4 - i * 2.3, ln, fontsize=7.6, color=tc, va="center", alpha=0.95)
        y -= h + 0.9

    ax.text(3, 97.5, "MIDGARD FORGE  ·  SOFTWARE STACK", fontsize=15,
            fontweight="bold", color=NAVY, va="center")
    ax.text(3, 94.3, "Veldris Ltd  ·  every layer self-hosted; no third-party training telemetry leaves the forge",
            fontsize=8.6, color=STEEL, va="center")
    fig.tight_layout()
    fig.savefig(path, dpi=185, facecolor=PAPER, bbox_inches="tight")
    plt.close(fig)
    print("wrote", path)


def power_budget(path):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15.2, 7.4),
                                   gridspec_kw={"width_ratios": [1.25, 1]})
    fig.patch.set_facecolor(PAPER)

    items = ["3 × DGX Spark", "2 × Mac mini M4 Pro", "4 × 2U fan panel",
             "6 × GDSTIME AC fan", "RPi 5 + TB5 vault", "SW-DATA + SW-MGMT",
             "LCD console"]
    nameplate = [720, 310, 20, 38, 37, 19, 8]
    sustained = [510, 180, 20, 38, 20, 19, 8]

    ypos = range(len(items))
    ax1.barh([y + 0.19 for y in ypos], nameplate, height=0.36,
             color=AMBER, edgecolor=NAVY, lw=0.9, label="Nameplate / worst case (W)")
    ax1.barh([y - 0.19 for y in ypos], sustained, height=0.36,
             color=STEEL, edgecolor=NAVY, lw=0.9, label="Sustained training draw (W)")
    ax1.set_yticks(list(ypos)); ax1.set_yticklabels(items, fontsize=9)
    ax1.invert_yaxis()
    ax1.set_xlabel("Watts", fontsize=9)
    ax1.set_title("Load breakdown", fontsize=12, fontweight="bold", color=NAVY, loc="left")
    ax1.legend(fontsize=8, loc="lower right", frameon=True)
    ax1.grid(axis="x", color="#D6DEE7", lw=0.7)
    ax1.set_axisbelow(True)
    for s in ("top", "right"):
        ax1.spines[s].set_visible(False)
    for i, (n, s) in enumerate(zip(nameplate, sustained)):
        ax1.text(n + 12, i + 0.19, f"{n} W", va="center", fontsize=7.6, color=NAVY)
        ax1.text(s + 12, i - 0.19, f"{s} W", va="center", fontsize=7.6, color=STEEL)

    tot_n, tot_s = sum(nameplate), sum(sustained)
    ax2.axis("off")
    ax2.set_xlim(0, 10); ax2.set_ylim(0, 10)
    rows = [
        ("Nameplate aggregate", f"{tot_n:,} W", f"{tot_n/230:.1f} A @ 230 V", AMBER),
        ("Sustained training draw", f"{tot_s:,} W", f"{tot_s/230:.1f} A @ 230 V", STEEL),
        ("Idle / standby estimate", "≈ 210 W", "0.9 A @ 230 V", GREY),
        ("Heat rejected (sustained)", f"{tot_s*3.412:,.0f} BTU/hr", "≈ 0.23 ton of cooling", RED),
        ("UK 13 A socket ceiling", "3,120 W", "single socket outlet", GREEN),
        ("Headroom on one socket", f"{3120-tot_n:,} W", f"{100*(1-tot_n/3120):.0f} % spare", GREEN),
        ("Annual energy @ 40 % duty", f"{tot_s*24*365*0.40/1000:,.0f} kWh", "≈ £700–900 at 25 p/kWh", NAVY),
    ]
    ax2.text(0.2, 9.6, "Aggregate power & thermal envelope", fontsize=12,
             fontweight="bold", color=NAVY, va="center")
    for i, (k, v, note, c) in enumerate(rows):
        yy = 8.5 - i * 1.14
        ax2.add_patch(FancyBboxPatch((0.2, yy - 0.44), 9.5, 0.92,
                                     boxstyle="round,pad=0.03,rounding_size=0.1",
                                     facecolor="#F4F7FA", edgecolor="#D6DEE7", lw=1))
        ax2.add_patch(Rectangle((0.2, yy - 0.44), 0.13, 0.92, facecolor=c, edgecolor="none"))
        ax2.text(0.55, yy + 0.13, k, fontsize=8.8, fontweight="bold", color=NAVY, va="center")
        ax2.text(0.55, yy - 0.22, note, fontsize=7.3, color=GREY, va="center")
        ax2.text(9.5, yy, v, fontsize=10.6, fontweight="bold", color=c, va="center", ha="right")

    ax2.text(0.2, 0.35,
             "Recommendation: feed PDU-A and PDU-B from two separate 13 A outlets on\n"
             "different ring segments. Total draw fits one socket, but split feeds contain\n"
             "a single-socket failure and halve inrush at cold start.",
             fontsize=7.6, color=SLATE, va="center")

    fig.suptitle("MIDGARD FORGE  ·  POWER & THERMAL BUDGET", fontsize=15,
                 fontweight="bold", color=NAVY, x=0.012, ha="left", y=0.985)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(path, dpi=185, facecolor=PAPER, bbox_inches="tight")
    plt.close(fig)
    print("wrote", path)


if __name__ == "__main__":
    rack_elevations("diagrams/01_rack_elevations.png")
    software_stack("diagrams/05_software_stack.png")
    power_budget("diagrams/06_power_thermal.png")
