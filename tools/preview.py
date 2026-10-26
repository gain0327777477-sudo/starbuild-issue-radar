"""미리 보기 서버 — 이 PC에서 화면을 띄우고, 같은 와이파이의 핸드폰으로 접속해 볼 수 있게 한다.

실행:  python tools/preview.py            (또는 '미리보기_실행.bat' 더블클릭)
       python tools/preview.py --port 8080

  · PC 에서:   http://localhost:8000
  · 핸드폰에서: 화면에 나오는 http://192.168.x.x:8000 (같은 와이파이일 때)

※ 미리 보기는 HTTP 라서 핸드폰에서는 '홈 화면 설치·오프라인 저장·공유 버튼'이 동작하지 않습니다.
  (브라우저가 HTTPS 주소에서만 허용) 6단계에서 GitHub Pages(HTTPS)에 올리면 동작합니다.
※ 처음 실행할 때 Windows 방화벽 창이 뜨면 '개인 네트워크'만 허용하세요.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/javascript", ".js")


class Handler(SimpleHTTPRequestHandler):
    """/data/… 는 data 폴더에서, 나머지는 web 폴더에서 보여줌 (GitHub Pages 에 올렸을 때와 같은 구조)."""

    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0].split("#", 1)[0]
        rel = Path(*[p for p in path.split("/") if p and p not in (".", "..")])
        base = DATA if rel.parts[:1] == ("data",) else WEB
        target = (base / Path(*rel.parts[1:])) if base is DATA else (base / rel)
        target = target.resolve()
        if base not in target.parents and target != base:      # 폴더 밖 파일은 보여주지 않음
            return str(WEB / "__없음__")
        return str(target)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")             # 미리 보기는 늘 최신 파일로
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        if args and str(args[1]).startswith(("4", "5")):           # 오류만 보여줌
            sys.stderr.write(f"  ! {self.address_string()} {fmt % args}\n")


def lan_ip() -> str | None:
    """같은 와이파이의 핸드폰이 접속할 이 PC 주소 (인터넷으로 실제 전송은 하지 않음)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="이슈레이더 미리 보기")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0", help="127.0.0.1 로 하면 이 PC 에서만 보임")
    args = ap.parse_args()

    if not (DATA / "issues.json").exists():
        print("⚠ data/issues.json 이 없습니다. 먼저 '수집_실행.bat'(또는 python -m collector.main)으로 한 번 수집하세요.")
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        print(f"❌ {args.port}번 포트를 열 수 없습니다({e}). 다른 번호로: python tools/preview.py --port 8080")
        return 1

    ip = lan_ip()
    print("\n이슈레이더 미리 보기를 시작했습니다. (끄려면 이 창에서 Ctrl+C)\n")
    print(f"  · 이 PC 에서     : http://localhost:{args.port}")
    if args.host == "0.0.0.0" and ip:
        print(f"  · 핸드폰에서     : http://{ip}:{args.port}   ← 같은 와이파이에 연결된 핸드폰 인터넷 주소창에 입력")
    print("\n  ※ 미리 보기(HTTP)에서는 홈 화면 설치·오프라인 저장·공유 버튼이 동작하지 않습니다(6단계 HTTPS 이후 동작).")
    print("  ※ Windows 방화벽 창이 뜨면 '개인 네트워크'만 허용하세요. 핸드폰에서 안 열리면 README_KO.md '미리 보기' 참고.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("미리 보기를 끝냈습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
