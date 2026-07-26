from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bd_models", "0011_ball_position_not_assigned"),
    ]

    operations = [
        migrations.AddField(
            model_name="ball",
            name="hidden_from_packs",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "If enabled, this footballer is excluded from pack draws that check "
                    "this flag (currently: /packs multipackly)"
                ),
            ),
        ),
    ]
