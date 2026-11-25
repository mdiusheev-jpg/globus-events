# -*- coding: utf-8 -*-
"""Афиша мировых концертов для сайта. Без ключей и без ИИ: Wikipedia (даты), Wikidata (названия по-русски и по-кыргызски), Wikimedia Commons (свободные фото)."""
import datetime as dt, io, json, re, sys, time, urllib.parse
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "docs"
IMG = OUT / "img"
TODAY = dt.date.today()
LIMIT = 36                # карточек концертов
HORIZON = 540             # дней вперёд
S = requests.Session()
S.headers["User-Agent"] = "concert-events-bot/1.0 (python-requests)"
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
MON = {m: i for i, m in enumerate("january february march april may june july august september october november december".split(), 1)}


def get(url, tries=8, **kw):
    for n in range(tries):
        try:
            r = S.get(url, timeout=40, **kw)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            if r.status_code == 429:   # просят подождать — ждём
                time.sleep(min(int(r.headers.get("retry-after") or 30), 90) + 1)
                continue
        except requests.RequestException:
            pass
        time.sleep(2 * (n + 1))
    return None


def api(host="en.wikipedia.org", **p):
    p.update(format="json", formatversion=2)
    r = get(f"https://{host}/w/api.php", params=p)
    return r.json() if r else {}


def category(name):
    d = api(action="query", list="categorymembers", cmtitle="Category:" + name, cmlimit=500)
    return [x["title"] for x in d.get("query", {}).get("categorymembers", []) if x["ns"] == 0]


def clean(el):
    for x in el.select("sup, style, .sortkey, .reference"):
        x.decompose()
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip(" ,")


def href_title(el):
    """Статья, на которую ссылается ячейка (для перевода через Wikidata)"""
    for a in el.select("a[href^='/wiki/']"):
        t = urllib.parse.unquote(a["href"][6:].split("#")[0])
        if ":" not in t and clean(BeautifulSoup(str(a), "html.parser")):
            return t.replace("_", " ")
    return None


def parse_date(s, year=None):
    s = s.lower().replace("\xa0", " ")
    for rx, di, mi, yi in ((r"([a-z]+) (\d{1,2})(?:,? (20\d\d))?", 2, 1, 3), (r"(\d{1,2}) ([a-z]+)(?:,? (20\d\d))?", 1, 2, 3)):
        for m in re.finditer(rx, s):
            y = m[yi] or year
            if m[mi] in MON and y:
                try:
                    return dt.date(int(y), MON[m[mi]], int(m[di]))
                except ValueError:
                    pass
    return None


def grid(table):
    rows, spans = [], {}
    for tr in table.select("tr"):
        cells, col = [], 0
        tds = tr.find_all(["td", "th"], recursive=False)
        i = 0
        while i < len(tds) or col in spans:
            if col in spans:
                el, left = spans[col]
                cells.append(el)
                if left <= 1:
                    del spans[col]
                else:
                    spans[col] = (el, left - 1)
                col += 1
                continue
            el = tds[i]; i += 1
            try:
                rs = int(re.sub(r"\D", "", el.get("rowspan", "1")) or 1); cs = int(re.sub(r"\D", "", el.get("colspan", "1")) or 1)
            except ValueError:
                rs = cs = 1
            for _ in range(cs):
                cells.append(el)
                if rs > 1:
                    spans[col] = (el, rs - 1)
                col += 1
        rows.append(cells)
    return rows


BAD = re.compile(r"cancel|postpon|reschedul", re.I)


def tour(title):
    d = api(action="parse", page=title, prop="text", redirects=1)
    html = d.get("parse", {}).get("text")
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    box = soup.select_one("table.infobox")
    artist = None
    if box:
        # строка «… tour by X» в карточке статьи
        for tr in box.select("tr"):
            if re.search(r"\b(tour|concert|residency|concerts|tours)\b.*\bby\b", tr.get_text(" ", strip=True), re.I):
                tail = [a for a in tr.select("a[href^='/wiki/']") if ":" not in a["href"]]
                txt = str(tr)
                pos = re.search(r"\bby\b", txt)
                for a in tail:
                    if pos and txt.find(str(a)) > pos.start():
                        artist = (a.get_text(" ", strip=True), urllib.parse.unquote(a["href"][6:].split("#")[0]).replace("_", " "))
                        break
                break
    if not artist:
        return None
    shows = {}
    for t in soup.select("table.wikitable"):
        g = grid(t)
        head = None
        cap = t.find("caption")
        h = t.find_previous(["h2", "h3", "h4"])
        if BAD.search((cap.get_text(" ") if cap else "") + " " + (h.get_text(" ") if h else "")):
            continue
        for cells in g:
            names = [clean(BeautifulSoup(str(c), "html.parser")).lower() for c in cells]
            if head is None:
                if any(n.startswith("date") for n in names) and "city" in " ".join(names):
                    head = names
                    if any("reason" in n for n in names):
                        break
                continue
            def col(*keys):
                for k in keys:
                    for i, n in enumerate(head):
                        if n.startswith(k) and i < len(cells):
                            return cells[i]
                return None
            dc = col("date")
            if dc is None or col("city") is None:
                continue
            hd = next(n for n in head if n.startswith("date"))
            hy = re.search(r"20\d\d", hd)
            day = parse_date(names[head.index(hd)], hy and hy[0])
            if not day or day < TODAY or (day - TODAY).days > HORIZON:
                continue
            if BAD.search(" ".join(names)):
                continue
            city_el, ctry_el, ven_el = col("city"), col("country", "region", "state"), col("venue")
            city = clean(BeautifulSoup(str(city_el), "html.parser")); country = clean(BeautifulSoup(str(ctry_el), "html.parser")) if ctry_el is not None else ""
            venue = clean(BeautifulSoup(str(ven_el), "html.parser")) if ven_el is not None else ""
            key = (city, country, venue)
            s = shows.setdefault(key, {"city": city, "country": country, "venue": re.sub(r"\[.*?\]", "", venue), "ct": href_title(city_el), "d": set()})
            s["d"].add(day.isoformat())
    if not shows:
        return None
    name = clean(BeautifulSoup(str(box.select_one(".infobox-above, .infobox-title, caption") or f"<b>{title}</b>"), "html.parser"))
    return {"title": re.sub(r"\s*\(.*?tour.*?\)", "", title, flags=re.I), "wiki": title, "artist": artist[0], "aw": artist[1],
            "shows": [dict(s, d=sorted(s["d"])) for s in shows.values()]}


def counts(host, titles):
    """Просмотры статей за 60 дней, пакетами"""
    out, titles = {}, list(titles)
    for i in range(0, len(titles), 50):
        cont = {}
        while True:
            d = api(host, action="query", titles="|".join(titles[i:i + 50]), redirects=1, prop="pageviews", pvipdays=60, **cont)
            q = d.get("query", {})
            back = {}
            for k in ("normalized", "redirects"):
                for r in q.get(k, []):
                    back[r["to"]] = back.get(r["from"], r["from"])
            for pg in q.get("pages", []):
                if pg.get("pageviews"):
                    out[back.get(pg["title"], pg["title"])] = sum(v or 0 for v in pg["pageviews"].values())
            if "continue" not in d:
                break
            cont = d["continue"]
    return out


def views(arts):
    """Популярность у нашей аудитории: русская Википедия, английская с малым весом"""
    ru = {}
    for i in range(0, len(arts), 50):
        cont = {}
        while True:
            d = api(action="query", titles="|".join(arts[i:i + 50]), redirects=1, prop="langlinks", lllang="ru", lllimit=500, **cont)
            q = d.get("query", {})
            back = {r["to"]: r["from"] for k in ("normalized", "redirects") for r in q.get(k, [])}
            for pg in q.get("pages", []):
                if pg.get("langlinks"):
                    ru[back.get(pg["title"], pg["title"])] = pg["langlinks"][0]["title"]
            if "continue" not in d:
                break
            cont = d["continue"]
    en, rv = counts("en.wikipedia.org", arts), counts("ru.wikipedia.org", ru.values())
    return {a: rv.get(ru.get(a), 0) + en.get(a, 0) // 150 for a in arts}


def revisions(names):
    """Номер версии и текст статей, пакетами"""
    out = {}
    for i in range(0, len(names), 25):
        d = api(action="query", titles="|".join(names[i:i + 25]), redirects=1, prop="revisions", rvprop="ids|content", rvslots="main")
        for pg in d.get("query", {}).get("pages", []):
            try:
                r = pg["revisions"][0]
                out[pg["title"]] = (r["revid"], r["slots"]["main"]["content"])
            except (KeyError, IndexError):
                continue
    return out


def has_future(t):
    for m in re.finditer(r"\{\{(?:start|end)[ _-]?date[^}|]*\|(?:[a-z]+=[^|}]*\|)*(20\d\d)\|(\d{1,2})", t, re.I):
        if (int(m[1]), int(m[2])) >= (TODAY.year, TODAY.month):
            return True
    for m in re.finditer(r"[A-Z][a-z]+ \d{1,2},? 20\d\d|\d{1,2} [A-Z][a-z]+,? 20\d\d", t.replace("&nbsp;", " ")):
        d = parse_date(m.group(0))
        if d and d >= TODAY:
            return True
    return False


def labels(titles):
    """Названия городов и стран на ru и ky из Wikidata"""
    out, titles = {}, sorted(set(t for t in titles if t))
    for i in range(0, len(titles), 45):
        part = titles[i:i + 45]
        d = api("www.wikidata.org", action="wbgetentities", sites="enwiki", titles="|".join(part), props="labels|sitelinks", languages="ru|ky|en", sitefilter="enwiki")
        for e in d.get("entities", {}).values() if isinstance(d.get("entities"), dict) else []:
            sl = e.get("sitelinks", {}).get("enwiki", {}).get("title")
            if sl:
                out[sl] = {k: v["value"] for k, v in e.get("labels", {}).items()}
    # переадресации
    rest = [t for t in titles if t not in out]
    for i in range(0, len(rest), 45):
        d = api(action="query", titles="|".join(rest[i:i + 45]), redirects=1, prop="pageprops", ppprop="wikibase_item")
        q = d.get("query", {})
        back = {r["to"]: r["from"] for r in q.get("redirects", [])}
        ids = {p["pageprops"]["wikibase_item"]: back.get(p["title"], p["title"]) for p in q.get("pages", []) if p.get("pageprops")}
        if ids:
            e = api("www.wikidata.org", action="wbgetentities", ids="|".join(ids), props="labels", languages="ru|ky|en")
            for k, v in e.get("entities", {}).items():
                out[ids[k]] = {x: y["value"] for x, y in v.get("labels", {}).items()}
    return out


def photo(article, slug):
    """Фото раз в 14 дней сверяем с источником, между сверками берём сохранённое"""
    f = IMG / f"{slug}.webp"; mark = IMG / f"{slug}.txt"
    try:
        m = json.loads(mark.read_text(encoding="utf-8"))
        if f.exists() and (TODAY - dt.date.fromisoformat(m["day"])).days < 14:
            return m["ph"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    d = api(action="query", titles=article, redirects=1, prop="pageimages", piprop="name|thumbnail", pilicense="free", pithumbsize=1000)
    pg = (d.get("query", {}).get("pages") or [{}])[0]
    fn = pg.get("pageimage"); th = pg.get("thumbnail", {}).get("source")
    if not fn or not th or not re.search(r"\.(jpe?g|png|webp)$", fn, re.I) or re.search(r"logo|wordmark", fn, re.I):
        return None
    m = api("commons.wikimedia.org", action="query", titles="File:" + fn, prop="imageinfo", iiprop="extmetadata|url", iiextmetadatafilter="Artist|LicenseShortName|LicenseUrl")
    pgs = m.get("query", {}).get("pages") or [{}]
    if not pgs[0].get("imageinfo"):
        return None
    ii = pgs[0]["imageinfo"][0]; em = ii.get("extmetadata", {})
    lic = em.get("LicenseShortName", {}).get("value", "")
    if not lic:
        return None
    if not f.exists() or not mark.exists() or fn not in mark.read_text(encoding="utf-8"):
        r = get(th)
        if not r:
            return None
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        if im.width < 400:
            return None
        im.thumbnail((900, 1100), Image.LANCZOS)
        im.save(f, "WEBP", quality=76, method=6)
    author = re.sub(r"\s+", " ", BeautifulSoup(em.get("Artist", {}).get("value", ""), "html.parser").get_text(" ", strip=True))[:70] or "Wikimedia Commons"
    ph = {"src": f"img/{slug}.webp", "by": author, "lic": lic, "page": ii.get("descriptionurl")}
    mark.write_text(json.dumps({"fn": fn, "ph": ph, "day": TODAY.isoformat()}, ensure_ascii=False), encoding="utf-8")
    return ph


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50] or "x"


def main():
    IMG.mkdir(parents=True, exist_ok=True)
    names = []
    for y in range(TODAY.year, (TODAY + dt.timedelta(days=HORIZON)).year + 1):
        names += category(f"{y} concert tours")
    names = sorted(set(names) - set(CFG["block_tours"]))
    cf = HERE / "cache.json"      # разобранные статьи: повторно читаем только изменившиеся
    cache = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else {}
    rev = {n: v for n, v in revisions(names).items() if has_future(v[1])}
    todo = [n for n, v in rev.items() if cache.get(n, {}).get("rev") != v[0]]
    print("статей", len(names), "с будущими датами в тексте", len(rev), "перечитать", len(todo), flush=True)
    tours = []
    for n, (rid, _) in rev.items():
        if n in todo:
            cache[n] = {"rev": rid, "t": tour(n)}
            cf.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.6)
        t = json.loads(json.dumps(cache[n]["t"]))
        if not t or t["artist"] in CFG["block_artists"]:
            continue
        for s in t["shows"]:
            s["d"] = [d for d in s["d"] if d >= TODAY.isoformat()]
        t["shows"] = [s for s in t["shows"] if s["d"]]
        if t["shows"]:
            tours.append(t)
    cf.write_text(json.dumps({n: cache[n] for n in rev}, ensure_ascii=False), encoding="utf-8")
    print("с будущими датами", len(tours))
    # один артист — одна карточка
    byart = {}
    for t in tours:
        byart.setdefault(t["aw"], []).append(t)
    arts = []
    vs = views(list(byart))
    for aw, ts in byart.items():
        v = vs.get(aw, 0)
        ts.sort(key=lambda t: min(s["d"][0] for s in t["shows"]))
        t = dict(ts[0]); t["shows"] = [s for x in ts for s in x["shows"]]
        reg = CFG["regions"]
        for s in t["shows"]:
            c = CFG["country_fix"].get(s["country"], s["country"]); s["country"] = c
            s["r"] = next((k for k, l in reg.items() if c in l), "other")
        near = sum(1 for s in t["shows"] if s["r"] in ("mideast", "asia", "cis", "europe"))
        t["views"] = v; t["score"] = v * (1.0 if near else 0.12) * (3 if any(s["r"] in ("cis", "mideast") for s in t["shows"]) else 1)
        arts.append(t)
    arts.sort(key=lambda t: (not any(s["country"] == "Kyrgyzstan" for s in t["shows"]), -t["score"]))
    L = labels([s["ct"] for t in arts[:LIMIT * 2] for s in t["shows"]] + [s["country"] for t in arts for s in t["shows"]])
    ev, seen = [], set()
    for t in arts:
        if len(ev) >= LIMIT:
            break
        slug = slugify(t["artist"])
        if slug in seen:
            continue
        ph = photo(t["aw"], slug)
        if not ph:
            print("нет свободного фото:", t["artist"])
            continue
        seen.add(slug)
        sh = []
        for s in sorted(t["shows"], key=lambda s: s["d"][0]):
            lc, lk = L.get(s["ct"] or "", {}), L.get(s["country"], {})
            sh.append({"c": CFG["ru"].get(s["city"]) or lc.get("ru") or s["city"], "ck": CFG["ky"].get(s["city"]) or lc.get("ky") or CFG["ru"].get(s["city"]) or lc.get("ru") or s["city"],
                       "k": CFG["ru"].get(s["country"]) or lk.get("ru") or s["country"], "kk": CFG["ky"].get(s["country"]) or lk.get("ky") or lk.get("ru") or s["country"],
                       "v": s["venue"], "d": s["d"], "r": s["r"]})
        ev.append({"id": slug, "a": t["artist"][0].upper() + t["artist"][1:], "t": "" if t["title"].startswith("List of") else t["title"].strip('"'), "type": "concert", "img": ph["src"], "by": ph["by"], "lic": ph["lic"], "pg": ph["page"],
                   "src": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(t["wiki"].replace(" ", "_")), "s": sh, "w": t["views"]})
    for m in CFG["manual"]:   # свои события из config.json: спорт, фестивали, местные концерты
        sh = [s for s in m["s"] if s["d"][-1] >= TODAY.isoformat()]
        if not sh:
            continue
        m = dict(m, s=sh)
        if m.get("photo_article"):
            ph = photo(m["photo_article"], m["id"])
            if not ph:
                print("нет свободного фото:", m["id"]); continue
            m.update(img=ph["src"], by=ph["by"], lic=ph["lic"], pg=ph["page"])
        m.pop("photo_article", None)
        ev.append(m)
    nconc = sum(e["type"] == "concert" for e in ev)
    if nconc < 10:      # источник сломался — старую афишу не портим
        print("мало событий:", nconc); sys.exit(1)
    (OUT / "events.json").write_text(json.dumps({"updated": TODAY.isoformat(), "events": ev}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    keep = {e["id"] for e in ev}
    for f in IMG.iterdir():
        if f.stem not in keep:
            f.unlink()
    print("в афише", len(ev))


if __name__ == "__main__":
    main()
