import requests
import pickle

login_url = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
test_url = 'https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd'

headers = {
    "accept": "application/json, text/javascript, */*; q=0.01",
    "accept-language": "ko,en;q=0.9,en-US;q=0.8",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    "origin": "https://data.krx.co.kr",
    "referer": "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc",
    "sec-ch-ua": '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
    "x-requested-with": "XMLHttpRequest",
}

data = {
    "mbrNm": "",
    "telNo": "",
    "di": "",
    "certType": "",
    "mbrId": "", # user id
    "pw": "",  # password
    "skipDup": "Y",
}
COOKIE_FILE = "krx_cookies.pkl"

def save_cookies_pickle(session: requests.Session, filename: str = COOKIE_FILE):
    with open(filename, "wb") as f:
        pickle.dump(session.cookies, f)

def load_cookies_pickle(session: requests.Session, filename: str = COOKIE_FILE):
    try:
        with open(filename, "rb") as f:
            session.cookies.update(pickle.load(f))
    except FileNotFoundError:
        pass
with requests.Session() as s:
    load_cookies_pickle(s)
    r = s.post(login_url, headers=headers, data=data, timeout=30)
    r.raise_for_status()
    print(r.status_code)
    print(r.text)
    # (3) 현재 쿠키 저장
    save_cookies_pickle(s)

with requests.Session() as s:
    load_cookies_pickle(s)
    res = s.post(
                test_url,
                headers=headers,
                data={
                    "bld": "dbms/MDC/STAT/standard/MDCSTAT03501",
                    "mktId": "STK",
                    "trdDd": '20260109',
                },
                timeout=10
                )
    print(r.status_code)
    print(r.text)