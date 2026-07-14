#!/usr/bin/env python3
"""Probe whether gov-scan sources are reachable from a GitHub Actions runner.

Korean government sites often block overseas IPs; GitHub runners are US-based.
This probe hits each source's list endpoint once (1 page, polite) and reports
OK / BLOCKED / ERROR per source, so the daily pipeline can be split between
Actions (reachable sources) and the local scheduler (blocked ones) on facts,
not guesses.

API sources (g2b, applyhome) are probed only when DATA_GO_KR_API_KEY is set
(repository secret); otherwise they are reported as SKIPPED.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

results = {}


def probe(name, fn):
    try:
        ok, note = fn()
        results[name] = ("OK" if ok else "BLOCKED", note)
    except Exception as e:  # noqa: BLE001 — the whole point is to classify failures
        msg = str(e)
        kind = "BLOCKED" if any(x in msg for x in ("403", "timed out", "reset", "refused")) else "ERROR"
        results[name] = (kind, msg[:120])
    time.sleep(1.0)


def make_session():
    try:
        from curl_cffi import requests as cr

        return cr.Session(impersonate="chrome")
    except ImportError:
        return None


sess = make_session()


def get(url, data=None):
    if sess is not None:
        r = sess.post(url, data=data, timeout=25) if data else sess.get(url, timeout=25)
        return r.status_code, r.text
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


probe("iris", lambda: (lambda s, b: (s == 200 and "listBsnsAncmBtinSitu" in b, f"HTTP {s}"))(
    *get("https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do",
         data={"pageIndex": "1", "ancmStt": "", "bsnsTl": ""})))
probe("gojobs", lambda: (lambda s, b: (s == 200 and "fn_apmView" in b, f"HTTP {s}"))(
    *get("https://www.gojobs.go.kr/apmList.do?menuNo=401&pageIndex=1")))
probe("alio", lambda: (lambda s, b: (s == 200 and "recruitview.do?idx=" in b, f"HTTP {s}"))(
    *get("https://job.alio.go.kr/recruit.do?pageNo=1")))
probe("s2b", lambda: (lambda s, b: (s == 200 and "stmo001Form" in b, f"HTTP {s}"))(
    *get("https://www.s2b.kr/S2BNCustomer/stmo001.do")))
probe("kosmes", lambda: (lambda s, b: (s == 200 and "ds_infoList" in b, f"HTTP {s}"))(
    *get("https://www.kosmes.or.kr/sh/nts/notice_list.json",
         data={"nowPage": "1", "pageCount": "10", "rowCount": "10",
               "param": "proc=List", "bKind": "popluar", "activatedTab": "01"})))
probe("semas", lambda: (lambda s, b: (s == 200 and "sbiz24.kr/#/pbanc" in b, f"HTTP {s}"))(
    *get("https://www.semas.or.kr/web/board/webBoardList.kmdc?bCd=2001&pNm=BOA0121&page=1")))

key = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
if key:
    q = urllib.parse.urlencode({
        "serviceKey": key, "pageNo": "1", "numOfRows": "1", "inqryDiv": "1",
        "inqryBgnDt": "202607130000", "inqryEndDt": "202607142359", "type": "json"})
    probe("g2b", lambda: (lambda s, b: (s == 200 and '"resultCode":"00"' in b.replace(" ", ""), f"HTTP {s}"))(
        *get(f"https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc?{q}")))
    q2 = urllib.parse.urlencode({"serviceKey": key, "page": "1", "perPage": "1"})
    probe("applyhome", lambda: (lambda s, b: (s == 200 and '"data"' in b, f"HTTP {s}"))(
        *get(f"https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail?{q2}")))
else:
    results["g2b"] = ("SKIPPED", "no DATA_GO_KR_API_KEY secret")
    results["applyhome"] = ("SKIPPED", "no DATA_GO_KR_API_KEY secret")

print(f"\n{'source':<12}{'result':<10}note")
print("-" * 50)
blocked = 0
for name, (status, note) in results.items():
    print(f"{name:<12}{status:<10}{note}")
    if status in ("BLOCKED", "ERROR"):
        blocked += 1
print(f"\nsummary: {json.dumps({k: v[0] for k, v in results.items()})}")
sys.exit(0)  # informational — never fail the workflow
