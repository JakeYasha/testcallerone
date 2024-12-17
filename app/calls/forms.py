from django import forms

class ManualDTMFForm(forms.Form):
    sequence = forms.CharField(
        max_length=10,
        label='DTMF последовательность',
        help_text='Введите последовательность цифр (например: 1 или 123)'
    )
    description = forms.CharField(
        max_length=255,
        label='Описание',
        help_text='Опишите, что делает эта последовательность'
    )
