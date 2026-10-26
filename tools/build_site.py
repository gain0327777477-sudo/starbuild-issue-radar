"""배포용 사이트 묶기: web/ 화면 + data/ 중 핸드폰이 쓰는 파일만 → _site/

실행:  python tools/build_site.py --version abc1234-99
  · 서비스워커 VERSION 을 바꿔서, 화면 파일을 고치면 핸드폰에도 새 화면이 들어가게 함
  · data/ 중 화면에 필요 없는 파일(원본 기사 articles/·내부 기록·블로그 글 목록)은 올리지 않음
  · _site/version.json: 배포 버전과 묶은 시각 (반영 지연 측정·설정 화면 표시용)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"
SITE_FILES = ("status.json", "issues.json", "today.json")     # 핸드폰이 읽는 데이터
SITE_DIRS = ("packets",)
KST = timezone(timedelta(hours=9))


def build(out: Path, version: str) -> dict:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out)

    sw = out / "sw.js"
    text, n = re.subn(r'const VERSION = "[^"]*";', f'const VERSION = "{version}";', sw.read_text(encoding="utf-8"))
    if n != 1:
        raise SystemExit("sw.js 에서 VERSION 줄을 찾지 못했습니다")
    sw.write_text(text, encoding="utf-8")

    (out / "data").mkdir()
    copied = []
    for name in SITE_FILES:
        if (DATA / name).exists():
            shutil.copy2(DATA / name, out / "data" / name)
            copied.append(name)
    for d in SITE_DIRS:
        if (DATA / d).is_dir():
            shutil.copytree(DATA / d, out / "data" / d)
            copied.append(f"{d}/ ({len(list((DATA / d).glob('*.json')))}개)")
    info = {"version": version, "built_at": datetime.now(KST).isoformat(timespec="seconds")}
    (out / "version.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")          # GitHub Pages 가 파일을 건드리지 않게
    return {"copied": copied, **info}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="local")
    ap.add_argument("--out", default=str(ROOT / "_site"))
    args = ap.parse_args()
    res = build(Path(args.out), args.version)
    size = sum(p.stat().st_size for p in Path(args.out).rglob("*") if p.is_file())
    print(f"사이트를 묶었습니다: {args.out} ({size / 1024:.0f}KB) 버전 {res['version']}")
    print("  데이터:", ", ".join(res["copied"]) or "(없음 — 첫 실행이면 정상)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
