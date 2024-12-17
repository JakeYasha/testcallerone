from django.db import models
from django.utils import timezone
import os
import logging
from django.contrib.postgres.fields import ArrayField

logger = logging.getLogger(__name__)

class PhoneNumber(models.Model):
    STATUS_CHOICES = [
        ('new', 'New'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('failed', 'Failed')
    ]

    number = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    dtmf_map = models.JSONField(null=True, blank=True)  # Карта нажатий
    summary = models.TextField(null=True, blank=True)  # Сводка от GPT
    summary_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.number} ({self.status})"


class CallRecord(models.Model):
    phone_number = models.ForeignKey(PhoneNumber, on_delete=models.CASCADE, related_name='call_records')
    recording_file = models.CharField(max_length=255, help_text="Имя файла записи")
    dtmf_sequence = models.JSONField(default=list)  # Последовательность нажатий
    transcription = models.TextField(null=True, blank=True)  # Транскрипция разговора
    duration = models.IntegerField(default=0)  # Длительность звонка в секундах
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Call to {self.phone_number.number} at {self.created_at}"


class DTMFSequence(models.Model):
    """Модель для хранения DTMF последовательностей."""
    phone_number = models.ForeignKey(PhoneNumber, on_delete=models.CASCADE, related_name='dtmf_sequences')
    sequence = models.JSONField()  # Список нажатий, например ['1', '2', '3']
    description = models.TextField(null=True, blank=True)
    level = models.IntegerField(default=1)  # Уровень глубины в меню
    is_submenu = models.BooleanField(default=False)  # Указывает, ведет ли эта последовательность к подменю
    created_at = models.DateTimeField(auto_now_add=True)  # Добавляем поле created_at
    explored = models.BooleanField(default=False)
    
    class Meta:
        unique_together = ['phone_number', 'sequence']
        ordering = ['level', 'id']  # Используем id вместо created_at
    
    def __str__(self):
        if isinstance(self.sequence, list):
            return '-'.join(self.sequence)
        return str(self.sequence)
    
    def get_full_path(self):
        """Возвращает полный путь последовательности"""
        if isinstance(self.sequence, list):
            return ' -> '.join(self.sequence)
        return str(self.sequence)
    
    def get_sequence_with_delays(self):
        """
        Возвращает последовательность DTMF в формате для отправки.
        Returns:
            list: Список словарей с ключами 'digit' и 'delay'
        """
        if not isinstance(self.sequence, list):
            logger.error(f"Invalid sequence format for DTMFSequence {self.id}: {self.sequence}")
            return []
            
        result = []
        for i, digit in enumerate(self.sequence):
            # Первая цифра - задержка 5 секунд
            # Последующие - задержка 5 секунд после предыдущей
            delay = 5
            
            result.append({
                'digit': str(digit),  # Убеждаемся, что digit - строка
                'delay': delay
            })
            
        return result
        
    def save(self, *args, **kwargs):
        """
        Убеждаемся, что sequence сохраняется в правильном формате.
        """
        if isinstance(self.sequence, str):
            # Если передана строка, преобразуем её в список
            self.sequence = list(self.sequence)
        elif isinstance(self.sequence, (list, tuple)):
            # Если передан список или кортеж, преобразуем все элементы в строки
            self.sequence = [str(x) for x in self.sequence]
        else:
            logger.error(f"Invalid sequence type for DTMFSequence: {type(self.sequence)}")
            self.sequence = []
            
        super().save(*args, **kwargs)


class Note(models.Model):
    """Модель для хранения заметок."""
    title = models.CharField(max_length=200, verbose_name="Заголовок")
    content = models.TextField(verbose_name="Содержание")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    
    class Meta:
        verbose_name = "Заметка"
        verbose_name_plural = "Заметки"
        ordering = ['-created_at']
    
    def __str__(self):
        return self.title


class CallQueue(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),      # Ожидает выполнения
        ('processing', 'Processing'), # В процессе выполнения
        ('completed', 'Completed'),   # Успешно выполнен
        ('failed', 'Failed')         # Ошибка выполнения
    ]
    
    phone_number = models.ForeignKey(PhoneNumber, on_delete=models.CASCADE, related_name='queue_items')
    dtmf_sequence = models.JSONField(help_text="Список нажатий DTMF в формате [{'digit': '1', 'delay': 5}]")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)

    class Meta:
        unique_together = ('phone_number', 'dtmf_sequence')
        verbose_name = 'Call Queue Item'
        verbose_name_plural = 'Call Queue Items'
        
    def __str__(self):
        return f"Call to {self.phone_number.number} ({self.status})"

    def save(self, *args, **kwargs):
        """
        Переопределяем метод save для автоматического удаления записей
        со статусом 'completed' или 'failed'
        """
        if self.status in ['completed', 'failed']:
            # Если запись уже существует, удаляем её
            if self.pk:
                self.delete()
                return
            # Если это новая запись, даже не сохраняем
            return
        
        super().save(*args, **kwargs)

class SMSMessage(models.Model):
    # Константы для статусов
    STATUS_RECEIVED = 'received'
    STATUS_PROCESSED = 'processed'
    STATUS_FAILED = 'failed'
    
    STATUS_CHOICES = [
        (STATUS_RECEIVED, 'Received'),
        (STATUS_PROCESSED, 'Processed'),
        (STATUS_FAILED, 'Failed')
    ]

    sender_number = models.CharField(max_length=20)
    message_text = models.TextField()
    received_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    response_text = models.TextField(null=True, blank=True)  # Новое поле для хранения ответа

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return f"Message from {self.sender_number} at {self.received_at}"
