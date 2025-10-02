import pymorphy2
from pathlib import Path
from tqdm import tqdm
import yaml
import os
import psycopg2
from dotenv import load_dotenv
import json

# --- НАСТРОЙКИ ---
# Промежуточный файл со списком канонических названий (будет создан автоматически)
CANONICAL_GEO_NAMES_FILE = Path("RasaProject/data/lookup/geo_places.txt")

# Финальный YML-файл с синонимами для Rasa
SYNONYM_FILE_PATH = Path("RasaProject/data/geo_place_synonyms.yml")
# -----------------

def fetch_canonical_geo_names():
    """
    Этап 1: Выгружает все 'simplified_name' из таблицы geographical_entity
    и сохраняет их в текстовый файл.
    """
    print("Этап 1: Выгрузка канонических названий топонимов из базы данных...")
    load_dotenv()
    try:
        db_config = {
            "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
            "password": os.environ["DB_PASSWORD"], "host": os.environ["DB_HOST"],
            "port": os.environ["DB_PORT"]
        }
        # Запрос для извлечения поля 'simplified_name' из JSONB
        query = "SELECT feature_data->>'simplified_name' as simplified_name FROM geographical_entity WHERE feature_data->>'simplified_name' IS NOT NULL;"
        conn = psycopg2.connect(**db_config)
        with conn.cursor() as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
        conn.close()
        
        # Собираем уникальные, непустые названия
        unique_names = sorted(list({row[0].strip() for row in results if row[0] and row[0].strip()}))
        
        # Создаем директорию, если ее нет
        CANONICAL_GEO_NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        # Записываем в файл
        with open(CANONICAL_GEO_NAMES_FILE, 'w', encoding='utf-8') as f:
            for name in unique_names:
                f.write(f"{name}\n")

        print(f"✅ Успешно выгружено и сохранено {len(unique_names)} уникальных топонимов в {CANONICAL_GEO_NAMES_FILE}")
        return unique_names
    except Exception as e:
        print(f"❌ Ошибка при работе с базой данных на Этапе 1: {e}")
        return None

def generate_synonym_file(canonical_names):
    """
    Этап 2: Читает список канонических названий и генерирует для них YML-файл с синонимами.
    """
    if not canonical_names:
        print("Нет названий для генерации синонимов. Пропускаем Этап 2.")
        return

    print("\nЭтап 2: Генерация файла синонимов для Rasa...")
    morph = pymorphy2.MorphAnalyzer()
    
    with open(SYNONYM_FILE_PATH, 'w', encoding='utf-8') as f:
        f.write('version: "3.1"\n\nnlu:\n')
        for name in tqdm(canonical_names, desc="Генерация синонимов"):
            words = name.split()
            if not words: continue

            all_phrases = set()
            
            # Находим главное слово (первое существительное или последнее)
            main_word_index = -1
            for i, word in enumerate(words):
                if 'NOUN' in morph.parse(word)[0].tag:
                    main_word_index = i
                    break
            if main_word_index == -1: main_word_index = len(words) - 1

            main_word_parse = morph.parse(words[main_word_index])[0]

            for main_word_form in main_word_parse.lexeme:
                required_grammemes = set()
                if main_word_form.tag.case: required_grammemes.add(main_word_form.tag.case)
                if main_word_form.tag.number: required_grammemes.add(main_word_form.tag.number)
                
                new_phrase_words = []
                is_possible = True
                for i, word in enumerate(words):
                    p = morph.parse(word)[0]
                    if 'ADJF' in p.tag:
                        inflected = p.inflect(required_grammemes)
                        new_phrase_words.append(inflected.word if inflected else word)
                    elif i == main_word_index:
                        new_phrase_words.append(main_word_form.word)
                    else:
                        new_phrase_words.append(word)
                
                all_phrases.add(" ".join(new_phrase_words).lower())

            # Добавляем варианты с предлогами
            prepositions = ["в", "на", "у", "о", "из", "с"]
            phrases_with_prepositions = set()
            for phrase in all_phrases:
                for prep in prepositions:
                    phrases_with_prepositions.add(f"{prep} {phrase}")

            all_phrases.update(phrases_with_prepositions)
            
            syn_list = sorted([phrase for phrase in all_phrases if phrase != name.lower()])
            if syn_list:
                f.write(f'- synonym: {name}\n')
                f.write('  examples: |\n')
                for synonym in syn_list:
                    f.write(f'    - {synonym}\n')

    print("-" * 30)
    print(f"✅ УСПЕШНО! Файл с синонимами создан: {SYNONYM_FILE_PATH}")
    print(f"   Обработано названий: {len(canonical_names)}")
    print("-" * 30)

if __name__ == "__main__":
    # Выполняем оба этапа последовательно
    names_from_db = fetch_canonical_geo_names()
    generate_synonym_file(names_from_db)