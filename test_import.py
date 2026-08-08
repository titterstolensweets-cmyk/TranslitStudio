import sys
import os

print("=== Диагностика импорта модулей ===")
print(f"Текущая рабочая директория: {os.getcwd()}")
print(f"Директория скрипта: {os.path.dirname(os.path.abspath(__file__))}")
print()
print("sys.path:")
for i, path in enumerate(sys.path):
    print(f"  [{i}] {path}")
print()

# Проверяем наличие папки modules
script_dir = os.path.dirname(os.path.abspath(__file__))
modules_dir = os.path.join(script_dir, 'modules')
print(f"Папка modules существует: {os.path.exists(modules_dir)}")
print(f"Путь к папке: {modules_dir}")

if os.path.exists(modules_dir):
    print(f"Файлы в папке modules:")
    for f in os.listdir(modules_dir):
        print(f"  - {f}")

# Пробуем импортировать
print()
try:
    from modules.shortcut_display import format_shortcut_for_display
    print("✅ Импорт успешен!")
except Exception as e:
    print(f"❌ Ошибка импорта: {e}")