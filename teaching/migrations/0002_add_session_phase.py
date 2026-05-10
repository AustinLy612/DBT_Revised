from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("teaching", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="teachingsession",
            name="phase",
            field=models.CharField(
                choices=[
                    ("pre_mood_recording", "教学前心情记录"),
                    ("info_collection", "信息收集"),
                    ("skill_selection", "技能选择"),
                    ("rag_retrieval_for_teaching", "RAG教学检索"),
                    ("teaching", "教学中"),
                ],
                default="pre_mood_recording",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="teachingsession",
            name="teaching_plan",
            field=models.JSONField(default=dict),
        ),
    ]
