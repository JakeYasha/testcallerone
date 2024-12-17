FROM python:3.9

# Установка рабочей директории
WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Установка netcat для проверки готовности базы данных
RUN apt-get update && apt-get install -y netcat-openbsd postgresql-client && rm -rf /var/lib/apt/lists/*


# Копирование entrypoint скрипта
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Копирование кода приложения
COPY ./app .

# Запуск entrypoint скрипта
ENTRYPOINT ["/entrypoint.sh"]
