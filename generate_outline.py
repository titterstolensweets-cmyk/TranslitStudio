import re

def generate_outline(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    outline = []
    for i, line in enumerate(lines, 1):
        # Ищем классы и функции на верхнем уровне
        if re.match(r'^(class|def)\s+\w+', line):
            indent = len(line) - len(line.lstrip())
            prefix = "  " * (indent // 4)
            outline.append(f"{i}: {prefix}{line.strip()}")

    with open('Supervertaler_outline.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(outline))
    print(f"Оглавление создано. Всего блоков: {len(outline)}")

generate_outline('Supervertaler.py')