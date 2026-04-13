import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _parse_csv(value: str | None, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    items = tuple(part.strip() for part in str(value).split(",") if part and part.strip())
    return items or default


@dataclass(frozen=True)
class AppSettings:
    environment: str
    render_base: str
    metrics_http_endpoint_enabled: bool
    blocked_user_agents: frozenset[str]
    be_doc_paths: frozenset[str]
    exempt_paths: frozenset[str]
    be_admin_ips: frozenset[str]
    cors_origins: tuple[str, ...]
    invite_redirect_url: str
    install_redirect_url: str
    app_title: str
    app_description: str
    app_version: str
    uvicorn_host: str
    uvicorn_port: int
    uvicorn_workers: int
    uvicorn_access_log: bool
    uvicorn_server_header: bool
    uvicorn_date_header: bool
    uvicorn_timeout_keep_alive: int
    uvicorn_proxy_headers: bool
    uvicorn_forwarded_allow_ips: str

    def uvicorn_kwargs(self) -> dict:
        return {
            "host": self.uvicorn_host,
            "port": self.uvicorn_port,
            "workers": self.uvicorn_workers,
            "access_log": self.uvicorn_access_log,
            "server_header": self.uvicorn_server_header,
            "date_header": self.uvicorn_date_header,
            "timeout_keep_alive": self.uvicorn_timeout_keep_alive,
            "proxy_headers": self.uvicorn_proxy_headers,
            "forwarded_allow_ips": self.uvicorn_forwarded_allow_ips,
        }


settings = AppSettings(
    environment=os.getenv("ENVIRONMENT", "development").strip().lower(),
    render_base=os.getenv("RENDER_BASE", "http://localhost:9001").strip(),
    metrics_http_endpoint_enabled=_parse_bool(os.getenv("METRICS_HTTP_ENDPOINT_ENABLED", "0")),
    blocked_user_agents=frozenset({
        "bot", "crawler", "spider", "scraper", "curl", "wget",
        "python-requests", "go-http-client", "java", "apache",
        "scanner", "nikto", "sqlmap", "nmap", "masscan", "shodan",
    }),
    be_doc_paths=frozenset({"/be/docs", "/openapi-be.json", "/redoc", "/openapi.json"}),
    exempt_paths=frozenset({"/favicon.ico", "/discord_login/callback"}),
    be_admin_ips=frozenset(_parse_csv(os.getenv("BE_ADMIN_IPS"), default=("127.0.0.1", "::1"))),
    cors_origins=_parse_csv(
        os.getenv(
            "CORS_ALLOW_ORIGINS",
            "http://127.0.0.1:5500,https://127.0.0.1:5500,https://api.mococobot.kr,https://mococobot.kr,https://lopec.kr",
        )
    ),
    invite_redirect_url=os.getenv(
        "INVITE_REDIRECT_URL",
        "https://discord.com/oauth2/authorize?client_id=1207646886002036748&permissions=8&integration_type=0&scope=bot",
    ).strip(),
    install_redirect_url=os.getenv(
        "INSTALL_REDIRECT_URL",
        "https://discord.com/oauth2/authorize?client_id=1207646886002036748",
    ).strip(),
    app_title=os.getenv("APP_TITLE", "모코코 봇 API").strip(),
    app_description=os.getenv("APP_DESCRIPTION", "Discord 레이드 파티 관리 시스템").strip(),
    app_version=os.getenv("APP_VERSION", "2.1.2").strip(),
    uvicorn_host=os.getenv("UVICORN_HOST", "127.0.0.1").strip(),
    uvicorn_port=int(os.getenv("UVICORN_PORT", "8000")),
    uvicorn_workers=int(os.getenv("UVICORN_WORKERS", "1")),
    uvicorn_access_log=_parse_bool(os.getenv("UVICORN_ACCESS_LOG", "1"), default=True),
    uvicorn_server_header=_parse_bool(os.getenv("UVICORN_SERVER_HEADER", "0"), default=False),
    uvicorn_date_header=_parse_bool(os.getenv("UVICORN_DATE_HEADER", "0"), default=False),
    uvicorn_timeout_keep_alive=int(os.getenv("UVICORN_TIMEOUT_KEEP_ALIVE", "5")),
    uvicorn_proxy_headers=_parse_bool(os.getenv("UVICORN_PROXY_HEADERS", "1"), default=True),
    uvicorn_forwarded_allow_ips=os.getenv("UVICORN_FORWARDED_ALLOW_IPS", "127.0.0.1,::1").strip(),
)
