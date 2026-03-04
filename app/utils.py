import hashlib
import re
from typing import Iterable

# Base58 characters
B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58_decode(v: str) -> bytes | None:
    decimal = 0
    for char in v:
        if char not in B58_ALPHABET:
            return None
        decimal = decimal * 58 + B58_ALPHABET.index(char)
    
    res = []
    while decimal > 0:
        res.append(decimal % 256)
        decimal //= 256
    
    # Handle leading '1's
    pad = 0
    for char in v:
        if char == '1': pad += 1
        else: break
        
    return bytes([0] * pad + res[::-1])


def validate_base58_checksum(address: str) -> bool:
    """Validate a Base58Check encoded address (BTC, TRX, LTC legacy)."""
    try:
        decoded = base58_decode(address)
        if not decoded or len(decoded) < 5:
            return False
        
        data, checksum = decoded[:-4], decoded[-4:]
        hash1 = hashlib.sha256(data).digest()
        hash2 = hashlib.sha256(hash1).digest()
        
        return hash2[:4] == checksum
    except Exception:
        return False


def is_valid_crypto_address(address: str, symbol: str) -> bool:
    """Enhanced address validation with checksums."""
    address = address.strip()
    symbol = symbol.upper()
    
    if symbol == "BTC":
        # Segwit (bech32) starts with bc1
        if address.lower().startswith("bc1"):
            return len(address) >= 42 and all(c in "qpzry9x8gf2tvdw0s3jn54khce6mua7l" for c in address[3:].lower())
        # Legacy/P2SH starts with 1 or 3
        if address.startswith(("1", "3")):
            return validate_base58_checksum(address)
        return False
        
    if symbol in ("TRX", "USDT"): # TRX and USDT TRC20
        if not address.startswith("T"):
            return False
        return validate_base58_checksum(address)
        
    if symbol == "ETH":
        return bool(re.match(r"^0x[a-fA-F0-9]{40}$", address))
        
    if symbol == "LTC":
        if address.startswith(("L", "M", "3")):
            return validate_base58_checksum(address)
        return False

    return len(address) > 20 # Fallback for others


def _parse_float(raw: str) -> float | None:
    cleaned = raw.strip().replace(" ", "").replace(",", ".")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_admin_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.add(int(chunk))
        except ValueError:
            continue
    return result


def parse_amount(raw: str) -> float | None:
    value = _parse_float(raw)
    if value is None:
        return None
    if value <= 0:
        return None
    return value


def parse_non_negative_amount(raw: str) -> float | None:
    value = _parse_float(raw)
    if value is None:
        return None
    if value < 0:
        return None
    return value


def fmt_money(value: float) -> str:
    return f"{round(value):,}".replace(",", " ")


def fmt_coin(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


def safe_username(username: str | None) -> str:
    if username:
        return f"@{username}"
    return "@N/A"


def first_or_none(values: Iterable[str]) -> str | None:
    for item in values:
        return item
    return None
