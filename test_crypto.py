from app.utils import is_valid_crypto_address

def main():
    print("TRX TRC20 valid:", is_valid_crypto_address("TGE2wN657wEEDwUoB2w1y7g9x7xX4fD8gK", "TRX"))
    print("TRX TRC20 invalid:", is_valid_crypto_address("TGE2wN657wEEDwUoB2w1y7g9x7xX4fD8gA", "TRX")) # Wrong checksum
    print("BTC valid legacy:", is_valid_crypto_address("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "BTC"))
    print("BTC valid segwit:", is_valid_crypto_address("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", "BTC"))

if __name__ == "__main__":
    main()
