# Generated by Django 3.2.16 on 2022-12-06 20:59

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0005_alter_webhook_event_status_noop'),
    ]

    operations = [
        migrations.AlterField(
            model_name='webhook',
            name='url',
            field=models.URLField(help_text='The URL that receives a POST request from the webhook.', max_length=2000, validators=[django.core.validators.URLValidator(schemes=['https'])]),
        ),
    ]