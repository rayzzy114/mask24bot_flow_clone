import re

with open("app/overrides.py", "r", encoding="utf-8") as f:
    content = f.read()

new_parse_money = """def _parse_money_value(token: str) -> float | None:
    cleaned = (token or "").replace("\\u00a0", " ").replace(" ", "")
    # Handle European format 1.000,50 -> 1000.50
    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(".") > cleaned.rfind(","):
            # Format: 1,000.50
            cleaned = cleaned.replace(",", "")
        else:
            # Format: 1.000,50
            cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        # Either no thousand separator, or only one type of separator
        cleaned = cleaned.replace(",", ".")

    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None"""

content = re.sub(
    r"def _parse_money_value\(token: str\) -> float \| None:\n.*?(?=\n\ndef )",
    new_parse_money,
    content,
    flags=re.DOTALL
)

with open("app/overrides.py", "w", encoding="utf-8") as f:
    f.write(content)
