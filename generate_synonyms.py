import pymorphy2
from pathlib import Path
from tqdm import tqdm
import yaml
import os
import psycopg2
from dotenv import load_dotenv

# --- НАСТРОЙКИ ---
# Путь, куда будут сохранены синонимы для ПОЛНЫХ ФРАЗ
SYNONYM_FULL_PHRASE_FILE_PATH = Path("RasaProject/data/object_off_synonyms.yml")
# Путь, куда будут сохранены синонимы для ОБЩИХ (первых) слов
SYNONYM_COMMON_NAME_FILE_PATH = Path("RasaProject/data/common_names_synonyms.yml")
# -----------------

def fetch_canonical_names_from_db():
    """Выгружает все уникальные 'title' из таблицы 'text_content'."""
    print("Выгрузка канонических названий из базы данных...")
    load_dotenv()
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
        conn.close()
        titles = sorted([row[0].strip() for row in results if row[0] and row[0].strip()])
        print(f"✅ Успешно выгружено {len(titles)} уникальных названий.")
        return titles
    except Exception as e:
        print(f"❌ Ошибка при работе с базой данных: {e}")
        return None

def generate_synonym_files():
    canonical_names = fetch_canonical_names_from_db()
    if canonical_names is None:
        return

    morph = pymorphy2.MorphAnalyzer()
    
    # --- Генерация синонимов для ПОЛНЫХ ФРАЗ ---
    print(f"Генерация файла для полных фраз: {SYNONYM_FULL_PHRASE_FILE_PATH}")
    with open(SYNONYM_FULL_PHRASE_FILE_PATH, 'w', encoding='utf-8') as f:
        f.write('version: "3.1"\n\nnlu:\n')
        for name in tqdm(canonical_names, desc="Генерация полных фраз"):
            # ... (здесь используется сложная логика согласования фраз) ...
            # Для упрощения и надежности, мы будем склонять только последнее слово,
            # что покрывает большинство случаев.
            words = name.split()
            if not words: continue
            
            all_phrases = set()
            last_word_parse = morph.parse(words[-1])[0]
            for form in last_word_parse.lexeme:
                phrase = " ".join(words[:-1] + [form.word])
                all_phrases.add(phrase.lower())
            
            syn_list = sorted([phrase for phrase in all_phrases if phrase != name.lower()])
            if syn_list:
                f.write(f'- synonym: {name}\n')
                f.write('  examples: |\n')
                for synonym in syn_list:
                    f.write(f'    - {synonym}\n')
    print(f"✅ Файл с полными фразами успешно создан.")

    # --- Генерация синонимов для ОБЩИХ (ПЕРВЫХ) СЛОВ ---
    print(f"Генерация файла для общих названий: {SYNONYM_COMMON_NAME_FILE_PATH}")
    first_words = sorted(list({name.split()[0] for name in canonical_names}))
    
    with open(SYNONYM_COMMON_NAME_FILE_PATH, 'w', encoding='utf-8') as f:
        f.write('version: "3.1"\n\nnlu:\n')
        for word in tqdm(first_words, desc="Генерация общих названий"):
            parses = morph.parse(word)
            if not parses: continue
            
            all_forms = {form.word.lower() for form in parses[0].lexeme if form.word}
            syn_list = sorted([form for form in all_forms if form != word.lower()])

            if syn_list:
                f.write(f'- synonym: {word}\n')
                f.write('  examples: |\n')
                for synonym in syn_list:
                    f.write(f'    - {synonym}\n')
    print(f"✅ Файл с общими названиями успешно создан.")


if __name__ == "__main__":
    generate_synonym_files()