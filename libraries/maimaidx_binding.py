"""Short-lived, provider-specific OAuth binding sessions."""

import asyncio
import re
import time
from dataclasses import dataclass, field

from .maimaidx_user import ServiceName


@dataclass
class PendingBinding:
    client_id: str
    expires_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class PendingBindings:
    def __init__(self):
        self._sessions: dict[tuple[str, str, str], dict[ServiceName, PendingBinding]] = {}

    @staticmethod
    def key(event) -> tuple[str, str, str]:
        platform = event.get_platform_id() if hasattr(event, 'get_platform_id') else ''
        bot = event.get_self_id() if hasattr(event, 'get_self_id') else ''
        return str(platform), str(bot), str(event.get_sender_id())

    def active(self, key) -> dict[ServiceName, PendingBinding]:
        now = time.monotonic()
        for session_key, sessions in list(self._sessions.items()):
            for provider, session in list(sessions.items()):
                if session.expires_at <= now:
                    del sessions[provider]
            if not sessions:
                del self._sessions[session_key]
        return self._sessions.get(key, {}).copy()

    def start(self, key, provider: ServiceName, client_id: str, ttl: int = 600):
        self.active(key)
        self._sessions.setdefault(key, {})[provider] = PendingBinding(
            client_id=client_id,
            expires_at=time.monotonic() + max(int(ttl), 1),
        )

    def consume(self, key, provider: ServiceName, session: PendingBinding):
        sessions = self._sessions.get(key, {})
        if sessions.get(provider) is session:
            del sessions[provider]
            if not sessions:
                self._sessions.pop(key, None)

    def clear(self):
        self._sessions.clear()


def normalize_binding_code(value: str, provider: ServiceName) -> str | None:
    body = re.sub(r'[\s_—-]', '', value).upper()
    alphabet = 'BCDFGHJKLMNPQRSTVWXZ' if provider == ServiceName.DIVINGFISH else 'A-Z0-9'
    if not re.fullmatch(f'[{alphabet}]{{12}}', body):
        return None
    return '-'.join(body[index:index + 4] for index in range(0, 12, 4))


pending_bindings = PendingBindings()
