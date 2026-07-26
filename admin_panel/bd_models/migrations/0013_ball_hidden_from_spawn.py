from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bd_models", "0012_ball_hidden_from_packs"),
    ]

    operations = [
        migrations.AddField(
            model_name="ball",
            name="hidden_from_spawn",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "If enabled, this footballer is excluded from /spawn, /spawnrare, and "
                    "/spawnregime (including automatic wild spawns)."
                ),
            ),
        ),
    ]
