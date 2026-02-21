import hashlib
import hmac
from urllib.parse import parse_qsl


def validate_telegram_init_data(init_data: str, bot_token: str) -> bool:
    if not init_data or not bot_token:
        return False

    pairs = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return False

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    # Telegram spec: secret_key = SHA256(bot_token)
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    return hmac.compare_digest(calculated_hash, received_hash)
