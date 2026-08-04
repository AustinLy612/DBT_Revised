from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teaching", "0003_chatmessage_teaching_step"),
    ]

    operations = [
        migrations.AddField(
            model_name="teachingsession",
            name="inquiry_data",
            field=models.JSONField(default=dict),
        ),
    ]
