from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("teaching", "0002_add_session_phase"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatmessage",
            name="teaching_step",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
