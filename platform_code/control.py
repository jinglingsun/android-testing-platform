from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class TaskControl:
    pause_event: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)

    def wait_if_paused(self) -> None:
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.5)


CONTROLS: dict[int, TaskControl] = {}


def get_control(task_id: int) -> TaskControl:
    return CONTROLS.setdefault(task_id, TaskControl())


def clear_control(task_id: int) -> None:
    CONTROLS.pop(task_id, None)
