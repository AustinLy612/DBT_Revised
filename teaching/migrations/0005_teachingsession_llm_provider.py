from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teaching", "0004_teachingsession_inquiry_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="teachingsession",
            name="llm_provider",
            field=models.CharField(
                choices=[
                    ("deepseek", "DeepSeek Flash"),
                    ("doubao", "豆包（负载兜底）"),
                ],
                default="deepseek",
                max_length=16,
            ),
        ),
    ]
