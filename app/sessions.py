from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserSession:
    state_id: str
    history: list[str] = field(default_factory=list)
    awaiting_payment_proof: bool = False
    payment_context: str = ""
    selected_payment_method: str = ""
    selected_coin: str = ""
    updated_at: float = field(default_factory=time.time)
    last_action_ts: float = 0.0
    last_input: str = ""  # Помним последний ввод юзера
    last_shown_max: float = 0.0  # Max amount shown to user at last max-error state render
    _dirty: bool = False # Флаг изменения

    def push_state(self, state_id: str) -> None:
        if not self.history or self.history[-1] != state_id:
            self.history.append(state_id)
            self._dirty = True
        if len(self.history) > 20:
            self.history = self.history[-20:]
        self.state_id = state_id
        self.updated_at = time.time()
        self._dirty = True

    def jump_to_state(self, state_id: str, reset_history: bool = False) -> None:
        if reset_history:
            self.history = [state_id]
        else:
            if not self.history or self.history[-1] != state_id:
                self.history.append(state_id)
        self.state_id = state_id
        self.updated_at = time.time()
        self._dirty = True

    def pop_state(self) -> str | None:
        if len(self.history) <= 1:
            return None
        self.history.pop()  # current
        prev = self.history[-1]
        self.state_id = prev
        self.updated_at = time.time()
        self._dirty = True
        return prev

    def mark_dirty(self) -> None:
        self._dirty = True

    def clear_dirty(self) -> None:
        self._dirty = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "history": self.history,
            "awaiting_payment_proof": self.awaiting_payment_proof,
            "payment_context": self.payment_context,
            "selected_payment_method": self.selected_payment_method,
            "selected_coin": self.selected_coin,
            "updated_at": self.updated_at,
            "last_input": self.last_input,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserSession:
        return cls(
            state_id=data.get("state_id", ""),
            history=data.get("history", []),
            awaiting_payment_proof=data.get("awaiting_payment_proof", False),
            payment_context=data.get("payment_context", ""),
            selected_payment_method=data.get("selected_payment_method", ""),
            selected_coin=data.get("selected_coin", ""),
            updated_at=data.get("updated_at", time.time()),
            last_input=data.get("last_input", ""),
        )
