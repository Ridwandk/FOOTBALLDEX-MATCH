from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bd_models", "0009_ballinstance_deleted_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="ball",
            name="position",
            field=models.CharField(
                choices=[
                    ("GK", "Goalkeeper"),
                    ("CB", "Centre-Back"),
                    ("LB", "Left-Back"),
                    ("RB", "Right-Back"),
                    ("LWB", "Left Wing-Back"),
                    ("RWB", "Right Wing-Back"),
                    ("CDM", "Defensive Midfielder"),
                    ("CM", "Central Midfielder"),
                    ("CAM", "Attacking Midfielder"),
                    ("LM", "Left Midfielder"),
                    ("RM", "Right Midfielder"),
                    ("LW", "Left Winger"),
                    ("RW", "Right Winger"),
                    ("ST", "Striker"),
                    ("CF", "Centre-Forward"),
                ],
                default="ST",
                help_text="Footballer's position, used for the Starting XI match system",
                max_length=4,
            ),
        ),
        migrations.CreateModel(
            name="XiTeam",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(help_text="Name given to this saved team", max_length=32)),
                ("formation", models.CharField(default="4-3-3", max_length=16)),
                ("tactic", models.CharField(default="Balanced", max_length=16)),
                (
                    "slots",
                    models.JSONField(
                        default=dict,
                        help_text=(
                            "Maps slot index (0-10, 0 is always GK) to the BallInstance id "
                            "placed in that slot."
                        ),
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=False, help_text="Whether this is the player's active match team"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="xi_teams",
                        to="bd_models.player",
                    ),
                ),
            ],
            options={
                "managed": True,
                "db_table": "xiteam",
                "unique_together": {("player", "name")},
            },
        ),
        migrations.CreateModel(
            name="MatchGame",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("guild_id", models.BigIntegerField(help_text="Discord guild ID the match was played in")),
                ("channel_id", models.BigIntegerField(blank=True, null=True)),
                ("score1", models.SmallIntegerField(default=0)),
                ("score2", models.SmallIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("declined", "Declined"),
                            ("expired", "Expired"),
                            ("completed", "Completed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "player1",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="matches_as_p1",
                        to="bd_models.player",
                    ),
                ),
                (
                    "player2",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="matches_as_p2",
                        to="bd_models.player",
                    ),
                ),
                (
                    "team1",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="bd_models.xiteam",
                    ),
                ),
                (
                    "team2",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="bd_models.xiteam",
                    ),
                ),
            ],
            options={
                "managed": True,
                "db_table": "matchgame",
            },
        ),
        migrations.CreateModel(
            name="MatchEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("minute", models.SmallIntegerField()),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("goal", "Goal"),
                            ("miss", "Miss"),
                            ("save", "Save"),
                            ("card", "Card"),
                            ("foul", "Foul"),
                        ],
                        max_length=8,
                    ),
                ),
                ("description", models.TextField()),
                (
                    "order",
                    models.SmallIntegerField(default=0, help_text="Tie-break ordering within a minute"),
                ),
                (
                    "match",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="bd_models.matchgame",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="bd_models.xiteam",
                    ),
                ),
                (
                    "ball_instance",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="bd_models.ballinstance",
                    ),
                ),
                (
                    "related_ball_instance",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="bd_models.ballinstance",
                    ),
                ),
            ],
            options={
                "managed": True,
                "db_table": "matchevent",
                "ordering": ["minute", "order"],
            },
        ),
    ]
