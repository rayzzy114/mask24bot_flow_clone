with open("app/storage.py", "r") as f:
    for i, line in enumerate(f.readlines()):
        if "def cleanup" in line:
            with open("app/storage.py", "r") as f2:
                print("".join(f2.readlines()[i:i+15]))
