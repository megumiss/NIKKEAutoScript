"""Small request/response models kept independent from the UI framework."""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class PatchResult:
    ok: bool
    applied: Dict[str, Any]
    derived: Dict[str, Any]
    invalid: bool = False
    message: str = ''

    def dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InstanceInfo:
    name: str
    state: int
    mod: str
    current_task: Optional[str] = None
    next_task: Optional[str] = None
    remark: str = ''
    avatar: str = ''

    def dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QueueInfo:
    running: List[Dict[str, Any]]
    pending: List[Dict[str, Any]]
    waiting: List[Dict[str, Any]]

    def dict(self) -> Dict[str, Any]:
        return asdict(self)
