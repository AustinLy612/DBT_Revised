# Generated manually for adding created_at to TestQuestion model

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("testing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="testquestion",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AlterModelOptions(
            name="testquestion",
            options={"ordering": ["created_at"]},
        ),
    ]
