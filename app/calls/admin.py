from django.contrib import admin
from django.contrib import messages
from .models import PhoneNumber, CallRecord, DTMFSequence, CallQueue, SMSMessage

@admin.register(PhoneNumber)
class PhoneNumberAdmin(admin.ModelAdmin):
    list_display = ('number', 'status', 'created_at')
    search_fields = ('number',)
    list_filter = ('status', 'created_at')

    def mark_as_completed(self, request, queryset):
        # Обновляем статус для выбранных записей
        updated = queryset.update(status='completed')
        
        # Выводим сообщение о результате
        if updated == 1:
            message = '1 номер телефона был отмечен как выполненный'
        else:
            message = f'{updated} номеров телефона были отмечены как выполненные'
        
        self.message_user(request, message, messages.SUCCESS)
    
    # Описание действия для админки
    mark_as_completed.short_description = "Отметить как выполненные"
    
    # Добавляем действие в список доступных действий
    actions = ['mark_as_completed']

@admin.register(CallRecord)
class CallRecordAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'recording_file', 'duration', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('phone_number__number', 'recording_file')
    raw_id_fields = ('phone_number',)

@admin.register(DTMFSequence)
class DTMFSequenceAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'get_sequence_display', 'created_at')
    search_fields = ('phone_number__number',)
    list_filter = ('phone_number',)
    raw_id_fields = ('phone_number',)

    def get_sequence_display(self, obj):
        return str(obj.sequence)
    get_sequence_display.short_description = 'Sequence'

@admin.register(CallQueue)
class CallQueueAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'get_dtmf_display', 'status', 'attempts', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('phone_number__number',)
    raw_id_fields = ('phone_number',)
    readonly_fields = ('attempts', 'last_error')

    def get_dtmf_display(self, obj):
        return str(obj.dtmf_sequence)
    get_dtmf_display.short_description = 'DTMF Sequence'

@admin.register(SMSMessage)
class SMSMessageAdmin(admin.ModelAdmin):
    list_display = ('sender_number', 'message_text', 'received_at', 'status', 'response_text')
    search_fields = ('sender_number', 'message_text', 'response_text')
    list_filter = ('status', 'received_at')
