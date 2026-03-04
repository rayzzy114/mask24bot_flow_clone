import re

with open("app/catalog.py", "r", encoding="utf-8") as f:
    content = f.read()

new_hints = """OPERATOR_HINTS = (
    "support",
    "оператор",
    "помощ",
    "тикет",
    "ticket",
    "админ",
    "admin",
    "поддержк",
)"""

content = re.sub(
    r"OPERATOR_HINTS = \([\s\S]*?\)",
    new_hints,
    content
)

with open("app/catalog.py", "w", encoding="utf-8") as f:
    f.write(content)
