#!/usr/bin/env python3
"""
Larroudé CRM Dashboard — Auto Updater
Fetches Klaviyo data for L28D, L60D, L90D and updates index.html
"""

import os, json, re, time, requests
from datetime import date, timedelta
from collections import defaultdict

def _post(url, **kwargs):
    for attempt in range(6):
        try:
            r = requests.post(url, **kwargs)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 20))
                print(f"  rate-limited, waiting {wait}s…")
                time.sleep(wait)
                continue
            return r
        except requests.exceptions.ConnectionError:
            wait = 10 * (attempt + 1)
            print(f"  connection error, retrying in {wait}s…")
            time.sleep(wait)
    raise RuntimeError(f"Failed after retries: POST {url}")

def _get(url, **kwargs):
    for attempt in range(6):
        try:
            r = requests.get(url, **kwargs)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 20))
                print(f"  rate-limited, waiting {wait}s…")
                time.sleep(wait)
                continue
            return r
        except requests.exceptions.ConnectionError:
            wait = 10 * (attempt + 1)
            print(f"  connection error, retrying in {wait}s…")
            time.sleep(wait)
    raise RuntimeError(f"Failed after retries: GET {url}")

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

# ── Freshness guard ──────────────────────────────────────────────────────────
# If index.html already contains today's date (meaning the local refresh job
# already pushed a fresh version), skip this run so we don't overwrite it
# with the simplified template.  Pass --force to bypass.
import os as _os, pathlib as _pathlib, sys as _sys
_force = "--force" in _sys.argv
_idx = _pathlib.Path("index.html")
if not _force and _idx.exists():
    _content = _idx.read_text(errors="ignore")
    _today_str = TODAY.strftime("%b %d, %Y").upper()  # e.g. MAY 05, 2026
    if _today_str in _content:
        print(f"✓ index.html already contains {_today_str} — skipping update (local job already ran). Use --force to override.")
        raise SystemExit(0)
# ─────────────────────────────────────────────────────────────────────────────

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
        "statistics": [
            "recipients", "open_rate", "click_rate", "conversion_rate",
            "conversions", "bounce_rate", "unsubscribe_rate",
            "conversion_value", "revenue_per_recipient",
        ],
        "group_by": ["campaign_id", "campaign_message_id", "send_channel"],
    }}}
    r = _post(f"{BASE}/campaign-values-reports/", headers=HEADERS, json=payload)
    if not r.ok:
        raise RuntimeError(f"campaign-values-reports {r.status_code}: {r.text[:3000]}")
    body = r.json()
    results = body.get("data", {}).get("attributes", {}).get("results") or body.get("data") or []
    return {"data": results}

def campaign_series(start, end):
    payload = {"data": {"type": "campaign-series-report", "attributes": {
        "timeframe": {"start": iso(start), "end": iso_end(end)},
        "conversion_metric_id": PLACED_ORDER_ID,
        "statistics": [
            "recipients", "open_rate", "click_rate",
            "conversion_value", "revenue_per_recipient",
        ],
        "interval": "day",
    }}}
    r = _post(f"{BASE}/campaign-series-reports/", headers=HEADERS, json=payload)
    if r.status_code == 404:
        return {"data": []}  # endpoint indisponível nesta conta/revisão
    if not r.ok:
        raise RuntimeError(f"campaign-series-reports {r.status_code}: {r.text[:3000]}")
    return r.json()

def flow_report(start, end):
    payload = {"data": {"type": "flow-values-report", "attributes": {
        "timeframe": {"start": iso(start), "end": iso_end(end)},
        "conversion_metric_id": PLACED_ORDER_ID,
        "statistics": [
            "recipients", "open_rate", "click_rate", "conversion_rate",
            "conversions", "bounce_rate", "unsubscribe_rate",
            "conversion_value", "revenue_per_recipient",
        ],
        "group_by": ["flow_id", "flow_message_id", "send_channel"],
    }}}
    r = _post(f"{BASE}/flow-values-reports/", headers=HEADERS, json=payload)
    if not r.ok:
        raise RuntimeError(f"flow-values-reports {r.status_code}: {r.text[:3000]}")
    body = r.json()
    results = body.get("data", {}).get("attributes", {}).get("results") or body.get("data") or []
    return {"data": results}

def metric_agg_weekly(metric_id, start, end):
    payload = {"data": {"type": "metric-aggregate", "attributes": {
        "metric_id": metric_id,
        "measurements": ["count"],
        "interval": "week",
        "filter": f"greater-or-equal(datetime,{iso(start)}),less-than(datetime,{iso_end(end)})",
    }}}
    r = _post(f"{BASE}/metric-aggregates/", headers=HEADERS, json=payload)
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
        cid = g.get("campaign_id", "")
        cinfo = campaign_names.get(cid, {})
        name = cinfo.get("name", cid) if isinstance(cinfo, dict) else cinfo
        rc  = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        cv  = safe(st.get("conversion_value", 0), 2)
        rpr = safe(st.get("revenue_per_recipient", 0))
        rows.append({
            "name": name,
            "st":   cinfo.get("st", "") if isinstance(cinfo, dict) else "",
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
        rc  = int(st.get("recipients", 0) or 0)
        if not dt or rc == 0: continue
        daily[dt]["v"]     += safe(st.get("conversion_value", 0), 2)
        daily[dt]["r"]     += rc
        daily[dt]["o_sum"] += pct(st.get("open_rate")) * rc
        daily[dt]["c_sum"] += pct(st.get("click_rate")) * rc
        daily[dt]["cnt"]   += rc

    dates = sorted(daily.keys())
    d, v, r, o, c, p = [], [], [], [], [], []
    for dt in dates:
        rec = daily[dt]["r"]
        if rec < 1000: continue  # skip days dominated by test/tiny campaigns
        rev = daily[dt]["v"]
        d.append(dt)
        v.append(round(rev, 2))
        r.append(rec)
        o.append(round(daily[dt]["o_sum"]/rec, 1) if rec else 0)
        c.append(round(daily[dt]["c_sum"]/rec, 2) if rec else 0)
        p.append(round(rev/rec, 4) if rec else 0)
    return {"d":d,"v":v,"r":r,"o":o,"c":c,"p":p}

def get_campaign_names(start, end):
    # Pede send_time (data real de envio) E scheduled_at (fallback).
    # Sort por -scheduled_at (Klaviyo só permite sort por scheduled_at).
    # Paginação é tolerante: não para por campos nulos.
    params = {
        "filter": "equals(messages.channel,'email')",
        "fields[campaign]": "name,scheduled_at,send_time",
        "sort": "-scheduled_at",
    }
    info = {}
    url = f"{BASE}/campaigns/"
    start_s, end_s = str(start), str(end)
    pages = 0
    older_streak = 0  # quantas campanhas seguidas com data válida < start_s
    while url and pages < 60:
        r = _get(url, headers=HEADERS, params=params)
        r.raise_for_status()
        body = r.json()
        for item in body.get("data", []):
            attrs = item["attributes"]
            send_time = (attrs.get("send_time")    or "")[:10]
            sched     = (attrs.get("scheduled_at") or "")[:10]
            # Prioriza data real de envio; cai pro agendado se faltar
            st = send_time or sched
            if not st:
                # Campanha sem data alguma (rascunho, ou send_time ainda não populado).
                # Pula sem interromper paginação.
                continue
            if st < start_s:
                older_streak += 1
                continue
            older_streak = 0
            if st <= end_s:
                info[item["id"]] = {
                    "name": attrs.get("name", item["id"]),
                    "st":   st,
                }
        next_url = body.get("links", {}).get("next")
        # Só para se achou 25+ campanhas seguidas mais antigas que start_s
        # (margem de segurança contra send_time != scheduled_at).
        url = None if (not next_url or older_streak >= 25) else next_url
        params = {}
        pages += 1
    return info

def build_flow_rows(resp, flow_names):
    """All flow rows sorted desc by conversion_value (no truncation).
    Caller slices [:15] for display; totals use full list.
    Aggregates by flow_id so multi-message flows (e.g. Welcome Series) appear as one row."""
    acc = {}
    for item in (resp.get("data") or []):
        g  = item.get("groupings", {})
        st = item.get("statistics", {})
        fid = g.get("flow_id", "")
        rc  = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        if fid not in acc:
            acc[fid] = {"rc": 0, "cn": 0, "cv": 0.0,
                        "opr_s": 0.0, "ctr_s": 0.0, "cvr_s": 0.0,
                        "br_s": 0.0, "ur_s": 0.0}
        a = acc[fid]
        a["rc"]    += rc
        a["cn"]    += int(st.get("conversions", 0) or 0)
        a["cv"]    += safe(st.get("conversion_value", 0), 2)
        a["opr_s"] += pct(st.get("open_rate"))        * rc
        a["ctr_s"] += pct(st.get("click_rate"))        * rc
        a["cvr_s"] += pct(st.get("conversion_rate"))   * rc
        a["br_s"]  += pct(st.get("bounce_rate"))       * rc
        a["ur_s"]  += pct(st.get("unsubscribe_rate"))  * rc
    rows = []
    for fid, a in acc.items():
        rc = a["rc"]
        rows.append({
            "name":   flow_names.get(fid, fid),
            "rc":     rc,
            "opr":    round(a["opr_s"] / rc, 3) if rc else 0,
            "ctr":    round(a["ctr_s"] / rc, 3) if rc else 0,
            "cvr":    round(a["cvr_s"] / rc, 3) if rc else 0,
            "cn":     a["cn"],
            "cv":     round(a["cv"], 2),
            "rpr":    round(a["cv"] / rc, 4) if rc else 0,
            "br":     round(a["br_s"] / rc, 3) if rc else 0,
            "ur":     round(a["ur_s"] / rc, 3) if rc else 0,
            "status": "live",
        })
    rows.sort(key=lambda x: x["cv"], reverse=True)
    return rows

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
    r = _get(f"{BASE}/flows/", headers=HEADERS, params={"fields[flow]":"name"})
    r.raise_for_status()
    names = {}
    for item in r.json().get("data", []):
        names[item["id"]] = item["attributes"].get("name", item["id"])
    return names

def get_weekly_counts(metric_id, start, end):
    resp = metric_agg_weekly(metric_id, start, end)
    attrs = resp.get("data", {}).get("attributes", {})
    # New API: attrs["data"][0]["measurements"]["count"] is a list per week
    data_rows = attrs.get("data", [])
    if data_rows:
        return [int(v or 0) for v in data_rows[0].get("measurements", {}).get("count", [])]
    return []

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

        cid   = g.get("campaign_id", "")
        cinfo = cn.get(cid, {})
        rc    = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        all_c_rows.append({
            "name": cinfo.get("name", cid) if isinstance(cinfo, dict) else cinfo,
            "rc":   rc,
            "opr":  pct(st.get("open_rate")),
            "ctr":  pct(st.get("click_rate")),
            "cvr":  pct(st.get("conversion_rate")),
            "cn":   int(st.get("conversions", 0) or 0),
            "br":   pct(st.get("bounce_rate")),
            "ur":   pct(st.get("unsubscribe_rate")),
            "cv":   safe(st.get("conversion_value", 0), 2),
            "rpr":  safe(st.get("revenue_per_recipient", 0)),
        })

    all_f_rows = build_flow_rows(f_resp, fn)
    ct = build_camp_totals(all_c_rows)
    ft = build_flow_totals(all_f_rows)

    no_cs    = lambda rows: [r for r in rows if not re.search(r'\bcs\b| - cs ', r["name"], re.I)]
    nc_all   = no_cs(all_c_rows)
    nf_all   = no_cs(all_f_rows)
    trec_c   = sum(r["rc"] for r in nc_all) or 1
    trec_f   = sum(r["rc"] for r in nf_all) or 1
    return {
        "camp_rev":  ct["tcv"],  "flow_rev":  ft["tcv"],
        "camp_conv": ct["tc"],   "flow_conv": ft["tc"],
        "or":   round(sum(r["opr"]*r["rc"] for r in nc_all)/trec_c, 2),
        "cr":   round(sum(r["ctr"]*r["rc"] for r in nc_all)/trec_c, 2),
        "rpr":  ct["avg_rpr"],
        "camp_vol": ct["trec"], "flow_vol": ft["trec"],
        "flow_or":  round(sum(r["opr"]*r["rc"] for r in nf_all)/trec_f, 2),
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

        cid   = g.get("campaign_id", "")
        cinfo = cn.get(cid, {})
        rc    = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        all_c_rows.append({
            "name": cinfo.get("name", cid) if isinstance(cinfo, dict) else cinfo,
            "rc":   rc,
            "opr":  pct(st.get("open_rate")),
            "ctr":  pct(st.get("click_rate")),
            "cvr":  pct(st.get("conversion_rate")),
            "cn":   int(st.get("conversions", 0) or 0),
            "br":   pct(st.get("bounce_rate")),
            "ur":   pct(st.get("unsubscribe_rate")),
            "cv":   safe(st.get("conversion_value", 0), 2),
            "rpr":  safe(st.get("revenue_per_recipient", 0)),
        })

    all_f_rows = build_flow_rows(f_resp, fn)
    ct = build_camp_totals(all_c_rows)
    ft = build_flow_totals(all_f_rows)
    no_cs    = lambda rows: [r for r in rows if not re.search(r'\bcs\b| - cs ', r["name"], re.I)]
    nc_all   = no_cs(all_c_rows)
    nf_all   = no_cs(all_f_rows)
    trec_c   = sum(r["rc"] for r in nc_all) or 1
    trec_f   = sum(r["rc"] for r in nf_all) or 1
    return {
        "camp_rev":  ct["tcv"],  "flow_rev":  ft["tcv"],
        "camp_conv": ct["tc"],   "flow_conv": ft["tc"],
        "or":        round(sum(r["opr"]*r["rc"] for r in nc_all)/trec_c, 2),
        "cr":        round(sum(r["ctr"]*r["rc"] for r in nc_all)/trec_c, 2),
        "rpr":       ct["avg_rpr"],
        "camp_vol":  ct["trec"], "flow_vol":  ft["trec"],
        "flow_or":   round(sum(r["opr"]*r["rc"] for r in nf_all)/trec_f, 2),
        "flow_rpr":  ft["avg_rpr"],
    }

def build_overtime_from_rows(c_resp, cn, start, end):
    """Build daily overtime by grouping campaigns from c_resp by their send date.
    Used as fallback when campaign-series-reports endpoint is unavailable (404).
    Each day's revenue = sum of conversion_value of campaigns sent that day."""
    daily = defaultdict(lambda: {"v":0.0, "r":0, "o_sum":0.0, "c_sum":0.0})
    for item in (c_resp.get("data") or []):
        g  = item.get("groupings", {})
        st = item.get("statistics", {})
        channel = g.get("send_channel", "")
        if channel and channel != "email": continue
        cid   = g.get("campaign_id", "")
        cinfo = cn.get(cid, {})
        send_date = cinfo.get("st", "") if isinstance(cinfo, dict) else ""
        rc = int(st.get("recipients", 0) or 0)
        if rc == 0 or not send_date: continue
        daily[send_date]["v"]     += safe(st.get("conversion_value", 0), 2)
        daily[send_date]["r"]     += rc
        daily[send_date]["o_sum"] += pct(st.get("open_rate")) * rc
        daily[send_date]["c_sum"] += pct(st.get("click_rate")) * rc
    d, v, r, o, c, p = [], [], [], [], [], []
    for dt in sorted(daily.keys()):
        rec = daily[dt]["r"]
        if rec < 1000: continue
        rev = daily[dt]["v"]
        d.append(dt)
        v.append(round(rev, 2))
        r.append(rec)
        o.append(round(daily[dt]["o_sum"]/rec, 1))
        c.append(round(daily[dt]["c_sum"]/rec, 2))
        p.append(round(rev/rec, 4))
    return {"d":d,"v":v,"r":r,"o":o,"c":c,"p":p}

def build_period(days):
    s, e   = period_dates(days)
    c_resp = campaign_report(s, e)
    cs_resp = campaign_series(s, e)
    f_resp = flow_report(s, e)
    fn     = get_flow_names()
    cn     = get_campaign_names(s, e)

    all_c_rows = []
    for item in (c_resp.get("data") or []):
        g     = item.get("groupings", {})
        st    = item.get("statistics", {})
        cid   = g.get("campaign_id", "")
        cinfo = cn.get(cid, {})
        rc    = int(st.get("recipients", 0) or 0)
        if rc == 0: continue
        all_c_rows.append({
            "name": cinfo.get("name", cid) if isinstance(cinfo, dict) else cinfo,
            "st":   cinfo.get("st", "") if isinstance(cinfo, dict) else "",
            "rc":   rc,
            "opr":  pct(st.get("open_rate")),
            "ctr":  pct(st.get("click_rate")),
            "cvr":  pct(st.get("conversion_rate")),
            "cn":   int(st.get("conversions", 0) or 0),
            "br":   pct(st.get("bounce_rate")),
            "ur":   pct(st.get("unsubscribe_rate")),
            "cv":   safe(st.get("conversion_value", 0), 2),
            "rpr":  safe(st.get("revenue_per_recipient", 0)),
        })

    c_rows_top = sorted(all_c_rows, key=lambda x: x["cv"], reverse=True)[:20]
    all_f_rows = build_flow_rows(f_resp, fn)
    f_rows_top = all_f_rows[:20]
    ct = build_camp_totals(all_c_rows)

    no_cs  = lambda rows: [r for r in rows if not re.search(r'\bcs\b| - cs ', r["name"], re.I)]
    nc_all = no_cs(all_c_rows)
    trec_nc = sum(r["rc"] for r in nc_all) or 1
    ct["nc_actr"] = round(sum(r["ctr"]*r["rc"] for r in nc_all)/trec_nc, 2)
    ct["nc_aor"]  = round(sum(r["opr"]*r["rc"] for r in nc_all)/trec_nc, 2)

    overtime = build_overtime(cs_resp)
    if not overtime.get("d"):
        print(f"  series unavailable, building overtime from campaign send dates for {days}d…")
        overtime = build_overtime_from_rows(c_resp, cn, s, e)

    return {
        "camps": {
            "top":      c_rows_top,
            "overtime": overtime,
            "totals":   ct,
        },
        "flows": {
            "top":    f_rows_top,
            "totals": build_flow_totals(all_f_rows),
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
    print("Fetching L7D…")
    l7d = build_period(7)
    time.sleep(3)
    print("Fetching L28D…")
    l28d = build_period(28)
    time.sleep(3)
    print("Fetching L60D…")
    l60d = build_period(60)
    time.sleep(3)
    print("Fetching L90D…")
    l90d = build_period(90)
    time.sleep(3)

    print("Fetching PRIOR periods…")
    prior = {
        "l7d":  build_prior(7),
        "l28d": build_prior(28),
        "l60d": build_prior(60),
        "l90d": build_prior(90),
    }

    print("Fetching YoY periods…")
    yoy = {
        "l7d":  build_yoy(7),
        "l28d": build_yoy(28),
        "l60d": build_yoy(60),
        "l90d": build_yoy(90),
    }

    print("Fetching list health…")
    lgdata = build_lgdata()
    spam, flowvol = build_spam_flowvol()
    wk = build_wk_labels()

    data_js = {
        "l7d":  l7d,
        "l28d": l28d,
        "l60d": l60d,
        "l90d": l90d,
    }

    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Preserve 'segments' section from the previous DATA — this script does not
    # regenerate it, so without this merge the section disappears from the dashboard.
    m_prev = re.search(r'const DATA\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m_prev:
        try:
            prev_data = json.loads(m_prev.group(1))
            for period in ("l7d", "l28d", "l60d", "l90d"):
                seg = (prev_data.get(period) or {}).get("segments")
                if seg is not None:
                    data_js[period]["segments"] = seg
        except Exception as ex:
            print(f"  WARNING: could not preserve segments from prior DATA: {ex}")

    today_str = TODAY.strftime("%b %d %Y").upper()
    today_display = TODAY.strftime("%b %d, %Y").upper()
    # Update <span class="updated">...</span> timestamp
    html = re.sub(
        r'(<span class="updated">)[^<]*(</span>)',
        rf'\g<1>{today_display} — updated\g<2>',
        html,
    )
    # Legacy DATA: pattern (fallback)
    html = re.sub(r'DATA:\s*[A-Z]{3}\s+\d+\s+\d+', f'DATA: {today_str}', html)

    def replace_var(src, name, value):
        pattern = rf'const {name}\s*=.*?;'
        replacement = f'const {name} = {json.dumps(value, separators=(",",":"))};'
        new_src, n = re.subn(pattern, lambda _: replacement, src, count=1, flags=re.DOTALL)
        if n == 0:
            print(f"  WARNING: pattern for const {name} not found — value not substituted")
        return new_src

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
    import traceback, subprocess
    try:
        main()
        if os.path.exists("script_error.txt"):
            os.remove("script_error.txt")
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        with open("script_error.txt", "w") as f:
            f.write(err)
        subprocess.run(["git", "add", "script_error.txt"])
