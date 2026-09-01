from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
PLUGIN_ALGORITHM_DIR = PROJECT_ROOT / "plugins" / "algorithms"
PLUGIN_PROPERTY_DIR = PROJECT_ROOT / "plugins" / "properties"


@dataclass(frozen=True)
class TestConfig:
    device_id: str
    apk_path: str
    test_cases: int = 3
    events_per_case: int = 50
    algorithm: str = "random"
    seed: int | None = None
    min_action_interval_sec: float = 1.0
    stable_poll_ms: int = 400
    stable_samples: int = 2
    stable_timeout_sec: float = 4.0
    property_probability: float = 0.2
    enable_properties: bool = False
    text_min_len: int = 1
    text_max_len: int = 12
    action_weights: dict[str, float] = field(
        default_factory=lambda: {
            "click": 0.7,
            "long_click": 0.0,
            "swipe": 0.0,
            "input": 0.15,
            "back": 0.05,
        }
    )
    auto_allow_permissions: bool = True
