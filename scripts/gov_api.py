#!/usr/bin/env python3
"""data.go.kr API clients for gov-scan: 나라장터(g2b) bids, 청약홈(applyhome) housing.

Both need a free 공공데이터포털 service key (auto-approved after signup):
  1. Sign up at https://www.data.go.kr
  2. Request use of:
     - 나라장터 입찰공고정보서비스  https://www.data.go.kr/data/15129394/openapi.do
     - 청약홈 분양정보 조회 서비스  https://www.data.go.kr/data/15098547/openapi.do
  3. Store the key (either place):
     - env  DATA_GO_KR_API_KEY
     - file ~/.config/gov-scan/apikey  (single line, the decoded key)

Without a key this script prints the guidance above and exits with code 2 —
the survey then reports these sources as 미갱신 rather than silently empty.

Usage:
  python3 gov_api.py g2b -o g2b.jsonl --days 14
  python3 gov_api.py applyhome -o applyhome.jsonl

Output: same unified JSONL schema as gov_crawl.py:
  {"source", "id", "title", "field", "org", "apply_start", "apply_end",
   "reg_date", "url"}

Standard REST + JSON — stdlib urllib only, no curl_cffi needed.
Field mappings were written from the official API docs; on the first keyed
run, verify a few records against the portal pages (loud errors otherwise).
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

DELAY = 0.3
KEY_FILE = os.path.expanduser("~/.config/gov-scan/apikey")


def load_key():
    key = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
    if not key and os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
    if not key:
        sys.stderr.write(
            "[gov-scan] 공공데이터포털 서비스키가 없습니다.\n"
            "  발급(무료·자동승인): https://www.data.go.kr 가입 후\n"
            "    - 나라장터 입찰공고정보서비스: https://www.data.go.kr/data/15129394/openapi.do\n"
            "    - 청약홈 분양정보 조회 서비스: https://www.data.go.kr/data/15098547/openapi.do\n"
            "  저장: 환경변수 DATA_GO_KR_API_KEY 또는 파일 "
            f"{KEY_FILE}\n"
        )
        sys.exit(2)
    return key


def get_json(url, params):
    q = urllib.parse.urlencode(params, safe="%")
    req = urllib.request.Request(f"{url}?{q}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def norm_dt(s):
    """'202607141530' / '2026-07-14 15:30' / '20260714' -> YYYY-MM-DD."""
    s = str(s or "").strip()
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return s


# --------------------------------------------------------------------------
# 나라장터 (g2b) — 입찰공고정보서비스. Four business categories, one call each.
# --------------------------------------------------------------------------

G2B_BASE = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"
G2B_OPS = [
    ("getBidPblancListInfoThng", "물품"),
    ("getBidPblancListInfoServc", "용역"),
    ("getBidPblancListInfoCnstwk", "공사"),
    ("getBidPblancListInfoFrgcpt", "외자"),
]


def fetch_g2b(key, days):
    end = dt.datetime.now()
    begin = end - dt.timedelta(days=days)
    items = []
    for op, label in G2B_OPS:
        page = 1
        while True:
            d = get_json(
                f"{G2B_BASE}/{op}",
                {
                    "serviceKey": key,
                    "pageNo": str(page),
                    "numOfRows": "100",
                    "inqryDiv": "1",  # 1 = by notice date range
                    "inqryBgnDt": begin.strftime("%Y%m%d0000"),
                    "inqryEndDt": end.strftime("%Y%m%d2359"),
                    "type": "json",
                },
            )
            body = (d.get("response") or {}).get("body") or {}
            rows = body.get("items") or []
            if isinstance(rows, dict):  # single item comes as dict
                rows = rows.get("item") or []
            if isinstance(rows, dict):
                rows = [rows]
            for r in rows:
                no = str(r.get("bidNtceNo", ""))
                ord_ = str(r.get("bidNtceOrd", "") or "")
                items.append(
                    {
                        "source": "g2b",
                        "id": f"{no}-{ord_}" if ord_ else no,
                        "title": str(r.get("bidNtceNm", "")).strip(),
                        "field": label,
                        "org": " / ".join(
                            x for x in (str(r.get("ntceInsttNm", "")).strip(), str(r.get("dminsttNm", "")).strip()) if x
                        ),
                        "apply_start": norm_dt(r.get("bidBeginDt")),
                        "apply_end": norm_dt(r.get("bidClseDt")),
                        "reg_date": norm_dt(r.get("bidNtceDt")),
                        "url": str(r.get("bidNtceDtlUrl", "")).strip()
                        or f"https://www.g2b.go.kr:8101/ep/invitation/publish/bidInfoDtl.do?bidno={no}",
                    }
                )
            total = int(body.get("totalCount") or 0)
            sys.stderr.write(f"[gov-scan] g2b {label} p{page}: {len(rows)} rows (total {total})\n")
            if page * 100 >= total or not rows:
                break
            page += 1
            time.sleep(DELAY)
        time.sleep(DELAY)
    return items


# --------------------------------------------------------------------------
# 청약홈 (applyhome) — 분양정보 조회 서비스 (odcloud style: page/perPage).
# --------------------------------------------------------------------------

APPLYHOME_OPS = [
    ("https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail", "APT"),
    ("https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getRemndrLttotPblancDetail", "무순위/잔여"),
    ("https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getUrbtyOfctlLttotPblancDetail", "오피스텔/도시형/민간임대"),
]


def fetch_applyhome(key, months):
    since = (dt.date.today() - dt.timedelta(days=months * 31)).strftime("%Y-%m-%d")
    items = []
    for url, label in APPLYHOME_OPS:
        page = 1
        while True:
            d = get_json(
                url,
                {
                    "serviceKey": key,
                    "page": str(page),
                    "perPage": "100",
                    "cond[RCRIT_PBLANC_DE::GTE]": since,
                },
            )
            rows = d.get("data") or []

            def first(rec, *keys):
                # date field names differ per operation: APT uses RCEPT_*,
                # 무순위/오피스텔 use SUBSCRPT_RCEPT_* (일반분 GNRL_* as fallback)
                for k in keys:
                    v = rec.get(k)
                    if v:
                        return v
                return ""

            for r in rows:
                mng = str(r.get("HOUSE_MANAGE_NO", ""))
                pbl = str(r.get("PBLANC_NO", "") or "")
                items.append(
                    {
                        "source": "applyhome",
                        "id": f"{mng}-{pbl}" if pbl and pbl != mng else mng,
                        "title": str(r.get("HOUSE_NM", "")).strip(),
                        "field": " / ".join(
                            x
                            for x in (
                                label,
                                str(r.get("HOUSE_SECD_NM", "") or "").strip(),
                                str(r.get("SUBSCRPT_AREA_CODE_NM", "") or "").strip(),
                            )
                            if x
                        ),
                        "org": str(r.get("BSNS_MBY_NM", "") or "").strip(),
                        "apply_start": norm_dt(first(r, "RCEPT_BGNDE", "SUBSCRPT_RCEPT_BGNDE", "GNRL_RCEPT_BGNDE")),
                        "apply_end": norm_dt(first(r, "RCEPT_ENDDE", "SUBSCRPT_RCEPT_ENDDE", "GNRL_RCEPT_ENDDE")),
                        "reg_date": norm_dt(r.get("RCRIT_PBLANC_DE")),
                        "url": str(r.get("PBLANC_URL", "") or "").strip()
                        or "https://www.applyhome.co.kr/ai/aia/selectAPTLttotPblancListView.do",
                    }
                )
            total = int(d.get("totalCount") or 0)
            sys.stderr.write(f"[gov-scan] applyhome {label} p{page}: {len(rows)} rows (total {total})\n")
            if page * 100 >= total or not rows:
                break
            page += 1
            time.sleep(DELAY)
        time.sleep(DELAY)
    return items


def main():
    ap = argparse.ArgumentParser(description="data.go.kr API clients (gov-scan)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_g2b = sub.add_parser("g2b", help="나라장터 입찰공고 (4 business categories)")
    p_g2b.add_argument("-o", "--output", default="g2b.jsonl")
    p_g2b.add_argument("--days", type=int, default=14, help="notice-date lookback window")

    p_ah = sub.add_parser("applyhome", help="청약홈 분양정보 (APT/무순위/오피스텔)")
    p_ah.add_argument("-o", "--output", default="applyhome.jsonl")
    p_ah.add_argument("--months", type=int, default=2, help="notice-date lookback window")

    args = ap.parse_args()
    key = load_key()

    try:
        if args.cmd == "g2b":
            items = fetch_g2b(key, args.days)
        else:
            items = fetch_applyhome(key, args.months)
    except Exception as e:  # noqa: BLE001 — fail loudly with the real reason
        sys.stderr.write(
            f"[gov-scan] API error: {e}\n"
            "  키가 방금 발급됐다면 반영까지 수 분~1시간 걸릴 수 있습니다.\n"
            "  계속 실패하면 data.go.kr 마이페이지에서 활용신청 승인 상태를 확인하세요.\n"
        )
        sys.exit(1)

    with open(args.output, "w", encoding="utf-8") as f:
        for i in items:
            f.write(json.dumps(i, ensure_ascii=False) + "\n")
    sys.stderr.write(f"[gov-scan] saved: {args.output} ({len(items)} items)\n")


if __name__ == "__main__":
    main()
