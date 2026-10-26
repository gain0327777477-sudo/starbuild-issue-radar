"""API 키 읽기와 가리기.

키는 환경변수(GitHub Secrets)에서 읽고, 로컬 테스트 때는 .env 파일도 읽는다.
오류 메시지·로그에 키가 섞여 나가지 않도록 mask()를 거쳐서 출력한다.
"""
from __future__ import annotations

import os
from pathlib import Path

SECRET_NAMES = ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
ROOT_ENV = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv(path: Path) -> list[str]:
    """.env 파일의 KEY=VALUE 를 환경변수로 올린다. 이미 있는 값은 덮어쓰지 않는다.

    반환값: 새로 읽은 이름 목록(값은 반환하지 않음).
    """
    if not path.exists():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if name and value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def get_secret(name: str) -> str | None:
    """키가 없거나 빈칸이면 None."""
    value = os.environ.get(name, "").strip()
    return value or None


def has_naver_keys() -> bool:
    return bool(get_secret("NAVER_CLIENT_ID") and get_secret("NAVER_CLIENT_SECRET"))


def mask(text: str) -> str:
    """문자열 안에 들어 있는 키 값을 가린다. 앞 2글자만 남김."""
    for name in SECRET_NAMES:
        value = get_secret(name)
        if value and value in text:
            text = text.replace(value, value[:2] + "****")
    return text
