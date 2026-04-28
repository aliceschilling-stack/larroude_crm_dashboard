#!/usr/bin/env python3
"""
Larroudé CRM Dashboard — Auto Updater
Fetches Klaviyo data for L28D, L60D, L90D and updates index.html
"""

import os, json, re, requests
from datetime import date, timedelta
from collections import defaultdict

API_KEY = os.environ["KLAVIYO_API_KEY"].strip()
BASE    = "https://a.klaviyo.com/api"
PLACED_ORDER_ID   = "RWb2qv"
SUBSCRIBED_ID     = "S2Baiy"
UNSUBSCRIBED_ID   = "YAyGPh"
SPAM_ID           = "YeYeGA"
RECEIVED_EMAIL_ID = "UPnuts"

HEADERS = {
    "Authorization": f"Klaviyo-API-Key {API_KEY}",
    "revision": "2024-10-15",
    "accept": "application/json",
    "content-type": "application/json",
}

TODAY = date.today()

def iso(d): return d.isoformat() + "T00:00:00Z"
def iso_end(d): return d.isoformat() + "T23:59:59Z"

def period_dates(days, offset=0):
    end   = TODAY - timedelta(days=1 + offset)
    start = end   - timedelta(days=days - 1)
    return start, end

def campaign_report(start, end):
    payload = {"data": {"type": "campaign-values-report", "attributes": {
        "timeframe": {"start": iso(start), "end": iso_end(end)},
        "conversion_metric_id": PLACED_ORDER_ID,
        "filter": "equals(messages.channel,'email')",
        "statistics": [
            "recipients", "open_rate", "click_rate", "conversion_rate",
            "conversions", "bounce_rate", "unsubscribe_rate",
        ],
        "value_statistics": ["conversion_value", "revenue_per_recipient"],
        "group_by": ["campaign_id", "campaign_message_id", "send_channel"],
        "sort": "-conversion_value",
    }}}
    r = requests.post(f"{BASE}/campaign-values-reports/", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

def campaign_series(start, end):
    payload = {"data": {"type": "campaign-series-report", "attributes": {
        "timeframe": {"start": iso(start), "end": iso_end(end)},
        "conversion_metric_id": PLACED_ORDER_ID,
        "filter": "equals(messages.channel,'email')",
        "statistics": [
            "recipients", "open_rate", "click_rate",
        ],
        "value_statistics": ["conversion_value", "revenue_per_recipient"],
        "interval": "day",
    }}}
    r = requests.post(f"{BASE}/campaign-series-reports/", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

def flow_report(start, end):
    payload = {"data": {"type": "flow-values-report", "attributes": {
        "timeframe": {"start": iso(start), "end": iso_end(end)},
        "conversion_metric_id": PLACED_ORDER_ID,
        "filter": "equals(messages.channel,'email')",
        "statistics": [
            "recipients", "open_rate", "click_rate", "conversion_rate",
            "conversions", "bounce_rate", "unsubscribe_rate",
        ],
        "value_statistics": ["conversion_value", "revenue_per_recipient"],
        "group_by": ["flow_id", "send_channel"],
        "sort": "-conversion_value",
    }}}
    r = requests.post(f"{BASE}/flow-values-reports/", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

def metric_agg_weekly(metric_id, start, end):
    payload = {"data": {"type": "metric-aggregate", "attributes": {
        "metric_id": metric_id,
        "measurements": ["count"],
        "interval": "week",
        "timeframe": {"start": iso(start), "end": iso_end(end)},
    }}}
    r = requests.post(f"{BASE}/metric-aggregates/", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

def pct(v):
    if v is None: return 0.0
    return round(float(v) * 100, 4)

def safe(v, decimals=4):
    if v is None: return 0.0
    return round(float(v), decimals)

def build_camp_top(resp, campaign_names):
    rows = []
    for item in (resp.get("data") or []):
        g  = item.get("groupings", {})
        st = item.get("statistics", {})
        vst= item.get("value_statistics", {})
        cid = g.get("campaign_id", "")
        name = campaign_names.get(cid, cid)
        rc  = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        cv  = safe(vst.get("conversion_value", 0), 2)
        rpr = safe(vst.get("revenue_per_recipient", 0))
        rows.append({
            "name": name,
            "st":   g.get("send_time", "")[:10] if g.get("send_time") else "",
            "rc":   rc,
            "opr":  pct(st.get("open_rate")),
            "ctr":  pct(st.get("click_rate")),
            "cvr":  pct(st.get("conversion_rate")),
            "cn":   int(st.get("conversions", 0) or 0),
            "br":   pct(st.get("bounce_rate")),
            "ur":   pct(st.get("unsubscribe_rate")),
            "cv":   cv,
            "rpr":  rpr,
        })
    rows.sort(key=lambda x: x["cv"], reverse=True)
    return rows[:15]

def build_camp_totals(rows):
    if not rows: return {"tcv":0,"tc":0,"trec":0,"aor":0,"actr":0,"acr":0,"avg_rpr":0,"nc":0}
    tcv  = round(sum(r["cv"]  for r in rows), 2)
    tc   = sum(r["cn"]  for r in rows)
    trec = sum(r["rc"]  for r in rows)
    aor  = round(sum(r["opr"]*r["rc"] for r in rows)/trec, 2) if trec else 0
    actr = round(sum(r["ctr"]*r["rc"] for r in rows)/trec, 2) if trec else 0
    acr  = round(tc/trec*100, 3) if trec else 0
    avg_rpr = round(tcv/trec, 4) if trec else 0
    return {"tcv":tcv,"tc":tc,"trec":trec,"aor":aor,"actr":actr,"acr":acr,"avg_rpr":avg_rpr,"nc":len(rows)}

def build_overtime(series_resp):
    daily = defaultdict(lambda: {"v":0,"r":0,"o_sum":0,"c_sum":0,"cnt":0})
    for item in (series_resp.get("data") or []):
        dt  = item.get("date", "")[:10]
        st  = item.get("statistics", {})
        vst = item.get("value_statistics", {})
        rc  = int(st.get("recipients", 0) or 0)
        if not dt or rc == 0: continue
        daily[dt]["v"]     += safe(vst.get("conversion_value", 0), 2)
        daily[dt]["r"]     += rc
        daily[dt]["o_sum"] += pct(st.get("open_rate")) * rc
        daily[dt]["c_sum"] += pct(st.get("click_rate")) * rc
        daily[dt]["cnt"]   += rc

    dates = sorted(daily.keys())
    d, v, r, o, c, p = [], [], [], [], [], []
    for dt in dates:
        rec = daily[dt]["r"]
        rev = daily[dt]["v"]
        d.append(dt)
        v.append(round(rev, 2))
        r.append(rec)
        o.append(round(daily[dt]["o_sum"]/rec, 1) if rec else 0)
        c.append(round(daily[dt]["c_sum"]/rec, 2) if rec else 0)
        p.append(round(rev/rec, 4) if rec else 0)
    return {"d":d,"v":v,"r":r,"o":o,"c":c,"p":p}

def get_campaign_names(start, end):
    params = {
        "filter": f"greater-or-equal(send_time,{iso(start)}),less-or-equal(send_time,{iso_end(end)})",
        "fields[campaign]": "name,send_time",
    }
    r = requests.get(f"{BASE}/campaigns/", headers=HEADERS, params=params)
    r.raise_for_status()
    names = {}
    for item in r.json().get("data", []):
        names[item["id"]] = item["attributes"].get("name", item["id"])
    return names

def build_flow_top(resp, flow_names):
    rows = []
    for item in (resp.get("data") or []):
        g   = item.get("groupings", {})
        st  = item.get("statistics", {})
        vst = item.get("value_statistics", {})
        fid = g.get("flow_id", "")
        name = flow_names.get(fid, fid)
        rc  = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        cv  = safe(vst.get("conversion_value", 0), 2)
        rpr = safe(vst.get("revenue_per_recipient", 0))
        rows.append({
            "name": name,
            "rc":   rc,
            "opr":  pct(st.get("open_rate")),
            "ctr":  pct(st.get("click_rate")),
            "cvr":  pct(st.get("conversion_rate")),
            "cn":   int(st.get("conversions", 0) or 0),
            "cv":   cv,
            "rpr":  rpr,
            "br":   pct(st.get("bounce_rate")),
            "ur":   pct(st.get("unsubscribe_rate")),
            "status": "live",
        })
    rows.sort(key=lambda x: x["cv"], reverse=True)
    return rows[:15]

def build_flow_totals(rows):
    if not rows: return {"tcv":0,"tc":0,"trec":0,"aor":0,"actr":0,"acr":0,"avg_rpr":0,"nf":0}
    tcv  = round(sum(r["cv"]  for r in rows), 2)
    tc   = sum(r["cn"]  for r in rows)
    trec = sum(r["rc"]  for r in rows)
    aor  = round(sum(r["opr"]*r["rc"] for r in rows)/trec, 2) if trec else 0
    actr = round(sum(r["ctr"]*r["rc"] for r in rows)/trec, 2) if trec else 0
    acr  = round(tc/trec*100, 3) if trec else 0
    avg_rpr = round(tcv/trec, 4) if trec else 0
    return {"tcv":tcv,"tc":tc,"trec":trec,"aor":aor,"actr":actr,"acr":acr,"avg_rpr":avg_rpr,"nf":len(rows)}

def get_flow_names():
    r = requests.get(f"{BASE}/flows/", headers=HEADERS, params={"fields[flow]":"name"})
    r.raise_for_status()
    names = {}
    for item in r.json().get("data", []):
        names[item["id"]] = item["attributes"].get("name", item["id"])
    return names

def get_weekly_counts(metric_id, start, end):
    resp = metric_agg_weekly(metric_id, start, end)
    vals = []
    for item in (resp.get("data", {}).get("attributes", {}).get("dates", []) or []):
        vals.append(int(item.get("values", [0])[0] or 0))
    return vals

def build_prior(days):
    s, e = period_dates(days, offset=days)
    c_resp = campaign_report(s, e)
    f_resp = flow_report(s, e)
    fn     = get_flow_names()
    cn     = get_campaign_names(s, e)

    all_c_rows = []
    for item in (c_resp.get("data") or []):
        g   = item.get("groupings", {})
        st  = item.get("statistics", {})
        vst = item.get("value_statistics", {})
        cid = g.get("campaign_id", "")
        rc  = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        all_c_rows.append({
            "name": cn.get(cid, cid),
            "rc":   rc,
            "opr":  pct(st.get("open_rate")),
            "ctr":  pct(st.get("click_rate")),
            "cvr":  pct(st.get("conversion_rate")),
            "cn":   int(st.get("conversions", 0) or 0),
            "br":   pct(st.get("bounce_rate")),
            "ur":   pct(st.get("unsubscribe_rate")),
            "cv":   safe(vst.get("conversion_value", 0), 2),
            "rpr":  safe(vst.get("revenue_per_recipient", 0)),
        })

    c_rows = sorted(all_c_rows, key=lambda x: x["cv"], reverse=True)[:15]
    f_rows = build_flow_top(f_resp, fn)
    ct = build_camp_totals(c_rows)
    ft = build_flow_totals(f_rows)

    no_cs   = lambda rows: [r for r in rows if not re.search(r'\bcs\b| - cs ', r["name"], re.I)]
    nc_all  = no_cs(all_c_rows)
    nf_rows = no_cs(f_rows)
    trec_c  = sum(r["rc"] for r in nc_all) or 1
    trec_f  = sum(r["rc"] for r in nf_rows) or 1
    return {
        "camp_rev":  ct["tcv"],  "flow_rev":  ft["tcv"],
        "camp_conv": ct["tc"],   "flow_conv": ft["tc"],
        "or":   round(sum(r["opr"]*r["rc"] for r in nc_all)/trec_c, 2),
        "cr":   round(sum(r["ctr"]*r["rc"] for r in nc_all)/trec_c, 2),
        "rpr":  ct["avg_rpr"],
        "camp_vol": ct["trec"], "flow_vol": ft["trec"],
        "flow_or":  round(sum(r["opr"]*r["rc"] for r in nf_rows)/trec_f, 2),
        "flow_rpr": ft["avg_rpr"],
    }

def build_yoy(days):
    s, e = period_dates(days)
    s_y  = s.replace(year=s.year-1)
    e_y  = e.replace(year=e.year-1)
    c_resp = campaign_report(s_y, e_y)
    f_resp = flow_report(s_y, e_y)
    fn     = get_flow_names()
    cn     = get_campaign_names(s_y, e_y)

    all_c_rows = []
    for item in (c_resp.get("data") or []):
        g   = item.get("groupings", {})
        st  = item.get("statistics", {})
        vst = item.get("value_statistics", {})
        cid = g.get("campaign_id", "")
        rc  = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        all_c_rows.append({
            "name": cn.get(cid, cid),
            "rc":   rc,
            "opr":  pct(st.get("open_rate")),
            "ctr":  pct(st.get("click_rate")),
            "cvr":  pct(st.get("conversion_rate")),
            "cn":   int(st.get("conversions", 0) or 0),
            "br":   pct(st.get("bounce_rate")),
            "ur":   pct(st.get("unsubscribe_rate")),
            "cv":   safe(vst.get("conversion_value", 0), 2),
            "rpr":  safe(vst.get("revenue_per_recipient", 0)),
        })

    c_rows = sorted(all_c_rows, key=lambda x: x["cv"], reverse=True)[:15]
    f_rows = build_flow_top(f_resp, fn)
    ct = build_camp_totals(c_rows)
    ft = build_flow_totals(f_rows)
    no_cs   = lambda rows: [r for r in rows if not re.search(r'\bcs\b| - cs ', r["name"], re.I)]
    nc_all  = no_cs(all_c_rows)
    nf_rows = no_cs(f_rows)
    trec_c  = sum(r["rc"] for r in nc_all) or 1
    trec_f  = sum(r["rc"] for r in nf_rows) or 1
    return {
        "camp_rev":  ct["tcv"],  "flow_rev":  ft["tcv"],
        "camp_conv": ct["tc"],   "flow_conv": ft["tc"],
        "or":        round(sum(r["opr"]*r["rc"] for r in nc_all)/trec_c, 2),
        "cr":        round(sum(r["ctr"]*r["rc"] for r in nc_all)/trec_c, 2),
        "rpr":       ct["avg_rpr"],
        "camp_vol":  ct["trec"], "flow_vol":  ft["trec"],
        "flow_or":   round(sum(r["opr"]*r["rc"] for r in nf_rows)/trec_f, 2),
        "flow_rpr":  ft["avg_rpr"],
    }

def build_period(days):
    s, e   = period_dates(days)
    c_resp = campaign_report(s, e)
    cs_resp = campaign_series(s, e)
    f_resp = flow_report(s, e)
    fn     = get_flow_names()
    cn     = get_campaign_names(s, e)

    all_c_rows = []
    for item in (c_resp.get("data") or []):
        g   = item.get("groupings", {})
        st  = item.get("statistics", {})
        vst = item.get("value_statistics", {})
        cid = g.get("campaign_id", "")
        rc  = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        all_c_rows.append({
            "name": cn.get(cid, cid),
            "st":   g.get("send_time", "")[:10] if g.get("send_time") else "",
            "rc":   rc,
            "opr":  pct(st.get("open_rate")),
            "ctr":  pct(st.get("click_rate")),
            "cvr":  pct(st.get("conversion_rate")),
            "cn":   int(st.get("conversions", 0) or 0),
            "br":   pct(st.get("bounce_rate")),
            "ur":   pct(st.get("unsubscribe_rate")),
            "cv":   safe(vst.get("conversion_value", 0), 2),
            "rpr":  safe(vst.get("revenue_per_recipient", 0)),
        })

    c_rows = sorted(all_c_rows, key=lambda x: x["cv"], reverse=True)[:15]
    f_rows = build_flow_top(f_resp, fn)
    ct = build_camp_totals(c_rows)

    no_cs  = lambda rows: [r for r in rows if not re.search(r'\bcs\b| - cs ', r["name"], re.I)]
    nc_all = no_cs(all_c_rows)
    trec_nc = sum(r["rc"] for r in nc_all) or 1
    ct["nc_actr"] = round(sum(r["ctr"]*r["rc"] for r in nc_all)/trec_nc, 2)
    ct["nc_aor"]  = round(sum(r["opr"]*r["rc"] for r in nc_all)/trec_nc, 2)

    return {
        "camps": {
            "top":      c_rows,
            "overtime": build_overtime(cs_resp),
            "totals":   ct,
        },
        "flows": {
            "top":    f_rows,
            "totals": build_flow_totals(f_rows),
        },
    }

def build_lgdata():
    # 14 weeks back from last Sunday
    end   = TODAY - timedelta(days=TODAY.weekday()+1)
    start = end - timedelta(weeks=14) + timedelta(days=1)
    sub_vals   = get_weekly_counts(SUBSCRIBED_ID,   start, end)
    unsub_vals = get_weekly_counts(UNSUBSCRIBED_ID, start, end)
    # Pad/trim to 14 weeks
    def normalise(lst):
        if len(lst) >= 14: return lst[-14:]
        return [0]*(14-len(lst)) + lst
    return {"sub": normalise(sub_vals), "unsub": normalise(unsub_vals)}

def build_spam_flowvol():
    end   = TODAY - timedelta(days=TODAY.weekday()+1)
    start = end - timedelta(weeks=14) + timedelta(days=1)
    spam_vals    = get_weekly_counts(SPAM_ID,           start, end)
    flowvol_vals = get_weekly_counts(RECEIVED_EMAIL_ID, start, end)
    def normalise(lst):
        if len(lst) >= 14: return lst[-14:]
        return [0]*(14-len(lst)) + lst
    return normalise(spam_vals), normalise(flowvol_vals)

def build_wk_labels():
    end   = TODAY - timedelta(days=TODAY.weekday()+1)
    start = end - timedelta(weeks=14) + timedelta(days=1)
    labels = []
    cur = start
    for _ in range(14):
        labels.append(f"{cur.month}/{cur.day}")
        cur += timedelta(weeks=1)
    return labels

def main():
    print("Fetching L28D…")
    l28d = build_period(28)
    print("Fetching L60D…")
    l60d = build_period(60)
    print("Fetching L90D…")
    l90d = build_period(90)

    print("Fetching PRIOR periods…")
    prior = {
        "l28d": build_prior(28),
        "l60d": build_prior(60),
        "l90d": build_prior(90),
    }

    print("Fetching YoY periods…")
    yoy = {
        "l28d": build_yoy(28),
        "l60d": build_yoy(60),
        "l90d": build_yoy(90),
    }

    print("Fetching list health…")
    lgdata = build_lgdata()
    spam, flowvol = build_spam_flowvol()
    wk = build_wk_labels()

    data_js = {
        "l28d": l28d,
        "l60d": l60d,
        "l90d": l90d,
    }

    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    today_str = TODAY.strftime("%b %d %Y").upper()
    html = re.sub(r'DATA:\s*[A-Z]{3}\s+\d+\s+\d+', f'DATA: {today_str}', html)

    def replace_var(src, name, value):
        pattern = rf'const {name}=.*?;'
        replacement = f'const {name}={json.dumps(value, separators=(",",":"))};'
        return re.sub(pattern, replacement, src, count=1, flags=re.DOTALL)

    html = replace_var(html, "DATA",    data_js)
    html = replace_var(html, "PRIOR",   prior)
    html = replace_var(html, "YOY",     yoy)
    html = replace_var(html, "LGDATA",  lgdata)
    html = replace_var(html, "SPAM",    spam)
    html = replace_var(html, "FLOWVOL", flowvol)
    html = replace_var(html, "WK",      wk)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done — dashboard updated for {today_str}")

if __name__ == "__main__":
    import traceback, sys
    print(f"API_KEY length: {len(API_KEY)}, starts: {API_KEY[:8] if API_KEY else 'EMPTY'}")
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
