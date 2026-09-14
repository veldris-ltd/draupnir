import { BASE_FOR_TIER, TIER_OF, type Tier } from '@draupnir/api-client';

/**
 * The specification S09 Compose starts from. RF-35.
 *
 * It named its base by hand, and named the Tier B base for GBR, which is Tier
 * A. The API refuses a base that is not the jurisdiction's tier's, so the first
 * dry run anybody made on the screen was a 422. Nothing here names a base: the
 * tier comes from the jurisdiction and the base from the tier, both from the
 * table generated from the one the API validates against, and the site from
 * `/healthz`, because the base's address is the site's.
 *
 * Shared with J2, whose submission journeys had the same hand-typed base.
 */

export const DEFAULT_JURISDICTION = 'GBR';

/** The subset of SAD 6.2's run specification this default fills in. */
export interface RunSpecification {
  apiVersion: 'draupnir/v1';
  kind: 'AdapterRun';
  metadata: { name: string; jurisdiction: string; tier: Tier };
  spec: {
    base: { artefact: string; expectSha256: string };
    dataset: { artefact: string; expectSha256: string; cutoffPercentile: number };
    train: {
      driver: string;
      method: string;
      precision: string;
      params: { rank: number };
    };
    placement: { driver: string; partition: string; nodes: number };
    evaluate: { driver: string; suites: string[]; gates: string[]; baseline: null };
    release: { route: string; formats: string[]; approval: string };
  };
}

/** A jurisdiction's tier, or an error naming it. Never a default tier. */
export function tierOf(jurisdiction: string): Tier {
  const tier = (TIER_OF as Partial<Record<string, Tier>>)[jurisdiction];
  if (tier === undefined) {
    throw new Error(`${jurisdiction} is not a CIM-56 jurisdiction, and there is no default tier.`);
  }
  return tier;
}

/** The `hodd://` address of a jurisdiction's base at a site. `tiers.base_artefact`. */
export function baseArtefact(jurisdiction: string, site: string): string {
  return `hodd://${site}/models/core/${BASE_FOR_TIER[tierOf(jurisdiction)]}`;
}

export function defaultSpecification({
  site,
  jurisdiction = DEFAULT_JURISDICTION,
  name = `cim-${jurisdiction.toLowerCase()}-v0.5`,
}: {
  site: string;
  jurisdiction?: string;
  name?: string;
}): RunSpecification {
  return {
    apiVersion: 'draupnir/v1',
    kind: 'AdapterRun',
    metadata: { name, jurisdiction, tier: tierOf(jurisdiction) },
    spec: {
      base: {
        artefact: baseArtefact(jurisdiction, site),
        expectSha256: 'a'.repeat(64),
      },
      dataset: {
        artefact: `hodd://corpora/${jurisdiction}/curated`,
        expectSha256: 'b'.repeat(64),
        cutoffPercentile: 99,
      },
      train: {
        driver: 'hamarr.llamafactory/v1',
        method: 'lora',
        precision: 'bf16',
        // No `save_steps`. HAMARR derives the checkpoint interval so that no
        // more than thirty minutes of work is ever unwritten, and refuses an
        // authored one that would leave more. This default authored 500, which
        // at the assumed twelve seconds a step is a hundred minutes: once its
        // base was right, that was the next refusal (RF-35). The editor arrives
        // with a specification that dry runs cleanly, because the first thing
        // an operator does with it is press Dry run.
        params: { rank: 16 },
      },
      placement: { driver: 'motsognir.slurm/v1', partition: 'default', nodes: 1 },
      evaluate: { driver: 'raun.lmeval/v1', suites: ['legal-qa'], gates: ['E1'], baseline: null },
      release: { route: 'tier-a', formats: ['gguf'], approval: 'required' },
    },
  };
}
