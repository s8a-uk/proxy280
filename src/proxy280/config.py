import os
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "PROXY280_"


@dataclass(frozen=True)
class Settings:
    blocklist_url_template: str = (
        "https://280blocker.net/files/280blocker_domain_ag_{year}{month:02d}.txt"
    )
    allowlist_path: Path = Path("allowlist.txt")
    host: str = "0.0.0.0"
    port: int = 28000
    timezone: str = "Asia/Tokyo"
    fetch_timeout: float = 10.0
    # 上流の更新を確認する間隔 (秒)。上流の Cache-Control: max-age=86400 に合わせる
    refresh_interval: float = 86400.0
    # 取得に失敗した後、再試行するまでの秒数
    retry_interval: float = 600.0

    @classmethod
    def from_env(cls) -> Settings:
        def env(name: str) -> str | None:
            return os.environ.get(ENV_PREFIX + name)

        defaults = cls()
        return cls(
            blocklist_url_template=env("BLOCKLIST_URL_TEMPLATE")
            or defaults.blocklist_url_template,
            allowlist_path=Path(env("ALLOWLIST_PATH") or defaults.allowlist_path),
            host=env("HOST") or defaults.host,
            port=int(env("PORT") or defaults.port),
            timezone=env("TIMEZONE") or defaults.timezone,
            fetch_timeout=float(env("FETCH_TIMEOUT") or defaults.fetch_timeout),
            refresh_interval=float(
                env("REFRESH_INTERVAL") or defaults.refresh_interval
            ),
            retry_interval=float(env("RETRY_INTERVAL") or defaults.retry_interval),
        )
