#!/usr/bin/env python3
"""Build the gov-scan daily static page from crawl runs.

Reads the two most recent run directories (runs/YYYYMMDD/*.jsonl, the same
9-field records gov_crawl.py / gov_api.py emit), computes a (source, id) diff
against the previous run, and writes a single self-contained index.html:

  - 오늘 신규 (previous run에 없던 공고)
  - 마감 임박 (apply_end가 오늘~7일 이내인 현재 공고)
  - 도메인 탭 + 클라이언트 사이드 텍스트 필터

The page is a data dashboard only — no eligibility verdicts (those need the
gov-scan skill), no personal data, no API keys.

Usage:
  python3 build_page.py --runs-dir ~/Documents/gov-scan-daily/runs --site-dir ~/Documents/gov-scan-daily/site
"""
import argparse
import datetime as dt
import html
import json
from pathlib import Path

DOMAINS = {
    "alio": ("채용", "잡알리오"),
    "gojobs": ("채용", "나라일터"),
    "applyhome": ("청약", "청약홈"),
    "iris": ("R&D", "IRIS"),
    "g2b": ("입찰", "나라장터"),
    "s2b": ("학교장터", "S2B"),
    "kosmes": ("정책자금", "중진공"),
    "semas": ("정책자금", "소진공"),
}
DOMAIN_ORDER = ["채용", "청약", "R&D", "입찰", "학교장터", "정책자금"]


def load_run(d: Path):
    records = {}
    for f in sorted(d.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("source") and r.get("id"):
                records[(r["source"], str(r["id"]))] = r
    return records


def dday(end, today):
    try:
        return (dt.date.fromisoformat(end) - today).days
    except ValueError:
        return None


def item_view(r, today):
    domain, src_label = DOMAINS.get(r["source"], ("기타", r["source"]))
    d = dday(r.get("apply_end", ""), today)
    return {
        "dom": domain,
        "src": src_label,
        "t": r.get("title", ""),
        "o": r.get("org", ""),
        "f": r.get("field", ""),
        "e": r.get("apply_end", "") or "불명",
        "d": d,
        "u": r.get("url", ""),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", required=True, type=Path)
    ap.add_argument("--site-dir", required=True, type=Path)
    args = ap.parse_args()

    runs = sorted([d for d in args.runs_dir.iterdir() if d.is_dir() and d.name.isdigit()])
    if not runs:
        raise SystemExit("no run directories found")
    curr_dir = runs[-1]
    prev_dir = runs[-2] if len(runs) > 1 else None

    curr = load_run(curr_dir)
    prev = load_run(prev_dir) if prev_dir else {}
    today = dt.date.today()

    prev_sources = {k[0] for k in prev}
    curr_sources = {k[0] for k in curr}
    common = prev_sources & curr_sources

    new_items = [curr[k] for k in curr if prev and k not in prev and k[0] in common]
    first_run = not prev
    # deadline within 0..7 days, among all current records
    urgent = []
    for r in curr.values():
        d = dday(r.get("apply_end", ""), today)
        if d is not None and 0 <= d <= 7:
            urgent.append(r)
    missing_sources = sorted(
        {s for s in DOMAINS if s not in curr_sources}
    )

    views_new = sorted(
        (item_view(r, today) for r in (curr.values() if first_run else new_items)),
        key=lambda v: (v["e"] == "불명", v["e"]),
    )
    views_urgent = sorted((item_view(r, today) for r in urgent), key=lambda v: v["e"])

    src_counts = {}
    for s, label in ((s, DOMAINS[s][1]) for s in DOMAINS):
        n = sum(1 for k in curr if k[0] == s)
        src_counts[label] = n

    data = {
        "updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "runDate": curr_dir.name,
        "prevDate": prev_dir.name if prev_dir else None,
        "firstRun": first_run,
        "total": len(curr),
        "newCount": len(views_new),
        "urgentCount": len(views_urgent),
        "srcCounts": src_counts,
        "missing": [DOMAINS[s][1] for s in missing_sources],
        "domains": DOMAIN_ORDER,
        "new": views_new,
        "urgent": views_urgent,
    }

    tpl = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>공공 공고 데일리 — 채용·청약·R&D·입찰·정책자금</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#667;--line:#e2e5ea;--acc:#0b57d0;--urg:#c62828;--chip:#f1f3f6}
@media(prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e8eaed;--mut:#9aa;--line:#2c3138;--acc:#7ab0ff;--urg:#ff8a80;--chip:#22262c}}
*{box-sizing:border-box}body{margin:0;font-family:'Segoe UI',Pretendard,system-ui,sans-serif;background:var(--bg);color:var(--fg);line-height:1.5}
.wrap{max-width:980px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:1.35rem;margin:.2em 0}.sub{color:var(--mut);font-size:.85rem}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}
.stat{background:var(--chip);border-radius:10px;padding:10px 16px;min-width:100px}
.stat b{display:block;font-size:1.4rem}.stat span{font-size:.78rem;color:var(--mut)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:18px 0 10px}
.tab{border:1px solid var(--line);background:none;color:var(--fg);border-radius:20px;padding:6px 14px;cursor:pointer;font-size:.88rem}
.tab.on{background:var(--acc);border-color:var(--acc);color:#fff}
input#q{width:100%;padding:10px 14px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--fg);font-size:.95rem;margin:4px 0 14px}
h2{font-size:1.05rem;border-bottom:2px solid var(--line);padding-bottom:6px;margin-top:28px}
.item{padding:10px 2px;border-bottom:1px solid var(--line)}
.item a{color:var(--acc);text-decoration:none;font-weight:600}.item a:hover{text-decoration:underline}
.meta{font-size:.8rem;color:var(--mut);margin-top:2px}
.b{display:inline-block;font-size:.72rem;border-radius:6px;padding:1px 7px;margin-right:6px;background:var(--chip)}
.b.urg{background:var(--urg);color:#fff}.b.dom{background:var(--acc);color:#fff}
.note{background:var(--chip);border-radius:10px;padding:12px 16px;font-size:.82rem;color:var(--mut);margin-top:34px}
.empty{color:var(--mut);padding:18px 2px;font-size:.9rem}
</style>
</head>
<body><div class="wrap">
<h1>공공 공고 데일리</h1>
<div class="sub" id="sub"></div>
<div class="stats" id="stats"></div>
<div class="tabs" id="tabs"></div>
<input id="q" type="search" placeholder="공고명·기관·분류 검색 (예: 개발, 세종, 정규직)">
<h2 id="h-urgent"></h2><div id="urgent"></div>
<h2 id="h-new"></h2><div id="new"></div>
<div class="note">
데이터 출처: 공공데이터포털(조달청 나라장터·한국부동산원 청약홈 OpenAPI) 및 각 기관 공개 공고 페이지(IRIS·나라일터·잡알리오·S2B·중진공·소진공).
본 페이지는 조사 시점의 목록 정보를 요약한 것으로, 마감·자격·조건은 수시로 변경됩니다 — <b>신청 전 반드시 원문과 접수기관에서 확인하세요.</b>
자격 판정이 아닌 목록 대시보드이며, 마감일 '불명'은 목록에 마감 정보가 없는 공고입니다.
</div>
</div>
<script>
const D=__DATA__;
const sub=document.getElementById('sub');
sub.textContent=`갱신 ${D.updated} · 수집 ${D.total.toLocaleString()}건`+(D.prevDate?` · 직전 조사 ${D.prevDate.replace(/(\\d{4})(\\d{2})(\\d{2})/,'$1-$2-$3')} 대비`:' · 최초 수집')+(D.missing.length?` · 미수집: ${D.missing.join(', ')}`:'');
const stats=document.getElementById('stats');
[[D.firstRun?'전체(최초)':'오늘 신규',D.newCount],['마감 임박(7일)',D.urgentCount],...Object.entries(D.srcCounts).map(([k,v])=>[k,v])].forEach(([k,v])=>{
  const s=document.createElement('div');s.className='stat';s.innerHTML=`<b>${v.toLocaleString()}</b><span>${k}</span>`;stats.appendChild(s);});
let dom='전체',q='';
const tabs=document.getElementById('tabs');
['전체',...D.domains].forEach(d=>{const b=document.createElement('button');b.className='tab'+(d===dom?' on':'');b.textContent=d;
  b.onclick=()=>{dom=d;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.textContent===d));render();};tabs.appendChild(b);});
document.getElementById('q').addEventListener('input',e=>{q=e.target.value.toLowerCase();render();});
function row(v){
  const dd=v.d===null?'':(v.d<0?'':(v.d===0?'<span class="b urg">오늘 마감</span>':(v.d<=3?`<span class="b urg">D-${v.d}</span>`:`<span class="b">D-${v.d}</span>`)));
  return `<div class="item"><span class="b dom">${v.dom}</span>${dd}<a href="${v.u}" target="_blank" rel="noopener">${esc(v.t)}</a>
  <div class="meta">${esc(v.o)}${v.f?' · '+esc(v.f):''} · 마감 ${v.e} · ${v.src}</div></div>`;}
function esc(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function match(v){return (dom==='전체'||v.dom===dom)&&(!q||(v.t+v.o+v.f+v.src).toLowerCase().includes(q));}
function render(){
  const u=D.urgent.filter(match),n=D.new.filter(match);
  document.getElementById('h-urgent').textContent=`⏰ 마감 임박 — 7일 이내 (${u.length})`;
  document.getElementById('h-new').textContent=(D.firstRun?`📋 전체 공고 — 최초 수집 (${n.length})`:`🆕 오늘 신규 (${n.length})`);
  document.getElementById('urgent').innerHTML=u.map(row).join('')||'<div class="empty">해당 없음</div>';
  document.getElementById('new').innerHTML=n.map(row).join('')||'<div class="empty">해당 없음</div>';}
render();
</script>
</body></html>"""

    page = tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    args.site_dir.mkdir(parents=True, exist_ok=True)
    (args.site_dir / "index.html").write_text(page, encoding="utf-8")
    print(
        f"[gov-scan] page built: {args.site_dir / 'index.html'} "
        f"(total {data['total']}, new {data['newCount']}, urgent {data['urgentCount']}, "
        f"{len(page) // 1024}KB)"
    )


if __name__ == "__main__":
    main()
