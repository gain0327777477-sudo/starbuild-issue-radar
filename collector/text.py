"""낱말 찾기·조사 고르기 같은 글자 도구."""
from __future__ import annotations

import re
from functools import lru_cache

HANGUL = re.compile(r"[가-힣]")


@lru_cache(maxsize=4096)
def _ascii_pattern(term: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z])", re.I)


def contains(text: str, term: str) -> bool:
    """영문 낱말은 앞뒤가 영문이 아닐 때만 ('IC' 가 'ICT' 에 걸리지 않게, 'SK하이닉스'는 걸리게)."""
    if not term:
        return False
    if term.isascii():
        return bool(_ascii_pattern(term).search(text))
    return term in text


def strip_phrases(text: str, phrases: list[str]) -> str:
    """'전세계' 같은 낱말을 지운 글. 자리를 공백으로 채워 앞뒤 낱말이 붙지 않게."""
    for p in phrases:
        if p in text:
            text = text.replace(p, " " * len(p))
    return text


def has_batchim(word: str) -> bool | None:
    """마지막 글자 받침 여부. 한글이 아니면 None."""
    if not word or not HANGUL.match(word[-1]):
        return None
    return (ord(word[-1]) - 0xAC00) % 28 != 0


def _rieul(word: str) -> bool:
    return bool(word) and HANGUL.match(word[-1]) and (ord(word[-1]) - 0xAC00) % 28 == 8


JOSA = {"으로": ("으로", "로"), "은": ("은", "는"), "는": ("은", "는"), "이": ("이", "가"), "가": ("이", "가"),
        "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와")}


def fill_place(sentence: str, place: str, placeholder: str = "{지역}") -> str:
    """'{지역}으로' → '포항으로' / '서울로', '{지역}은' → '포항은' / '부산은'… 조사를 받침에 맞게."""
    def repl(m: re.Match) -> str:
        josa = m.group(1)
        if not josa:
            return place
        with_b, without_b = JOSA[josa]
        b = has_batchim(place)
        if b is None:
            return place + josa
        if josa == "으로":
            return place + ("로" if (not b or _rieul(place)) else "으로")
        return place + (with_b if b else without_b)

    pattern = re.escape(placeholder) + r"(으로|은|는|이|가|을|를|과|와)?(?![가-힣])"
    out = re.sub(pattern, repl, sentence)
    return out.replace(placeholder, place)
