
import os
import logging
import psutil
from flask import Flask, render_template_string
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
from pathlib import Path

# --- НАСТРОЙКА ---
# Админ-панель будет работать на порту 5002
ADMIN_PORT = 5002
# -----------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)
app = Flask(__name__)
auth = HTTPBasicAuth()

# --- БЕЗОПАСНОСТЬ ---
# Убедитесь, что вы задали переменные окружения: ADMIN_USER и ADMIN_PASS
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'admin')

users = {
    ADMIN_USER: generate_password_hash(ADMIN_PASS)
}

@auth.verify_password
def verify_password(username, password):
    if username in users and \
            check_password_hash(users.get(username), password):
        return username

# --- HTML-ШАБЛОН АДМИНКИ ---
ADMIN_TEMPLATE = """
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Админ-панель EcoBot</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
      .log-box {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: .25rem;
        padding: 1rem;
        max-height: 600px;
        overflow-y: scroll;
        white-space: pre-wrap;
        font-family: monospace;
        font-size: 0.8rem;
      }
    </style>
  </head>
  <body>
    <div class="container mt-4">
      <h1>Админ-панель EcoBot</h1>
      
      <ul class="nav nav-tabs mt-4" id="myTab" role="tablist">
        <li class="nav-item" role="presentation">
          <button class="nav-link active" id="logs-tab" data-bs-toggle="tab" data-bs-target="#logs" type="button" role="tab">Логи</button>
        </li>
        <li class="nav-item" role="presentation">
          <button class="nav-link" id="metrics-tab" data-bs-toggle="tab" data-bs-target="#metrics" type="button" role="tab">Метрики</button>
        </li>
      </ul>

      <div class="tab-content" id="myTabContent">
        <div class="tab-pane fade show active" id="logs" role="tabpanel">
          <div class="row mt-3">
            <div class="col-md-6">
              <h4>Основной лог (bot.log)</h4>
              <div class="log-box">{{ bot_log }}</div>
            </div>
            <div class="col-md-6">
              <h4>Нераспознанные запросы (unhandled_queries.log)</h4>
              <div class="log-box">{{ unhandled_log }}</div>
            </div>
          </div>
        </div>
        <div class="tab-pane fade" id="metrics" role="tabpanel">
          <div class="row mt-3">
             <h4>Технические метрики</h4>
             <ul class="list-group">
                <li class="list-group-item d-flex justify-content-between align-items-center">
                    Нагрузка на CPU
                    <span class="badge bg-primary rounded-pill">{{ metrics.cpu_percent }} %</span>
                </li>
                <li class="list-group-item d-flex justify-content-between align-items-center">
                    Использование памяти (всем сервером)
                    <span class="badge bg-primary rounded-pill">{{ metrics.memory_mb }} MB</span>
                </li>
             </ul>
             <h4 class="mt-4">Пользовательские метрики (MVP)</h4>
             <ul class="list-group">
                <li class="list-group-item d-flex justify-content-between align-items-center">
                    Всего нераспознанных запросов
                    <span class="badge bg-danger rounded-pill">{{ metrics.unhandled_count }}</span>
                </li>
             </ul>
          </div>
        </div>
      </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
  </body>
</html>
"""

def read_log_tail(log_path, num_lines=100):
    """Читает последние N строк из файла."""
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # Возвращаем в обратном порядке, чтобы новые были сверху
            return ''.join(reversed(lines[-num_lines:]))
    except FileNotFoundError:
        return f"Ошибка: файл {log_path} не найден."
    except Exception as e:
        return f"Ошибка при чтении файла {log_path}: {e}"

@app.route('/admin')
@auth.login_required
def admin_panel_route():
    # Пути к логам строятся относительно текущего файла
    current_dir = Path(__file__).parent
    bot_log_content = read_log_tail(current_dir / 'bot.log')
    unhandled_log_content = read_log_tail(current_dir / 'unhandled_queries.log')
    
    # Сбор метрик
    metrics_data = {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_mb": round(psutil.virtual_memory().used / (1024 * 1024), 2),
    }
    try:
        with open(current_dir / 'unhandled_queries.log', 'r', encoding='utf-8') as f:
            metrics_data["unhandled_count"] = sum(1 for _ in f)
    except FileNotFoundError:
        metrics_data["unhandled_count"] = 0

    return render_template_string(
        ADMIN_TEMPLATE, 
        bot_log=bot_log_content, 
        unhandled_log=unhandled_log_content,
        metrics=metrics_data
    )

if __name__ == '__main__':
    logger.info(f"Запуск Админ-панели на http://0.0.0.0:{ADMIN_PORT}/admin")
    app.run(host='0.0.0.0', port=ADMIN_PORT, debug=False)