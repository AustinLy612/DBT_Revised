from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ema_log', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='emasubmission',
            name='suicide_urge_score',
            field=models.IntegerField(null=True),
        ),
    ]
