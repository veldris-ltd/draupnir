import { useEffect, useRef, useState } from 'react';

/**
 * The run board's live connection. AC-U4, AC-N3.
 *
 * "The run board reflects a state change within 5 seconds by server sent
 * events, with no manual refresh and no full list poll."
 *
 * Three things follow from that sentence and all three are here.
 *
 * **Deltas are merged, not refetched.** An event says what changed about one
 * subject; the board applies it to the row it already holds. Re-reading the
 * list on every event would be the full list poll the criterion rules out,
 * arriving by a different route.
 *
 * **The freshness is stated in words.** UX 9.1 asks for it beneath the table
 * rather than implied by an animation, because a board that has silently
 * stopped updating looks exactly like a board where nothing is happening. This
 * hook therefore reports whether it is connected and when it last heard
 * anything, and the board renders that.
 *
 * **A gap is not papered over.** The API refuses a `Last-Event-ID` it can no
 * longer serve and says to resynchronise. The browser's `EventSource` resends
 * the last id automatically on reconnect, so that refusal arrives as an error
 * event; the board is told to re-read rather than left holding state that is
 * quietly wrong.
 */

export interface RunDelta {
  seq: number;
  kind: string;
  siteId: string;
  subjectId: string;
  runId: string | null;
  at: string;
  changed: Record<string, unknown>;
}

export type Liveness = 'connecting' | 'live' | 'reconnecting' | 'stale';

export interface EventFeed {
  /** The most recent delta, so a caller can merge it. */
  last: RunDelta | null;
  liveness: Liveness;
  /** When an event or keep-alive last arrived. */
  lastHeardAt: Date | null;
  /** Set when the server says the client must re-read. */
  mustResynchronise: boolean;
}

/**
 * Subscribe to a site's event stream.
 *
 * `EventSource` rather than `fetch` with a reader: it reconnects on its own,
 * it resends `Last-Event-ID`, and both are behaviours this would otherwise
 * have to reimplement and get subtly wrong.
 */
export function useEvents(url: string, enabled = true): EventFeed {
  const [last, setLast] = useState<RunDelta | null>(null);
  const [liveness, setLiveness] = useState<Liveness>('connecting');
  const [lastHeardAt, setLastHeardAt] = useState<Date | null>(null);
  const [mustResynchronise, setMustResynchronise] = useState(false);
  const source = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!enabled || typeof EventSource === 'undefined') {
      setLiveness('stale');
      return;
    }

    const stream = new EventSource(url);
    source.current = stream;
    setLiveness('connecting');

    stream.onopen = () => {
      setLiveness('live');
      setLastHeardAt(new Date());
    };

    const receive = (event: MessageEvent<string>): void => {
      setLastHeardAt(new Date());
      setLiveness('live');
      try {
        setLast(JSON.parse(event.data) as RunDelta);
      } catch {
        // A frame that is not JSON is a bug on the wire, not a reason to tear
        // the board down. It is dropped and the connection is left alone.
      }
    };

    for (const kind of ['run.state', 'run.progress', 'array.element', 'gate.result']) {
      stream.addEventListener(kind, receive as EventListener);
    }
    stream.onmessage = receive;

    stream.onerror = () => {
      // `EventSource` reconnects by itself unless it is closed. A repeated
      // failure to reconnect is what "stale" means, and the board says so
      // rather than showing rows that stopped moving for no stated reason.
      setLiveness(stream.readyState === EventSource.CLOSED ? 'stale' : 'reconnecting');
      if (stream.readyState === EventSource.CLOSED) setMustResynchronise(true);
    };

    return () => {
      stream.close();
      source.current = null;
    };
  }, [url, enabled]);

  return { last, liveness, lastHeardAt, mustResynchronise };
}

/** How the board says its freshness in words (UX 9.1). */
export function freshnessSentence(feed: EventFeed): string {
  switch (feed.liveness) {
    case 'live':
      return feed.lastHeardAt
        ? `Live. Last update ${feed.lastHeardAt.toLocaleTimeString()}.`
        : 'Live.';
    case 'connecting':
      return 'Connecting to the event stream.';
    case 'reconnecting':
      return 'The event stream dropped and is reconnecting. This list may be behind.';
    case 'stale':
      return 'Not receiving updates. Re-read the list to see the current state.';
  }
}
