import hashlib
from app.utils import base58_decode

def validate_base58_checksum(address: str) -> bool:
    try:
        decoded = base58_decode(address)
        if not decoded or len(decoded) < 5:
            return False

        data, checksum = decoded[:-4], decoded[-4:]
        hash1 = hashlib.sha256(data).digest()
        hash2 = hashlib.sha256(hash1).digest()

        print(f"data hex: {data.hex()}")
        print(f"expected checksum: {hash2[:4].hex()}")
        print(f"actual checksum: {checksum.hex()}")

        return hash2[:4] == checksum
    except Exception:
        return False

print("TRX Validating:")
validate_base58_checksum("TGE2wN657wEEDwUoB2w1y7g9x7xX4fD8gK")
print("BTC Validating:")
validate_base58_checksum("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
