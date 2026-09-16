"""Short-lived, user-confirmed Cookie synchronization requests."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Dict, Optional
from uuid import uuid4


REQUEST_TTL = timedelta(minutes=5)
RESULT_TTL = timedelta(minutes=2)


@dataclass
class CookieSyncRequest:
    request_id: str
    instance: str
    cookie: str
    uid: str
    username: str
    created_at: datetime
    status: str = 'pending'
    message: str = ''
    finished_at: Optional[datetime] = None

    def expired(self, now: datetime) -> bool:
        deadline = self.finished_at or self.created_at
        ttl = RESULT_TTL if self.finished_at else REQUEST_TTL
        return now - deadline > ttl

    def summary(self):
        return {
            'request_id': self.request_id,
            'instance': self.instance,
            'uid': self.uid,
            'username': self.username,
            'status': self.status,
            'message': self.message,
            'created_at': self.created_at.isoformat(),
        }


_lock = Lock()
_requests: Dict[str, CookieSyncRequest] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cleanup(now: datetime):
    for request_id, request in tuple(_requests.items()):
        if request.expired(now):
            del _requests[request_id]


def parse_cookie(cookie: str):
    values = {}
    for pair in cookie.split(';'):
        name, separator, value = pair.strip().partition('=')
        if separator and name:
            values.setdefault(name, value)
    return values


def create(instance: str, cookie: str) -> CookieSyncRequest:
    now = _now()
    values = parse_cookie(cookie)
    with _lock:
        _cleanup(now)
        # One pending request per instance prevents an extension from filling
        # the UI with stale confirmations while the user is deciding.
        for request in _requests.values():
            if request.instance == instance and request.status == 'pending':
                request.cookie = cookie
                request.uid = values.get('game_uid', '')
                request.username = values.get('game_user_name', '')
                request.created_at = now
                return request
        request = CookieSyncRequest(
            request_id=uuid4().hex,
            instance=instance,
            cookie=cookie,
            uid=values.get('game_uid', ''),
            username=values.get('game_user_name', ''),
            created_at=now,
        )
        _requests[request.request_id] = request
        return request


def get(request_id: str) -> Optional[CookieSyncRequest]:
    now = _now()
    with _lock:
        _cleanup(now)
        return _requests.get(request_id)


def pending():
    now = _now()
    with _lock:
        _cleanup(now)
        return [request for request in _requests.values() if request.status == 'pending']


def finish(request_id: str, status: str, message: str = '') -> Optional[CookieSyncRequest]:
    now = _now()
    with _lock:
        _cleanup(now)
        request = _requests.get(request_id)
        if request is None or request.status != 'pending':
            return request
        request.status = status
        request.message = message
        request.finished_at = now
        return request
