with open("app/overrides.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if line.startswith("def _parse_money_value(token: str) -> float | None:"):
        skip = True
        new_lines.append(line)
        new_lines.append('    cleaned = (token or "").replace("\\u00a0", " ").replace(" ", "")\n')
        new_lines.append('    if "." in cleaned and "," in cleaned:\n')
        new_lines.append('        if cleaned.rfind(".") > cleaned.rfind(","): cleaned = cleaned.replace(",", "")\n')
        new_lines.append('        else: cleaned = cleaned.replace(".", "").replace(",", ".")\n')
        new_lines.append('    else: cleaned = cleaned.replace(",", ".")\n')
        new_lines.append('    cleaned = re.sub(r"[^0-9.]", "", cleaned)\n')
        new_lines.append('    if not cleaned or cleaned.count(".") > 1: return None\n')
        new_lines.append('    try: return float(cleaned)\n')
        new_lines.append('    except ValueError: return None\n')
        continue

    if skip:
        if line.startswith("def "):
            skip = False
            new_lines.append("\n")
            new_lines.append(line)
    else:
        new_lines.append(line)

with open("app/overrides.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
