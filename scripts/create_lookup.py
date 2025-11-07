import os
import psycopg2
from dotenv import load_dotenv
from pathlib import Path

# --- НАСТРОЙКИ ---
# Путь, куда будет сохранен новый YML-файл с lookup-таблицей
OUTPUT_FILE_PATH = Path("RasaProject/data/object_off_lookup.yml")
# -----------------

def generate_lookup_yml_file():
    """
    Создает отдельный YML-файл с lookup-таблицей для Rasa,
    выгружая названия из базы данных.
    """
    load_dotenv()
    print(f"Начинаем создание lookup-файла: {OUTPUT_FILE_PATH}")

    # 1. Получаем список названий из базы данных
    try:
        db_config = {
            "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
            "password": os.environ["DB_PASSWORD"], "host": os.environ["DB_HOST"],
            "port": os.environ["DB_PORT"]
        }
        query = "SELECT DISTINCT title FROM text_content WHERE title IS NOT NULL AND title <> '';"
        conn = psycopg2.connect(**db_config)
        with conn.cursor() as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
            titles = sorted([row[0].strip() for row in results if row[0] and row[0].strip()])
        conn.close()
        print(f"✅ Успешно выгружено {len(titles)} уникальных названий из базы.")
    except Exception as e:
        print(f"❌ Ошибка при работе с базой данных: {e}")
        return

    # 2. Формируем содержимое для нового YML-файла
    yml_content = [
        'version: "3.1"\n\n',
        'nlu:\n',
        '- lookup: object_OFF\n',
        '  examples: |\n'
    ]
    for title in titles:
        yml_content.append(f'    - {title}\n')

    # 3. Создаем директорию, если нужно, и записываем файл
    try:
        OUTPUT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE_PATH, 'w', encoding='utf-8') as f:
            f.writelines(yml_content)
        
        print("-" * 30)
        print(f"✅ УСПЕШНО! Файл создан: {OUTPUT_FILE_PATH}")
        print(f"   Всего записано названий: {len(titles)}")
        print("-" * 30)
    except Exception as e:
        print(f"❌ Ошибка при записи файла {OUTPUT_FILE_PATH}: {e}")


if __name__ == "__main__":
    generate_lookup_yml_file()