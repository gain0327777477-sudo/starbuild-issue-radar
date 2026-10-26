"""도메인 → 언론사 이름. 네이버 뉴스 API는 언론사 이름을 주지 않아서 원문 주소로 알아낸다.
목록에 없으면 도메인을 그대로 보여준다(예: 'example.co.kr')."""

PRESS_BY_DOMAIN = {
    "yna.co.kr": "연합뉴스", "yonhapnewstv.co.kr": "연합뉴스TV", "newsis.com": "뉴시스", "news1.kr": "뉴스1",
    "chosun.com": "조선일보", "biz.chosun.com": "조선비즈", "joongang.co.kr": "중앙일보", "donga.com": "동아일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "hankookilbo.com": "한국일보", "seoul.co.kr": "서울신문",
    "kmib.co.kr": "국민일보", "segye.com": "세계일보", "munhwa.com": "문화일보", "naeil.com": "내일신문",
    "hankyung.com": "한국경제", "mk.co.kr": "매일경제", "sedaily.com": "서울경제", "mt.co.kr": "머니투데이",
    "edaily.co.kr": "이데일리", "asiae.co.kr": "아시아경제", "fnnews.com": "파이낸셜뉴스", "heraldcorp.com": "헤럴드경제",
    "ajunews.com": "아주경제", "etnews.com": "전자신문", "dt.co.kr": "디지털타임스", "inews24.com": "아이뉴스24",
    "newspim.com": "뉴스핌", "ekn.kr": "에너지경제", "bizwatch.co.kr": "비즈워치", "thebell.co.kr": "더벨",
    "dnews.co.kr": "대한경제", "kbs.co.kr": "KBS", "news.kbs.co.kr": "KBS", "imbc.com": "MBC", "sbs.co.kr": "SBS",
    "news.sbs.co.kr": "SBS", "biz.sbs.co.kr": "SBS Biz", "ytn.co.kr": "YTN", "jtbc.co.kr": "JTBC", "mbn.co.kr": "MBN",
    "ichannela.com": "채널A", "tvchosun.com": "TV조선", "wowtv.co.kr": "한국경제TV", "mtn.co.kr": "MTN",
    "busan.com": "부산일보", "kookje.co.kr": "국제신문", "imaeil.com": "매일신문", "yeongnam.com": "영남일보",
    "kwnews.co.kr": "강원일보", "kado.net": "강원도민일보", "joongdo.co.kr": "중도일보", "daejonilbo.com": "대전일보",
    "kyeonggi.com": "경기일보", "kihoilbo.co.kr": "기호일보", "incheonilbo.com": "인천일보", "jnilbo.com": "전남일보",
    "kjdaily.com": "광주매일신문", "jjan.kr": "전북일보", "jejunews.com": "제주일보", "ihalla.com": "한라일보",
    "knnews.co.kr": "경남신문", "idomin.com": "경남도민일보", "ksilbo.co.kr": "경상일보", "newdaily.co.kr": "뉴데일리",
    "nocutnews.co.kr": "노컷뉴스", "ohmynews.com": "오마이뉴스", "pressian.com": "프레시안", "sisajournal.com": "시사저널",
    "electimes.com": "전기신문", "meconomynews.com": "시장경제신문", "traveltimes.co.kr": "여행신문",
    "korea.kr": "정책브리핑", "m.korea.kr": "정책브리핑", "ktv.go.kr": "KTV", "molit.go.kr": "국토교통부",
}


def press_name(domain: str) -> str:
    d = domain.lower()
    while d:
        if d in PRESS_BY_DOMAIN:
            return PRESS_BY_DOMAIN[d]
        if "." not in d:
            break
        d = d.split(".", 1)[1]          # news.hankyung.com → hankyung.com
        if d.count(".") == 0 or d in ("co.kr", "or.kr", "go.kr", "com", "kr", "net"):
            break
    return domain
