"""
game/management/commands/seed_tables.py
========================================
Management command to populate the Table model with the initial level
configuration. Run once after the first migration:

    python manage.py seed_tables

The command is idempotent — running it multiple times will not create
duplicates (it uses update_or_create).

Table design rationale
----------------------
Level 0 — No bots, lowest stakes. Always accessible.
           Purpose: Learning / warming up without card-drain pressure.

Level 1 — 1 bot. Deck has mild disruption.
           Unlocks after earning a modest profit above the starting balance.

Level 2 — 2 bots. Card counting becomes noticeably harder.
           Unlocks at a mid-tier balance.

Level 3 — 3 bots. High-stakes, high bet ceiling.
           Unlocks only once the player is firmly profitable.

Level 4 — 4 bots. Expert table, maximum bets.
           Unlocks at the highest balance threshold. The leaderboard at
           this level separates serious players.

Adjust any of the numeric values here — this is the single source of
truth for the game economy. No code changes needed elsewhere.
"""

from decimal import Decimal
from django.core.management.base import BaseCommand

from game.models import Table
from game.services import build_shoe_with_cut


class Command(BaseCommand):
    help = "Seed the database with the default Blackjack table levels."

    # ──────────────────────────────────────────────────────────────────────────
    # Table configuration
    #
    # Each entry is a dict matching the Table model fields.
    # unlock_balance of 0.00 means always accessible.
    # ──────────────────────────────────────────────────────────────────────────
    TABLES: list[dict] = [
        {
            "level": 0,
            "bot_count": 0,
            "min_bet": Decimal("10.00"),
            "max_bet": Decimal("100.00"),
            "unlock_balance": Decimal("0.00"),      # Always accessible
        },
        {
            "level": 1,
            "bot_count": 1,
            "min_bet": Decimal("20.00"),
            "max_bet": Decimal("250.00"),
            "unlock_balance": Decimal("1200.00"),   # 200 profit over starting 1000
        },
        {
            "level": 2,
            "bot_count": 2,
            "min_bet": Decimal("50.00"),
            "max_bet": Decimal("500.00"),
            "unlock_balance": Decimal("2000.00"),
        },
        {
            "level": 3,
            "bot_count": 3,
            "min_bet": Decimal("100.00"),
            "max_bet": Decimal("1000.00"),
            "unlock_balance": Decimal("5000.00"),
        },
        {
            "level": 4,
            "bot_count": 4,
            "min_bet": Decimal("250.00"),
            "max_bet": Decimal("5000.00"),
            "unlock_balance": Decimal("15000.00"),
        },
    ]

    def handle(self, *args, **kwargs) -> None:  # type: ignore[override]
        self.stdout.write(self.style.MIGRATE_HEADING("Seeding Blackjack tables…"))

        created_count = 0
        updated_count = 0

        for table_data in self.TABLES:
            level = table_data.pop("level")
            obj, created = Table.objects.update_or_create(
                level=level,
                defaults=table_data,
            )
            # Restore level for the next iteration (update_or_create pops it)
            table_data["level"] = level

            action = "Created" if created else "Updated"
            color = self.style.SUCCESS if created else self.style.WARNING
            self.stdout.write(
                color(
                    f"  {action}: Level {obj.level} — "
                    f"{obj.bot_count} bot(s), "
                    f"bet {obj.min_bet}–{obj.max_bet}, "
                    f"unlock at {obj.unlock_balance}"
                )
            )

            # Initialise the shoe only when first created or when empty
            if created or not obj.shoe_state:
                obj.shoe_state = build_shoe_with_cut()
                obj.save(update_fields=["shoe_state"])
                self.stdout.write(
                    self.style.SUCCESS(f"    → Shoe initialised ({len(obj.shoe_state)} cards)")
                )

            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created_count} table(s) created, "
                f"{updated_count} table(s) already existed (updated)."
            )
        )