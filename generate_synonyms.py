import pymorphy2
from pathlib import Path
from tqdm import tqdm
import yaml
import os
import psycopg2
from dotenv import load_dotenv

# --- НАСТРОЙКИ ---
SYNONYM_FULL_PHRASE_FILE_PATH = Path("RasaProject/data/object_off_synonyms.yml")
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
            words = name.split()
            if len(words) < 2: continue

            all_phrases = set()
            
            main_word_index = -1
            for i, word in enumerate(words):
                if 'NOUN' in morph.parse(word)[0].tag:
                    main_word_index = i
                    break
            if main_word_index == -1: main_word_index = len(words) - 1

            main_word_parse = morph.parse(words[main_word_index])[0]

            for main_word_form in main_word_parse.lexeme:
                # --- НАЧАЛО ГЛАВНОГО ИСПРАВЛЕНИЯ ---
                required_grammemes = set()
                if main_word_form.tag.case:
                    required_grammemes.add(main_word_form.tag.case)
                if main_word_form.tag.number:
                    required_grammemes.add(main_word_form.tag.number)
                # --- КОНЕЦ ГЛАВНОГО ИСПРАВЛЕНИЯ ---

                new_phrase_words = []
                is_possible = True
                
                for i, word in enumerate(words):
                    p = morph.parse(word)[0]
                    if 'ADJF' in p.tag or 'PRTF' in p.tag:
                        inflected = p.inflect(required_grammemes) # Используем безопасный набор граммем
                        if inflected:
                            new_phrase_words.append(inflected.word)
                        else:
                            is_possible = False; break
                    elif i == main_word_index:
                        new_phrase_words.append(main_word_form.word)
                    else:
                        new_phrase_words.append(word)
                
                if is_possible:
                    all_phrases.add(" ".join(new_phrase_words).lower())

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