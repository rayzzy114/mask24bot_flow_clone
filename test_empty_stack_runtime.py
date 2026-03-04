import sys

with open("app/runtime.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "def _resolve_back_state" in line:
        for j in range(i, i+10):
            print(f"{j+1}: {lines[j].rstrip()}")
        break
