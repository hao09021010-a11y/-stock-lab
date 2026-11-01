#!/usr/bin/env python3
"""每天抓台股(上市)收盤資料 → 累積歷史 → 計算指標 → 輸出 data/screen.json"""
import csv, json, re, sys, time, datetime as dt
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HIST = DATA / "history.csv"
KEEP_DAYS = 90      # 歷史資料保留幾個交易日
MIN_DAYS = 21       # 至少要幾天資料才會算指標
BACKFILL_TO = 30    # 第一次執行時，回補到幾個交易日
HEAD = {"User-Agent": "Mozilla/5.0 (personal stock lab)"}
COLS = ["date", "code", "name", "o", "h", "l", "c", "v"]
TW = dt.timezone(dt.timedelta(hours=8))


def num(x):
    try:
        return float(str(x).replace(",", "").replace("+", "").strip())
    except ValueError:
        return None


def get_json(url, params=None):
    for _ in range(3):
        try:
            r = requests.get(url, params=params, headers=HEAD, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print("retry:", url, e, file=sys.stderr)
            time.sleep(5)
    return None


def iso_date(s):
    s = str(s).strip()
    if len(s) == 7 and s.isdigit():          # 民國 1141031
        return f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:]}"
    if len(s) == 8 and s.isdigit():          # 20261031
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def make_row(date, code, name, o, h, l, c, v):
    code = str(code).strip()
    if not re.fullmatch(r"\d{4}", code):      # 只收 4 碼一般股票，排除 ETF、權證
        return None
    o, h, l, c, v = (num(x) for x in (o, h, l, c, v))
    if None in (h, l, c, v) or v == 0:
        return None
    return dict(date=date, code=code, name=str(name).strip(), o=o if o is not None else c, h=h, l=l, c=c, v=v)


def fetch_today():
    j = get_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL") or []
    date = next((iso_date(d.get("Date", "")) for d in j if iso_date(d.get("Date", ""))), None)
    date = date or dt.datetime.now(TW).date().isoformat()
    rows = [make_row(date, d.get("Code"), d.get("Name", ""), d.get("OpeningPrice"), d.get("HighestPrice"),
                     d.get("LowestPrice"), d.get("ClosingPrice"), d.get("TradeVolume")) for d in j]
    return date, [r for r in rows if r]


def fetch_day(date):
    """回補用：抓指定日期的全部上市股票。假日會回傳空串列。"""
    j = get_json("https://www.twse.com.tw/exchangeReport/MI_INDEX",
                 {"response": "json", "date": date.replace("-", ""), "type": "ALLBUT0999"})
    if not j or j.get("stat") != "OK":
        return []
    for t in j.get("tables", []):
        f = t.get("fields", [])
        if "證券代號" in f and "收盤價" in f:
            ix = {k: f.index(k) for k in ["證券代號", "證券名稱", "成交股數", "開盤價", "最高價", "最低價", "收盤價"]}
            rows = [make_row(date, d[ix["證券代號"]], d[ix["證券名稱"]], d[ix["開盤價"]], d[ix["最高價"]],
                             d[ix["最低價"]], d[ix["收盤價"]], d[ix["成交股數"]]) for d in t.get("data", [])]
            return [r for r in rows if r]
    return []


def fetch_inst(date):
    """外資買賣超（張）。抓不到就回傳空 dict，外資欄位會是 0。"""
    j = get_json("https://www.twse.com.tw/fund/T86",
                 {"response": "json", "date": date.replace("-", ""), "selectType": "ALLBUT0999"})
    if not j or j.get("stat") != "OK":
        return {}
    f = j.get("fields", [])
    fi = next((i for i, k in enumerate(f) if k.startswith("外") and "買賣超股數" in k), None)
    if fi is None or "證券代號" not in f:
        return {}
    ci = f.index("證券代號")
    out = {}
    for d in j.get("data", []):
        v = num(d[fi])
        if v is not None:
            out[str(d[ci]).strip()] = round(v / 1000)
    return out


IND = {"01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
       "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造業",
       "15": "航運業", "16": "觀光餐旅", "17": "金融保險業", "18": "貿易百貨業", "20": "其他", "21": "化學工業",
       "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
       "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業", "31": "其他電子業",
       "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活"}
NEWS = DATA / "news.json"
DIV = DATA / "dividends.json"


def openapi(path):
    j = get_json("https://openapi.twse.com.tw/v1" + path)
    return j if isinstance(j, list) else []


def val(d, *subs, none=()):
    """用欄位名稱的關鍵字找值，欄位名稱有小差異（- 或 _）也能對上。"""
    for k, v in d.items():
        if all(s in k for s in subs) and not any(n in k for n in none):
            return v
    return None


def fetch_fund():
    """本益比、股價淨值比、殖利率"""
    out = {}
    for d in openapi("/exchangeReport/BWIBBU_ALL"):
        code = str(d.get("Code") or val(d, "代號") or "").strip()
        if code:
            out[code] = dict(pe=num(d.get("PEratio", val(d, "本益比"))), pb=num(d.get("PBratio", val(d, "淨比"))),
                             dy=num(d.get("DividendYield", val(d, "殖利率"))))
    return out


def fetch_rev():
    """月營收（仟元）、月增、年增、產業別"""
    out = {}
    for d in openapi("/opendata/t187ap05_L"):
        code = str(val(d, "公司代號") or "").strip()
        if not code:
            continue
        ind = str(val(d, "產業別") or "").strip()
        out[code] = dict(ind=IND.get(ind.zfill(2), ind) if ind.isdigit() else ind,
                         ym=str(val(d, "資料年月") or ""),
                         rev=num(val(d, "當月營收", none=("去年", "上月", "累計"))),
                         mom=num(val(d, "上月比較增減")),
                         yoy=num(val(d, "去年同月增減", none=("累計",))))
    return out


def fetch_margin():
    """融資融券餘額（張）與較前一日變化"""
    out = {}
    for d in openapi("/exchangeReport/MI_MARGN"):
        code = str(val(d, "代號") or "").strip()
        a, a0 = num(val(d, "融資", "今日餘額")), num(val(d, "融資", "前日餘額"))
        b, b0 = num(val(d, "融券", "今日餘額")), num(val(d, "融券", "前日餘額"))
        if code and a is not None:
            out[code] = dict(mf=a, mfc=None if a0 is None else a - a0, ms=b, msc=None if None in (b, b0) else b - b0)
    return out


def update_news():
    """每日重大訊息，累積保留每檔最新 5 則"""
    old = json.loads(NEWS.read_text("utf-8")) if NEWS.exists() else {}
    for d in openapi("/opendata/t187ap04_L"):
        code = str(val(d, "公司代號") or "").strip()
        title = str(val(d, "主旨") or "").strip()
        day = str(val(d, "發言日期") or "").strip()
        if not re.fullmatch(r"\d{4}", code) or not title:
            continue
        item = [iso_date(day) or day, title[:60]]
        if item not in old.setdefault(code, []):
            old[code].append(item)
    for code in old:
        old[code] = sorted(old[code], key=lambda x: x[0], reverse=True)[:5]
    NEWS.write_text(json.dumps(old, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return old


def first(d, *subs):
    """欄位名稱含任一關鍵字就取值（中英文欄名都試）。"""
    for k, v in d.items():
        if any(s.lower() in k.lower() for s in subs):
            return v
    return None


def update_div():
    """證交所除權除息預告表 → 累積成 {股號: [[除息日, 每股現金股利], ...]}（每日累積，不會消失）"""
    old = json.loads(DIV.read_text("utf-8")) if DIV.exists() else {}
    rows = openapi("/exchangeReport/TWT48U_ALL")
    ok = 0
    for d in rows:
        code = str(first(d, "Code", "代號") or "").strip()
        ex = iso_date(str(first(d, "Date", "除權息日期", "日期") or "").strip())
        cash = num(first(d, "CashDividend", "現金股利"))
        if not re.fullmatch(r"\d{4}", code) or not ex or not cash:
            continue
        item = [ex, cash]
        lst = old.setdefault(code, [])
        lst[:] = [x for x in lst if x[0] != ex] + [item]
        ok += 1
    if rows and not ok:
        print("dividend fields not matched, sample keys:", list(rows[0].keys()), file=sys.stderr)
    DIV.write_text(json.dumps(old, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return ok


def load_hist():
    if not HIST.exists():
        return []
    with open(HIST, encoding="utf-8", newline="") as fh:
        return [dict(r, o=float(r["o"]), h=float(r["h"]), l=float(r["l"]), c=float(r["c"]), v=float(r["v"]))
                for r in csv.DictReader(fh)]


def save_hist(rows):
    dates = sorted({r["date"] for r in rows})[-KEEP_DAYS:]
    keep = set(dates)
    rows = sorted((r for r in rows if r["date"] in keep), key=lambda r: (r["date"], r["code"]))
    with open(HIST, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def rsi14(c):
    d = [c[i] - c[i - 1] for i in range(len(c) - 14, len(c))]
    g = sum(x for x in d if x > 0) / 14
    l = -sum(x for x in d if x < 0) / 14
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def pick(cands, p, below):
    """從候選價位挑最近的兩個；彼此相差不到 0.5% 的視為同一個。"""
    xs = sorted((x for x in cands if (x < p if below else x > p)), key=lambda x: abs(x - p))
    out = []
    for x in xs:
        if all(abs(x / y - 1) > 0.005 for y in out):
            out.append(x)
    k = 1
    while len(out) < 2:                      # 找不到就用 ±5%、±10% 補
        out.append(p * (1 - 0.05 * k) if below else p * (1 + 0.05 * k))
        k += 1
    return [round(x, 2) for x in out[:2]]


def clip(x):
    return int(max(0, min(100, round(x))))


def build_screen(rows, inst, date, extra=None, news=None):
    extra, news = extra or {}, news or {}
    by = {}
    for r in sorted(rows, key=lambda r: r["date"]):
        by.setdefault(r["code"], []).append(r)
    stocks = []
    for code, rs in by.items():
        if len(rs) < MIN_DAYS or rs[-1]["date"] != date:
            continue
        c = [r["c"] for r in rs]
        h = [r["h"] for r in rs]
        l = [r["l"] for r in rs]
        v = [r["v"] for r in rs]
        p = c[-1]
        ch = (p / c[-2] - 1) * 100
        avgv = sum(v[-21:-1]) / 20
        vr = v[-1] / avgv if avgv else 1
        ma20 = sum(c[-20:]) / 20
        ma60 = sum(c[-60:]) / len(c[-60:])
        f = inst.get(code, 0)
        cands = [max(h[-20:]), max(h[-60:]), min(l[-20:]), min(l[-60:]), ma20, ma60]
        trend = clip(50 + (p / ma20 - 1) * 500)
        chip = clip(50 + f / max(avgv / 1000, 1) * 500)
        pv = clip(50 + ch * 5 + (vr - 1) * 10)
        stocks.append(dict(c=code, n=rs[-1]["name"], p=round(p, 2), ch=round(ch, 2), vr=round(vr, 2),
                           rsi=round(rsi14(c), 1), ma=round(ma20, 2), f=f,
                           lo=round(min(l[-20:]), 2), hi=round(max(h[-20:]), 2),
                           s=pick(cands, p, True), r=pick(cands, p, False), sc=[trend, chip, pv], nw=news.get(code, [])[:3], **extra.get(code, {})))
    return {"date": date, "stocks": sorted(stocks, key=lambda s: s["c"])}


def main():
    DATA.mkdir(exist_ok=True)
    rows = load_hist()
    date, today = fetch_today()
    if not today:
        sys.exit("STOCK_DAY_ALL 沒有資料，結束")
    rows = [r for r in rows if r["date"] != date] + today
    have = {r["date"] for r in rows}
    if len(have) < MIN_DAYS:                 # 第一次執行：往前回補
        d = dt.date.fromisoformat(date)
        for i in range(1, 70):
            if len(have) >= BACKFILL_TO:
                break
            x = (d - dt.timedelta(days=i)).isoformat()
            if dt.date.fromisoformat(x).weekday() >= 5 or x in have:
                continue
            got = fetch_day(x)
            time.sleep(4)                    # 放慢，避免被證交所限流
            if got:
                rows += got
                have.add(x)
                print("backfilled", x, len(got))
    save_hist(rows)
    extra = {}
    for fn in (fetch_fund, fetch_rev, fetch_margin):
        try:
            for code, v in fn().items():
                extra.setdefault(code, {}).update(v)
        except Exception as e:
            print("extra failed:", fn.__name__, e, file=sys.stderr)
    try:
        news = update_news()
    except Exception as e:
        print("news failed:", e, file=sys.stderr)
        news = {}
    try:
        print("dividends parsed:", update_div())
    except Exception as e:
        print("dividends failed:", e, file=sys.stderr)
    print("extra data for", len(extra), "stocks;", len(news), "with news")
    screen = build_screen(rows, fetch_inst(date), date, extra, news)
    (DATA / "screen.json").write_text(json.dumps(screen, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("done", date, len(screen["stocks"]), "stocks")


if __name__ == "__main__":
    main()
