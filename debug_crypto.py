from app.utils import base58_decode, validate_base58_checksum, B58_ALPHABET

def debug():
    addr = "TGE2wN657wEEDwUoB2w1y7g9x7xX4fD8gK"
    print("Alphabet:", B58_ALPHABET)
    print("decoded base58:", base58_decode(addr))
    print("valid?", validate_base58_checksum(addr))

if __name__ == "__main__":
    debug()
