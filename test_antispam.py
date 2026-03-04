# Let's check how cleanup is done
with open("app/runtime.py", "r") as f:
    for i, line in enumerate(f.readlines()):
        if "def _cleanup_loop" in line:
            with open("app/runtime.py", "r") as f2:
                print("".join(f2.readlines()[i:i+20]))
