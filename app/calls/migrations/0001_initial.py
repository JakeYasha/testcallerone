from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='PhoneNumber',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.CharField(max_length=20)),
                ('status', models.CharField(choices=[('new', 'New'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('failed', 'Failed')], default='new', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('dtmf_map', models.JSONField(blank=True, null=True)),
                ('summary', models.TextField(blank=True, null=True)),
                ('summary_updated_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='DTMFSequence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.JSONField()),
                ('explored', models.BooleanField(default=False)),
                ('result', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('phone_number', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dtmf_sequences', to='calls.phonenumber')),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.CreateModel(
            name='CallRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('recording_file', models.CharField(max_length=255)),
                ('dtmf_sequence', models.JSONField()),
                ('transcript', models.TextField(blank=True, null=True)),
                ('duration', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('phone_number', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='call_records', to='calls.phonenumber')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
