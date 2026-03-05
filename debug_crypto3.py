import hashlib
from app.utils import base58_decode

def is_valid_crypto_address(address: str, symbol: str) -> bool:
    """Enhanced address validation with checksums."""
    address = address.strip()
    symbol = symbol.upper()

    if symbol == "BTC":
        if address.lower().startswith("bc1"):
            return len(address) >= 42 and all(c in "qpzry9x8gf2tvdw0s3jn54khce6mua7l" for c in address[3:].lower())
        if address.startswith(("1", "3")):
            return validate_base58_checksum(address)
        return False

    if symbol in ("TRX", "USDT"):
        if not address.startswith("T"):
            return False
        return validate_base58_checksum(address)

    # ... rest
    return False

def validate_base58_checksum(address: str) -> bool:
    try:
        decoded = base58_decode(address)
        if not decoded or len(decoded) < 5:
            return False

        data, checksum = decoded[:-4], decoded[-4:]

        # In Tron, the address starts with 0x41. Wait, base58 decode of T-address gives 0x41 followed by 20 bytes.
        # Checksum is sha256(sha256(data))[:4]

        hash1 = hashlib.sha256(data).digest()
        hash2 = hashlib.sha256(hash1).digest()

        return hash2[:4] == checksum
    except Exception:
        return False

# Test with a known valid TRX address
trx_addr = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t" # USDT token contract on Tron
print(f"TRX {trx_addr} valid? {validate_base58_checksum(trx_addr)}")
