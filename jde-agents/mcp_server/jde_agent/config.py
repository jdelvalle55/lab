import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        raise RuntimeError(f"Missing required environment variable {name}")
    return val


@dataclass(frozen=True)
class Settings:
    dsn: str = field(default_factory=lambda: _env("ORACLE_DSN"))
    db_user: str = field(default_factory=lambda: _env("ORACLE_USER", "JDEAGENT_RO"))
    db_password: str = field(default_factory=lambda: _env("ORACLE_PASSWORD"))
    jde_user: str = field(default_factory=lambda: _env("JDE_AGENT_USER"))
    host: str = field(default_factory=lambda: _env("MCP_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("MCP_PORT", "8082")))
    service_level_z: float = field(default_factory=lambda: float(_env("SERVICE_LEVEL_Z", "1.65")))
    review_period_days: int = field(default_factory=lambda: int(_env("REVIEW_PERIOD_DAYS", "30")))
    default_lead_time_days: float = field(default_factory=lambda: float(_env("DEFAULT_LEAD_TIME_DAYS", "14")))
    min_lt_receipts: int = field(default_factory=lambda: int(_env("MIN_LT_RECEIPTS", "3")))
    lt_drift_pct: float = field(default_factory=lambda: float(_env("LT_DRIFT_PCT", "25")))
    usage_weeks: int = field(default_factory=lambda: int(_env("USAGE_WEEKS", "13")))
    planned_stocking_types: tuple[str, ...] = field(
        default_factory=lambda: tuple(s.strip() for s in _env("PLANNED_STOCKING_TYPES", "P,M").split(","))
    )
