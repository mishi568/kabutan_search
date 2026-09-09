"""HTTP取得の共通設定

kabutan.jpは通常の`requests`ライブラリのTLS通信フィンガープリント(JA3等)を検知して
403で弾くBot対策を行っている(旧アプリはQWebEngineView=本物のChromiumブラウザで
読み込んでいたため、この問題が起きていなかった)。

`curl_cffi` はTLS/HTTP2レベルで実ブラウザの指紋を模倣できるライブラリで、
`requests`とほぼ同じインターフェースのまま使える。Playwrightのような実ブラウザ起動より
大幅に軽量なため、まずこちらで回避を試みる。
"""
from curl_cffi import requests as cffi_requests

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# ブラウザのTLS/HTTP2指紋を模倣する対象(curl_cffiが対応しているプリセット)
IMPERSONATE = "chrome124"


def create_session():
    """ブラウザのTLS指紋を模倣したHTTPセッションを作成する"""
    session = cffi_requests.Session(impersonate=IMPERSONATE)
    for key, value in DEFAULT_HEADERS.items():
        session.headers.setdefault(key, value)
    return session
