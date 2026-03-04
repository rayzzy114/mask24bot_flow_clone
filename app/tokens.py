import hashlib
from typing import Callable


def action_token(action_text: str) -> str:
    digest = hashlib.sha1(action_text.encode("utf-8")).hexdigest()[:24]
    return f"a:{digest}"


class TokenRegistry:
    def __init__(self):
        self.action_to_token: dict[str, str] = {}
        self.token_to_action: dict[str, str] = {}

    def get_token(self, action_text: str) -> str:
        if action_text in self.action_to_token:
            return self.action_to_token[action_text]
        
        token = action_token(action_text)
        self.action_to_token[action_text] = token
        self.token_to_action[token] = action_text
        return token

    def get_action(self, token: str) -> str | None:
        return self.token_to_action.get(token)
