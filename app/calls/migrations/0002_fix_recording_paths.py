from django.db import migrations
import os

def fix_recording_paths(apps, schema_editor):
    CallRecord = apps.get_model('calls', 'CallRecord')
    for record in CallRecord.objects.all():
        if record.recording_file:
            # Убираем лишние слэши и /recordings/
            clean_path = record.recording_file.replace('/recordings/', '').strip('/')
            # Убираем дублирование .wav
            if clean_path.endswith('.wav.wav'):
                clean_path = clean_path[:-4]
            record.recording_file = clean_path
            record.save()

def reverse_recording_paths(apps, schema_editor):
    CallRecord = apps.get_model('calls', 'CallRecord')
    for record in CallRecord.objects.all():
        if record.recording_file:
            record.recording_file = f"/recordings/{record.recording_file}"
            record.save()

class Migration(migrations.Migration):
    dependencies = [
        ('calls', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(fix_recording_paths, reverse_recording_paths),
    ]
