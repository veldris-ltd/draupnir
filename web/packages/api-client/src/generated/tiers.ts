/* Generated tier table. Do not edit.
 *
 * Produced by `scripts/generate_ts_tiers.py` from `draupnir/hamarr/tiers.py`,
 * the table the API validates a specification against. The pipeline
 * regenerates this file and fails on any diff (AC-Q2).
 *
 * A client that composes a specification takes its base from here rather than
 * naming one, because the API refuses a base that is not the tier's (RF-35).
 */

/** A CIM-56 tier. */
export type Tier = 'A' | 'B';

/** The base model each tier trains against. SAD 13.5. */
export const BASE_FOR_TIER = {
  A: 'MIDGARD-CORE-GEMMA3-27B-v1.0',
  B: 'MIDGARD-CORE-QWEN36-35B-A3B-v1.0',
} as const satisfies Record<Tier, string>;

/** Every CIM-56 jurisdiction's tier, by ISO 3166-1 alpha-3 code. */
export const TIER_OF = {
  ATG: 'B',
  AUS: 'A',
  BGD: 'B',
  BHS: 'B',
  BLZ: 'B',
  BRB: 'B',
  BRN: 'B',
  BWA: 'B',
  CAN: 'A',
  CMR: 'B',
  CYP: 'A',
  DMA: 'B',
  FJI: 'B',
  GAB: 'B',
  GBR: 'A',
  GHA: 'B',
  GMB: 'B',
  GRD: 'B',
  GUY: 'B',
  IND: 'A',
  JAM: 'B',
  KEN: 'B',
  KIR: 'B',
  KNA: 'B',
  LCA: 'B',
  LKA: 'B',
  LSO: 'B',
  MDV: 'B',
  MLT: 'A',
  MOZ: 'B',
  MUS: 'B',
  MWI: 'B',
  MYS: 'B',
  NAM: 'B',
  NGA: 'A',
  NRU: 'B',
  NZL: 'B',
  PAK: 'B',
  PNG: 'B',
  RWA: 'B',
  SGP: 'A',
  SLB: 'B',
  SLE: 'B',
  SWZ: 'B',
  SYC: 'B',
  TGO: 'B',
  TON: 'B',
  TTO: 'B',
  TUV: 'B',
  TZA: 'B',
  UGA: 'B',
  VCT: 'B',
  VUT: 'B',
  WSM: 'B',
  ZAF: 'A',
  ZMB: 'B',
} as const satisfies Record<string, Tier>;
