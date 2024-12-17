#!/bin/bash

# Ждем, пока база данных будет доступна
echo "Waiting for database..."
while ! nc -z db 5432; do
    sleep 0.1
done
echo "Database is up!"

# Ждем дополнительно для полной готовности базы
sleep 5

# Применяем миграции
echo "Applying migrations..."
python manage.py migrate auth
python manage.py migrate admin
python manage.py migrate contenttypes
python manage.py migrate sessions
python manage.py migrate --run-syncdb
python manage.py migrate calls

# Создаем суперпользователя
echo "Creating superuser..."
python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.create_superuser('admin', 'admin@example.com', 'admin') if not User.objects.filter(username='admin').exists() else None"

# Собираем статические файлы
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Запускаем команду, переданную в CMD
exec "$@"
