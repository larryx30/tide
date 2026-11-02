# -*- coding: utf-8 -*-
"""
釣點潮汐通：自動更新程式
- 潮汐：向中央氣象署開放資料平台抓「未來 1 個月潮汐預報」(F-A0021-001)
- 浪況：向 Open-Meteo 抓浪高與風力（當網頁打開時沒網路，就用這份備份）
結果寫成 tide_data.js、sea_data.js，放在本程式同一個資料夾，網頁打開就會讀到。
授權碼請填在同資料夾的 config.txt（CWA_API_KEY=你的授權碼）。
"""
import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "update_log.txt")

# 網頁使用的代號 -> 氣象署鄉鎮預報點代碼
TIDE_LOCATIONS = {
    "beigan": "09007020",    # 連江縣北竿鄉
    "magong": "10016010",    # 澎湖縣馬公市
    "baisha": "10016030",    # 澎湖縣白沙鄉
    "keelung": "10017010",   # 基隆市中正區（八斗子、碧砂）
    "gongliao": "65000260",  # 新北市貢寮區（澳底）
}
SEA_POINTS = {
    "beigan_om": (26.25, 120.03),
    "penghu_om": (23.58, 119.50),
    "north3_om": (25.62, 122.05),
    "kly_om": (25.20, 121.78),
    "aodi_om": (25.06, 121.94),
}


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_key():
    key = os.environ.get("CWA_API_KEY", "").strip()
    cfg = os.path.join(HERE, "config.txt")
    if not key and os.path.exists(cfg):
        with open(cfg, encoding="utf-8-sig") as f:
            for line in f:
                if line.strip().startswith("CWA_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key or key.startswith("請貼上"):
        return None
    return key


def get_json(url, timeout=60):
    """先用 requests（Anaconda 內建），SSL 失敗時改用 Windows 系統憑證再試一次。"""
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        if "SSL" not in repr(e) and "certificate" not in repr(e).lower():
            raise
        log(f"requests 憑證驗證失敗，改用 Windows 系統憑證重試：{e!r}")
    ctx = ssl.create_default_context()
    ctx.load_default_certs()
    with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def parse_location(loc):
    """把某一個預報點的資料轉成 {日期: [[H/L, 'HH:MM', 潮高cm], ...], 潮差}"""
    days = {}
    for d in walk(loc):
        if "Date" in d and "Time" in d and isinstance(d["Time"], list):
            evs = []
            for t in d["Time"]:
                tide = str(t.get("Tide", ""))
                kind = "H" if "滿" in tide else ("L" if "乾" in tide else None)
                dt = str(t.get("DateTime", ""))
                if not kind or len(dt) < 16:
                    continue
                hts = t.get("TideHeights") or {}
                h = hts.get("AboveChartDatum")
                if h in (None, ""):
                    h = hts.get("AboveLocalMSL", 0)
                try:
                    h = int(round(float(h)))
                except (TypeError, ValueError):
                    h = 0
                evs.append([kind, dt[11:16], h])
            evs.sort(key=lambda e: e[1])
            label = str(d.get("TideRange", "") or "")[:1] or "中"
            days[str(d["Date"])[:10]] = [evs, label]
    return days


def update_tides(key):
    url = ("https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-A0021-001"
           f"?Authorization={key}&format=JSON")
    data = get_json(url, timeout=120)
    if str(data.get("success")).lower() != "true":
        raise RuntimeError(f"氣象署回應失敗：{str(data)[:200]}")
    want = set(TIDE_LOCATIONS.values())
    found = {}
    for d in walk(data):
        lid = str(d.get("LocationId", ""))
        if lid in want and lid not in found:
            found[lid] = d
    tides = {}
    for name, lid in TIDE_LOCATIONS.items():
        if lid not in found:
            log(f"⚠ 氣象署資料裡找不到 {name}（{lid}）")
            continue
        tides[name] = parse_location(found[lid])
    if not tides or not any(tides.values()):
        raise RuntimeError("沒有解析到任何潮汐資料，資料格式可能變了")
    out = {"updated": f"{datetime.now():%Y-%m-%d %H:%M}", "source": "CWA F-A0021-001", "tides": tides}
    with open(os.path.join(HERE, "tide_data.js"), "w", encoding="utf-8") as f:
        f.write("window.TIDE_DATA = " + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n")
    log("潮汐更新完成：" + "、".join(f"{k} {len(v)} 天" for k, v in tides.items()))


def update_sea():
    keys = list(SEA_POINTS)
    lat = ",".join(str(SEA_POINTS[k][0]) for k in keys)
    lon = ",".join(str(SEA_POINTS[k][1]) for k in keys)
    q = f"latitude={lat}&longitude={lon}&timezone=Asia%2FTaipei&forecast_days=10"
    w = get_json("https://marine-api.open-meteo.com/v1/marine?" + q + "&hourly=wave_height,wave_period,wave_direction")
    m = get_json("https://api.open-meteo.com/v1/forecast?" + q + "&hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m&wind_speed_unit=kn")
    if isinstance(w, dict):
        w, m = [w], [m]
    r = lambda x: None if x is None else round(x)
    r1 = lambda x: None if x is None else round(x, 1)
    sea = {}
    for i, k in enumerate(keys):
        a, b = w[i]["hourly"], m[i]["hourly"]
        idx = {t: j for j, t in enumerate(b["time"])}
        rows = []
        for j in range(0, len(a["time"]), 3):
            t = a["time"][j]
            bj = idx.get(t)
            rows.append([t[:13], r1(a["wave_height"][j]), r(a["wave_period"][j]), r(a["wave_direction"][j]),
                         None if bj is None else r(b["wind_speed_10m"][bj]),
                         None if bj is None else r(b["wind_gusts_10m"][bj]),
                         None if bj is None else r(b["wind_direction_10m"][bj])])
        sea[k] = rows
    out = {"updated": f"{datetime.now():%Y-%m-%d %H:%M}", "sea": sea}
    with open(os.path.join(HERE, "sea_data.js"), "w", encoding="utf-8") as f:
        f.write("window.SEA_DATA = " + json.dumps(out, separators=(",", ":")) + ";\n")
    log("浪況備份更新完成")


def main():
    ok = True
    key = read_key()
    if key:
        try:
            update_tides(key)
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"❌ 潮汐更新失敗：{e!r}")
    else:
        ok = False
        log("❌ 尚未設定氣象署授權碼：請在 config.txt 填入 CWA_API_KEY=你的授權碼")
    try:
        update_sea()
    except Exception as e:  # noqa: BLE001
        log(f"⚠ 浪況備份更新失敗（不影響網頁即時抓取）：{e!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
