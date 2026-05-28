/**
 * src/components/Leaderboard.tsx
 * ================================
 * Live Top-5 leaderboard panel driven by the SSE stream.
 * Highlights entries that changed position since the last push.
 *
 * Highlight logic
 * ---------------
 * We keep a `prevLeaderboard` ref. On each SSE update we compare
 * usernames at each rank. Any entry that is NEW (wasn't in the previous
 * list) or MOVED UP gets a `bumped` CSS class applied for one cycle.
 * A `useEffect` clears the highlight set 900ms later so it only
 * pulses once.
 */

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getLeaderboard } from '../api';
import { queryKeys } from '../queryClient';
import { useLeaderboard } from '../hooks/useLeaderboard';
import type { LeaderboardEntry } from '../types';
import styles from './Leaderboard.module.css';

interface LeaderboardProps {
  tableId: number;
}

export default function Leaderboard({ tableId }: LeaderboardProps) {
  // ── Initial REST load ──────────────────────────────────────────────────────
  const { data: initialData } = useQuery({
    queryKey: queryKeys.leaderboard(tableId),
    queryFn:  () => getLeaderboard(tableId),
    staleTime: Infinity, // SSE handles updates; REST is a one-shot seed
  });

  // ── SSE live stream ────────────────────────────────────────────────────────
  const { leaderboard: liveLeaderboard, connectionStatus, reshuffleSignal } = useLeaderboard(tableId);

  // Brief "Shoe reshuffled" banner driven by the SSE reshuffle event
  const [showReshuffle, setShowReshuffle] = useState(false);
  useEffect(() => {
    if (reshuffleSignal === 0) return;
    setShowReshuffle(true);
    const t = setTimeout(() => setShowReshuffle(false), 3000);
    return () => clearTimeout(t);
  }, [reshuffleSignal]);

  // Merge: use SSE data if we've received any, otherwise fall back to REST
  const leaderboard = liveLeaderboard.length > 0
    ? liveLeaderboard
    : (initialData?.leaderboard ?? []);

  // ── Change-highlight logic ────────────────────────────────────────────────
  const prevRef = useRef<LeaderboardEntry[]>([]);
  const [highlighted, setHighlighted] = useState<Set<string>>(new Set());

  useEffect(() => {
    const prev = prevRef.current;
    if (prev.length === 0) {
      prevRef.current = leaderboard;
      return;
    }

    const changed = new Set<string>();
    leaderboard.forEach((entry, idx) => {
      const prevEntry = prev[idx];
      // Highlight if: new to list OR balance changed OR rank changed
      if (!prevEntry || prevEntry.username !== entry.username || prevEntry.balance !== entry.balance) {
        changed.add(entry.username);
      }
    });

    if (changed.size > 0) {
      setHighlighted(changed);
      const t = setTimeout(() => setHighlighted(new Set()), 900);
      prevRef.current = leaderboard;
      return () => clearTimeout(t);
    }
    prevRef.current = leaderboard;
  }, [leaderboard]);

  // ── Status indicator ──────────────────────────────────────────────────────
  const statusDot = {
    connected:   styles.dotGreen,
    connecting:  styles.dotYellow,
    reconnecting:styles.dotYellow,
    error:       styles.dotRed,
    idle:        styles.dotGrey,
    closed:      styles.dotGrey,
  }[connectionStatus] ?? styles.dotGrey;

  const statusLabel = {
    connected:    'Live',
    connecting:   'Connecting…',
    reconnecting: 'Reconnecting…',
    error:        'Offline',
    idle:         '—',
    closed:       'Closed',
  }[connectionStatus] ?? '—';

  const medals = ['🥇', '🥈', '🥉', '4', '5'];

  return (
    <aside className={styles.panel}>
      {/* Header */}
      <div className={styles.header}>
        <h3 className={styles.title}>Leaderboard</h3>
        <div className={styles.statusRow}>
          <span className={`${styles.dot} ${statusDot}`} />
          <span className={styles.statusLabel}>{statusLabel}</span>
        </div>
      </div>

      {/* Reshuffle banner — fades in for 3 s after SSE reshuffle event */}
      {showReshuffle && (
        <div className={styles.reshuffleBanner} role="status" aria-live="polite">
          ♻ Shoe reshuffled
        </div>
      )}

      {/* Divider */}
      <div className={styles.divider} />

      {/* Entries */}
      {leaderboard.length === 0 ? (
        <p className={styles.empty}>No players yet.<br/>Be the first!</p>
      ) : (
        <ol className={styles.list}>
          {leaderboard.map((entry, idx) => {
            const isBumped    = highlighted.has(entry.username);
            const isTopThree  = idx < 3;
            const balance     = parseFloat(entry.balance);
            const isNegative  = balance < 0;

            return (
              <li
                key={entry.username}
                className={`${styles.entry} ${isBumped ? styles.bumped : ''} ${isTopThree ? styles.topThree : ''}`}
              >
                <span className={styles.rank}>
                  {idx < 3 ? medals[idx] : <span className={styles.rankNum}>{idx + 1}</span>}
                </span>
                <span className={styles.username}>{entry.username}</span>
                <span className={`${styles.balance} ${isNegative ? styles.balanceNeg : ''}`}>
                  {balance.toLocaleString('en-US', {
                    minimumFractionDigits: 0,
                    maximumFractionDigits: 0,
                  })}
                </span>
              </li>
            );
          })}
        </ol>
      )}

      {/* Footer */}
      <div className={styles.footer}>
        <span className={styles.footerNote}>Table level rankings</span>
      </div>
    </aside>
  );
}
