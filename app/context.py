from dataclasses import dataclass
from pathlib import Path

import httpx
from .rates import RateService
from .storage import MediaStore, OrdersStore, SessionsStore, SettingsStore, UsersStore


@dataclass
class AppContext:
    settings: SettingsStore
    users: UsersStore
    orders: OrdersStore
    sessions: SessionsStore
    media: MediaStore
    rates: RateService
    http_client: httpx.AsyncClient
    admin_ids: set[int]
    env_path: Path

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids
