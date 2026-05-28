/**
 * src/components/Lobby.tsx
 * ========================
 * Table selection lobby screen.
 * Fetches the table list via React Query (is_locked is server-computed).
 * Shows the player's current balance and a grid of playable levels.
 */

import { useQuery } from '@tanstack/react-query';
import { getTables, logout } from '../api';
import { queryKeys } from '../queryClient';
import { useAppStore, useUser } from '../store';
import type { Table } from '../types';
import styles from './Lobby.module.css';

interface LobbyProps {
  onSelectTable: (table: Table) => void;
}

export default function Lobby({ onSelectTable }: LobbyProps) {
  const user      = useUser();
  const clearAuth = useAppStore(s => s.clearAuth);

  const { data: tables = [], isLoading, isError } = useQuery({
    queryKey: queryKeys.tables,
    queryFn:  getTables,
    staleTime: 30_000,
  });

  const handleLogout = async () => {
    await logout();
    clearAuth();
  };

  const balance = parseFloat(user?.balance ?? '0');

  return (
    <div className={styles.page}>
      {/* ── Top bar ── */}
      <header className={styles.topBar}>
        <div className={styles.brand}>
          <span className={styles.brandSuit} aria-hidden>♠</span>
          <span className={styles.brandName}>Royal Table</span>
        </div>

        <div className={styles.balanceChip}>
          <span className={styles.balanceLabel}>Balance</span>
          <span className={`${styles.balanceValue} ${balance < 0 ? styles.balanceNeg : ''}`}>
            {balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </span>
          <span className={styles.balanceCurrency}>chips</span>
        </div>

        <button className={styles.logoutBtn} onClick={handleLogout}>
          Sign Out
        </button>
      </header>

      {/* ── Hero ── */}
      <div className={styles.hero}>
        <h2 className={styles.heroTitle}>Choose Your Table</h2>
        <p className={styles.heroSub}>
          Higher levels seat more AI opponents — each one draws from your shared deck.
        </p>
      </div>

      {/* ── Table grid ── */}
      {isLoading && (
        <div className={styles.loadingRow}>
          {[0,1,2,3,4].map(i => <div key={i} className={styles.skeleton} />)}
        </div>
      )}

      {isError && (
        <p className={styles.errorMsg}>
          Could not load tables. Please refresh the page.
        </p>
      )}

      {!isLoading && !isError && (
        <div className={styles.grid}>
          {tables.map(table => (
            <TableCard
              key={table.id}
              table={table}
              playerBalance={balance}
              onSelect={() => onSelectTable(table)}
            />
          ))}
        </div>
      )}

      {/* ── Footer flavour ── */}
      <footer className={styles.footer}>
        <span aria-hidden>♠ ♥ ♣ ♦</span>
        &nbsp; Play responsibly &nbsp;
        <span aria-hidden>♦ ♣ ♥ ♠</span>
      </footer>
    </div>
  );
}

/* ── Individual table card ──────────────────────────────────────────────────── */

interface TableCardProps {
  table:         Table;
  playerBalance: number;
  onSelect:      () => void;
}

const BOT_ICONS = ['', '🤖', '🤖🤖', '🤖🤖🤖', '🤖🤖🤖🤖'];
const LEVEL_NAMES = ['Beginner', 'Novice', 'Skilled', 'Expert', 'Master'];

function TableCard({ table, playerBalance, onSelect }: TableCardProps) {
  const locked = table.is_locked;
  const minBet = parseFloat(table.min_bet);
  const maxBet = parseFloat(table.max_bet);
  const unlock = parseFloat(table.unlock_balance);

  const needed   = locked ? unlock - playerBalance : 0;
  const levelName = LEVEL_NAMES[table.level] ?? `Level ${table.level}`;
  const botLabel  = table.bot_count === 0
    ? 'Solo play — no bots'
    : `${table.bot_count} bot${table.bot_count > 1 ? 's' : ''} at the table`;

  return (
    <div
      className={`${styles.tableCard} ${locked ? styles.locked : styles.unlocked}`}
      onClick={locked ? undefined : onSelect}
      role={locked ? undefined : 'button'}
      tabIndex={locked ? -1 : 0}
      onKeyDown={e => { if (!locked && e.key === 'Enter') onSelect(); }}
      aria-disabled={locked}
      aria-label={`${levelName} table, ${locked ? 'locked' : 'available'}`}
    >
      {/* Level badge */}
      <div className={styles.levelBadge}>
        {locked ? '🔒' : `Lv.${table.level}`}
      </div>

      {/* Title */}
      <h3 className={styles.tableName}>{levelName}</h3>

      {/* Bot display */}
      <div className={styles.botRow} aria-label={botLabel}>
        {table.bot_count === 0
          ? <span className={styles.soloLabel}>Solo</span>
          : <span className={styles.botIcons}>{BOT_ICONS[table.bot_count]}</span>
        }
        <span className={styles.botDesc}>{botLabel}</span>
      </div>

      {/* Bet range */}
      <div className={styles.betRange}>
        <div className={styles.betItem}>
          <span className={styles.betLabel}>Min Bet</span>
          <span className={styles.betValue}>{minBet.toLocaleString()}</span>
        </div>
        <div className={styles.betDivider} />
        <div className={styles.betItem}>
          <span className={styles.betLabel}>Max Bet</span>
          <span className={styles.betValue}>{maxBet.toLocaleString()}</span>
        </div>
      </div>

      {/* Lock overlay */}
      {locked && (
        <div className={styles.lockOverlay}>
          <div className={styles.lockIcon}>🔒</div>
          <p className={styles.lockText}>
            Need <strong>{needed.toLocaleString(undefined, { maximumFractionDigits: 0 })} more chips</strong>
          </p>
          <p className={styles.lockSub}>
            Unlock at {unlock.toLocaleString()} balance
          </p>
        </div>
      )}

      {/* Play CTA */}
      {!locked && (
        <div className={styles.playRow}>
          <span className={styles.playCta}>Play Now →</span>
        </div>
      )}
    </div>
  );
}
