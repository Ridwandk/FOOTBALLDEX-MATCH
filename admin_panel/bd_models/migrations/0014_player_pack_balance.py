from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bd_models", "0013_ball_hidden_from_spawn"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="pack_balance",
            field=models.IntegerField(
                default=0,
                help_text=(
                    "Persistent pack currency balance, spent by /packs commands "
                    "(packly, multipackly, gamblepack). Survives bot restarts."
                ),
            ),
        ),
    ]
