import itertools
import random
from dataclasses import dataclass

import requests


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
]


@dataclass
class HttpClient:
    """Small compliant HTTP client with retries, UA rotation, and optional proxies."""

    proxies: list[str] | None = None
    timeout: int = 20

    def __post_init__(self):
        self._proxy_cycle = itertools.cycle(self.proxies or [])

    def get(self, url: str, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", random.choice(USER_AGENTS))
        proxy_url = next(self._proxy_cycle, None) if self.proxies else None
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        response = requests.get(url, headers=headers, proxies=proxies, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response
