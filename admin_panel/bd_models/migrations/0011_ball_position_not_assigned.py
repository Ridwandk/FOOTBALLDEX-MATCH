from django.db import migrations, models

POSITION_CHOICES = [
    ("N/A", "Not Assigned"),
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
]


def reset_untouched_defaults(apps, schema_editor):
    """
    Migration 0010 gave every existing Ball a position of "ST" by default, since that
    was the field's default at the time. Nobody has had a chance to review/curate real
    positions yet, so this blanket-resets every "ST" row back to "N/A" - i.e. it assumes
    none of them have been manually set on purpose since 0010 ran.

    If you HAVE already manually gone through and set some real strikers to "ST" via the
    admin panel or Django shell before running this migration, skip this data migration
    (comment out the RunPython call below) or those will get reset to "N/A" too.
    """
    Ball = apps.get_model("bd_models", "Ball")
    Ball.objects.filter(position="ST").update(position="N/A")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("bd_models", "0010_ball_position_xiteam_matchgame_matchevent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ball",
            name="position",
            field=models.CharField(
                choices=POSITION_CHOICES,
                default="N/A",
                help_text="Footballer's position, used for the Starting XI match system",
                max_length=4,
            ),
        ),
        migrations.RunPython(reset_untouched_defaults, noop_reverse),
    ]
