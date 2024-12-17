import os
from pathlib import Path
from celery.schedules import crontab
from datetime import timedelta
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-your-secret-key-here'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'calls.apps.CallsConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'calls_db'),
        'USER': os.getenv('POSTGRES_USER', 'postgres'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'postgres'),
        'HOST': os.getenv('POSTGRES_HOST', 'db'),
        'PORT': os.getenv('POSTGRES_PORT', '5432'),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Europe/Riga'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Asterisk recordings
ASTERISK_RECORDING_PATH = "/var/spool/asterisk/recording"
RECORDINGS_PATH = "/recordings"

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Celery Configuration
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# soft и hard тайм-ауты для задач
CELERY_TASK_TIME_LIMIT = 300  # Максимальное время выполнения задачи (в секундах)
CELERY_TASK_SOFT_TIME_LIMIT = 200  # Мягкий тайм-аут, после которого задача может быть остановлена

# Настройка уровня логов
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

# Ограничение размера результатов задач
CELERY_RESULT_EXPIRES = 3600  # Хранить результаты в Redis только 1 час


# Celery Beat schedule configuration
CELERY_BEAT_SCHEDULE = {
    'process-unprocessed-recordings': {
        'task': 'calls.tasks.process_unprocessed_recordings',
        'schedule': crontab(minute='*/2'),  # каждые 2 минуты
    },
    'update-phone-summaries': {
        'task': 'calls.tasks.update_phone_summaries',
        'schedule': crontab(minute='*/20'),  # каждые 20 минут
    },
    'process-new-phones': {
        'task': 'calls.tasks.process_new_phones',
        'schedule': crontab(minute='*/2'),  # Каждые 2 минуты
    },
    'process-call-queue': {
        'task': 'calls.tasks.process_call_queue',
        'schedule': timedelta(seconds=10),  # Каждые 10 секунд
    },
    'analyze-recordings-for-dtmf': {
        'task': 'calls.tasks.analyze_recordings_for_dtmf',
        'schedule': crontab(minute='*/6'),  # Каждые 6 минут
    },
    'check-stalled-recordings': {
        'task': 'calls.tasks.check_stalled_recordings',
        'schedule': crontab(minute='*/15'),  # Каждые 15 минут
    },
    'check-unexplored-dtmf': {
        'task': 'calls.tasks.check_unexplored_dtmf',
        'schedule': crontab(minute='*/5'),  # Каждые 5 минут
    },
    'process-sms-every-minute': {
        'task': 'calls.tasks.process_sms_messages',
        'schedule': crontab(minute='*'),  # Каждую минуту
    },
}

# OpenAI Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Asterisk ARI Configuration
ARI_URL = os.getenv('ARI_URL', 'http://165.227.123.113:8088')
ARI_USERNAME = os.getenv('ARI_USERNAME', 'aridid')
ARI_PASSWORD = os.getenv('ARI_PASSWORD', '6TK5VA3zDSN01')

# Caller API settings
CALLER_SERVER_IP = "165.227.123.113"
CALLER_SERVER_PORT = 5050
