import { describe, expect, it } from 'vitest';
import { BASE_FOR_TIER, TIER_OF } from '@draupnir/api-client';
import { defaultSpecification, tierOf } from './specification';

/**
 * RF-35. The compose screen's default named the Tier B base for GBR, which the
 * API refuses, and nothing failed until a journey was run. The dry run itself
 * is J2's; what is asserted here is the rule the API applies, against the
 * table generated from the one it applies it with.
 */

describe('the default run specification', () => {
  it('names the base of its jurisdiction’s tier, at the site it is composed for', () => {
    const specification = defaultSpecification({ site: 'sindri' });

    expect(specification.metadata).toMatchObject({ jurisdiction: 'GBR', tier: 'A' });
    expect(specification.spec.base.artefact).toBe(
      'hodd://sindri/models/core/MIDGARD-CORE-GEMMA3-27B-v1.0',
    );
  });

  it.each(Object.keys(TIER_OF))('declares %s’s tier and names that tier’s base', (code) => {
    const specification = defaultSpecification({ site: 'brokkr', jurisdiction: code });
    const tier = TIER_OF[code as keyof typeof TIER_OF];

    expect(specification.metadata.tier).toBe(tier);
    expect(specification.spec.base.artefact).toBe(
      `hodd://brokkr/models/core/${BASE_FOR_TIER[tier]}`,
    );
  });

  it('is composed from the whole programme, one base per tier', () => {
    const codes = Object.keys(TIER_OF);
    expect(codes).toHaveLength(56);
    expect(codes.filter((code) => TIER_OF[code as keyof typeof TIER_OF] === 'A')).toHaveLength(9);
    expect(BASE_FOR_TIER.A).not.toBe(BASE_FOR_TIER.B);
  });

  it('refuses a jurisdiction outside the programme rather than guessing a tier', () => {
    expect(() => tierOf('FRA')).toThrow(/not a CIM-56 jurisdiction/);
    expect(() => defaultSpecification({ site: 'sindri', jurisdiction: 'FRA' })).toThrow();
  });
});
