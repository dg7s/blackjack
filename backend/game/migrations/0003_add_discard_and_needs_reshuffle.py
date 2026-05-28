# Generated manually 2026-05-22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0002_add_left_at_and_table_shoe'),
    ]

    operations = [
        migrations.AddField(
            model_name='table',
            name='discard_state',
            field=models.JSONField(
                default=list,
                help_text='Cards played in previous hands, waiting to be reshuffled into the shoe.',
            ),
        ),
        migrations.AddField(
            model_name='table',
            name='needs_reshuffle',
            field=models.BooleanField(
                default=False,
                help_text='Set True when the CUT sentinel is drawn. Triggers reshuffle before the next hand.',
            ),
        ),
    ]
