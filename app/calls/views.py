from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView
from django.views.generic.edit import FormView
from django.contrib import messages
from django.urls import reverse_lazy
from .models import PhoneNumber, CallRecord, DTMFSequence, SMSMessage
from .tasks import make_call_with_sequence, make_initial_call, extract_phone_numbers
from django import forms
from django.http import JsonResponse, FileResponse, Http404
from celery import chain
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.conf import settings
import os
from .forms import ManualDTMFForm
import logging

logger = logging.getLogger(__name__)

class PhoneNumberInputForm(forms.Form):
    text = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 5}),
        label='Введите текст с телефонными номерами'
    )

class HomeView(ListView):
    model = PhoneNumber
    template_name = 'calls/home.html'
    context_object_name = 'phone_numbers'
    paginate_by = 20

    def get_queryset(self):
        return PhoneNumber.objects.all().order_by('-created_at')

class PhoneDetailView(DetailView):
    model = PhoneNumber
    template_name = 'phone_detail.html'
    context_object_name = 'phone'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        phone = self.get_object()
        if phone.summary:
            # Передаем raw JSON строку в шаблон
            context['phone'].summary_raw = phone.summary
        return context

class PhoneNumberDetailView(DetailView):
    model = PhoneNumber
    template_name = 'calls/phone_detail.html'
    context_object_name = 'phone'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        phone = self.get_object()
        context['call_records'] = phone.call_records.all()
        context['dtmf_sequences'] = phone.dtmf_sequences.all()
        if phone.summary:
            # Передаем raw JSON строку в шаблон
            context['phone'].summary_raw = phone.summary
        return context

class AddPhoneNumbersView(FormView):
    template_name = 'calls/add_numbers.html'
    form_class = PhoneNumberInputForm
    success_url = reverse_lazy('home')

    def form_valid(self, form):
        text = form.cleaned_data['text']
        
        # Сохраняем SMS-сообщение
        sms_message = SMSMessage(
            sender_number='user_input', 
            message_text=text,
            status='received'
        )
        sms_message.save()

        # Запускаем задачу извлечения номеров
        
        messages.success(self.request, 'Текст отправлен на обработку. Номера будут обработаны автоматически.')
        return super().form_valid(form)

def queue_count(request):
    """Возвращает количество номеров в очереди на обработку"""
    count = PhoneNumber.objects.filter(status='new').count()
    return JsonResponse({'count': count})

def phone_list(request):
    """API endpoint для получения списка номеров"""
    phones = PhoneNumber.objects.all().order_by('-created_at')[:20]
    data = [{
        'id': phone.id,
        'number': phone.number,
        'status': phone.status,
        'created_at': phone.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'call_count': phone.call_records.count()
    } for phone in phones]
    return JsonResponse({'phones': data})

def serve_recording(request, filepath):
    """Отдает файл записи из директории recordings"""
    # Убираем возможные лишние слэши и нормализуем путь
    filepath = os.path.normpath(filepath).lstrip('/')
    file_path = os.path.join(settings.RECORDINGS_PATH, filepath)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(open(file_path, 'rb'), content_type='audio/wav')
    raise Http404(f"Recording {filepath} not found")

@require_http_methods(["POST"])
def delete_phone(request, pk):
    """Удаление телефонного номера и всех связанных данных"""
    phone = get_object_or_404(PhoneNumber, pk=pk)
    phone.delete()
    messages.success(request, f'Номер {phone.number} успешно удален')
    return redirect('home')

@require_http_methods(["POST"])
def add_manual_dtmf(request, pk):
    """Добавление DTMF последовательности вручную"""
    phone = get_object_or_404(PhoneNumber, pk=pk)
    form = ManualDTMFForm(request.POST)
    
    if form.is_valid():
        sequence = form.cleaned_data['sequence']
        description = form.cleaned_data['description']
        
        # Создаем новую DTMF последовательность
        DTMFSequence.objects.get_or_create(
            phone_number=phone,
            sequence=sequence,
            defaults={
                'description': description,
                'level': 1,
                'explored': False
            }
        )
        
        # Обновляем dtmf_map
        current_map = phone.dtmf_map or {}
        current_map[sequence] = {
            'description': description,
            'analyzed': True
        }
        phone.dtmf_map = current_map
        phone.save()
        
        messages.success(request, f'DTMF последовательность {sequence} добавлена')
    else:
        messages.error(request, 'Ошибка в форме. Проверьте введенные данные.')
    
    return redirect('phone_detail', pk=pk)

@require_http_methods(["POST"])
def recall_phone(request, pk):
    """Перезвонить на номер телефона"""
    try:
        phone = get_object_or_404(PhoneNumber, pk=pk)
        phone.status = 'new'
        phone.save(update_fields=['status'])
        
        messages.success(request, f'Телефон {phone.number} добавлен в очередь на перезвон')
        return JsonResponse({'status': 'success'})
    except Exception as e:
        logger.error(f"Error recalling phone {pk}: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
