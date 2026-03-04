from app.utils import _parse_float

def main():
    test_cases = [
        "100.5",
        "100,5",
        "1 000.5",
        "1 000,5",
        " 1000 ",
        "10.0.5",
        "10,0,5",
        "1000 руб.",
        "1.000,50",
    ]
    for case in test_cases:
        print(f"'{case}' -> {_parse_float(case)}")

if __name__ == "__main__":
    main()
