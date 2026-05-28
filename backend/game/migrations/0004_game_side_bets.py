# Generated manually 2026-05-22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0003_add_discard_and_needs_reshuffle'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='side_bets',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Side bets resolved immediately after the initial deal. "
                    "Keys: 'perfect_pairs', 'twenty_one_three'. "
                    "Each value: {bet, outcome, net_payout}."
                ),
            ),
        ),
    ]
