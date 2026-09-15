import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useEvents, type RunDelta } from './useEvents';

/**
 * RF-41's verification found J2 failing only when the other journeys ran
 * beside it. The board merged from `feed.last`, which is state: two frames
 * dispatched in one task render once, with the second, so a new run's delta
 * that arrived beside another run's was never merged and the run never
 * appeared. Asserted here without a browser: both frames reach the caller.
 */

class FakeSource {
  static current: FakeSource | null = null;
  static readonly CLOSED = 2;
  readonly listeners = new Map<string, EventListener>();
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    FakeSource.current = this;
  }

  addEventListener(kind: string, listener: EventListener): void {
    this.listeners.set(kind, listener);
  }

  emit(kind: string, delta: RunDelta): void {
    this.listeners.get(kind)?.(new MessageEvent(kind, { data: JSON.stringify(delta) }));
  }

  close(): void {
    this.readyState = FakeSource.CLOSED;
  }
}

function delta(seq: number, runId: string): RunDelta {
  return {
    seq,
    kind: 'run.state',
    siteId: 'sindri',
    subjectId: runId,
    runId,
    at: '2026-09-15T08:00:00+00:00',
    changed: { state: 'DRAFT' },
  };
}

describe('the site event feed', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('hands the caller every delta that arrives in one task, not only the last', () => {
    vi.stubGlobal('EventSource', FakeSource);
    const seen: number[] = [];
    renderHook(() =>
      useEvents('/v1/events', true, (received) => {
        seen.push(received.seq);
      }),
    );

    act(() => {
      FakeSource.current?.emit('run.state', delta(41, 'run-a'));
      FakeSource.current?.emit('run.state', delta(42, 'run-b'));
    });

    expect(seen).toEqual([41, 42]);
  });
});
