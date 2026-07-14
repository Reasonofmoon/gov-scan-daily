#!/usr/bin/env python3
"""Multi-source crawler for Korean public-sector open notices (gov-scan skill).

Covers the crawl-path sources of the tier-1 public-data domains
(the API-key sources — 나라장터 g2b, 청약홈 applyhome — live in gov_api.py):

  iris     IRIS (범부처통합연구지원시스템) — government R&D calls  [JSON]
  gojobs   나라일터 — public-sector job openings (civil service)   [HTML]
  alio     잡알리오 — public-institution job openings              [HTML]
  s2b      학교장터 S2B — school procurement bid notices           [HTML]
  kosmes   중소벤처기업진흥공단 — notices incl. policy-fund plans  [JSON]
  semas    소상공인시장진흥공단 — business announcements (사업공고) [HTML]

Only public announcement pages are accessed; no login, no private areas.
A polite delay is applied between requests.

Usage:
  python3 gov_crawl.py list iris -o iris.jsonl
  python3 gov_crawl.py list all -o gov_all.jsonl --max-pages 15
  python3 gov_crawl.py detail <url> [<url> ...] -o details/

Unified JSONL schema (same contract as ir-search sources_crawl.py):
  {"source", "id", "title", "field", "org", "apply_start", "apply_end",
   "reg_date", "url"}
Empty string means the list page does not provide the value (report as 불명).

Dependency: curl_cffi>=0.15 recommended (TLS-fingerprint friendly).
Falls back to urllib; if blocked, an install hint is printed.
"""
import argparse
import datetime as dt
import html as htmllib
import json
import re
import sys
import time

DELAY = 0.4  # seconds between requests (politeness)


def make_fetcher():
    """Prefer curl_cffi (Chrome TLS fingerprint); fall back to urllib."""
    try:
        from curl_cffi import requests as cr

        sess = cr.Session(impersonate="chrome")

        def fetch(url, data=None):
            # data=dict switches to a POST form submit (some boards paginate that way)
            if data is None:
                r = sess.get(url, timeout=30)
            else:
                r = sess.post(url, data=data, timeout=30)
            return r.status_code, r.text

        return fetch, "curl_cffi"
    except ImportError:
        import urllib.parse
        import urllib.request

        def fetch(url, data=None):
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    )
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")

        return fetch, "urllib"


def clean(s):
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


def norm_date(s):
    """Normalize date-ish strings to YYYY-MM-DD; return input if not parseable."""
    s = clean(s)
    m = re.search(r"(\d{4})[.\-/\s]+(\d{1,2})[.\-/\s]+(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)  # 26.07.28
    if m:
        return f"20{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)  # 20260728
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s


def split_period(s):
    """Split '2026-07-07 ~ 2026-07-17'-style ranges into (start, end)."""
    parts = re.split(r"~|∼", s)
    if len(parts) == 2:
        return norm_date(parts[0]), norm_date(parts[1])
    return "", norm_date(s)


# --------------------------------------------------------------------------
# Per-source parsers. Each returns (items, has_more) for one page.
# Endpoints and structures verified live on 2026-07-14; if a site redesign
# breaks a parser, it fails loudly (0 items) rather than returning wrong data.
# --------------------------------------------------------------------------

def page_iris(fetch, page):
    # JSON endpoint behind the list page (list itself is jsrender-templated).
    status, body = fetch(
        "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do",
        data={"pageIndex": str(page), "ancmStt": "", "bsnsTl": ""},
    )
    if status != 200:
        return [], False
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        return [], False
    rows = d.get("listBsnsAncmBtinSitu", [])
    items = []
    for r in rows:
        org = " / ".join(x for x in (clean(r.get("blngGovdSeNm")), clean(r.get("sorgnNm"))) if x)
        items.append(
            {
                "source": "iris",
                "id": str(r.get("ancmId", "")),
                "title": clean(r.get("ancmTl")),
                "field": clean(r.get("pbofrTpSeNmLst")) or clean(r.get("rcveStt")),
                "org": org,
                "apply_start": norm_date(str(r.get("rcveStrDe", ""))),
                "apply_end": norm_date(str(r.get("rcveEndDe", ""))),
                "reg_date": norm_date(str(r.get("ancmDe", ""))),
                "url": f"https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId={r.get('ancmId', '')}",
            }
        )
    pi = d.get("paginationInfo", {})
    more = pi.get("currentPageNo", page) < pi.get("totalPageCount", page)
    return items, bool(items) and more


def page_gojobs(fetch, page):
    # Rows: no / title+fn_apmView('type','empmnsn') / org / reg date / deadline / views
    status, h = fetch(
        f"https://www.gojobs.go.kr/apmList.do?menuNo=401&pageIndex={page}"
    )
    if status != 200:
        return [], False
    items = []
    for row in re.findall(r"<tr[^>]*>[\s\S]*?</tr>", h):
        m = re.search(r"fn_apmView\('(\d+)',\s*'(\d+)'\)[^>]*>([\s\S]*?)</a>", row)
        if not m:
            continue
        tds = [clean(re.sub(r"<[^>]+>", " ", td)) for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)]
        # tds: [no, title-cell, org, reg_date, deadline, views]
        dates = [t for t in tds if re.search(r"\d{4}-\d{2}-\d{2}", t)]
        items.append(
            {
                "source": "gojobs",
                "id": m.group(2),
                "title": clean(m.group(3)),
                "field": "",
                "org": tds[2] if len(tds) > 2 else "",
                "apply_start": "",
                "apply_end": norm_date(dates[1]) if len(dates) > 1 else "",
                "reg_date": norm_date(dates[0]) if dates else "",
                "url": f"https://www.gojobs.go.kr/apmView.do?empmnsn={m.group(2)}",
            }
        )
    return items, bool(items)


def page_alio(fetch, page):
    # Rows: checkbox / no / title+link(/recruitview.do?idx=) / org / region /
    #       employment type / reg date / deadline(+D-day) / status
    status, h = fetch(f"https://job.alio.go.kr/recruit.do?pageNo={page}")
    if status != 200:
        return [], False
    items = []
    for row in re.findall(r"<tr[^>]*>[\s\S]*?</tr>", h):
        m = re.search(r'href="/recruitview\.do\?idx=(\d+)"[^>]*/?>([\s\S]*?)</a>', row)
        if not m:
            continue
        tds = [clean(re.sub(r"<[^>]+>", " ", td)) for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)]
        # tds: [ckbox, no, title, org, region, emp_type, reg, deadline+dday, status]
        deadline = re.sub(r"D\s*-\s*\d+|마감|진행중", "", tds[7]) if len(tds) > 7 else ""
        items.append(
            {
                "source": "alio",
                "id": m.group(1),
                "title": clean(m.group(2)),
                "field": " / ".join(x for x in (tds[5] if len(tds) > 5 else "", tds[4] if len(tds) > 4 else "") if x),
                "org": tds[3] if len(tds) > 3 else "",
                "apply_start": "",
                "apply_end": norm_date(deadline),
                "reg_date": norm_date(tds[6]) if len(tds) > 6 else "",
                "url": f"https://job.alio.go.kr/recruitview.do?idx={m.group(1)}",
            }
        )
    return items, bool(items)


def page_s2b(fetch, page):
    # Bid-notice tab only (forwardName=list); list02/03/04 are opened-bid /
    # award / contract RESULT boards — not open notices, so not crawled.
    # Search window: last 4 months by notice date (server rejects >4 months).
    today = dt.date.today()
    start = (today - dt.timedelta(days=119)).strftime("%Y%m%d")
    status, h = fetch(
        "https://www.s2b.kr/S2BNCustomer/stmo001.do",
        data={
            "forwardName": "list",
            "pageNo": str(page),
            "tender_num": "",
            "tender_step_code": "",
            "page_flag": "",
            "tender_sep1": "1",
            "tender_name": "",
            "company_name_s": "",
            "tender_sep2": "1",  # window by notice date (keeps coverage; report filters by apply_end)
            "tender_date_start": start,
            "tender_date_end": today.strftime("%Y%m%d"),
            "tender_item": "",
            "city": "",
        },
    )
    if status != 200:
        return [], False
    items = []
    # Two <tr> per item: row1 = no/공고번호/분류/공고명(f_detail)/상태,
    # row2 = 도서·산간/기관명/공고일/입찰서제출마감일.
    rows = re.findall(r"<tr>[\s\S]*?</tr>", h)
    for i, row in enumerate(rows):
        m = re.search(r"f_detail\('([^']+)'\);?[^>]*>([\s\S]*?)</a>", row)
        if not m:
            continue
        tds1 = [clean(re.sub(r"<[^>]+>", " ", td)) for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)]
        tds2 = []
        if i + 1 < len(rows):
            tds2 = [clean(re.sub(r"<[^>]+>", " ", td)) for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", rows[i + 1])]
        # tds2: [도서/산간, 기관명, 공고일 HH:MM, 입찰서제출마감일 HH:MM]
        items.append(
            {
                "source": "s2b",
                "id": m.group(1),
                "title": clean(m.group(2)),
                "field": tds1[2] if len(tds1) > 2 else "",
                "org": tds2[1] if len(tds2) > 1 else "",
                "apply_start": norm_date(tds2[2]) if len(tds2) > 2 else "",
                "apply_end": norm_date(tds2[3]) if len(tds2) > 3 else "",
                "reg_date": norm_date(tds2[2]) if len(tds2) > 2 else "",
                "url": (
                    "https://www.s2b.kr/S2BNCustomer/stmo001.do"
                    f"?forwardName=view&tender_num={m.group(1)}&tender_step_code=A&page_flag=2"
                ),
            }
        )
    return items, bool(items)


def page_kosmes(fetch, page):
    # AXGrid JSON endpoint behind the notice board (tab 01 = KOSME's own notices).
    # Policy-fund plans are posted here; deadlines usually live in attachments,
    # so apply_end stays empty (report as 불명, check detail).
    status, body = fetch(
        "https://www.kosmes.or.kr/sh/nts/notice_list.json",
        data={
            "nowPage": str(page),
            "pageCount": "10",
            "rowCount": "10",
            "param": "proc=List",
            "bKind": "popluar",
            "activatedTab": "01",
        },
    )
    if status != 200:
        return [], False
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        return [], False
    items = []
    for r in d.get("ds_infoList", []):
        items.append(
            {
                "source": "kosmes",
                "id": str(r.get("SLNO", "")),
                "title": clean(r.get("TITL_NM")),
                "field": clean(r.get("CATG_CD")),
                "org": "중소벤처기업진흥공단",
                "apply_start": "",
                "apply_end": "",
                "reg_date": norm_date(str(r.get("REG_DTM", ""))),
                "url": f"https://www.kosmes.or.kr/nsh/SH/NTS/SHNTS001F0.do?seqNo={r.get('SLNO', '')}",
            }
        )
    return items, bool(items)


def page_semas(fetch, page):
    # 사업공고 board (bCd=2001). Items link out to 소상공인24 (sbiz24.kr) SPA;
    # the sbiz24 detail page is JS-rendered, so detail text may be thin —
    # keep the URL and treat missing details as 불명/링크 참조.
    status, h = fetch(
        "https://www.semas.or.kr/web/board/webBoardList.kmdc"
        f"?bCd=2001&pNm=BOA0121&page={page}"
    )
    if status != 200:
        return [], False
    items = []
    for block in re.findall(r'<a class="aconbox"[\s\S]*?</a>', h):
        m = re.search(r'href="(https://www\.sbiz24\.kr/#/pbanc/(\d+))"', block)
        if not m:
            continue
        title = re.search(r'<div class="cut_text1">\s*([\s\S]*?)\s*</div>', block)
        period = re.search(r'<div class="date">\s*([^<]+?)\s*</div>', block)
        start, end = split_period(period.group(1)) if period else ("", "")
        items.append(
            {
                "source": "semas",
                "id": m.group(2),
                "title": clean(title.group(1)) if title else "",
                "field": "사업공고",
                "org": "소상공인시장진흥공단",
                "apply_start": start,
                "apply_end": end,
                "reg_date": "",
                "url": m.group(1),
            }
        )
    return items, bool(items)


SOURCES = {
    "iris": page_iris,
    "gojobs": page_gojobs,
    "alio": page_alio,
    "s2b": page_s2b,
    "kosmes": page_kosmes,
    "semas": page_semas,
}


def crawl(source, fetch, max_pages):
    pager = SOURCES[source]
    seen = {}
    for page in range(1, max_pages + 1):
        items, has_more = pager(fetch, page)
        new = [i for i in items if i["id"] not in seen]
        for i in items:
            seen[i["id"]] = i
        print(
            f"[gov-scan] {source} p{page}: {len(items)} parsed, {len(new)} new, total {len(seen)}",
            file=sys.stderr,
        )
        if not has_more or not new:
            break
        time.sleep(DELAY)
    return list(seen.values())


def strip_html(text):
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = htmllib.unescape(text)
    return re.sub(r"\n\s*\n+", "\n", text)


def cmd_detail(fetch, urls, outdir):
    """Save the text of notice detail pages (any source) for eligibility checks."""
    import os

    os.makedirs(outdir, exist_ok=True)
    allowed = (
        "iris.go.kr", "gojobs.go.kr", "alio.go.kr", "s2b.kr",
        "kosmes.or.kr", "semas.or.kr", "sbiz24.kr",
    )
    for url in urls:
        host = re.sub(r"^https?://([^/]+).*", r"\1", url)
        if not host.endswith(allowed):
            print(f"[gov-scan] skip non-source url: {url[:60]}", file=sys.stderr)
            continue
        try:
            status, h = fetch(url)
            if status != 200:
                print(f"[gov-scan] {url[:60]}: HTTP {status}", file=sys.stderr)
                continue
            name = re.sub(r"\W+", "_", url.split("://", 1)[1])[:80]
            path = f"{outdir}/{name}.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write(url + "\n\n" + strip_html(h))
            print(f"[gov-scan] saved: {path}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — skip failures, keep going
            print(f"[gov-scan] {url[:60]}: error {e}", file=sys.stderr)
        time.sleep(DELAY)


def main():
    ap = argparse.ArgumentParser(
        description="Crawl Korean public-sector open-notice boards (gov-scan)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="crawl notice lists")
    p_list.add_argument("source", choices=[*SOURCES, "all"], help="source to crawl")
    p_list.add_argument("-o", "--output", default="gov_sources.jsonl")
    p_list.add_argument(
        "--max-pages",
        type=int,
        default=30,
        help="page cap per source (alio lists many notices — recent pages usually suffice)",
    )

    p_det = sub.add_parser("detail", help="save detail-page text for given URLs")
    p_det.add_argument("urls", nargs="+", help="notice detail URLs")
    p_det.add_argument("-o", "--output", default="details")

    args = ap.parse_args()

    fetch, backend = make_fetcher()
    print(f"[gov-scan] fetch backend: {backend}", file=sys.stderr)
    if backend == "urllib":
        print(
            "[gov-scan] tip: pip install 'curl_cffi>=0.15' if requests get blocked",
            file=sys.stderr,
        )

    if args.cmd == "detail":
        cmd_detail(fetch, args.urls, args.output)
        return

    names = list(SOURCES) if args.source == "all" else [args.source]
    out = []
    for name in names:
        out.extend(crawl(name, fetch, args.max_pages))
        time.sleep(DELAY)
    with open(args.output, "w", encoding="utf-8") as f:
        for i in out:
            f.write(json.dumps(i, ensure_ascii=False) + "\n")
    print(f"[gov-scan] saved: {args.output} ({len(out)} items)", file=sys.stderr)


if __name__ == "__main__":
    main()
