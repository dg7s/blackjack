/**
 * src/components/Card.tsx
 * =======================
 * Renders a single playing card.
 *
 * Props
 * -----
 * card        CardString | undefined   — undefined or omitted = face-down
 * small       boolean                  — compact size for bot hands
 * animateIn   boolean                  — triggers the deal-in CSS animation
 * faceDown    boolean                  — explicit override to force face-down
 */

import type { CSSProperties } from 'react';
import { parseCard, RANK_DISPLAY, SUIT_SYMBOL, isRedSuit } from '../types';
import type { CardString } from '../types';
import styles from './Card.module.css';

interface CardProps {
  card?:        CardString;
  small?:       boolean;
  animateIn?:   boolean;
  faceDown?:    boolean;
  flipReveal?:  boolean;  // flip from face-down to face-up (hole card reveal)
}

export default function Card({ card, small = false, animateIn = false, faceDown = false, flipReveal = false }: CardProps) {
  const isHidden = faceDown || !card;

  const cls = [
    styles.card,
    small       ? styles.small   : '',
    isHidden    ? styles.faceDown : styles.faceUp,
    animateIn   ? styles.dealIn  : '',
    flipReveal  ? styles.flipIn  : '',
  ].filter(Boolean).join(' ');

  if (isHidden) {
    return (
      <div className={cls} aria-label="Face-down card">
        <div className={styles.backPattern}>
          <div className={styles.backDiamond} />
        </div>
      </div>
    );
  }

  const { rank, suit } = parseCard(card);
  const red = isRedSuit(suit);
  const rankLabel = RANK_DISPLAY[rank];
  const suitSymbol = SUIT_SYMBOL[suit];

  const colorStyle: CSSProperties = {
    color: red ? 'var(--card-red)' : 'var(--card-black)',
  };

  return (
    <div className={cls} aria-label={`${rankLabel} of ${suitSymbol}`}>
      {/* Top-left corner */}
      <div className={styles.corner} style={colorStyle}>
        <span className={styles.cornerRank}>{rankLabel}</span>
        <span className={styles.cornerSuit}>{suitSymbol}</span>
      </div>

      {/* Centre suit */}
      <div className={styles.center} style={colorStyle}>
        {suitSymbol}
      </div>

      {/* Bottom-right corner (rotated 180°) */}
      <div className={`${styles.corner} ${styles.cornerBottom}`} style={colorStyle}>
        <span className={styles.cornerRank}>{rankLabel}</span>
        <span className={styles.cornerSuit}>{suitSymbol}</span>
      </div>
    </div>
  );
}
