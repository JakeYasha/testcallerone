# Система автоматического обзвона

Система для автоматического обзвона телефонных номеров с записью и анализом DTMF последовательностей.

## Основные возможности

- Извлечение телефонных номеров из текста с помощью gpt-4o-mini-mini
- Автоматический обзвон номеров
- Запись разговоров
- Анализ и сохранение DTMF последовательностей
- Веб-интерфейс для управления и мониторинга

## Требования

- Docker и Docker Compose
- Asterisk сервер с настроенным ARI
- OpenAI API ключ для gpt-4o-mini

## Установка и запуск

1. Клонируйте репозиторий:
```bash
git clone <repository-url>
cd <repository-directory>
```

2. Создайте файл .env в корневой директории:
```
OPENAI_API_KEY=your-openai-api-key
ARI_URL=http://your-asterisk-server:8088
ARI_USERNAME=your-ari-username
ARI_PASSWORD=your-ari-password
```

3. Запустите приложение с помощью Docker Compose:
```bash
docker-compose up -d
```

4. Примените миграции базы данных:
```bash
docker-compose exec web python manage.py migrate
```

5. Создайте суперпользователя (опционально):
```bash
docker-compose exec web python manage.py createsuperuser
```

## Структура проекта

- `app/` - Django приложение
  - `calls/` - Основное приложение для работы с звонками
  - `core/` - Настройки проекта
- `docker/` - Файлы Docker
- `recordings/` - Директория для хранения записей разговоров

## Использование

1. Откройте веб-интерфейс по адресу http://localhost:8000
2. Добавьте телефонные номера через форму "Добавить номера"
3. Система автоматически начнет обзвон и анализ номеров
4. Просматривайте результаты на странице деталей каждого номера

## Разработка

### Запуск тестов
```bash
docker-compose exec web python manage.py test
```

### Проверка кода
```bash
docker-compose exec web flake8
```

## Лицензия

MIT License
