import json
import os
import random
import time
from pathlib import Path
from typing import Literal, TypedDict, cast

import aiofiles

from .constants import DEFAULT_PAYMENT_METHODS, DEFAULT_SELL_WALLETS, SELL_WALLET_LABELS


async def _atomic_save(path: Path, data: dict | list) -> None:
    """Saves data to a JSON file atomically using a temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    async with aiofiles.open(temp_path, mode="w", encoding="utf-8") as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(temp_path, path)


class SplitMethodRequisitesData(TypedDict):
    bank: str
    value: str


class RequisitesData(TypedDict):
    mode: Literal["single", "split"]
    single_bank: str
    single_value: str
    payment_methods: list[str]
    split_by_method: dict[str, SplitMethodRequisitesData]


class SettingsData(TypedDict):
    commission_percent: float
    links: dict[str, str]
    sell_wallets: dict[str, str]
    requisites: RequisitesData


class HistoryEntry(TypedDict):
    ts: int
    side: str
    coin: str
    amount_coin: float
    amount_rub: float


class UserProfile(TypedDict):
    trades_total: int
    turnover_rub: float
    invited: int
    bonus_balance: float
    history: list[HistoryEntry]
    addresses: list[dict[str, str]]


class OrderData(TypedDict):
    order_id: str
    user_id: int
    username: str
    wallet: str
    coin_symbol: str
    coin_amount: float
    amount_rub: float
    payment_method: str
    bank: str
    status: Literal["pending_payment", "paid", "confirmed", "cancelled"]
    created_at: int
    updated_at: int
    confirmed_by: int | None


def _get_int(d: dict, key: str, default: int = 0) -> int:
    val = d.get(key)
    return int(val) if isinstance(val, (int, float)) else default


def _get_float(d: dict, key: str, default: float = 0.0) -> float:
    val = d.get(key)
    return float(val) if isinstance(val, (int, float)) else default


def _get_str(d: dict, key: str, default: str = "") -> str:
    val = d.get(key)
    return str(val).strip() if val is not None else default


class SettingsStore:
    def __init__(self, path: Path, default_commission: float, env_links: dict[str, str]):
        self.path = path
        self.data: SettingsData = {
            "commission_percent": float(default_commission),
            "links": dict(env_links),
            "sell_wallets": dict(DEFAULT_SELL_WALLETS),
            "requisites": {
                "mode": "single",
                "single_bank": "Сбербанк",
                "single_value": "2200 0000 0000 0000",
                "payment_methods": list(DEFAULT_PAYMENT_METHODS),
                "split_by_method": {},
            },
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.save_sync()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            raw = {}

        self.data["commission_percent"] = _get_float(raw, "commission_percent", self.data["commission_percent"])

        links_raw = raw.get("links")
        if isinstance(links_raw, dict):
            for key, value in links_raw.items():
                if isinstance(key, str) and isinstance(value, str):
                    self.data["links"][key] = value

        sell_wallets_raw = raw.get("sell_wallets")
        if isinstance(sell_wallets_raw, dict):
            for key, value in sell_wallets_raw.items():
                if isinstance(key, str) and key in SELL_WALLET_LABELS and isinstance(value, str):
                    self.data["sell_wallets"][key] = value.strip()

        req_raw = raw.get("requisites")
        if isinstance(req_raw, dict):
            req = self.data["requisites"]
            mode_raw = req_raw.get("mode")
            if mode_raw in {"single", "split"}:
                req["mode"] = cast(Literal["single", "split"], mode_raw)

            req["single_bank"] = _get_str(req_raw, "single_bank", req["single_bank"])
            req["single_value"] = _get_str(req_raw, "single_value", req["single_value"])

            methods_raw = req_raw.get("payment_methods")
            if isinstance(methods_raw, list):
                methods = [str(item).strip() for item in methods_raw if item]
                if methods:
                    req["payment_methods"] = methods

            split_raw = req_raw.get("split_by_method")
            if isinstance(split_raw, dict):
                for method, val in split_raw.items():
                    if isinstance(method, str) and isinstance(val, dict):
                        bank = _get_str(val, "bank")
                        value = _get_str(val, "value")
                        if bank and value:
                            req["split_by_method"][method.strip()] = {"bank": bank, "value": value}

            # Legacy migration
            if not req["split_by_method"] and "split" in req_raw:
                legacy = req_raw["split"]
                if isinstance(legacy, dict):
                    bank = _get_str(legacy, "selected_bank", req["single_bank"])
                    val = req["single_value"]
                    banks = legacy.get("banks")
                    if isinstance(banks, dict) and bank in banks:
                        val = _get_str(banks, bank, val)
                    for m in req["payment_methods"]:
                        req["split_by_method"][m] = {"bank": bank, "value": val}

        self._normalize_split_map()
        self.save_sync()

    def save_sync(self) -> None:
        """Synchronous save used only during initialization."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def save(self) -> None:
        await _atomic_save(self.path, self.data)

    def _normalize_split_map(self) -> None:
        req = self.data["requisites"]
        split = req["split_by_method"]
        for method in req["payment_methods"]:
            row = split.get(method)
            if row is not None and row["bank"].strip() and row["value"].strip():
                continue
            split[method] = {
                "bank": req["single_bank"],
                "value": req["single_value"],
            }
        for key in list(split.keys()):
            if key not in req["payment_methods"]:
                split.pop(key, None)

    @property
    def commission_percent(self) -> float:
        return self.data["commission_percent"]

    async def set_commission(self, value: float) -> None:
        self.data["commission_percent"] = float(value)
        await self.save()

    def link(self, key: str) -> str:
        return self.data["links"].get(key, "")

    def all_links(self) -> dict[str, str]:
        return dict(self.data["links"])

    async def set_link(self, key: str, value: str) -> None:
        self.data["links"][key] = value
        await self.save()

    def sell_wallet(self, key: str) -> str:
        return self.data["sell_wallets"].get(key, "")

    def all_sell_wallets(self) -> dict[str, str]:
        return dict(self.data["sell_wallets"])

    async def set_sell_wallet(self, key: str, value: str) -> bool:
        wallet_key = key.strip().lower()
        if wallet_key not in SELL_WALLET_LABELS:
            return False
        wallet_value = value.strip()
        if not wallet_value or len(wallet_value) > 256:
            return False
        self.data["sell_wallets"][wallet_key] = wallet_value
        await self.save()
        return True

    @property
    def requisites_mode(self) -> Literal["single", "split"]:
        return self.data["requisites"]["mode"]

    async def set_requisites_mode(self, mode: Literal["single", "split"]) -> None:
        self.data["requisites"]["mode"] = mode
        self._normalize_split_map()
        await self.save()

    async def toggle_requisites_mode(self) -> None:
        new_mode: Literal["single", "split"] = (
            "split" if self.requisites_mode == "single" else "single"
        )
        await self.set_requisites_mode(new_mode)

    @property
    def requisites_bank(self) -> str:
        return self.data["requisites"]["single_bank"]

    @property
    def requisites_value(self) -> str:
        return self.data["requisites"]["single_value"]

    async def set_requisites_bank(self, bank: str) -> None:
        bank = bank.strip()
        if not bank:
            return
        self.data["requisites"]["single_bank"] = bank
        if self.requisites_mode == "single":
            for method in self.data["requisites"]["payment_methods"]:
                self.data["requisites"]["split_by_method"][method]["bank"] = bank
        await self.save()

    async def set_requisites_value(self, value: str) -> None:
        value = value.strip()
        if not value:
            return
        self.data["requisites"]["single_value"] = value
        if self.requisites_mode == "single":
            for method in self.data["requisites"]["payment_methods"]:
                self.data["requisites"]["split_by_method"][method]["value"] = value
        await self.save()

    def payment_methods(self) -> list[str]:
        return list(self.data["requisites"]["payment_methods"])

    async def add_payment_method(self, value: str) -> bool:
        value = value.strip()
        if len(value) < 2:
            return False
        methods = self.data["requisites"]["payment_methods"]
        if value in methods:
            return False
        methods.append(value)
        self.data["requisites"]["split_by_method"][value] = {
            "bank": self.data["requisites"]["single_bank"],
            "value": self.data["requisites"]["single_value"],
        }
        await self.save()
        return True

    async def delete_payment_method(self, index: int) -> bool:
        methods = self.data["requisites"]["payment_methods"]
        if len(methods) <= 1:
            return False
        if index < 0 or index >= len(methods):
            return False
        deleted = methods.pop(index)
        self.data["requisites"]["split_by_method"].pop(deleted, None)
        await self.save()
        return True

    def split_method_map(self) -> dict[str, SplitMethodRequisitesData]:
        self._normalize_split_map()
        return {
            key: {"bank": value["bank"], "value": value["value"]}
            for key, value in self.data["requisites"]["split_by_method"].items()
        }

    def method_requisites(self, method: str) -> tuple[str, str]:
        if self.requisites_mode == "single":
            return self.requisites_bank, self.requisites_value
        row = self.data["requisites"]["split_by_method"].get(method)
        if row is None:
            return self.requisites_bank, self.requisites_value
        return row["bank"], row["value"]

    async def set_method_requisites(self, method: str, bank: str, value: str) -> bool:
        method = method.strip()
        bank = bank.strip()
        value = value.strip()
        if method not in self.data["requisites"]["payment_methods"]:
            return False
        if not bank or not value:
            return False
        self.data["requisites"]["split_by_method"][method] = {"bank": bank, "value": value}
        await self.save()
        return True


class UsersStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, UserProfile] = {}
        self.load()

    @staticmethod
    def _default_profile() -> UserProfile:
        return {
            "trades_total": 0,
            "turnover_rub": 0.0,
            "invited": 0,
            "bonus_balance": 0.0,
            "history": [],
            "addresses": [],
        }

    def load(self) -> None:
        if not self.path.exists():
            self.save_sync()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            raw = {}

        parsed: dict[str, UserProfile] = {}
        for uid, val in raw.items():
            if not isinstance(val, dict):
                continue
            profile = self._default_profile()
            profile["trades_total"] = _get_int(val, "trades_total")
            profile["invited"] = _get_int(val, "invited")
            profile["turnover_rub"] = _get_float(val, "turnover_rub")
            profile["bonus_balance"] = _get_float(val, "bonus_balance")

            if isinstance(val.get("history"), list):
                profile["history"] = [
                    {
                        "ts": _get_int(h, "ts"),
                        "side": _get_str(h, "side"),
                        "coin": _get_str(h, "coin"),
                        "amount_coin": _get_float(h, "amount_coin"),
                        "amount_rub": _get_float(h, "amount_rub"),
                    }
                    for h in val["history"]
                    if isinstance(h, dict)
                ][-20:]

            if isinstance(val.get("addresses"), list):
                profile["addresses"] = [
                    {
                        "coin": _get_str(a, "coin").upper(),
                        "address": _get_str(a, "address"),
                        "name": _get_str(a, "name"),
                    }
                    for a in val["addresses"]
                    if isinstance(a, dict) and _get_str(a, "coin") and _get_str(a, "address")
                ][:100]
            parsed[str(uid)] = profile

        self.data = parsed
        self.save_sync()

    def save_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def save(self) -> None:
        await _atomic_save(self.path, self.data)

    async def user(self, user_id: int) -> UserProfile:
        key = str(user_id)
        if key not in self.data:
            self.data[key] = self._default_profile()
            await self.save()
        return self.data[key]

    async def record_trade(
        self,
        user_id: int,
        side: str,
        coin: str,
        amount_coin: float,
        amount_rub: float,
    ) -> None:
        profile = await self.user(user_id)
        profile["trades_total"] += 1
        profile["turnover_rub"] = round(profile["turnover_rub"] + float(amount_rub), 2)
        profile["bonus_balance"] = round(profile["bonus_balance"] + float(amount_rub) * 0.01, 2)
        profile["history"].append(
            {
                "ts": int(time.time()),
                "side": side,
                "coin": coin,
                "amount_coin": round(amount_coin, 8),
                "amount_rub": round(amount_rub, 2),
            }
        )
        profile["history"] = profile["history"][-20:]
        await self.save()

    async def add_address(self, user_id: int, coin: str, address: str, name: str) -> None:
        profile = await self.user(user_id)
        profile["addresses"].append(
            {
                "coin": coin.strip().upper(),
                "address": address.strip(),
                "name": name.strip(),
            }
        )
        profile["addresses"] = profile["addresses"][:100]
        await self.save()

    async def list_addresses(self, user_id: int) -> list[dict[str, str]]:
        profile = await self.user(user_id)
        return [
            {"coin": item["coin"], "address": item["address"], "name": item["name"]}
            for item in profile["addresses"]
        ]

    async def delete_address(self, user_id: int, index: int) -> bool:
        profile = await self.user(user_id)
        if index < 0 or index >= len(profile["addresses"]):
            return False
        profile["addresses"].pop(index)
        await self.save()
        return True


class SessionData(TypedDict):
    state_id: str
    history: list[str]
    awaiting_payment_proof: bool
    payment_context: str
    selected_payment_method: str
    updated_at: float


class SessionsStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, SessionData] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = cast(dict[str, SessionData], raw)
        except Exception:
            self.data = {}

    def save_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def save(self) -> None:
        await _atomic_save(self.path, self.data)

    def get_session(self, user_id: int) -> SessionData | None:
        return self.data.get(str(user_id))

    def update_session(self, user_id: int, session_data: SessionData) -> None:
        self.data[str(user_id)] = session_data

    async def cleanup(self, max_age_seconds: int) -> int:
        now = time.time()
        to_delete = [
            uid for uid, sess in self.data.items()
            if now - sess.get("updated_at", 0) > max_age_seconds
        ]
        for uid in to_delete:
            del self.data[uid]
        if to_delete:
            await self.save()
        return len(to_delete)


class MediaStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, str] = {}  # sha256(path) -> file_id
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = cast(dict[str, str], raw)
        except Exception:
            self.data = {}

    def save_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def save(self) -> None:
        await _atomic_save(self.path, self.data)

    def get_file_id(self, key: str) -> str | None:
        return self.data.get(key)

    async def set_file_id(self, key: str, file_id: str) -> None:
        self.data[key] = file_id
        await self.save()


class OrdersStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, OrderData] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.save_sync()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            raw = {}

        parsed: dict[str, OrderData] = {}
        for oid, val in raw.items():
            if not isinstance(val, dict):
                continue
            
            status = _get_str(val, "status")
            if status not in {"pending_payment", "paid", "confirmed", "cancelled"}:
                status = "pending_payment"

            parsed[str(oid)] = {
                "order_id": _get_str(val, "order_id", str(oid)),
                "user_id": _get_int(val, "user_id"),
                "username": _get_str(val, "username"),
                "wallet": _get_str(val, "wallet"),
                "coin_symbol": _get_str(val, "coin_symbol"),
                "coin_amount": _get_float(val, "coin_amount"),
                "amount_rub": _get_float(val, "amount_rub"),
                "payment_method": _get_str(val, "payment_method"),
                "bank": _get_str(val, "bank"),
                "status": cast(Literal["pending_payment", "paid", "confirmed", "cancelled"], status),
                "created_at": _get_int(val, "created_at"),
                "updated_at": _get_int(val, "updated_at"),
                "confirmed_by": val.get("confirmed_by") if isinstance(val.get("confirmed_by"), int) else None,
            }
        self.data = parsed
        self.save_sync()

    def save_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def save(self) -> None:
        await _atomic_save(self.path, self.data)

    def _new_order_id(self) -> str:
        for _ in range(100):
            candidate = str(random.randint(100000, 999999))
            if candidate not in self.data:
                return candidate
        return str(int(time.time()))

    async def create_order(
        self,
        user_id: int,
        username: str,
        wallet: str,
        coin_symbol: str,
        coin_amount: float,
        amount_rub: float,
        payment_method: str,
        bank: str,
    ) -> OrderData:
        now_ts = int(time.time())
        order_id = self._new_order_id()
        order: OrderData = {
            "order_id": order_id,
            "user_id": user_id,
            "username": username,
            "wallet": wallet,
            "coin_symbol": coin_symbol,
            "coin_amount": float(coin_amount),
            "amount_rub": float(amount_rub),
            "payment_method": payment_method,
            "bank": bank,
            "status": "pending_payment",
            "created_at": now_ts,
            "updated_at": now_ts,
            "confirmed_by": None,
        }
        self.data[order_id] = order
        await self.save()
        return order

    def get_order(self, order_id: str) -> OrderData | None:
        return self.data.get(order_id)

    async def mark_paid(self, order_id: str) -> bool:
        order = self.data.get(order_id)
        if order is None:
            return False
        if order["status"] != "pending_payment":
            return False
        order["status"] = "paid"
        order["updated_at"] = int(time.time())
        await self.save()
        return True

    async def mark_cancelled(self, order_id: str) -> bool:
        order = self.data.get(order_id)
        if order is None:
            return False
        if order["status"] != "pending_payment":
            return False
        order["status"] = "cancelled"
        order["updated_at"] = int(time.time())
        await self.save()
        return True

    async def confirm_order(self, order_id: str, admin_id: int) -> tuple[bool, OrderData | None]:
        order = self.data.get(order_id)
        if order is None:
            return False, None
        if order["status"] != "paid":
            return False, order
        order["status"] = "confirmed"
        order["confirmed_by"] = admin_id
        order["updated_at"] = int(time.time())
        await self.save()
        return True, order
