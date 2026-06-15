import { createPortal }  from 'react-dom';
import { useEffect, useRef, useState } from 'react';
import { useAppStore, useCurrentGame, useActionInFlight } from '../store';
import { createGame, hit, stand, doubleDown, leaveGame }  from '../api';
import Card from './Card';
import type { ApiError, CardString, GameState, SideBetResult, Table } from '../types';
import styles from './GameTable.module.css';

// ── Constants ──────────────────────────────────────────────────────────────────

const CHIPS = [
  { value: 5,   label: '$5',   cls: styles.chip5   },
  { value: 10,  label: '$10',  cls: styles.chip10  },
  { value: 25,  label: '$25',  cls: styles.chip25  },
  { value: 100, label: '$100', cls: styles.chip100 },
  { value: 500, label: '$500', cls: styles.chip500 },
] as const;

// Vertical offsets to create arc — negative = up
const ARC_OFFSETS  = [-24, -10, 0, -10, -24] as const;
const FLY_DURATION = 500;   // ms — card flight from shoe to seat
const CARD_STAGGER = 250;   // ms — gap between consecutive deal launches
const DISCARD_DUR  = 260;   // ms — card return-to-shoe flight speed

// ── Types ──────────────────────────────────────────────────────────────────────

type Phase = 'select' | 'bet' | 'play' | 'animating' | 'result';

interface FlyCard {
  id:       string;
  card:     CardString | null;  // null → rendered face-down
  sx:       number;
  sy:       number;
  ex:       number;
  ey:       number;
  duration: number;
  onLand:   () => void;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function computeHandValue(cards: CardString[]): { value: number; isSoft: boolean } {
  let total = 0, aces = 0;
  for (const card of cards) {
    const rank = card[0];
    if (rank === 'A') { total += 11; aces++; }
    else if ('TJQK'.includes(rank)) total += 10;
    else total += parseInt(rank, 10);
  }
  while (total > 21 && aces > 0) { total -= 10; aces--; }
  return { value: total, isSoft: aces > 0 && total <= 21 };
}

function chipColor(value: number): string {
  if (value >= 500) return 'radial-gradient(circle at 38% 38%, #c070d8, #7030a0)';
  if (value >= 100) return 'radial-gradient(circle at 38% 38%, #4a5e6e, #1a2933)';
  if (value >= 25)  return 'radial-gradient(circle at 38% 38%, #3ac46a, #1a7840)';
  if (value >= 10)  return 'radial-gradient(circle at 38% 38%, #4a90d9, #1a66a8)';
  return 'radial-gradient(circle at 38% 38%, #e85c4a, #b03428)';
}

// ── Props ──────────────────────────────────────────────────────────────────────

interface GameTableProps {
  tableId:      number;
  table?:       Table;
  onLeaveTable: () => void;
}

// ═══════════════════════════════════════════════════════════════════════════════

export default function GameTable({ tableId, table, onLeaveTable }: GameTableProps) {
  const store          = useAppStore();
  const currentGame    = useCurrentGame();
  const actionInFlight = useActionInFlight();
  const user           = store.user;

  const [phase,      setPhase]      = useState<Phase>('select');
  const [playerSeat, setPlayerSeat] = useState<number | null>(null);
  const [botSeats,   setBotSeats]   = useState<number[]>([]);
  const [chipStack,  setChipStack]  = useState<number[]>([]);
  const [betError,   setBetError]   = useState('');
  const [flyCards,   setFlyCards]   = useState<FlyCard[]>([]);

  const [ppBet,                 setPpBet]                 = useState(0);
  const [t21Bet,                setT21Bet]                = useState(0);
  const [reshuffleAlert,        setReshuffleAlert]        = useState(false);
  const [reshufflingShoe,       setReshufflingShoe]       = useState(false);
  const [displayDiscardCount,   setDisplayDiscardCount]   = useState(0);
  const [displayCardsRemaining, setDisplayCardsRemaining] = useState<number | null>(null);

  const [displayPlayerCards,    setDisplayPlayerCards]    = useState<CardString[]>([]);
  const [displayDealerCards,    setDisplayDealerCards]    = useState<CardString[]>([]);
  const [displayBotCards,       setDisplayBotCards]       = useState<CardString[][]>([]);
  const [holePlaceholderVisible,setHolePlaceholderVisible]= useState(false);
  const [dealerHoleRevealed,    setDealerHoleRevealed]    = useState(false);
  const [botActionLabels,       setBotActionLabels]       = useState<Record<number,string>>({});

  const isFirstHandRef     = useRef(true);
  const shoeRef            = useRef<HTMLDivElement>(null);
  const discardRef         = useRef<HTMLDivElement>(null);
  const dealerHandRef      = useRef<HTMLDivElement>(null);
  const seatHandRefs       = useRef<(HTMLDivElement | null)[]>(Array(5).fill(null));
  const animTimersRef      = useRef<ReturnType<typeof setTimeout>[]>([]);
  const resolutionRunIdRef = useRef(0);
  const shouldAnimateRef   = useRef(false);

  const minBet   = parseFloat(table?.min_bet ?? currentGame?.table.min_bet ?? '1');
  const maxBet   = parseFloat(table?.max_bet ?? currentGame?.table.max_bet ?? '9999');
  const betTotal = chipStack.reduce((s, v) => s + v, 0);
  const balance  = parseFloat(user?.balance ?? '0');
  const busy     = !!actionInFlight;
  const lvl      = table?.level ?? currentGame?.table.level ?? 1;
  const inPlay   = phase === 'play' || phase === 'animating' || phase === 'result';

  // ── Auto-place bots at rightmost seats when entering a table ──────────────
  useEffect(() => {
    const n = table?.bot_count ?? 0;
    const seats = n > 0
      ? Array.from({ length: n }, (_, i) => 4 - i).sort((a, b) => a - b)
      : [];
    setBotSeats(seats);
    setPlayerSeat(null);
    setPhase('select');
    setChipStack([]);
    setBetError('');
    isFirstHandRef.current = true;
    setDisplayCardsRemaining(null);
    setDisplayDiscardCount(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableId]);

  useEffect(() => () => { animTimersRef.current.forEach(clearTimeout); }, []);

  // Persist discard count across hands — only reset when reshuffle occurs
  useEffect(() => {
    if (currentGame?.discard_count !== undefined) {
      setDisplayDiscardCount(currentGame.discard_count);
    }
  }, [currentGame?.discard_count]);

  // ── Fires when stand/double resolves to COMPLETED ─────────────────────────
  useEffect(() => {
    if (!currentGame || currentGame.status !== 'COMPLETED') return;
    animTimersRef.current.forEach(clearTimeout);
    animTimersRef.current = [];

    if (shouldAnimateRef.current) {
      shouldAnimateRef.current = false;
      runResolutionAnimation(currentGame);
    } else {
      // Bust from hit — instant reveal
      setDisplayPlayerCards([...currentGame.player_hand.cards]);
      setDisplayDealerCards([...currentGame.dealer_hand.cards]);
      setDisplayBotCards(currentGame.bot_hands.map(b => [...b.cards]));
      setHolePlaceholderVisible(false);
      setDealerHoleRevealed(false);
      setPhase('result');
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [`${currentGame?.game_id}_${currentGame?.status}`]);

  // Show reshuffle alert + shoe flash whenever a reshuffle occurred this hand
  useEffect(() => {
    if (currentGame?.reshuffle_occurred) {
      setReshuffleAlert(true);
      setReshufflingShoe(true);
      const t = setTimeout(() => setReshufflingShoe(false), 800);
      return () => clearTimeout(t);
    }
    if (phase === 'bet' || phase === 'select') {
      setReshuffleAlert(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentGame?.game_id, currentGame?.reshuffle_occurred, phase]);

  // ── Launch a single flying card; calls onLand when animation finishes ─────
  function launchFly(
    card: CardString | null,
    from: HTMLElement,
    to:   HTMLElement,
    dur:  number,
    onLand: () => void,
    prefix = 'fly',
  ) {
    const fr = from.getBoundingClientRect();
    const tr = to.getBoundingClientRect();
    const id = `${prefix}-${Date.now()}-${Math.random()}`;
    setFlyCards(prev => [...prev, {
      id, card, duration: dur,
      sx: fr.left + fr.width  / 2,
      sy: fr.top  + fr.height / 2,
      ex: tr.left + tr.width  / 2,
      ey: tr.top  + tr.height / 2,
      onLand: () => { setFlyCards(p => p.filter(fc => fc.id !== id)); onLand(); },
    }]);
  }

  // ── Seat click: bots are fixed; player click → immediately go to bet ──────
  function handleSeatClick(seatIdx: number) {
    if (phase !== 'select') return;
    if (botSeats.includes(seatIdx)) return;
    if (seatIdx === playerSeat) { setPlayerSeat(null); return; }
    setPlayerSeat(seatIdx);
    setPhase('bet');
  }

  function handleChipClick(value: number) {
    if (betTotal + value > maxBet) { setBetError(`Max bet is ${maxBet.toLocaleString()}`); return; }
    setChipStack(prev => [...prev, value]);
    setBetError('');
  }

  async function handleDeal() {
    if (busy || betTotal < minBet || playerSeat === null) return;
    if (betTotal > maxBet) { setBetError(`Max bet is ${maxBet.toLocaleString()}`); return; }
    setBetError('');
    store.setActionInFlight('create');
    const freshShoe = isFirstHandRef.current;
    isFirstHandRef.current = false;
    let game: GameState | null = null;
    try {
      game = await createGame({
        table_id:  tableId,
        bet:       betTotal.toFixed(2),
        bot_count: botSeats.length,
        ...(ppBet     > 0 ? { perfect_pairs_bet:    ppBet.toFixed(2) }  : {}),
        ...(t21Bet    > 0 ? { twenty_one_three_bet: t21Bet.toFixed(2) } : {}),
        ...(freshShoe     ? { fresh_shoe: true }                        : {}),
      });
      store.setGame(game);
    } catch (err) {
      setBetError((err as ApiError).message);
    } finally {
      store.setActionInFlight(null);
    }
    if (game) startDealAnimation(game);
  }

  // ── Initial deal — fly every card from shoe to seats ──────────────────────
  function startDealAnimation(game: GameState) {
    animTimersRef.current.forEach(clearTimeout);
    animTimersRef.current = [];
    setPhase('animating');
    setDisplayPlayerCards([]);
    setDisplayDealerCards([]);
    setDisplayBotCards(game.bot_hands.map(() => []));
    setHolePlaceholderVisible(false);
    setDealerHoleRevealed(false);

    const totalDealt = game.player_hand.cards.length + game.dealer_hand.cards.length
      + game.bot_hands.reduce((s, b) => s + b.cards.length, 0);
    setDisplayCardsRemaining(game.cards_remaining + totalDealt);

    const pSeat    = playerSeat!;
    const bSeats   = botSeats;
    // Right-to-left: descending seat index matches backend deal order
    const occupied = [...bSeats, pSeat].sort((a, b) => b - a);

    interface Step { dest: number | 'dealer'; card: CardString | null; onLand: () => void; }
    const steps: Step[] = [];

    const decCount = () => setDisplayCardsRemaining(prev => (prev ?? 1) - 1);

    for (let round = 0; round < 2; round++) {
      const r = round;
      for (const seat of occupied) {
        const isP = seat === pSeat;
        const bi  = isP ? -1 : bSeats.indexOf(seat);
        const card = isP ? game.player_hand.cards[r] : game.bot_hands[bi]?.cards[r] ?? null;
        const captBi = bi;
        steps.push({
          dest: seat, card,
          onLand: () => {
            decCount();
            if (isP) {
              setDisplayPlayerCards(game.player_hand.cards.slice(0, r + 1));
            } else {
              setDisplayBotCards(prev => {
                const next = prev.map(a => [...a]);
                next[captBi] = game.bot_hands[captBi].cards.slice(0, r + 1);
                return next;
              });
            }
          },
        });
      }
      if (round === 0) {
        const dc = game.dealer_hand.cards[0];
        steps.push({ dest: 'dealer', card: dc, onLand: () => { decCount(); setDisplayDealerCards([dc]); } });
      } else {
        steps.push({ dest: 'dealer', card: null, onLand: () => { decCount(); setHolePlaceholderVisible(true); } });
      }
    }

    const timers: ReturnType<typeof setTimeout>[] = [];
    steps.forEach((step, i) => {
      timers.push(setTimeout(() => {
        const from = shoeRef.current;
        const to   = step.dest === 'dealer' ? dealerHandRef.current : seatHandRefs.current[step.dest as number];
        if (from && to) launchFly(step.card, from, to, FLY_DURATION, step.onLand, `deal-${i}`);
        else step.onLand();
      }, i * CARD_STAGGER));
    });

    const totalMs = (steps.length - 1) * CARD_STAGGER + FLY_DURATION + 350;
    timers.push(setTimeout(() => runPrePlayerAnimation(game), totalMs));
    animTimersRef.current = timers;
  }

  // ── Pre-player animation — fly bot hit cards right-to-left before unlocking HIT/STAND ──
  function runPrePlayerAnimation(game: GameState) {
    const runId = ++resolutionRunIdRef.current;
    const alive = () => runId === resolutionRunIdRef.current;

    const flyAsync = (card: CardString | null, from: HTMLElement, to: HTMLElement): Promise<void> =>
      new Promise(resolve => launchFly(card, from, to, FLY_DURATION, resolve, `pre-${runId}`));

    const wait = (ms: number): Promise<void> => new Promise(r => setTimeout(r, ms));

    (async () => {
      setBotActionLabels({});
      await wait(300); if (!alive()) return;

      // Right-to-left: bots at higher seat indices act first
      const botOrder = botSeats
        .map((seatIdx, arrayIdx) => ({ seatIdx, arrayIdx }))
        .sort((a, b) => b.seatIdx - a.seatIdx);

      for (const { seatIdx, arrayIdx } of botOrder) {
        if (!alive()) return;
        const bot = game.bot_hands[arrayIdx];
        if (!bot) continue;

        // Cards dealt at game creation = first 2; any beyond are hits played server-side
        const DEAL_COUNT = 2;

        if (bot.status === 'BLACKJACK') {
          setBotActionLabels(prev => ({ ...prev, [arrayIdx]: 'BJ!' }));
          await wait(700); if (!alive()) return;
          setBotActionLabels(prev => { const n = { ...prev }; delete n[arrayIdx]; return n; });
        } else if (bot.cards.length <= DEAL_COUNT) {
          setBotActionLabels(prev => ({ ...prev, [arrayIdx]: 'STAND' }));
          await wait(600); if (!alive()) return;
          setBotActionLabels(prev => { const n = { ...prev }; delete n[arrayIdx]; return n; });
        } else {
          for (let ci = DEAL_COUNT; ci < bot.cards.length; ci++) {
            if (!alive()) return;
            setBotActionLabels(prev => ({ ...prev, [arrayIdx]: 'HIT' }));
            const from = shoeRef.current, to = seatHandRefs.current[seatIdx];
            if (from && to) await flyAsync(bot.cards[ci], from, to);
            else            await wait(FLY_DURATION);
            if (!alive()) return;
            setDisplayCardsRemaining(prev => (prev ?? 1) - 1);
            const count = ci;
            setDisplayBotCards(prev => {
              const next = prev.map(a => [...a]);
              next[arrayIdx] = bot.cards.slice(0, count + 1);
              return next;
            });
            setBotActionLabels(prev => { const n = { ...prev }; delete n[arrayIdx]; return n; });
            await wait(200);
          }
          const finalLabel = bot.status === 'BUST' ? 'BUST' : 'STAND';
          setBotActionLabels(prev => ({ ...prev, [arrayIdx]: finalLabel }));
          await wait(500); if (!alive()) return;
          setBotActionLabels(prev => { const n = { ...prev }; delete n[arrayIdx]; return n; });
        }
        await wait(150); if (!alive()) return;
      }

      await wait(300); if (!alive()) return;
      // All bots done — player's turn
      setPhase('play');
    })();
  }

  // ── Resolution animation — dealer only (bots already played before player's turn) ──
  function runResolutionAnimation(game: GameState) {
    const runId = ++resolutionRunIdRef.current;
    const alive = () => runId === resolutionRunIdRef.current;

    const flyAsync = (card: CardString | null, from: HTMLElement, to: HTMLElement): Promise<void> =>
      new Promise(resolve => launchFly(card, from, to, FLY_DURATION, resolve, `res-${runId}`));

    const wait = (ms: number): Promise<void> => new Promise(r => setTimeout(r, ms));

    (async () => {
      setPhase('animating');
      setBotActionLabels({});
      // Bot cards are already complete from pre-player animation; ensure dealer state correct
      setHolePlaceholderVisible(true);
      setDealerHoleRevealed(false);
      setDisplayDealerCards(game.dealer_hand.cards.slice(0, 1));

      await wait(400); if (!alive()) return;

      // Flip dealer hole card
      setHolePlaceholderVisible(false);
      setDealerHoleRevealed(true);
      setDisplayDealerCards(game.dealer_hand.cards.slice(0, 2));
      await wait(350); if (!alive()) return;

      // Dealer additional hits
      for (let ci = 2; ci < game.dealer_hand.cards.length; ci++) {
        if (!alive()) return;
        const from = shoeRef.current, to = dealerHandRef.current;
        if (from && to) await flyAsync(game.dealer_hand.cards[ci], from, to);
        else            await wait(FLY_DURATION);
        if (!alive()) return;
        setDisplayCardsRemaining(prev => (prev ?? 1) - 1);
        setDisplayDealerCards(game.dealer_hand.cards.slice(0, ci + 1));
        await wait(200);
      }

      await wait(900); if (!alive()) return;
      setPhase('result');
    })();
  }

  // ── Hit — fly the new card from shoe to player seat ───────────────────────
  async function handleHit() {
    if (!currentGame || busy) return;
    shouldAnimateRef.current = false;
    store.setActionInFlight('hit');
    let game: GameState | null = null;
    try {
      game = await hit(currentGame.game_id);
      store.setGame(game);
    } catch (err) {
      store.setGlobalError((err as ApiError).message);
    } finally {
      store.setActionInFlight(null);
    }
    if (!game) return;

    const newCard = game.player_hand.cards[game.player_hand.cards.length - 1];
    const from    = shoeRef.current;
    const to      = playerSeat !== null ? seatHandRefs.current[playerSeat] : null;

    if (from && to && newCard) {
      launchFly(newCard, from, to, FLY_DURATION, () => {
        setDisplayCardsRemaining(prev => (prev ?? 1) - 1);
        setDisplayPlayerCards([...game!.player_hand.cards]);
      }, 'hit');
    } else {
      setDisplayCardsRemaining(prev => (prev ?? 1) - 1);
      setDisplayPlayerCards([...game.player_hand.cards]);
    }
  }

  async function handleStand() {
    if (!currentGame || busy) return;
    shouldAnimateRef.current = true;
    store.setActionInFlight('stand');
    try {
      const game = await stand(currentGame.game_id);
      store.setGame(game);
    } catch (err) {
      store.setGlobalError((err as ApiError).message);
      shouldAnimateRef.current = false;
    } finally {
      store.setActionInFlight(null);
    }
  }

  async function handleDouble() {
    if (!currentGame || busy) return;
    shouldAnimateRef.current = true;
    store.setActionInFlight('double');
    let game: GameState | null = null;
    try {
      game = await doubleDown(currentGame.game_id);
      store.setGame(game);
    } catch (err) {
      store.setGlobalError((err as ApiError).message);
      shouldAnimateRef.current = false;
    } finally {
      store.setActionInFlight(null);
    }
    if (!game) return;
    const newCard = game.player_hand.cards[game.player_hand.cards.length - 1];
    const from    = shoeRef.current;
    const to      = playerSeat !== null ? seatHandRefs.current[playerSeat] : null;
    if (from && to && newCard) {
      launchFly(newCard, from, to, FLY_DURATION, () => {
        setDisplayCardsRemaining(prev => (prev ?? 1) - 1);
        setDisplayPlayerCards([...game!.player_hand.cards]);
      }, 'double');
    } else {
      setDisplayCardsRemaining(prev => (prev ?? 1) - 1);
      setDisplayPlayerCards([...game.player_hand.cards]);
    }
  }

  // ── Next hand — fly all cards back to shoe, then reset to bet ─────────────
  function handleNextHand() {
    animTimersRef.current.forEach(clearTimeout);
    animTimersRef.current = [];
    resolutionRunIdRef.current++;
    setBotActionLabels({});

    const discardEl = discardRef.current ?? shoeRef.current;
    if (!discardEl) { doResetNextHand(); return; }

    const dr = discardEl.getBoundingClientRect();
    const ex = dr.left + dr.width  / 2;
    const ey = dr.top  + dr.height / 2;

    const sources: Array<{ ref: HTMLElement | null; count: number }> = [];
    if (playerSeat !== null)
      sources.push({ ref: seatHandRefs.current[playerSeat], count: displayPlayerCards.length });
    botSeats.forEach((seatIdx, ai) =>
      sources.push({ ref: seatHandRefs.current[seatIdx], count: (displayBotCards[ai] ?? []).length }));
    sources.push({
      ref: dealerHandRef.current,
      count: displayDealerCards.length + (holePlaceholderVisible ? 1 : 0),
    });

    const newFly: FlyCard[] = [];
    sources.forEach(({ ref, count }) => {
      if (!ref || count === 0) return;
      const tr = ref.getBoundingClientRect();
      for (let i = 0; i < Math.min(count, 7); i++) {
        const id = `discard-${Date.now()}-${Math.random()}`;
        newFly.push({
          id, card: null, duration: DISCARD_DUR,
          sx: tr.left + tr.width  / 2 + (i - 1) * 8,
          sy: tr.top  + tr.height / 2,
          ex, ey,
          onLand: () => setFlyCards(p => p.filter(fc => fc.id !== id)),
        });
      }
    });

    setFlyCards(prev => [...prev, ...newFly]);
    setDisplayPlayerCards([]);
    setDisplayDealerCards([]);
    setDisplayBotCards(botSeats.map(() => []));
    setHolePlaceholderVisible(false);
    setDealerHoleRevealed(false);

    const t = setTimeout(() => { setFlyCards([]); doResetNextHand(); }, DISCARD_DUR + 200);
    animTimersRef.current = [t];
  }

  function doResetNextHand() {
    setPhase('bet');
    setChipStack([]);
    setPpBet(0);
    setT21Bet(0);
    setBetError('');
    setReshuffleAlert(false);
    setReshufflingShoe(false);
    setDisplayCardsRemaining(null);
    store.clearGame();
  }

  async function handleLeaveTable() {
    animTimersRef.current.forEach(clearTimeout);
    animTimersRef.current = [];
    resolutionRunIdRef.current++;
    setFlyCards([]);

    if (currentGame?.status === 'IN_PROGRESS') {
      store.setActionInFlight('leave');
      try {
        const result = await leaveGame(currentGame.game_id);
        store.setGame(result);
      } catch { /* leave anyway */ }
      finally { store.setActionInFlight(null); }
    }
    store.clearGame();
    onLeaveTable();
  }

  function getSeatType(i: number): 'player' | 'bot' | 'empty' {
    if (i === playerSeat)     return 'player';
    if (botSeats.includes(i)) return 'bot';
    return 'empty';
  }

  // Dealer value tracks only the currently-revealed cards during animation
  const { value: dealerLiveValue, isSoft: dealerLiveSoft } = computeHandValue(displayDealerCards);

  // ══════════════════════════════════════════════════════════════════════════
  return (
    <div className={styles.table}>

      {/* Top bar */}
      <div className={styles.topBar}>
        <button className={styles.backBtn} onClick={handleLeaveTable} disabled={busy && phase === 'animating'}>
          ← Lobby
        </button>
        <div className={styles.tableTitle}>
          <span className={styles.tableTitleText}>
            Royal Table · Level {lvl}
            {currentGame && ` · ${currentGame.table.bot_count} bot${currentGame.table.bot_count !== 1 ? 's' : ''}`}
          </span>
        </div>
        <div className={styles.balanceDisplay}>
          <span className={styles.balanceLabel}>Balance</span>
          <span className={`${styles.balanceValue} ${balance < 0 ? styles.balanceNeg : ''}`}>
            {balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </span>
        </div>
      </div>

      {/* Game area */}
      <div className={styles.gameArea}>
        <div className={styles.tableArena}>

          {/* Discard pile (top-left, opposite shoe) */}
          <div className={styles.discardContainer}>
            <div
              ref={discardRef}
              className={[
                styles.discard,
                displayDiscardCount > 0 ? styles.discardHasCards : '',
              ].filter(Boolean).join(' ')}
              aria-hidden
            />
            <span className={styles.discardLabel}>
              {displayDiscardCount > 0 ? `${displayDiscardCount} discarded` : 'DISCARD'}
            </span>
          </div>

          {/* Shoe */}
          <div className={styles.shoeContainer}>
            <div
              ref={shoeRef}
              className={[styles.shoe, reshufflingShoe ? styles.shoeReshuffling : ''].filter(Boolean).join(' ')}
              aria-hidden
            />
            <span className={styles.shoeLabel}>
              {displayCardsRemaining !== null
                ? `${displayCardsRemaining} cards`
                : currentGame
                  ? `${currentGame.cards_remaining} cards`
                  : 'SHOE'}
            </span>
          </div>

          {/* Dealer */}
          <div className={styles.dealerZone}>
            <span className={styles.dealerLabel}>Dealer</span>
            <div ref={dealerHandRef} className={styles.dealerHand}>
              {inPlay && (
                <>
                  {displayDealerCards.map((card, i) => (
                    <Card key={`d-${card}-${i}`} card={card} small flipReveal={dealerHoleRevealed && i === 1} />
                  ))}
                  {holePlaceholderVisible && <Card faceDown small animateIn />}
                </>
              )}
            </div>
            {inPlay && displayDealerCards.length > 0 && (
              <HandValue
                value={phase === 'result' ? (currentGame?.dealer_hand.value ?? dealerLiveValue) : dealerLiveValue}
                status={phase === 'result' ? (currentGame?.dealer_hand.status ?? 'ACTIVE') : 'ACTIVE'}
                isSoft={dealerLiveSoft}
              />
            )}
          </div>

          {/* 5 seats in arc */}
          <div className={styles.seatsSection}>
            {([0, 1, 2, 3, 4] as const).map(seatIdx => {
              const seatType    = getSeatType(seatIdx);
              const isPlayer    = seatType === 'player';
              const isBotSeat   = seatType === 'bot';
              const botArrayIdx = isBotSeat ? botSeats.indexOf(seatIdx) : -1;
              const displayNum  = 5 - seatIdx; // right-to-left: rightmost = Seat 1

              return (
                <div
                  key={seatIdx}
                  className={styles.seat}
                  style={{ transform: `translateY(${ARC_OFFSETS[seatIdx]}px)` }}
                >
                  {/* Bot action label (HIT / STAND during resolution) */}
                  {isBotSeat && botActionLabels[botArrayIdx] && (
                    <div className={styles.botActionLabel}>{botActionLabels[botArrayIdx]}</div>
                  )}

                  {/* Ref target for fly destinations */}
                  <div ref={el => { seatHandRefs.current[seatIdx] = el; }} className={styles.seatInner}>

                    {/* SELECT / BET: slot circles */}
                    {!inPlay && (
                      <div
                        className={[
                          styles.seatSlot,
                          phase === 'select' && !isBotSeat ? styles.seatSlotClickable : '',
                          isPlayer  ? styles.seatSlotPlayer : '',
                          isBotSeat ? styles.seatSlotBot    : '',
                        ].filter(Boolean).join(' ')}
                        onClick={() => handleSeatClick(seatIdx)}
                        role={phase === 'select' && !isBotSeat ? 'button' : undefined}
                        tabIndex={phase === 'select' && !isBotSeat ? 0 : -1}
                        onKeyDown={e => { if (e.key === 'Enter') handleSeatClick(seatIdx); }}
                        aria-label={
                          isBotSeat ? `Seat ${displayNum}: Bot` :
                          isPlayer  ? `Seat ${displayNum}: Your seat` :
                                      `Seat ${displayNum}: Empty — click to sit`
                        }
                      >
                        {isPlayer  && <span className={styles.seatSlotIcon}>YOU</span>}
                        {isBotSeat && <span className={styles.seatSlotBotIcon}>BOT</span>}
                        {!isPlayer && !isBotSeat && phase === 'select' && (
                          <span className={styles.seatSlotPlus}>+</span>
                        )}
                      </div>
                    )}

                    {/* PLAY / ANIMATING / RESULT: card hands */}
                    {inPlay && (
                      <div className={styles.seatCards}>
                        {isPlayer && displayPlayerCards.map((card, ci) => (
                          <Card key={`p-${card}-${ci}`} card={card} small />
                        ))}
                        {isBotSeat && (displayBotCards[botArrayIdx] ?? []).map((card, ci) => (
                          <Card key={`b${botArrayIdx}-${ci}-${card}`} card={card} small />
                        ))}
                        {!isPlayer && !isBotSeat && <div className={styles.emptySeatDot} />}
                      </div>
                    )}
                  </div>

                  {/* Chip stack visualization */}
                  {inPlay && isPlayer && chipStack.length > 0 && (
                    <div className={styles.chipStack}>
                      {[...chipStack].reverse().map((val, i) => (
                        <div key={i} className={styles.chipDisc} style={{ background: chipColor(val) }} />
                      ))}
                    </div>
                  )}
                  {inPlay && isBotSeat && (
                    <div className={styles.chipStack}>
                      <div className={`${styles.chipDisc} ${styles.chipDiscBot}`} />
                      <div className={`${styles.chipDisc} ${styles.chipDiscBot}`} />
                      <div className={`${styles.chipDisc} ${styles.chipDiscBot}`} />
                    </div>
                  )}

                  {/* Seat label */}
                  <span className={[
                    styles.seatLabel,
                    isPlayer  ? styles.seatLabelPlayer : '',
                    isBotSeat ? styles.seatLabelBot    : '',
                  ].filter(Boolean).join(' ')}>
                    {isPlayer ? 'YOU' : isBotSeat ? 'BOT' : `Seat ${displayNum}`}
                  </span>

                  {/* Hand value badge */}
                  {inPlay && isPlayer && displayPlayerCards.length >= 2 && currentGame && (
                    <HandValue
                      value={currentGame.player_hand.value}
                      status={currentGame.player_hand.status}
                      isSoft={currentGame.player_hand.is_soft}
                      small
                    />
                  )}
                  {phase === 'result' && isBotSeat && currentGame && (
                    <HandValue
                      value={currentGame.bot_hands[botArrayIdx]?.value ?? 0}
                      status={currentGame.bot_hands[botArrayIdx]?.status ?? 'ACTIVE'}
                      small
                    />
                  )}

                  {/* Bet badge */}
                  {phase === 'bet' && isPlayer && betTotal > 0 && (
                    <span className={styles.seatBetBadge}>${betTotal.toLocaleString()}</span>
                  )}

                  {/* Side bet circles on the felt (player seat only) */}
                  {isPlayer && (ppBet > 0 || t21Bet > 0 || currentGame?.side_bets?.perfect_pairs || currentGame?.side_bets?.twenty_one_three) && (
                    <div className={styles.sideBetCircles}>
                      {(ppBet > 0 || currentGame?.side_bets?.perfect_pairs) && (
                        <SideBetCircle
                          label="PP"
                          amount={ppBet}
                          result={currentGame?.side_bets?.perfect_pairs}
                        />
                      )}
                      {(t21Bet > 0 || currentGame?.side_bets?.twenty_one_three) && (
                        <SideBetCircle
                          label="21+3"
                          amount={t21Bet}
                          result={currentGame?.side_bets?.twenty_one_three}
                        />
                      )}
                    </div>
                  )}

                  {/* Inline result */}
                  {phase === 'result' && isPlayer && currentGame && <SeatResult game={currentGame} />}
                </div>
              );
            })}
          </div>
        </div>

        {/* Action area */}
        <div className={styles.actionArea}>

          {phase === 'select' && (
            <div className={styles.selectActions}>
              <p className={styles.selectHint}>
                {botSeats.length > 0
                  ? `${botSeats.length} bot${botSeats.length !== 1 ? 's' : ''} seated — pick your spot`
                  : 'Click a seat to join the table'}
              </p>
            </div>
          )}

          {phase === 'bet' && (
            <div className={styles.betArea}>
              <div className={styles.betDisplay}>
                <span className={styles.betLabel}>Bet</span>
                <span className={styles.betAmount}>{betTotal > 0 ? `$${betTotal.toLocaleString()}` : '—'}</span>
                {betTotal > 0 && (
                  <>
                    <button className={styles.undoBtn} onClick={() => setChipStack(p => p.slice(0, -1))} title="Undo last chip">↩</button>
                    <button className={styles.clearBtn} onClick={() => setChipStack([])} title="Clear bet">✕</button>
                  </>
                )}
              </div>
              <div className={styles.chipTray}>
                {CHIPS.map(chip => (
                  <button
                    key={chip.value}
                    className={`${styles.chip} ${chip.cls}`}
                    onClick={() => handleChipClick(chip.value)}
                    disabled={busy || betTotal + chip.value > maxBet}
                    aria-label={`Add ${chip.label} chip`}
                  >
                    {chip.label}
                  </button>
                ))}
              </div>
              {/* Side bets */}
              <div className={styles.sideBetRow}>
                <SideBetInput
                  label="Perfect Pairs"
                  sublabel="Your 2 cards form a pair"
                  bet={ppBet}
                  onAdd={v => setPpBet(p => Math.min(p + v, maxBet))}
                  onClear={() => setPpBet(0)}
                  disabled={busy}
                />
                <SideBetInput
                  label="21+3"
                  sublabel="Your cards + dealer's up-card"
                  bet={t21Bet}
                  onAdd={v => setT21Bet(p => Math.min(p + v, maxBet))}
                  onClear={() => setT21Bet(0)}
                  disabled={busy}
                />
              </div>

              {betError && <p className={styles.betErrorMsg}>{betError}</p>}
              <div className={styles.betActions}>
                <button className={styles.backToSelectBtn} onClick={() => { setPlayerSeat(null); setPhase('select'); }}>
                  ← Change Seat
                </button>
                <button
                  className={styles.dealBtn}
                  onClick={handleDeal}
                  disabled={busy || betTotal < minBet || betTotal > maxBet}
                >
                  {actionInFlight === 'create'
                    ? <ButtonSpinner />
                    : `Deal  ($${betTotal > 0 ? betTotal.toLocaleString() : '0'})`}
                </button>
              </div>
            </div>
          )}

          {phase === 'animating' && (
            <p className={styles.animatingLabel} aria-live="polite">
              {currentGame?.status === 'COMPLETED' ? 'Revealing cards…' : 'Dealing…'}
            </p>
          )}

          {phase === 'play' && currentGame?.status === 'IN_PROGRESS' && (
            <div className={styles.playActions}>
              <div className={styles.activeBet}>
                Bet: <strong>${parseFloat(currentGame.player_bet).toLocaleString()}</strong>
              </div>
              <div className={styles.actionBtns}>
                <ActionBtn label="Hit"    onClick={handleHit}    disabled={busy} loading={actionInFlight === 'hit'}    variant="primary"   />
                <ActionBtn label="Stand"  onClick={handleStand}  disabled={busy} loading={actionInFlight === 'stand'}  variant="secondary" />
                <ActionBtn
                  label="Double"
                  onClick={handleDouble}
                  disabled={busy || currentGame.player_hand.cards.length !== 2}
                  loading={actionInFlight === 'double'}
                  variant="ghost"
                  title={currentGame.player_hand.cards.length !== 2 ? 'Only on first two cards' : undefined}
                />
              </div>
            </div>
          )}

          {phase === 'result' && currentGame && (
            <div className={styles.resultActions}>
              <div className={styles.scoreSummaryRow}>
                <ScoreEntry label="You"    value={currentGame.player_hand.value} status={currentGame.player_hand.status} />
                <ScoreEntry label="Dealer" value={currentGame.dealer_hand.value} status={currentGame.dealer_hand.status} />
                {currentGame.bot_hands.map(b => (
                  <ScoreEntry key={b.bot_index} label={`Bot ${b.bot_index + 1}`} value={b.value} status={b.status} />
                ))}
              </div>
              {currentGame.new_balance && (
                <p className={styles.newBalanceLine}>
                  Balance: <strong>${parseFloat(currentGame.new_balance).toLocaleString('en-US', { minimumFractionDigits: 2 })}</strong>
                </p>
              )}
              {reshuffleAlert && (
                <div className={styles.reshuffleAlert}>
                  ♻ Shoe reshuffled — fresh 312-card deck in play
                </div>
              )}
              <button className={styles.nextHandBtn} onClick={handleNextHand}>Next Hand</button>
            </div>
          )}
        </div>
      </div>

      {flyCards.map(fc => <FlyingCard key={fc.id} data={fc} />)}

      {store.globalError && (
        <div className={styles.errorToast} role="alert">
          {store.globalError}
          <button className={styles.errorDismiss} onClick={() => store.setGlobalError(null)}>✕</button>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   SUB-COMPONENTS
═══════════════════════════════════════════════════════════════════════════════ */

function FlyingCard({ data }: { data: FlyCard }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const anim = el.animate(
      [
        { transform: `translate(0,0) rotate(-5deg) scale(0.8)`, opacity: 0.8 },
        { transform: `translate(${data.ex - data.sx}px,${data.ey - data.sy}px) rotate(0deg) scale(1)`, opacity: 1 },
      ],
      { duration: data.duration, easing: 'cubic-bezier(0.25,0.46,0.45,0.94)', fill: 'forwards' },
    );
    anim.onfinish = data.onLand;
    return () => anim.cancel();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return createPortal(
    <div ref={ref} style={{ position: 'fixed', left: data.sx, top: data.sy, zIndex: 9999, pointerEvents: 'none' }}>
      <Card card={data.card ?? undefined} faceDown={!data.card} small />
    </div>,
    document.body,
  );
}

function SeatResult({ game }: { game: GameState }) {
  const payout = parseFloat(game.net_payout ?? '0');
  const label  = ({ WIN: 'WIN!', BLACKJACK: 'BLACKJACK!', PUSH: 'PUSH', LOSE: 'LOSE' })[game.outcome ?? 'LOSE'] ?? 'DONE';
  const resultCls = [
    styles.seatResult,
    game.outcome === 'WIN' || game.outcome === 'BLACKJACK' ? styles.seatResultWin :
    game.outcome === 'PUSH'                                ? styles.seatResultPush : styles.seatResultLose,
  ].join(' ');
  const payoutCls = payout > 0 ? styles.payoutPos : payout < 0 ? styles.payoutNeg : styles.payoutNeu;
  const payoutStr = payout >= 0
    ? `+$${payout.toLocaleString('en-US', { minimumFractionDigits: 2 })}`
    : `-$${Math.abs(payout).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
  return (
    <>
      <div className={resultCls}>{label}</div>
      <div className={`${styles.seatPayoutLine} ${payoutCls}`}>{payoutStr}</div>
    </>
  );
}

interface HandValueProps { value: number; status: string; isSoft?: boolean; small?: boolean; }
function HandValue({ value, status, isSoft = false, small = false }: HandValueProps) {
  const label =
    status === 'BLACKJACK' ? 'BJ!' :
    status === 'BUST'      ? 'BUST' :
    isSoft                 ? `Soft ${value}` : String(value);
  const cls = [
    styles.handValue,
    small                  ? styles.handValueSmall : '',
    status === 'BUST'      ? styles.handValueBust  : '',
    status === 'BLACKJACK' ? styles.handValueBJ    : '',
  ].filter(Boolean).join(' ');
  return <span className={cls}>{label}</span>;
}

interface ActionBtnProps { label: string; onClick: () => void; disabled: boolean; loading: boolean; variant: 'primary'|'secondary'|'ghost'; title?: string; }
function ActionBtn({ label, onClick, disabled, loading, variant, title }: ActionBtnProps) {
  return (
    <button
      className={`${styles.actionBtn} ${styles[`actionBtn_${variant}`]}`}
      onClick={onClick} disabled={disabled || loading} title={title} aria-label={label}
    >
      {loading ? <ButtonSpinner /> : label}
    </button>
  );
}

function ScoreEntry({ label, value, status }: { label: string; value: number; status: string }) {
  const valCls = [styles.scoreEntryValue, status === 'BUST' ? styles.scoreBust : '', status === 'BLACKJACK' ? styles.scoreBJ : ''].filter(Boolean).join(' ');
  return (
    <div className={styles.scoreEntry}>
      <span className={styles.scoreEntryLabel}>{label}</span>
      <span className={valCls}>{status === 'BLACKJACK' ? 'BJ' : status === 'BUST' ? 'Bust' : value}</span>
    </div>
  );
}

function ButtonSpinner() { return <span className={styles.btnSpinner} aria-hidden />; }

// ── Side bet circle rendered on the table felt ────────────────────────────────
interface SideBetCircleProps { label: string; amount: number; result?: SideBetResult; }
function SideBetCircle({ label, amount, result }: SideBetCircleProps) {
  const won = result && result.outcome !== null;
  const payout = result ? parseFloat(result.net_payout) : 0;
  const cls = [
    styles.sideBetCircle,
    result && won  ? styles.sideBetCircleWin  : '',
    result && !won ? styles.sideBetCircleLose : '',
  ].filter(Boolean).join(' ');
  return (
    <div className={cls}>
      <span className={styles.sideBetCircleLabel}>{label}</span>
      {!result && amount > 0 && <span className={styles.sideBetCircleAmount}>${amount}</span>}
      {result && (
        <span className={styles.sideBetCircleOutcome}>
          {won
            ? (payout >= 0
                ? `+$${payout.toFixed(2)}`
                : `-$${Math.abs(payout).toFixed(2)}`)
            : 'LOSE'}
        </span>
      )}
    </div>
  );
}

// ── Side bet chip input (bet phase, action area) ──────────────────────────────
interface SideBetInputProps {
  label: string; sublabel: string;
  bet: number; onAdd: (v: number) => void; onClear: () => void;
  disabled: boolean;
}
const SIDE_CHIPS = [5, 10, 25] as const;
function SideBetInput({ label, sublabel, bet, onAdd, onClear, disabled }: SideBetInputProps) {
  return (
    <div className={styles.sideBetInput}>
      <div className={styles.sideBetInputHeader}>
        <span className={styles.sideBetInputLabel}>{label}</span>
        <span className={styles.sideBetInputSub}>{sublabel}</span>
        {bet > 0 && (
          <span className={styles.sideBetInputAmount}>${bet}</span>
        )}
      </div>
      <div className={styles.sideBetChips}>
        {SIDE_CHIPS.map(v => (
          <button
            key={v}
            className={styles.sideBetChipBtn}
            onClick={() => onAdd(v)}
            disabled={disabled}
          >
            +${v}
          </button>
        ))}
        {bet > 0 && (
          <button className={styles.sideBetClearBtn} onClick={onClear} disabled={disabled}>
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
