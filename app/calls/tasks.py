import asyncio
import logging
import colorlog
import traceback
import os
import json
import time
import httpx
from datetime import datetime, timedelta
from django.conf import settings
from django.db import models, transaction
from django.db.models import Q, Count
from django.utils import timezone
from celery import shared_task
from .models import PhoneNumber, CallRecord, DTMFSequence, CallQueue, SMSMessage
from .services import CallManager, TranscriptionService, PhoneNumberExtractor
import openai

# Настройка цветного логирования
handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s%(reset)s',
    log_colors={
        'DEBUG':    'cyan',
        'INFO':     'green',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'red,bg_white',
    },
    secondary_log_colors={},
    style='%'
))

logger = colorlog.getLogger('calls.tasks')
logger.handlers = []  # Очищаем существующие обработчики
logger.addHandler(handler)
logger.setLevel(logging.INFO)

@shared_task
def extract_phone_numbers(text):
    try:
        extractor = PhoneNumberExtractor()
        numbers = extractor.extract_numbers(text)
        return f"Successfully processed {len(numbers)} phone numbers"
    except Exception as e:
        logger.error(f"Error in extract_phone_numbers task: {str(e)}")
        return f"Error processing phone numbers: {str(e)}"


@shared_task
def make_initial_call(phone_id):
    """Первый звонок без DTMF."""
    task_id = make_initial_call.request.id
    logger.info(f"Task  started: making initial call for phone ID {phone_id}.")
    try:
        phone = PhoneNumber.objects.get(id=phone_id)
        call_manager = CallManager()
        recording_name = call_manager.make_call(phone.number)
        
        if recording_name:
            logger.info(f"Initial call succeeded: recording saved as {recording_name}.")
            # Создаем CallRecord и сразу сохраняем
            call_record = CallRecord.objects.create(
                phone_number=phone,
                recording_file=recording_name,
                dtmf_sequence=json.dumps([])
            )
            call_record.save()
            
            # Добавляем небольшую задержку перед запуском process_recording
            time.sleep(1)
            process_recording.delay(phone.id, recording_name)
    except Exception as e:
        logger.error(f"Error in task : {str(e)}")
        logger.error(traceback.format_exc())
        phone = PhoneNumber.objects.get(id=phone_id)
        phone.status = "failed"
        phone.save()


@shared_task
def process_recording(phone_id, recording_name):
    """Обработка записи разговора."""
    logger.info(f"Task {process_recording.request.id} started: processing recording {recording_name} for phone ID {phone_id}")
    
    try:
        # Формируем пути к файлам
        asterisk_path = f"{settings.ASTERISK_RECORDING_PATH}/{recording_name}"
        web_path = f"{settings.RECORDINGS_PATH}/{recording_name}"
        
        # Проверяем существование файла в директории Asterisk
        if not os.path.exists(asterisk_path):
            logger.error(f"Recording file not found at {asterisk_path}")
            return None
            
        # Копируем файл в директорию для веб-доступа
        os.makedirs(os.path.dirname(web_path), exist_ok=True)
        import shutil
        shutil.copy2(asterisk_path, web_path)
        logger.info(f"Copied recording from {asterisk_path} to {web_path}")
        
        # Получаем объект телефона
        phone = PhoneNumber.objects.get(id=phone_id)
        
        # Пытаемся получить запись звонка с несколькими попытками
        max_attempts = 3
        attempt = 0
        call_record = None
        
        while attempt < max_attempts:
            try:
                call_record = CallRecord.objects.filter(
                    phone_number=phone,
                    recording_file__icontains=recording_name
                ).first()
                
                if call_record:
                    break
                    
                attempt += 1
                if attempt < max_attempts:
                    logger.info(f"CallRecord not found, attempt {attempt} of {max_attempts}. Waiting...")
                    time.sleep(2)
            except Exception as e:
                logger.error(f"Error while fetching CallRecord: {str(e)}")
                attempt += 1
                if attempt < max_attempts:
                    time.sleep(2)
        
        if not call_record:
            logger.error("CallRecord not found after {max_attempts} attempts")
            # Создаем новую запись, если не нашли существующую
            call_record = CallRecord.objects.create(
                phone_number=phone,
                recording_file=recording_name,
                dtmf_sequence=json.dumps([])
            )
        
        # Если уже есть транскрипция, пропускаем
        if call_record.transcription:
            logger.info(f"Recording {recording_name} already has transcription")
            return
            
        # Получаем транскрипцию
        service = TranscriptionService()
        transcription = service.transcribe_audio(web_path)
        
        if transcription:
            logger.info(f"Got transcription for {recording_name}")
            call_record.transcription = transcription
            call_record.save()
            
            # Анализируем транскрипцию для определения DTMF последовательностей
            dtmf_options = service.analyze_transcription_for_dtmf(transcription, phone_id)
            
            if dtmf_options:
                logger.info(f"Found DTMF options: {dtmf_options}")
                # Создаем последовательности DTMF и добавляем в очередь
                for option in dtmf_options:
                    sequence = [option['digit']]  # Теперь это может быть последовательность
                    dtmf_seq, created = DTMFSequence.objects.get_or_create(
                        phone_number=phone,
                        sequence=sequence,
                        defaults={
                            'description': option['action'],
                            'level': len(sequence),
                            'is_submenu': option.get('submenu', False)
                        }
                    )
                    
                    # Создаем последовательность DTMF с задержками
                    dtmf_sequence_with_delays = json.dumps([{
                        'digit': str(digit),
                        'delay': 5  # 5 секунд между нажатиями
                    } for digit in sequence])
                    
                    # Добавляем в очередь звонков
                    CallQueue.objects.get_or_create(
                        phone_number=phone,
                        dtmf_sequence=dtmf_sequence_with_delays,
                        defaults={
                            'status': 'pending'
                        }
                    )
                    logger.info(f"Added sequence {sequence} to call queue for phone {phone.number}")
        else:
            logger.error(f"Failed to get transcription for {recording_name}")
            
    except Exception as e:
        logger.error(f"Error in process_recording: {str(e)}")
        logger.error(traceback.format_exc())


@shared_task
def make_call_with_sequence(phone_id, sequence_id):
    """Звонок с DTMF последовательностью."""
    try:
        phone = PhoneNumber.objects.get(id=phone_id)
        try:
            sequence = DTMFSequence.objects.get(id=sequence_id)
        except DTMFSequence.DoesNotExist:
            logger.warning(f"DTMFSequence with id={sequence_id} no longer exists, skipping call")
            return False
        
        logger.info(f"Making call to {phone.number} with sequence {sequence.sequence}")
        
        # Добавляем в очередь, избегая дублей
        CallQueue.objects.get_or_create(
            phone_number=phone,
            dtmf_sequence=json.dumps([{'digit': str(digit), 'delay': 2} for digit in sequence.sequence]),
            defaults={
                'status': 'pending'
            }
        )
        
        return True
    except Exception as e:
        logger.error(f"Error making call with sequence: {str(e)}")
        logger.error(traceback.format_exc())
        return False


@shared_task
def process_unprocessed_recordings():
    """
    Периодическая задача для обработки записей без транскрипции
    и обновления результатов анализа DTMF.
    """
    logger.info("Starting processing of unprocessed recordings")
    
    try:
        # Находим записи звонков без транскрипции
        unprocessed_records = CallRecord.objects.filter(
            transcription__isnull=True
        ).select_related('phone_number')
        
        # Создаем множество для хранения уникальных phone_number_id
        processed_phone_numbers = set()
        
        for record in unprocessed_records:
            # Запускаем обработку каждой записи
            process_recording.delay(record.phone_number.id, record.recording_file)
            processed_phone_numbers.add(record.phone_number.id)
            
        # Находим последовательности DTMF без результата
        unexplored_sequences = DTMFSequence.objects.filter(
            explored=False
        ).select_related('phone_number').order_by('level', 'id')[:5]  # Обрабатываем не более 5 за раз
        
        for sequence in unexplored_sequences:
            # Запускаем новый звонок для исследования следующего уровня меню
            make_call_with_sequence.delay(sequence.phone_number.id, sequence.id)
            processed_phone_numbers.add(sequence.phone_number.id)
            
        # Запускаем пересчет summary для всех обработанных номеров
        for phone_id in processed_phone_numbers:
            update_phone_summaries.delay(phone_id)
            
        logger.info(
            f"Processed {unprocessed_records.count()} recordings and "
            f"{unexplored_sequences.count()} DTMF sequences. "
            f"Updated summaries for {len(processed_phone_numbers)} phone numbers"
        )
        
    except Exception as e:
        logger.error(f"Error in process_unprocessed_recordings task: {str(e)}")
        logger.error(traceback.format_exc())


@shared_task
def update_phone_summaries(phone_id=None):
    """
    Обновление сводки по телефонам.
    
    Args:
        phone_id (int, optional): ID конкретного телефона для обновления.
            Если не указан, обновляются все телефоны.
    """
    logger.info(f"Starting update of phone summaries{f' for phone {phone_id}' if phone_id else ''}")
    try:
        service = TranscriptionService()
        
        if phone_id:
            phones = PhoneNumber.objects.filter(id=phone_id)
        else:
            phones = PhoneNumber.objects.all()
        
        for phone in phones:
            try:
                # Получаем все транскрипции для телефона
                transcriptions = [
                    record.transcription 
                    for record in phone.call_records.all() 
                    if record.transcription
                ]
                
                if not transcriptions:
                    continue
                    
                # Создаем сводку
                summary = service.create_summary(transcriptions, phone.number)
                
                if summary:
                    phone.summary = summary
                    phone.summary_updated_at = timezone.now()
                    phone.save()
                    logger.info(f"Updated summary for phone {phone.number}")
                    
                    # Запускаем анализ DTMF после обновления summary
                    analyze_recordings_for_dtmf.delay(phone.id)
                    
            except Exception as e:
                logger.error(f"Error updating summary for phone {phone.number}: {str(e)}")
                logger.error(traceback.format_exc())
                continue
                
    except Exception as e:
        logger.error(f"Error in update_phone_summaries task: {str(e)}")
        logger.error(traceback.format_exc())


def parse_ivr_structure(transcription_text):
    """Парсинг текста IVR в иерархическую структуру."""
    ivr_tree = []
    stack = []

    for line in transcription_text.splitlines():
        if "Press" in line or "Нажмите" in line:
            parts = line.split("to")
            button = parts[0].strip().split()[-1]
            description = parts[1].strip() if len(parts) > 1 else "Unknown"
            option = {
                "button": button,
                "description": description,
                "sub_options": []
            }
            if not stack:
                ivr_tree.append(option)
            else:
                stack[-1]["sub_options"].append(option)
            stack.append(option)
        elif "back" in line or "return" in line:
            stack.pop()

    return ivr_tree


def flatten_dtmf_tree(ivr_tree):
    """Преобразование дерева DTMF в плоский список."""
    flattened = []

    def traverse(node, path=""):
        full_sequence = f"{path}{node['button']}"
        flattened.append({
            "sequence": full_sequence,
            "description": node["description"]
        })
        for sub_option in node["sub_options"]:
            traverse(sub_option, full_sequence + " - ")

    for option in ivr_tree:
        traverse(option)

    return flattened


@shared_task
def analyze_recordings_for_dtmf(phone_id=None):
    """
    Периодическая задача для анализа записей телефонных номеров без карты DTMF.
    Анализирует как записи звонков, так и summary телефона.
    
    Args:
        phone_id (int, optional): ID конкретного телефонного номера для анализа.
            Если не указан, анализируются все номера без карты DTMF.
    """
    if phone_id:
        logger.info(f"Starting DTMF analysis for phone ID: {phone_id}")
    else:
        logger.info("Starting analysis of phone numbers without DTMF map")
    
    try:
        service = TranscriptionService()
        analyzed_count = 0
        
        # Формируем базовый QuerySet
        base_query = PhoneNumber.objects.prefetch_related('call_records')
        
        if phone_id:
            # Если указан конкретный номер, анализируем его
            phones_to_analyze = base_query.filter(id=phone_id)
        else:
            # Иначе берем все номера без карты DTMF
            phones_to_analyze = base_query.filter(dtmf_map__isnull=True)
        
        for phone in phones_to_analyze:
            dtmf_map = {}
            
            # Сначала анализируем summary, если оно есть
            if phone.summary:
                summary_options = service.analyze_summary_for_dtmf(phone.summary)
                if summary_options:
                    logger.info(f"Found DTMF options in summary for phone {phone.number}: {summary_options}")
                    for option in summary_options:
                        digit = option['digit']
                        if digit not in dtmf_map:
                            dtmf_map[digit] = {
                                'action': option['action'],
                                'submenu': option.get('submenu', False),
                                'source': 'summary'
                            }
            
            # Затем анализируем записи звонков
            call_records = phone.call_records.filter(
                transcription__isnull=False
            ).order_by('-created_at')
            
            for record in call_records:
                dtmf_options = service.analyze_transcription_for_dtmf(record.transcription, phone.id)
                
                if dtmf_options:
                    logger.info(f"Found DTMF options in recording {record.recording_file}: {dtmf_options}")
                    for option in dtmf_options:
                        digit = option['digit']
                        if digit not in dtmf_map:  # Не перезаписываем опции из summary
                            dtmf_map[digit] = {
                                'action': option['action'],
                                'submenu': option.get('submenu', False),
                                'source': 'transcription'
                            }
            
            if dtmf_map:
                # Если это перерасчет для конкретного номера, сначала очищаем старые последовательности
                if phone_id:
                    DTMFSequence.objects.filter(phone_number=phone).delete()
                    
                # Создаем DTMFSequence для каждой найденной опции
                for digit, info in dtmf_map.items():
                    sequence = [digit]
                    DTMFSequence.objects.get_or_create(
                        phone_number=phone,
                        sequence=sequence,
                        defaults={
                            'description': info['action'],
                            'level': len(sequence),
                            'is_submenu': info['submenu']
                        }
                    )
                analyzed_count += 1
                
        if phone_id:
            logger.info(f"Completed DTMF analysis for phone ID {phone_id}")
        else:
            logger.info(f"Analyzed DTMF options for {analyzed_count} phone numbers")
            
    except Exception as e:
        logger.error(f"Error in analyze_recordings_for_dtmf task: {str(e)}")
        logger.error(traceback.format_exc())


@shared_task
def check_unexplored_dtmf():
    """
    Периодическая задача для поиска DTMF последовательностей без звонков.
    Добавляет их в очередь звонков с низким приоритетом.
    """
    try:
        # Получаем все DTMF последовательности, которые не помечены как explored
        unexplored_sequences = DTMFSequence.objects.filter(
            explored=False
        ).select_related('phone_number')
        
        added_to_queue = 0
        for sequence in unexplored_sequences:
            # Преобразуем последовательность в формат для очереди звонков
            dtmf_sequence = json.dumps([{'digit': str(digit), 'delay': 2} for digit in sequence.sequence])
            
            # Добавляем в очередь с низким приоритетом
            CallQueue.objects.get_or_create(
                phone_number=sequence.phone_number,
                dtmf_sequence=dtmf_sequence,
                defaults={
                    'status': 'pending'
                }
            )
            added_to_queue += 1
            
        if added_to_queue:
            logger.info(f"Added {added_to_queue} unexplored DTMF sequences to call queue")
            
    except Exception as e:
        logger.error(f"Error in check_unexplored_dtmf task: {str(e)}")
        logger.error(traceback.format_exc())


@shared_task
def process_call_queue():
    """Обработка очереди звонков. Берет один звонок из очереди и выполняет его."""
    try:
        # Получаем один звонок из очереди со статусом pending
        with transaction.atomic():
            queue_item = (CallQueue.objects
                .select_for_update()
                .filter(status='pending')
                .order_by('created_at')
                .first())
            
            if not queue_item:
                logger.info("No pending calls in queue")
                return
                
            # Помечаем как в обработке
            queue_item.status = 'processing'
            queue_item.attempts += 1
            queue_item.save()
        
        # Создаем менеджер звонков
        call_manager = CallManager()
        
        # Делаем звонок
        try:
            dtmf_sequence = queue_item.dtmf_sequence
            if isinstance(dtmf_sequence, str):
                # Если строка - парсим JSON
                dtmf_sequence = json.loads(dtmf_sequence)
            
            recording_name = call_manager.make_call(
                queue_item.phone_number.number,
                dtmf_sequence
            )

            if recording_name:
                # Звонок успешен
                queue_item.status = 'completed'
                queue_item.save()
                
                # Создаем запись о звонке, сохраняем DTMF последовательность как есть
                CallRecord.objects.create(
                    phone_number=queue_item.phone_number,
                    recording_file=recording_name,
                    dtmf_sequence=queue_item.dtmf_sequence  # Используем как есть
                )
                
                logger.info(f"Successfully processed queue item {queue_item.id}")
            else:
                queue_item.status = 'failed'
                queue_item.last_error = "No recording name received"
                queue_item.save()
                logger.error(f"Failed to process queue item {queue_item.id}: No recording name")
                
        except Exception as e:
            logger.error(f"Error making call: {e}")
            queue_item.status = 'failed'
            queue_item.last_error = str(e)
            queue_item.save()
            return None
            
    except Exception as e:
        if queue_item:
            queue_item.status = 'failed'
            queue_item.last_error = str(e)
            queue_item.save()
        logger.error(f"Error processing queue item {queue_item.id if queue_item else None}: {str(e)}")
        logger.error(traceback.format_exc())


@shared_task
def process_new_phones():
    """
    Периодическая задача для обработки новых телефонных номеров.
    Добавляет новые номера в очередь звонков.
    """
    logger.info("Starting processing of new phone numbers")
    
    try:
        # Находим все новые номера
        new_phones = PhoneNumber.objects.filter(status='new')
        processed_count = 0
        
        for phone in new_phones:
            try:
                # Добавляем в очередь звонков
                CallQueue.objects.get_or_create(
                    phone_number=phone,
                    dtmf_sequence=json.dumps([]),
                    defaults={
                        'status': 'pending'
                    }
                )
                
                # Обновляем статус на processing
                phone.status = 'processing'
                phone.save(update_fields=['status'])
                
                processed_count += 1
                logger.info(f"Added phone {phone.number} to call queue")
                
            except Exception as e:
                logger.error(f"Error processing new phone {phone.number}: {str(e)}")
                logger.error(traceback.format_exc())
                continue
        
        logger.info(f"Processed {processed_count} new phone numbers")
        
    except Exception as e:
        logger.error(f"Error in process_new_phones task: {str(e)}")
        logger.error(traceback.format_exc())


@shared_task
def check_stalled_recordings():
    """
    Проверяет записи звонков, у которых нет транскрипции или транскрипция короче 20 символов более 20 минут.
    Удаляет такие записи и перезапускает звонок с той же последовательностью DTMF.
    """
    logger.info("Starting check for stalled recordings")
    
    try:
        # Находим записи старше 20 минут
        time_threshold = timezone.now() - timedelta(minutes=20)
        records = CallRecord.objects.filter(
            created_at__lt=time_threshold
        ).select_related('phone_number')
        
        stalled_records = []
        for record in records:
            # Проверяем транскрипцию
            if not record.transcription or len(record.transcription) < 20:
                stalled_records.append(record)
        
        for record in stalled_records:
            try:
                logger.info(f"Processing stalled record {record.id} for phone {record.phone_number.number}")
                
                # Получаем последовательность DTMF из записи
                dtmf_sequence = record.dtmf_sequence
                phone = record.phone_number
                
                # Удаляем запись из базы
                record.delete()
                logger.info(f"Deleted stalled record {record.id}")
                
                # Добавляем новый звонок в очередь с той же последовательностью DTMF
                CallQueue.objects.create(
                    phone_number=phone,
                    dtmf_sequence=dtmf_sequence,
                    status='pending'
                )
                logger.info(f"Created new call task for phone {phone.number} with DTMF sequence {dtmf_sequence}")
                
            except Exception as e:
                logger.error(f"Error processing stalled record {record.id}: {str(e)}")
                logger.error(traceback.format_exc())
                continue
                
        logger.info(f"Finished processing {len(stalled_records)} stalled records")
        
    except Exception as e:
        logger.error(f"Error in check_stalled_recordings task: {str(e)}")
        logger.error(traceback.format_exc())


@shared_task
def process_sms_messages():
    """
    Обработка SMS сообщений и извлечение телефонных номеров с помощью GPT.
    Запускается каждую минуту через Celery Beat.
    """
    http_client = None
    logger.info("Starting process_sms_messages task")
    
    try:
        # Проверяем API ключ
        if not settings.OPENAI_API_KEY:
            error_msg = "OpenAI API key is not set in settings"
            logger.error(error_msg)
            return {'status': 'error', 'message': error_msg}
            
        logger.info("OpenAI API key is configured")
        
        # Получаем сообщения с пустым response_text
        messages = SMSMessage.objects.filter(
            Q(response_text__isnull=True) | Q(response_text='')
        )
        messages_count = messages.count()
        logger.info(f"Found {messages_count} messages to process")

        if not messages.exists():
            logger.info("No messages to process")
            return {'status': 'success', 'message': 'No messages to process'}

        try:
            # Инициализируем клиент OpenAI
            logger.info("Initializing OpenAI client...")
            http_client = httpx.Client(timeout=30.0)
            client = openai.OpenAI(
                api_key=settings.OPENAI_API_KEY,
                http_client=http_client
            )
            logger.info("OpenAI client initialized successfully")
        except Exception as e:
            error_msg = f"Failed to initialize OpenAI client: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            messages.update(
                status=SMSMessage.STATUS_FAILED,
                response_text=error_msg
            )
            return {'status': 'error', 'message': error_msg}

        processed_count = 0
        failed_count = 0

        for message in messages:
            try:
                logger.info(f"Processing message {message.id} with text: {message.message_text[:100]}...")
                
                # Вызов API
                logger.info(f"Sending request to GPT for message {message.id}")
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": """You are a phone number extraction assistant. Your task is to find and format phone numbers from text.

                            Rules:
                            1. Extract ALL phone numbers from the input text
                            2. Format rules:
                               - For US numbers: Add 1 prefix if not present (e.g. "8007267864" -> "18007267864")
                               - Keep all digits, remove any separators or special characters
                               - Do NOT include + sign in the output
                            3. Return ONLY a JSON array of formatted numbers, nothing else
                            4. If you see a number that looks like a phone number, include it
                            """
                        },
                        {
                            "role": "user",
                            "content": message.message_text
                        }
                    ],
                    temperature=0
                )

                if not response or not hasattr(response, 'choices') or not response.choices:
                    error_msg = "Invalid response format from GPT"
                    logger.error(error_msg)
                    message.response_text = error_msg
                    message.status = SMSMessage.STATUS_FAILED
                    message.save()
                    failed_count += 1
                    continue

                content = response.choices[0].message.content
                logger.info(f"Raw response from GPT for message {message.id}: {content}")
                
                try:
                    numbers = json.loads(content)
                    if not isinstance(numbers, list):
                        raise ValueError("Response is not a list")

                    message.response_text = json.dumps(numbers)
                    message.status = SMSMessage.STATUS_PROCESSED
                    message.save()
                    processed_count += 1

                    # Обработка каждого номера
                    numbers_processed = 0
                    for number in numbers:
                        if len(number) > 20:
                            logger.warning(f"Skipping number {number} - too long")
                            continue
                            
                        try:
                            phone, created = PhoneNumber.objects.get_or_create(
                                number=number,
                                defaults={"status": "new"}
                            )
                            
                            if not created and phone.status not in ['new', 'processing']:
                                phone.status = 'new'
                                phone.save(update_fields=['status'])
                                
                            numbers_processed += 1
                            logger.info(f"{'Created' if created else 'Updated'} phone number: {number}")
                        except Exception as e:
                            error_msg = f"Error saving phone number {number}: {str(e)}"
                            logger.error(error_msg)
                            logger.error(traceback.format_exc())
                            continue

                    logger.info(f"Processed {numbers_processed} numbers for message {message.id}")

                except (json.JSONDecodeError, ValueError) as e:
                    error_msg = f"Error processing GPT response for message {message.id}: {str(e)}"
                    logger.error(error_msg)
                    logger.error(traceback.format_exc())
                    message.response_text = error_msg
                    message.status = SMSMessage.STATUS_FAILED
                    message.save()
                    failed_count += 1

            except Exception as e:
                error_msg = f"Error processing message {message.id}: {str(e)}"
                logger.error(error_msg)
                logger.error(traceback.format_exc())
                message.response_text = error_msg
                message.status = SMSMessage.STATUS_FAILED
                message.save()
                failed_count += 1
                continue

        result_msg = f"Processed {processed_count} messages, failed {failed_count} messages"
        logger.info(result_msg)
        return {'status': 'success', 'message': result_msg}

    except Exception as e:
        error_msg = f"Fatal error in process_sms_messages task: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        if messages:
            failed_update_count = messages.update(
                status=SMSMessage.STATUS_FAILED,
                response_text=error_msg
            )
            logger.info(f"Marked {failed_update_count} messages as failed")
        return {'status': 'error', 'message': error_msg}

    finally:
        if http_client:
            http_client.close()
            logger.info("HTTP client closed")