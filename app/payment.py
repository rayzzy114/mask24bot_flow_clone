import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from aiogram import Bot

from .context import AppContext
from .keyboards import kb_admin_order_confirm
from .utils import safe_username

logger = logging.getLogger(__name__)

RUB_AMOUNT_RE = re.compile(r"([0-9][0-9\s.,]{1,})\s*(?:RUB|руб)", re.IGNORECASE)
COIN_AMOUNT_RE = re.compile(
    r"([0-9]+(?:[.,][0-9]+)?)\s*(BTC|LTC|XMR|USDT|ETH|TRX|TON)\b",
    re.IGNORECASE,
)
WALLET_RE = re.compile(r"(?:кошелек|кошел[её]к|wallet)\s*:?\s*([^\n]+)", re.IGNORECASE)
ADDRESS_LINE_RE = re.compile(r"(?:перевод\s+[A-Z0-9$() ]+\s+по\s+адресу)\s*:?\s*([^\n]+)", re.IGNORECASE)


def parse_decimal(raw: str) -> float | None:
    cleaned = (raw or "").replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


class OrderExtractor:
    @staticmethod
    def extract_details(text: str) -> dict[str, Any]:
        amount_rub = 0.0
        coin_amount = 0.0
        coin_symbol = "BTC"
        wallet = "(не указан)"

        rub_matches = RUB_AMOUNT_RE.findall(text or "")
        if rub_matches:
            parsed_rub = parse_decimal(rub_matches[-1])
            if parsed_rub is not None:
                amount_rub = parsed_rub

        coin_matches = COIN_AMOUNT_RE.findall(text or "")
        if coin_matches:
            coin_amount_raw, coin_symbol_raw = coin_matches[-1]
            parsed_coin = parse_decimal(coin_amount_raw)
            if parsed_coin is not None:
                coin_amount = parsed_coin
            coin_symbol = coin_symbol_raw.upper()

        wallet_match = WALLET_RE.search(text or "")
        if not wallet_match:
            wallet_match = ADDRESS_LINE_RE.search(text or "")
        if wallet_match:
            wallet_value = wallet_match.group(1).strip()
            if wallet_value:
                wallet = wallet_value

        return {
            "amount_rub": amount_rub,
            "coin_amount": coin_amount,
            "coin_symbol": coin_symbol,
            "wallet": wallet,
        }


class PaymentHandler:
    def __init__(self, app_context: AppContext, project_dir: Path):
        self.app_context = app_context
        self.payment_proofs_path = project_dir / "data" / "admin" / "payment_proofs.json"
        self._ensure_payment_proofs_file()

    def _ensure_payment_proofs_file(self) -> None:
        self.payment_proofs_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.payment_proofs_path.exists():
            self.payment_proofs_path.write_text("[]\n", encoding="utf-8")

    async def forward_to_admins(
        self,
        bot: Bot,
        photo_file_id: str,
        caption: str,
        order_id: str | None = None,
    ) -> bool:
        markup = kb_admin_order_confirm(order_id) if order_id and order_id != "N/A" else None

        async def _send(admin_id: int) -> bool:
            try:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=photo_file_id,
                    caption=caption,
                    parse_mode=None,
                    reply_markup=markup,
                )
                return True
            except Exception as e:
                logger.error(f"Failed to forward payment to admin {admin_id}: {e}")
                return False

        results = await asyncio.gather(*[_send(aid) for aid in self.app_context.admin_ids])
        return any(results)

    def store_payment_proof(
        self,
        *,
        user_id: int,
        username: str,
        order_id: str,
        order_context: str,
        photo_file_id: str,
        forwarded_to_admins: bool,
    ) -> None:
        try:
            payload = json.loads(self.payment_proofs_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to read payment proofs: {e}")
            payload = []

        if not isinstance(payload, list):
            payload = []

        payload.append(
            {
                "user_id": int(user_id),
                "username": username,
                "order_id": order_id,
                "order_context": order_context,
                "photo_file_id": photo_file_id,
                "forwarded_to_admins": bool(forwarded_to_admins),
            }
        )

        try:
            self.payment_proofs_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save payment proof: {e}")

    def build_admin_caption(
        self,
        *,
        order_id: str,
        user_id: int,
        username: str,
        order_context: str,
    ) -> str:
        lines = [
            "Новая оплата от пользователя",
            f"order_id: {order_id}",
            f"user_id: {user_id}",
            f"username: {safe_username(username)}",
        ]
        context = (order_context or "").strip()
        if context:
            lines.append("")
            lines.append("Контекст заявки:")
            lines.append(context[:1200])
        return "\n".join(lines)
