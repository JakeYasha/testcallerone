import colorlog
import logging
import asyncio
import websockets
import requests
import shutil
from datetime import datetime
from django.conf import settings
import openai
from pathlib import Path
import httpx
import base64
import traceback
import os
import json
from django.db.models import Q
from typing import List
from .models import PhoneNumber, CallRecord, DTMFSequence, CallQueue
import re

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

logger = colorlog.getLogger('calls.services')
logger.handlers = []  # Очищаем существующие обработчики
logger.addHandler(handler)
logger.setLevel(logging.INFO)

class PhoneNumberExtractor:
    @staticmethod
    def extract_numbers(text: str) -> List[str]:
        """Извлекает телефонные номера из текста с помощью gpt-4o-minio-mini"""
        http_client = None
        try:
            logger.info(f"Starting phone number extraction from text: {text[:100]}...")
            
            # Инициализируем клиент OpenAI с API ключом
            logger.debug(f"Initializing OpenAI client with API key: {settings.OPENAI_API_KEY[:5]}...")
            http_client = httpx.Client(timeout=30.0)
            client = openai.OpenAI(
                api_key=settings.OPENAI_API_KEY,
                http_client=http_client
            )
            
            # Отправляем запрос к API
            logger.info("Sending request to OpenAI API")
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

Example output: ["18007267864", "19991234567"]"""
                    },
                    {
                        "role": "user",
                        "content": text
                    }
                ],
                temperature=0
            )
            logger.info("Received response from OpenAI API")
            
            # Извлекаем и парсим результат
            content = response.choices[0].message.content
            logger.info(f"Raw response from GPT: {content}")
            
            try:
                numbers = json.loads(content)
                logger.info(f"Successfully parsed numbers: {numbers}")
                
                # Добавляем номера в базу данных
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
                            
                        if created:
                            logger.info(f"Created new phone number: {number} (ID: {phone.id})")
                    except Exception as e:
                        logger.error(f"Error processing number {number}: {str(e)}")
                        continue
                        
                return numbers
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse GPT response as JSON: {content}")
                logger.error(f"JSON parse error: {str(e)}")
                return []
                
        except Exception as e:
            logger.error(f"Error extracting numbers: {str(e)}", exc_info=True)
            return []
        finally:
            if http_client:
                http_client.close()

class TranscriptionService:
    """Сервис для транскрибации аудио файлов и анализа IVR меню."""

    def __init__(self):
        """Инициализация сервиса."""
        # Создаем базовый HTTP клиент без прокси
        http_client = httpx.Client(timeout=30.0)
        
        self.client = openai.OpenAI(
            api_key=settings.OPENAI_API_KEY,
            http_client=http_client
        )

    def transcribe_audio(self, file_path: str) -> str:
        """
        Транскрибирует аудиофайл в текст
        """
        try:
            if not os.path.exists(file_path):
                logger.error(f"Audio file not found at path: {file_path}")
                return None
                
            with open(file_path, "rb") as audio_file:
                response = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )
                return response

        except Exception as e:
            logger.error(f"Error transcribing audio:\n{str(e)}\n")
            logger.error(traceback.format_exc())
            return None

    def analyze_ivr_menu(self, transcription: str, sequence: str = "no previous keys pressed") -> list:
        """
        Анализирует текст на наличие опций меню IVR.
        """
        try:
            prompt = (
                "You are an IVR menu analyzer. Your task is to identify DTMF options in the transcription.\n\n"
                f"Context: Previous key sequence: {sequence}\n\n"
                "Rules:\n"
                "1. Return ONLY a JSON array of objects with structure:\n"
                "   {\n"
                '     "digit": "string (the button to press)",\n'
                '     "action": "string (what happens when pressed)",\n'
                '     "submenu": boolean (true if this leads to another menu)\n'
                "   }\n"
                "2. Include only explicitly mentioned number options\n"
                "3. Return [] if no options found\n"
                "4. DO NOT include any explanatory text, only the JSON array\n\n"
                f"Transcription:\n{transcription}\n"
            )

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a JSON-only responder. Only output valid JSON arrays containing DTMF menu options. No explanatory text."
                    },
                    {
                        "role": "user", 
                        "content": prompt
                    }
                ],
                temperature=0
            )
            
            content = response.choices[0].message.content.strip()
            logger.info(f"GPT response for IVR menu: {content}")
            
            try:
                # Удаляем markdown обёртку, если она есть
                content = content.replace('```json', '').replace('```', '').strip()
                
                # Пытаемся распарсить ответ как JSON
                options = json.loads(content)
                if isinstance(options, list):
                    return options
                logger.warning(f"GPT returned non-list JSON: {content}")
                return []
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse GPT response as JSON: {content}")
                logger.error(f"JSON parse error: {str(e)}")
                
                # Пробуем извлечь JSON из текста
                match = re.search(r'\[(.*)\]', content, re.DOTALL)
                if match:
                    try:
                        options = json.loads(f"[{match.group(1)}]")
                        if isinstance(options, list):
                            return options
                    except:
                        pass
                
                # Если не удалось распарсить JSON, пробуем извлечь опции из текста
                options = []
                for line in content.split('\n'):
                    if 'press' in line.lower() and any(str(i) in line for i in range(10)):
                        # Извлекаем цифру
                        for i in range(10):
                            if str(i) in line:
                                options.append({
                                    'digit': str(i),
                                    'action': line.split('press')[1].strip(),
                                    'submenu': 'submenu' in line.lower() or 'menu' in line.lower()
                                })
                                break
                return options
                
        except Exception as e:
            logger.error(f"Error analyzing IVR menu: {str(e)}")
            logger.error(traceback.format_exc())
            return []

    def analyze_transcription_for_dtmf(self, transcription, phone_number_id):
        """
        Анализирует транскрипцию для определения необходимых DTMF последовательностей.
        Проверяет схожесть с предыдущими транскрипциями, чтобы избежать дублирования меню.
        """
        try:
            # Получаем последние транскрипции для этого номера
            from .models import CallRecord, DTMFSequence
            
            # Получаем текущую запись и её DTMF последовательность
            current_record = CallRecord.objects.filter(
                phone_number_id=phone_number_id,
                transcription=transcription
            ).first()
            
            sequence_str = "no previous keys pressed"
            if current_record and current_record.dtmf_sequence:
                sequence_str = "->".join(str(x) for x in current_record.dtmf_sequence)
            
            # Получаем существующие DTMF последовательности
            existing_sequences = DTMFSequence.objects.filter(
                phone_number_id=phone_number_id
            ).values_list('sequence', flat=True)
            
            # Анализируем новую транскрипцию с учетом последовательности
            dtmf_options = self.analyze_ivr_menu(transcription, sequence_str)
            
            # Если нашли опции, проверяем каждую
            if dtmf_options:
                # Фильтруем только новые опции и создаем последовательности
                new_options = []
                for option in dtmf_options:
                    # Если это подменю, получаем родительские последовательности
                    if option.get('submenu', False):
                        # Находим существующие последовательности, которые ведут к подменю
                        parent_sequences = DTMFSequence.objects.filter(
                            phone_number_id=phone_number_id,
                            is_submenu=True
                        ).values_list('sequence', flat=True)
                        
                        # Если есть родительские последовательности, создаем новые с их учетом
                        if parent_sequences:
                            for parent_seq in parent_sequences:
                                new_sequence = parent_seq + [option['digit']]
                                if new_sequence not in existing_sequences:
                                    new_option = option.copy()
                                    new_option['sequence'] = new_sequence
                                    new_options.append(new_option)
                        else:
                            # Если нет родительских последовательностей, добавляем как обычную опцию
                            if [option['digit']] not in existing_sequences:
                                option['sequence'] = [option['digit']]
                                new_options.append(option)
                    else:
                        # Для обычных опций просто проверяем наличие
                        if [option['digit']] not in existing_sequences:
                            option['sequence'] = [option['digit']]
                            new_options.append(option)
                
                if new_options:
                    logger.info(f"Found {len(new_options)} new DTMF options with sequences")
                    return new_options
                else:
                    logger.info("All DTMF options already exist")
                    return []
            else:
                logger.info("No DTMF options found in transcription")
                return []
        
        except Exception as e:
            logger.error(f"Error in analyze_transcription_for_dtmf: {str(e)}")
            logger.error(traceback.format_exc())
            return []

    def is_similar_transcription(self, transcription1: str, transcription2: str, threshold: float = 0.8) -> bool:
        """
        Проверяет, являются ли две транскрипции похожими (то есть описывают одно и то же меню).
        
        Args:
            transcription1: Первая транскрипция
            transcription2: Вторая транскрипция
            threshold: Порог схожести (0.0 - 1.0)
            
        Returns:
            bool: True если транскрипции похожи, False иначе
        """
        # Нормализуем тексты
        def normalize_text(text):
            # Приводим к нижнему регистру и удаляем пунктуацию
            text = text.lower()
            text = ''.join(c for c in text if c.isalnum() or c.isspace())
            # Разбиваем на слова и сортируем
            words = set(text.split())
            return words
        
        # Получаем множества слов
        words1 = normalize_text(transcription1)
        words2 = normalize_text(transcription2)
        
        # Считаем коэффициент Жаккара
        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))
        
        if union == 0:
            return False
            
        similarity = intersection / union
        logger.info(f"Transcription similarity: {similarity}")
        
        return similarity >= threshold

    def create_summary(self, phone_number: str, transcriptions: list[str]) -> str:
        """Создание summary на основе транскрипций."""
        try:
            # Объединяем все транскрипции в один текст
            all_transcriptions = "\n=== Next Transcription ===\n".join(transcriptions)
            
            prompt = (
                "You are a helper who creates short descriptions of phone menu navigation.\n\n"
                "Task: Provide a highly detailed list of all possible and available keystroke sequences and their results, "
                "including single key presses, sequences of key presses (e.g., '1-2-3'), and language selection presses. "
                "If there are no keys to press, provide voice commands that achieve the desired result. "
                "Use the context from the filenames to determine the company name or service.\n\n"
                "Instructions:\n"
                "- Format: For each item, use one of the following formats:\n"
                "  - For key presses:\n"
                "    - 'Call [Company Name or Service] [phone number, if available], press [sequence of numbers] to [end result].'\n"
                "  - For voice commands:\n"
                "    - 'Call [Company Name or Service] [phone number, if available], say \"[voice command]\" to [end result].'\n"
                "- Requirements:\n"
                "  - Include both single keystroke options and the sequences that follow them.\n"
                "  - Include all possible paths, even if they lead to the same result.\n"
                "  - Do not add any additional comments or explanations.\n"
                "  - Use the filenames indicated in the format '=== File: filename ===' to determine the context and company name.\n"
                "  - Your answer must be IN ENGLISH.\n\n"
                "Examples of correct answers (from other texts, please do the same):\n\n"
                "[START EXAMPLE]\n"
                f"- Call [Company Name or Service, if available] at {phone_number}, press 1 to resolve billing issues related to charges on your account.\n"
                f"- Call [Company Name or Service, if available] at {phone_number}, press 1-2-1-1 to book a new reservation using miles for 1 passenger.\n"
                "[END EXAMPLE]\n\n"
                f"Here are the transcriptions for phone number {phone_number}:\n\n{all_transcriptions}"
            )
            
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.client.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0
                }
            )
            response.raise_for_status()
            
            response_json = response.json()
            logger.info(f"GPT summary response: {response_json}")
            
            content = response_json["choices"][0]["message"]["content"].strip()
            return content
            
        except Exception as e:
            logger.error(f"Error creating summary: {str(e)}")
            return None

    def analyze_summary_for_dtmf(self, summary: str):
        """
        Анализирует summary телефонного номера для поиска DTMF опций.
        """
        if not summary:
            return []
            
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """You are analyzing a phone number summary to extract DTMF menu options.
                        Look for any mentions of button presses, menu options, or numeric choices.
                        
                        Rules:
                        1. For each menu option, identify:
                           - The button number (DTMF digit)
                           - The corresponding action or submenu
                        2. Return a JSON array of objects with structure:
                           {
                             "digit": "string (the button to press)",
                             "action": "string (what happens when pressed)",
                             "submenu": boolean (true if this leads to another menu)
                           }
                        3. Only include clearly stated options
                        4. If no menu options are found, return an empty array
                        
                        Example input: "This is an automated system. Press 1 for sales, 2 for support menu, or 3 to leave a message."
                        Example output:
                        [
                          {"digit": "1", "action": "sales", "submenu": false},
                          {"digit": "2", "action": "support menu", "submenu": true},
                          {"digit": "3", "action": "leave a message", "submenu": false}
                        ]"""
                    },
                    {
                        "role": "user",
                        "content": summary
                    }
                ],
                temperature=0
            )
            
            content = response.choices[0].message.content
            try:
                # Удаляем markdown обёртку, если она есть
                content = content.replace('```json', '').replace('```', '').strip()
                
                menu_options = json.loads(content)
                logger.info(f"Successfully parsed menu options from summary: {menu_options}")
                return menu_options
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse GPT response as JSON: {content}")
                logger.error(f"JSON parse error: {str(e)}")
                return []
                
        except Exception as e:
            logger.error(f"Error analyzing summary for DTMF: {str(e)}")
            logger.error(traceback.format_exc())
            return []

class CallManager:
    def __init__(self):
        self.api_url = f"http://{settings.CALLER_SERVER_IP}:{settings.CALLER_SERVER_PORT}/caller/"
        logger.info(f"Initialized CallManager with API URL: {self.api_url}")

    def make_call(self, phone_number, dtmf_sequence=None):
        """
        Выполняет звонок на указанный номер с опциональной DTMF последовательностью
        Args:
            phone_number (str): Номер телефона для звонка
            dtmf_sequence (list): Список словарей с ключами 'digit' и 'delay'
        Returns:
            str: Имя файла записи или None в случае ошибки
        """
        try:
            # Подготавливаем DTMF последовательность в нужном формате
            dtmf = []
            if dtmf_sequence:
                for item in dtmf_sequence:
                    digit = item['digit']
                    # Если в digit есть дефис, разбиваем на отдельные цифры
                    if '-' in str(digit):
                        digits = str(digit).split('-')
                        # Добавляем каждую цифру с тем же delay
                        for d in digits:
                            dtmf.append([int(d), item['delay']])
                    else:
                        dtmf.append([int(digit), item['delay']])
            
            # Формируем payload для API
            payload = {
                "number": phone_number,
                "dtmf": dtmf
            }

            logger.info(f"Making call to {phone_number} with DTMF sequence: {dtmf}")

            # Отправляем POST запрос к API
            response = requests.post(
                self.api_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            response.raise_for_status()
                
            # Получаем имя файла из ответа
            result = response.json()
            logger.info(f"API Response: {result}")  # Логируем полный ответ
                
            recording_name = result.get('recording', '')
            if recording_name:
                logger.info(f"Call successful, recording saved as: {recording_name}")
                return recording_name
            else:
                logger.error("No recording name in API response")
                return None

        except Exception as e:
            logger.error(f"Error making call to {phone_number}: {str(e)}")
            logger.error(traceback.format_exc())
            return None
