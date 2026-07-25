"""
backend/config.py

System-wide configuration loaded from environment variables.
Single source of truth for all service connection details.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


@dataclass
class MT5Config:
    """MetaTrader 5 connection configuration."""
    account: int = int(os.getenv("MT5_ACCOUNT", "0"))
    password: str = os.getenv("MT5_PASSWORD", "")
    server: str = os.getenv("MT5_SERVER", "")
    path: str = os.getenv("MT5_PATH", "")


@dataclass
class DerivMT5Config:
    """Deriv MT5 (Synthetics) connection — optional."""
    account: int = int(os.getenv("DERIV_MT5_ACCOUNT", "0"))
    password: str = os.getenv("DERIV_MT5_PASSWORD", "")
    server: str = os.getenv("DERIV_MT5_SERVER", "")
    path: str = os.getenv("DERIV_MT5_PATH", "")
    enabled: bool = bool(os.getenv("DERIV_MT5_ACCOUNT", ""))


@dataclass
class RedisConfig:
    url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")


@dataclass
class DatabaseConfig:
    url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///algoedge.db")


@dataclass
class ServerConfig:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


@dataclass
class SecurityConfig:
    encryption_key: str = os.getenv("ENCRYPTION_KEY", "")


@dataclass
class VAPIDConfig:
    private_key: str = os.getenv("VAPID_PRIVATE_KEY", "")
    public_key: str = os.getenv("VAPID_PUBLIC_KEY", "")
    claims_email: str = os.getenv("VAPID_CLAIMS_EMAIL", "")


@dataclass
class AppConfig:
    """Master configuration — aggregates all sub-configs."""
    mt5: MT5Config = field(default_factory=MT5Config)
    deriv_mt5: DerivMT5Config = field(default_factory=DerivMT5Config)
    redis: RedisConfig = field(default_factory=RedisConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    vapid: VAPIDConfig = field(default_factory=VAPIDConfig)

    # Paths
    project_root: Path = _PROJECT_ROOT
    snapshots_dir: Path = _PROJECT_ROOT / "snapshots"
    logs_dir: Path = _PROJECT_ROOT / "logs"
    data_dir: Path = _PROJECT_ROOT / "data"

    def __post_init__(self):
        """Ensure required directories exist."""
        for d in [self.snapshots_dir, self.logs_dir, self.data_dir]:
            d.mkdir(parents=True, exist_ok=True)


# Singleton instance
settings = AppConfig()
