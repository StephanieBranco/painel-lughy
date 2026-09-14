#!/usr/bin/env python3
"""
update_painel.py — Painel Comercial Lughy
Gera o index.html a partir dos dados do Pipedrive via REST API.
Executado pelo GitHub Actions toda segunda-feira.

Configuração:
  - PIPEDRIVE_TOKEN: token de API do Pipedrive (GitHub Secret)
"""

import os
import sys
import json
import requests
from datetime import date, timedelta

# ─── CONFIGURAÇÃO ────────────────────────────────────────────────
API_TOKEN = os.environ.get("PIPEDRIVE_TOKEN", "")
if not API_TOKEN:
    print("ERRO: variável PIPEDRIVE_TOKEN não definida.")
    sys.exit(1)

BASE_URL = "https://api.pipedrive.com/v1"
STEPHANIE_ID = 24155859
LUIS_ID      = 13236195

VALID_TYPES = {
    "pesquisa", "task", "primeira_mensagem_linkedin", "call",
    "ligacao_nao_atendida", "whatsapp", "email", "reuniao_pre_venda_",
    "meeting", "estimativa_", "elaborar_proposta_",
    "reuniao_de_apresentacao_de", "follow_up", "no_show"
}
MEETING_TYPES = {"meeting", "reuniao_pre_venda_", "reuniao_de_apresentacao_de"}

# Tabela de portes: (valor_mínimo, nome_porte, comissão)
PORTE_TABLE = [
    (500_000, "Top", 3000),
    (350_000, "A+",  2000),
    (180_000, "A-",  1000),
    ( 90_000, "B",    600),
    ( 20_000, "C",    300),
    (      1, "D",    150),
]


# ─── HELPERS ─────────────────────────────────────────────────────
def api_get(path, params=None):
    p = dict(params or {})
    p["api_token"] = API_TOKEN
    r = requests.get(f"{BASE_URL}{path}", params=p, timeout=30)
    r.raise_for_status()
    return r.json()


def get_all(path, base_params):
    items, start = [], 0
    while True:
        data = api_get(path, {**base_params, "limit": 500, "start": start})
        batch = data.get("data") or []
        items.extend(batch)
        more = (data.get("additional_data") or {}).get("pagination", {}).get("more_items_in_collection", False)
        if not more:
            break
        start += 500
    return items


# ─── DATAS ───────────────────────────────────────────────────────
def semana_passada():
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    return last_monday, last_sunday


def inicio_mes():
    t = date.today()
    return date(t.year, t.month, 1)


def q3_range():
    year = date.today().year
    return date(year, 7, 1), date(year, 9, 30)


def inicio_ano():
    t = date.today()
    return date(t.year, 1, 1)


# ─── PORTE / COMISSÃO ────────────────────────────────────────────
def get_porte(value):
    for threshold, porte, _ in PORTE_TABLE:
        if value >= threshold:
            return porte
    return "—"


def get_commission(value):
    for threshold, _, commission in PORTE_TABLE:
        if value >= threshold:
            return commission
    return 0


# ─── ATIVIDADES ──────────────────────────────────────────────────
def fetch_activities_full(user_id, start_date, end_date):
    """Returns (total, meetings, calls, daily_dict)
    daily_dict: {date_str: {total, calls, meetings}}
    """
    items = get_all("/activities", {
        "user_id":    user_id,
        "start_date": start_date.isoformat(),
        "end_date":   end_date.isoformat(),
        "done": 1,
    })
    valid = [a for a in items if (a.get("type") or "") in VALID_TYPES]

    daily = {}
    for a in valid:
        d = (a.get("due_date") or "")[:10]
        if not d:
            continue
        if d not in daily:
            daily[d] = {"total": 0, "calls": 0, "meetings": 0}
        daily[d]["total"] += 1
        if a.get("type") == "call":
            daily[d]["calls"] += 1
        if (a.get("type") or "") in MEETING_TYPES:
            daily[d]["meetings"] += 1

    total    = len(valid)
    meetings = sum(1 for a in valid if (a.get("type") or "") in MEETING_TYPES)
    calls    = sum(1 for a in valid if a.get("type") == "call")
    return total, meetings, calls, daily


# ─── PIPELINE ID ─────────────────────────────────────────────────
VENDAS_PIPELINE_NAME = "Vendas-Lughy"
_pipeline_id_cache = None

def get_vendas_pipeline_id():
    """Busca o ID do pipeline 'Vendas-Lughy' (com fallback None = sem filtro)."""
    global _pipeline_id_cache
    if _pipeline_id_cache is not None:
        return _pipeline_id_cache
    try:
        data = api_get("/pipelines")
        for p in (data.get("data") or []):
            if (p.get("name") or "").strip().lower() == VENDAS_PIPELINE_NAME.lower():
                _pipeline_id_cache = p["id"]
                return _pipeline_id_cache
    except Exception as e:
        print(f"Aviso: não foi possível buscar pipelines ({e}). Sem filtro de pipeline.")
    _pipeline_id_cache = -1  # sentinel: não encontrou
    return None


# ─── DEALS ───────────────────────────────────────────────────────
def fetch_won_deals_detail(user_id, start_date, end_date):
    """Returns list sorted by won_date asc, apenas pipeline Vendas-Lughy."""
    pipeline_id = get_vendas_pipeline_id()
    params = {"user_id": user_id, "status": "won"}
    if pipeline_id and pipeline_id != -1:
        params["pipeline_id"] = pipeline_id
    items = get_all("/deals", params)
    result = []
    for d in items:
        won = (d.get("won_time") or "")[:10]
        if not won or not (start_date.isoformat() <= won <= end_date.isoformat()):
            continue
        # segurança extra: ignorar deals de fora do pipeline se não foi filtrado na API
        if pipeline_id and pipeline_id != -1:
            if (d.get("pipeline_id") or d.get("pipeline", {}).get("id")) != pipeline_id:
                continue
        value = d.get("value") or 0
        result.append({
            "title":      d.get("title") or "—",
            "value":      value,
            "won_date":   won,
            "porte":      get_porte(value) if value > 0 else "R$0",
            "commission": get_commission(value) if value > 0 else 0,
        })
    result.sort(key=lambda x: x["won_date"])
    return result


def fetch_pipeline_stages():
    pipelines = api_get("/pipelines").get("data") or []
    pipeline_id = None
    for p in pipelines:
        name = (p.get("name") or "").lower()
        if "lughy" in name or "venda" in name:
            pipeline_id = p["id"]
            break
    if not pipeline_id:
        return {}
    stages = api_get("/stages", {"pipeline_id": pipeline_id}).get("data") or []
    return {(s.get("name") or "").lower(): s["id"] for s in stages}


def count_open_deals(user_id, stage_id):
    if not stage_id:
        return 0
    items = get_all("/deals", {"user_id": user_id, "status": "open", "stage_id": stage_id})
    return len(items)


# ─── FORMATAÇÃO ──────────────────────────────────────────────────
def brl_k(value):
    """R$356k ou R$89k"""
    if value >= 1000:
        return f"R${int(value/1000)}k"
    return f"R${int(value)}"


def brl_full(value):
    """R$ 1.780"""
    return f"R${int(value):,}".replace(",", ".")


def fmt_date_pt(iso):
    """2026-07-16 → 16/jul"""
    if not iso or len(iso) < 10:
        return "—"
    months = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
    m = int(iso[5:7])
    d = iso[8:10]
    return f"{d}/{months[m-1]}"


def fmt_mes_pt(d):
    """date → SET/2026"""
    months = ["JAN","FEV","MAR","ABR","MAI","JUN","JUL","AGO","SET","OUT","NOV","DEZ"]
    return f"{months[d.month-1]}/{d.year}"


def fmt_dd_mm(d):
    """date → 07/09"""
    return f"{d.day:02d}/{d.month:02d}"


# ─── HTML ────────────────────────────────────────────────────────
def deal_row_html(deal):
    won_str  = fmt_date_pt(deal["won_date"])
    val_str  = brl_k(deal["value"]) if deal["value"] > 0 else "R$0"
    comm_str = brl_full(deal["commission"]) if deal["commission"] > 0 else "—"
    porte    = deal["porte"]

    if deal["commission"] > 0:
        comm_html = f'<span style="font-family:\'Sora\',sans-serif;font-size:13px;font-weight:700;color:var(--gold)">+{comm_str}</span>'
    else:
        comm_html = '<span style="font-family:\'Sora\',sans-serif;font-size:13px;font-weight:700;color:var(--muted)">—</span>'

    return f'''          <div style="display:flex;align-items:center;justify-content:space-between;background:var(--s2);border-radius:var(--r-sm);padding:8px 12px">
            <span style="font-size:12.5px;color:var(--sub)">{deal["title"]}</span>
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:11px;color:var(--muted)">{won_str}</span>
              <span style="font-size:10.5px;background:var(--s3);border-radius:4px;padding:2px 7px;color:var(--muted)">{porte}</span>
              <span style="font-size:11.5px;color:var(--muted)">{val_str}</span>
              {comm_html}
            </div>
          </div>'''


def js_arr(lst):
    return json.dumps(lst)


def gerar_html(d):
    today    = d["today"]
    lmon     = d["lmon"]
    lsun     = d["lsun"]
    mes_ini  = d["mes_ini"]

    # semana labels
    sem_label = f"{fmt_dd_mm(lmon)}/{lmon.year} &#8211; {fmt_dd_mm(lsun)}/{lsun.year}"
    mes_label = fmt_mes_pt(today)
    mes_range = f"{fmt_dd_mm(mes_ini)}&#8211;{fmt_dd_mm(today)}/{today.month:02d}"
    gen_date  = today.strftime("%d/%m/%Y")

    # comissão Q3 deals html
    s_deals_html = "\n".join(deal_row_html(dl) for dl in d["s_q3_deals"]) if d["s_q3_deals"] else \
        '          <div style="margin-top:14px;background:var(--s2);border-radius:var(--r-sm);padding:12px 14px;font-size:11.5px;color:var(--muted)">Nenhum deal ganho no Q3/2026.</div>'
    l_deals_html = "\n".join(deal_row_html(dl) for dl in d["l_q3_deals"]) if d["l_q3_deals"] else \
        '          <div style="margin-top:14px;background:var(--s2);border-radius:var(--r-sm);padding:12px 14px;font-size:11.5px;color:var(--muted)">Nenhum deal ganho no Q3/2026.</div>'

    # bar chart meta
    s_meta_w = min(100, round(d["s_conv"] / 20 * 100, 1)) if d["s_conv"] else 0
    l_meta_w = min(100, round(d["l_conv"] / 20 * 100, 1)) if d["l_conv"] else 0

    s_conv_str = f"{d['s_conv']}%" if d["s_conv"] else "—"
    l_conv_str = f"{d['l_conv']}%" if d["l_conv"] else "—"

    CHART_H = 160  # altura total da área das barras (px)

    def funil_card(person_label, color_var, funil, meta_w):
        ref   = funil["ref"]
        prop  = funil["prop"]
        ganho = funil["ganho"]
        conv_rp = funil["conv_rp"]
        conv_pg = funil["conv_pg"]
        conv_pg_str = f"{conv_pg}%" if conv_pg else "—"
        max_v = max(ref, prop, ganho, 1)
        def bh(v):
            return max(8, round(v / max_v * CHART_H))
        meta_color = "var(--gold)" if conv_pg >= 20 else "var(--danger)"
        return f"""    <div class="person-card">
      <div class="person-bar" style="background:{color_var}"></div>
      <div class="person-body">
        <div class="person-name">
          <div class="person-dot" style="background:{color_var}"></div>{person_label}
          <span style="margin-left:auto;font-family:'Sora',sans-serif;font-size:22px;font-weight:800;color:{color_var}">{conv_pg_str}</span>
          <span style="font-size:11px;color:var(--muted);font-weight:400;margin-left:4px">Prop&rarr;Ganho</span>
        </div>

        <!-- Funil estilo Pipedrive Insights -->
        <div style="display:flex;align-items:flex-end;height:{CHART_H + 32}px;padding-bottom:30px;gap:0;margin:8px 0">

          <!-- Barra Refinamento -->
          <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;position:relative">
            <div style="position:absolute;top:0;font-size:13px;font-weight:700;color:var(--text);line-height:1">{ref}</div>
            <div style="width:88%;height:{bh(ref)}px;background:#F5A623;border-radius:4px 4px 0 0"></div>
            <div style="position:absolute;bottom:-22px;font-size:9px;color:var(--muted);text-align:center;line-height:1.3">Reuni&otilde;es de<br>Refinamento</div>
          </div>

          <!-- Badge ref→prop -->
          <div style="flex:0 0 52px;display:flex;align-items:flex-end;justify-content:center;height:100%;padding-bottom:2px">
            <div style="background:rgba(255,255,255,0.07);border-radius:10px;padding:3px 9px;font-size:11px;color:var(--sub);font-weight:600">{conv_rp}%</div>
          </div>

          <!-- Barra Proposta -->
          <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;position:relative">
            <div style="position:absolute;top:0;font-size:13px;font-weight:700;color:var(--text);line-height:1">{prop}</div>
            <div style="width:88%;height:{bh(prop)}px;background:#F5A623;border-radius:4px 4px 0 0"></div>
            <div style="position:absolute;bottom:-22px;font-size:9px;color:var(--muted);text-align:center">Proposta</div>
          </div>

          <!-- Badge prop→ganho -->
          <div style="flex:0 0 52px;display:flex;align-items:flex-end;justify-content:center;height:100%;padding-bottom:2px">
            <div style="background:rgba(255,255,255,0.07);border-radius:10px;padding:3px 9px;font-size:11px;color:var(--sub);font-weight:600">{conv_pg_str}</div>
          </div>

          <!-- Barra Ganho -->
          <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;position:relative">
            <div style="position:absolute;top:0;font-size:13px;font-weight:700;color:var(--text);line-height:1">{ganho}</div>
            <div style="width:88%;height:{bh(ganho)}px;background:#2ECC9A;border-radius:4px 4px 0 0"></div>
            <div style="position:absolute;bottom:-22px;font-size:9px;color:var(--muted);text-align:center">Ganho</div>
          </div>
        </div>

        <!-- Meta -->
        <div style="margin-top:8px;background:var(--gold-lo);border:1px solid rgba(240,192,64,.2);border-radius:var(--r-sm);padding:8px 12px;font-size:11px;color:var(--sub);display:flex;align-items:center;justify-content:space-between">
          <span>Meta 20%</span>
          <div style="width:120px;background:var(--s2);border-radius:4px;height:8px;overflow:hidden;position:relative">
            <div style="position:absolute;left:66.7%;top:0;bottom:0;width:1.5px;background:var(--gold)"></div>
            <div style="height:100%;background:{meta_color};border-radius:4px;width:{meta_w}%;opacity:0.7"></div>
          </div>
          <span style="font-weight:600;color:{meta_color}">{conv_pg_str}</span>
        </div>
      </div>
    </div>"""

    # day labels JS array
    day_labels_js = js_arr(d["day_labels"])

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel Comercial Lughy</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=DM+Sans:wght@400;500;600&display=swap">
<style>
:root {{
  --bg:       #0d1021;
  --s1:       #141829;
  --s2:       #1b2038;
  --s3:       #222642;
  --border:   #252a45;
  --text:     #e6eaf5;
  --sub:      #9aa0c0;
  --muted:    #5e658a;
  --steph:    #4A90D9;
  --steph-lo: rgba(74,144,217,.12);
  --luis:     #2ECC9A;
  --luis-lo:  rgba(46,204,154,.12);
  --danger:   #e25c70;
  --danger-lo:rgba(226,92,112,.12);
  --gold:     #f0c040;
  --gold-lo:  rgba(240,192,64,.10);
  --r: 10px;
  --r-sm: 6px;
}}
@media (prefers-color-scheme: light) {{
  :root:not([data-theme="dark"]) {{
    --bg:#f0f2f9;--s1:#ffffff;--s2:#f5f6fc;--s3:#eaecf6;
    --border:#d4d8f0;--text:#0d1021;--sub:#4a5080;--muted:#8890b8;
  }}
}}
:root[data-theme="light"] {{
  --bg:#f0f2f9;--s1:#ffffff;--s2:#f5f6fc;--s3:#eaecf6;
  --border:#d4d8f0;--text:#0d1021;--sub:#4a5080;--muted:#8890b8;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',system-ui,sans-serif;font-size:14px;line-height:1.5}}
canvas{{display:block}}
.hd{{background:var(--s1);border-bottom:1px solid var(--border);padding:20px 32px;display:flex;align-items:center;justify-content:space-between;gap:16px}}
.hd-left{{display:flex;align-items:center;gap:14px}}
.hd-logo{{width:40px;height:40px;background:linear-gradient(135deg,var(--steph),#6ea8e8);border-radius:10px;display:flex;align-items:center;justify-content:center;font-family:'Sora',sans-serif;font-weight:800;font-size:17px;color:#fff;flex-shrink:0}}
.hd-title{{font-family:'Sora',sans-serif;font-weight:700;font-size:17px;letter-spacing:-0.2px}}
.hd-title span{{color:var(--steph)}}
.hd-sub{{color:var(--sub);font-size:12.5px;margin-top:1px}}
.hd-right{{text-align:right}}
.hd-period{{font-family:'Sora',sans-serif;font-weight:600;font-size:13px}}
.hd-gen{{color:var(--muted);font-size:11.5px;margin-top:2px}}
.page{{max-width:1180px;margin:0 auto;padding:28px 24px 56px}}
.sec-head{{display:flex;align-items:center;gap:10px;margin:32px 0 14px}}
.sec-num{{font-family:'Sora',sans-serif;font-size:10px;font-weight:700;color:var(--muted);letter-spacing:1.5px;text-transform:uppercase}}
.sec-title{{font-family:'Sora',sans-serif;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--sub)}}
.sec-rule{{flex:1;height:1px;background:var(--border)}}
.card{{background:var(--s1);border:1px solid var(--border);border-radius:var(--r);padding:20px}}
.kpi-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:4px}}
.person-card{{background:var(--s1);border:1px solid var(--border);border-radius:var(--r);overflow:hidden}}
.person-bar{{height:4px}}
.person-bar.s{{background:linear-gradient(90deg,var(--steph),#6ea8e8)}}
.person-bar.l{{background:linear-gradient(90deg,var(--luis),#64debb)}}
.person-body{{padding:16px 18px 18px}}
.person-name{{font-family:'Sora',sans-serif;font-weight:700;font-size:14px;display:flex;align-items:center;gap:8px;margin-bottom:14px}}
.person-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.kpi-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}
.kpi-tile{{background:var(--s2);border-radius:var(--r-sm);padding:12px 10px;text-align:center}}
.kpi-n{{font-family:'Sora',sans-serif;font-size:32px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums}}
.kpi-lbl{{font-size:10.5px;color:var(--muted);margin-top:5px;line-height:1.3}}
.fv-wrap{{display:flex;align-items:flex-end;gap:0;height:120px;padding-bottom:24px;position:relative}}
.fv-stage{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;position:relative}}
.fv-bar{{width:100%;border-radius:4px 4px 0 0;transition:height .3s}}
.fv-count{{font-family:'Sora',sans-serif;font-size:13px;font-weight:800;line-height:1;margin-bottom:5px;font-variant-numeric:tabular-nums}}
.fv-lbl{{position:absolute;bottom:-20px;left:50%;transform:translateX(-50%);font-size:9.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;white-space:nowrap;font-weight:600}}
.fv-arrow{{width:36px;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;padding-bottom:24px;gap:2px;flex-shrink:0}}
.fv-pct{{font-family:'Sora',sans-serif;font-size:10px;font-weight:700;white-space:nowrap}}
.fv-chevron{{font-size:16px;color:var(--border);line-height:1}}
.chart-section{{display:grid;grid-template-columns:1fr 220px;gap:14px;align-items:start}}
.chart-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}}
.chart-title{{font-weight:600;font-size:13px}}
.legend{{display:flex;gap:14px}}
.leg-item{{display:flex;align-items:center;gap:5px;font-size:11.5px;color:var(--sub)}}
.leg-dot{{width:8px;height:8px;border-radius:2px;flex-shrink:0}}
.monthly{{background:var(--s1);border:1px solid var(--border);border-radius:var(--r);padding:16px}}
.monthly-head{{font-size:10.5px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600;margin-bottom:14px}}
.monthly-row{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}}
.monthly-name{{font-size:12.5px;color:var(--sub)}}
.monthly-val{{font-family:'Sora',sans-serif;font-size:26px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums}}
.monthly-hr{{border:none;border-top:1px solid var(--border);margin:12px 0}}
.monthly-total{{display:flex;align-items:center;justify-content:space-between}}
.monthly-total-lbl{{font-size:11px;color:var(--muted)}}
.monthly-total-val{{font-family:'Sora',sans-serif;font-size:20px;font-weight:700;color:var(--gold);font-variant-numeric:tabular-nums}}
.footer{{margin-top:48px;border-top:1px solid var(--border);padding-top:16px;display:flex;justify-content:space-between;align-items:flex-end;gap:24px}}
.footer-left{{font-size:11.5px;color:var(--muted);line-height:1.7}}
.footer-right{{font-size:11px;color:var(--muted);text-align:right;flex-shrink:0}}
</style>

<!-- HEADER -->
<div class="hd">
  <div class="hd-left">
    <div class="hd-logo">L</div>
    <div>
      <div class="hd-title">Painel Comercial <span>Lughy</span></div>
      <div class="hd-sub">Time comercial &middot; Stephanie &amp; Luis</div>
    </div>
  </div>
  <div class="hd-right">
    <div class="hd-period">Semana {sem_label}</div>
    <div class="hd-gen">Gerado em {gen_date} &middot; M&ecirc;s atual: {mes_range}</div>
  </div>
</div>

<div class="page">

  <!-- KPI CARDS – Semana passada -->
  <div class="sec-head" style="margin-top:0">
    <span class="sec-num">Semana passada &middot; {fmt_dd_mm(lmon)}/{lmon.year} &#8211; {fmt_dd_mm(lsun)}/{lsun.year}</span>
    <span class="sec-rule"></span>
  </div>
  <div class="kpi-grid">
    <div class="person-card">
      <div class="person-bar s"></div>
      <div class="person-body">
        <div class="person-name">
          <div class="person-dot" style="background:var(--steph)"></div>Stephanie
        </div>
        <div class="kpi-row">
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--steph)">{d['s_w_calls']}</div>
            <div class="kpi-lbl">Liga&ccedil;&otilde;es</div>
          </div>
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--steph)">{d['s_w_meet']}</div>
            <div class="kpi-lbl">Reuni&otilde;es</div>
          </div>
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--steph)">{d['s_w_total']}</div>
            <div class="kpi-lbl">Atividades conclu&iacute;das</div>
          </div>
        </div>
      </div>
    </div>
    <div class="person-card">
      <div class="person-bar l"></div>
      <div class="person-body">
        <div class="person-name">
          <div class="person-dot" style="background:var(--luis)"></div>Luis
        </div>
        <div class="kpi-row">
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--luis)">{d['l_w_calls']}</div>
            <div class="kpi-lbl">Liga&ccedil;&otilde;es</div>
          </div>
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--luis)">{d['l_w_meet']}</div>
            <div class="kpi-lbl">Reuni&otilde;es</div>
          </div>
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--luis)">{d['l_w_total']}</div>
            <div class="kpi-lbl">Atividades conclu&iacute;das</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- SECTION 01: FUNIL DE CONVERSÃO -->
  <div class="sec-head">
    <span class="sec-num">01</span>
    <span class="sec-title">Funil de Convers&atilde;o &mdash; Proposta &rarr; Ganho &middot; 2026</span>
    <span class="sec-rule"></span>
  </div>
  <div style="font-size:11px;color:var(--muted);margin-bottom:12px">Pipeline Vendas-Lughy &middot; apenas 2026 &middot; contagens inseridas manualmente &middot; meta: 20%</div>
  <div class="kpi-grid">
{funil_card("Stephanie", "var(--steph)", d["s_funil"], s_meta_w)}
{funil_card("Luis", "var(--luis)", d["l_funil"], l_meta_w)}
  </div>

  <!-- SECTION 02: COMISSÃO TRIMESTRAL -->
  <div class="sec-head">
    <span class="sec-num">02</span>
    <span class="sec-title">Comiss&atilde;o Trimestral &mdash; Q3/2026 &middot; Jul&ndash;Set</span>
    <span class="sec-rule"></span>
  </div>
  <div class="kpi-grid">
    <div class="person-card">
      <div class="person-bar s"></div>
      <div class="person-body">
        <div class="person-name">
          <div class="person-dot" style="background:var(--steph)"></div>Stephanie
        </div>
        <div class="kpi-row" style="margin-bottom:18px">
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--steph)">{d['s_q3_count']}</div>
            <div class="kpi-lbl">Ganhos Q3</div>
          </div>
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--steph);font-size:22px">{brl_k(d['s_q3_value'])}</div>
            <div class="kpi-lbl">Valor total</div>
          </div>
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--gold);font-size:22px">{brl_full(d['s_q3_commission'])}</div>
            <div class="kpi-lbl">Comiss&atilde;o</div>
          </div>
        </div>
        <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600;margin-bottom:8px">Breakdown por deal</div>
        <div style="display:flex;flex-direction:column;gap:6px">
{s_deals_html}
        </div>
      </div>
    </div>
    <div class="person-card">
      <div class="person-bar l"></div>
      <div class="person-body">
        <div class="person-name">
          <div class="person-dot" style="background:var(--luis)"></div>Luis
        </div>
        <div class="kpi-row" style="margin-bottom:18px">
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--luis)">{d['l_q3_count']}</div>
            <div class="kpi-lbl">Ganhos Q3</div>
          </div>
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--luis);font-size:22px">{brl_k(d['l_q3_value'])}</div>
            <div class="kpi-lbl">Valor total</div>
          </div>
          <div class="kpi-tile">
            <div class="kpi-n" style="color:var(--gold);font-size:22px">{brl_full(d['l_q3_commission'])}</div>
            <div class="kpi-lbl">Comiss&atilde;o</div>
          </div>
        </div>
        <div style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600;margin-bottom:8px">Breakdown por deal</div>
        <div style="display:flex;flex-direction:column;gap:6px">
{l_deals_html}
        </div>
        <div style="margin-top:14px;background:var(--s2);border-radius:var(--r-sm);padding:12px 14px;font-size:11.5px;color:var(--muted);line-height:1.5">
          Q3 em andamento &#8212; <strong style="color:var(--sub)">Set 2026</strong>. Novos fechamentos aumentam a comiss&atilde;o.
        </div>
      </div>
    </div>
  </div>

  <!-- Tabela de Portes -->
  <div class="card" style="margin-top:14px;padding:16px 24px">
    <div style="font-size:10.5px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600;margin-bottom:12px">Tabela de portes &middot; comiss&atilde;o por deal</div>
    <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px">
      <div style="background:var(--s2);border-radius:var(--r-sm);padding:10px;text-align:center">
        <div style="font-family:'Sora',sans-serif;font-size:14px;font-weight:700;color:var(--sub)">D</div>
        <div style="font-size:10px;color:var(--muted);margin:3px 0">&ge; R$1</div>
        <div style="font-family:'Sora',sans-serif;font-size:13px;font-weight:700;color:var(--gold)">R$150</div>
      </div>
      <div style="background:var(--s2);border-radius:var(--r-sm);padding:10px;text-align:center">
        <div style="font-family:'Sora',sans-serif;font-size:14px;font-weight:700;color:var(--sub)">C</div>
        <div style="font-size:10px;color:var(--muted);margin:3px 0">&ge; R$20k</div>
        <div style="font-family:'Sora',sans-serif;font-size:13px;font-weight:700;color:var(--gold)">R$300</div>
      </div>
      <div style="background:var(--s2);border-radius:var(--r-sm);padding:10px;text-align:center">
        <div style="font-family:'Sora',sans-serif;font-size:14px;font-weight:700;color:var(--sub)">B</div>
        <div style="font-size:10px;color:var(--muted);margin:3px 0">&ge; R$90k</div>
        <div style="font-family:'Sora',sans-serif;font-size:13px;font-weight:700;color:var(--gold)">R$600</div>
      </div>
      <div style="background:var(--s2);border-radius:var(--r-sm);padding:10px;text-align:center">
        <div style="font-family:'Sora',sans-serif;font-size:14px;font-weight:700;color:var(--sub)">A-</div>
        <div style="font-size:10px;color:var(--muted);margin:3px 0">&ge; R$180k</div>
        <div style="font-family:'Sora',sans-serif;font-size:13px;font-weight:700;color:var(--gold)">R$1.000</div>
      </div>
      <div style="background:var(--s2);border-radius:var(--r-sm);padding:10px;text-align:center">
        <div style="font-family:'Sora',sans-serif;font-size:14px;font-weight:700;color:var(--sub)">A+</div>
        <div style="font-size:10px;color:var(--muted);margin:3px 0">&ge; R$350k</div>
        <div style="font-family:'Sora',sans-serif;font-size:13px;font-weight:700;color:var(--gold)">R$2.000</div>
      </div>
      <div style="background:var(--s2);border-radius:var(--r-sm);padding:10px;text-align:center">
        <div style="font-family:'Sora',sans-serif;font-size:14px;font-weight:700;color:var(--gold)">Top</div>
        <div style="font-size:10px;color:var(--muted);margin:3px 0">&ge; R$500k</div>
        <div style="font-family:'Sora',sans-serif;font-size:13px;font-weight:700;color:var(--gold)">R$3.000</div>
      </div>
    </div>
    <div style="margin-top:10px;font-size:11px;color:var(--muted)">Deals com valor R$0 n&atilde;o contabilizados &middot; filtro Q3/2026 = 01/07&ndash;30/09 &middot; pipeline Vendas&ndash;Lughy</div>
  </div>

  <!-- SECTION 03: LIGAÇÕES -->
  <div class="sec-head">
    <span class="sec-num">03</span>
    <span class="sec-title">Liga&ccedil;&otilde;es</span>
    <span class="sec-rule"></span>
  </div>
  <div class="chart-section">
    <div class="card">
      <div class="chart-header">
        <span class="chart-title">Por dia &mdash; semana passada ({fmt_dd_mm(lmon)}&ndash;{fmt_dd_mm(lsun)}/set)</span>
        <div class="legend">
          <div class="leg-item"><div class="leg-dot" style="background:var(--steph)"></div> Stephanie</div>
          <div class="leg-item"><div class="leg-dot" style="background:var(--luis)"></div> Luis</div>
        </div>
      </div>
      <canvas id="cLig" height="160"></canvas>
    </div>
    <div class="monthly">
      <div class="monthly-head">M&ecirc;s atual &middot; {mes_label} {mes_range}</div>
      <div class="monthly-row">
        <span class="monthly-name">Stephanie</span>
        <span class="monthly-val" style="color:var(--steph)">{d['s_m_calls']}</span>
      </div>
      <div class="monthly-row">
        <span class="monthly-name">Luis</span>
        <span class="monthly-val" style="color:var(--luis)">{d['l_m_calls']}</span>
      </div>
      <hr class="monthly-hr">
      <div class="monthly-total">
        <span class="monthly-total-lbl">Total m&ecirc;s</span>
        <span class="monthly-total-val">{d['s_m_calls'] + d['l_m_calls']}</span>
      </div>
    </div>
  </div>

  <!-- SECTION 04: ATIVIDADES -->
  <div class="sec-head">
    <span class="sec-num">04</span>
    <span class="sec-title">Atividades Conclu&iacute;das</span>
    <span class="sec-rule"></span>
  </div>
  <div class="chart-section">
    <div class="card">
      <div class="chart-header">
        <span class="chart-title">Por dia &mdash; semana passada ({fmt_dd_mm(lmon)}&ndash;{fmt_dd_mm(lsun)}/set)</span>
        <div class="legend">
          <div class="leg-item"><div class="leg-dot" style="background:var(--steph)"></div> Stephanie</div>
          <div class="leg-item"><div class="leg-dot" style="background:var(--luis)"></div> Luis</div>
        </div>
      </div>
      <canvas id="cAtiv" height="160"></canvas>
    </div>
    <div class="monthly">
      <div class="monthly-head">M&ecirc;s atual &middot; {mes_label} {mes_range}</div>
      <div class="monthly-row">
        <span class="monthly-name">Stephanie</span>
        <span class="monthly-val" style="color:var(--steph)">{d['s_m_total']}</span>
      </div>
      <div class="monthly-row">
        <span class="monthly-name">Luis</span>
        <span class="monthly-val" style="color:var(--luis)">{d['l_m_total']}</span>
      </div>
      <hr class="monthly-hr">
      <div class="monthly-total">
        <span class="monthly-total-lbl">Total m&ecirc;s</span>
        <span class="monthly-total-val">{d['s_m_total'] + d['l_m_total']}</span>
      </div>
    </div>
  </div>

  <!-- SECTION 05: REUNIÕES -->
  <div class="sec-head">
    <span class="sec-num">05</span>
    <span class="sec-title">Reuni&otilde;es</span>
    <span class="sec-rule"></span>
  </div>
  <div class="chart-section">
    <div class="card">
      <div class="chart-header">
        <span class="chart-title">Por dia &mdash; semana passada ({fmt_dd_mm(lmon)}&ndash;{fmt_dd_mm(lsun)}/set)</span>
        <div class="legend">
          <div class="leg-item"><div class="leg-dot" style="background:var(--steph)"></div> Stephanie</div>
          <div class="leg-item"><div class="leg-dot" style="background:var(--luis)"></div> Luis</div>
        </div>
      </div>
      <canvas id="cReu" height="160"></canvas>
    </div>
    <div class="monthly">
      <div class="monthly-head">M&ecirc;s atual &middot; {mes_label} {mes_range}</div>
      <div class="monthly-row">
        <span class="monthly-name">Stephanie</span>
        <span class="monthly-val" style="color:var(--steph)">{d['s_m_meet']}</span>
      </div>
      <div class="monthly-row">
        <span class="monthly-name">Luis</span>
        <span class="monthly-val" style="color:var(--luis)">{d['l_m_meet']}</span>
      </div>
      <hr class="monthly-hr">
      <div class="monthly-total">
        <span class="monthly-total-lbl">Total m&ecirc;s</span>
        <span class="monthly-total-val">{d['s_m_meet'] + d['l_m_meet']}</span>
      </div>
    </div>
  </div>

  <!-- FOOTER -->
  <div class="footer">
    <div class="footer-left">
      <div>Liga&ccedil;&otilde;es = tipo <em>call</em> &middot; Reuni&otilde;es = <em>meeting</em> + <em>reuniao_pre_venda_</em> + <em>reuniao_de_apresentacao_de</em></div>
      <div>Funil de convers&atilde;o: estimativa via API (deals ganhos 2026 / deals ganhos + em proposta) &middot; pipeline Vendas&ndash;Lughy</div>
      <div>Comiss&atilde;o: Tabela de Portes &middot; Q3/2026 = 01/07&ndash;30/09 &middot; deals com valor R$0 exclu&iacute;dos</div>
      <div>Atividades: done=true &middot; semana {fmt_dd_mm(lmon)}&ndash;{fmt_dd_mm(lsun)} e m&ecirc;s {mes_range} &middot; apenas Stephanie (ID 24155859) e Luis (ID 13236195)</div>
    </div>
    <div class="footer-right">Gerado pelo GitHub Actions &middot; {gen_date}<br>Lughy &middot; Time Comercial</div>
  </div>

</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
(function() {{
  const s = getComputedStyle(document.documentElement);
  const STEPH  = s.getPropertyValue('--steph').trim()  || '#4A90D9';
  const LUIS   = s.getPropertyValue('--luis').trim()   || '#2ECC9A';
  const BORDER = s.getPropertyValue('--border').trim() || '#252a45';
  const MUTED  = s.getPropertyValue('--muted').trim()  || '#5e658a';

  const DAYS = {day_labels_js};

  const baseOpts = {{
    responsive:true,
    plugins:{{
      legend:{{display:false}},
      tooltip:{{mode:'index',intersect:false,backgroundColor:'#1b2038',titleColor:'#e6eaf5',bodyColor:'#9aa0c0',borderColor:'#252a45',borderWidth:1,padding:10}}
    }},
    scales:{{
      x:{{grid:{{color:BORDER}},ticks:{{color:MUTED,font:{{family:'DM Sans',size:11}}}}}},
      y:{{beginAtZero:true,grid:{{color:BORDER}},ticks:{{color:MUTED,font:{{family:'DM Sans',size:11}},stepSize:1,precision:0}}}}
    }}
  }};

  function ds(label,data,color){{
    return{{label,data,backgroundColor:color+'bb',hoverBackgroundColor:color,borderRadius:4,borderSkipped:false}};
  }}

  new Chart(document.getElementById('cLig'),{{
    type:'bar',
    data:{{labels:DAYS,datasets:[
      ds('Stephanie',{js_arr(d['s_daily_calls'])},STEPH),
      ds('Luis',     {js_arr(d['l_daily_calls'])},LUIS)
    ]}},
    options:{{...baseOpts,scales:{{...baseOpts.scales,y:{{...baseOpts.scales.y,max:Math.max(5,...{js_arr(d['s_daily_calls'])},...{js_arr(d['l_daily_calls'])})+1}}}}}}
  }});

  new Chart(document.getElementById('cAtiv'),{{
    type:'bar',
    data:{{labels:DAYS,datasets:[
      ds('Stephanie',{js_arr(d['s_daily_total'])},STEPH),
      ds('Luis',     {js_arr(d['l_daily_total'])},LUIS)
    ]}},
    options:{{...baseOpts,scales:{{...baseOpts.scales,y:{{...baseOpts.scales.y,ticks:{{...baseOpts.scales.y.ticks,stepSize:5}}}}}}}}
  }});

  new Chart(document.getElementById('cReu'),{{
    type:'bar',
    data:{{labels:DAYS,datasets:[
      ds('Stephanie',{js_arr(d['s_daily_meet'])},STEPH),
      ds('Luis',     {js_arr(d['l_daily_meet'])},LUIS)
    ]}},
    options:baseOpts
  }});
}})();
</script>

</body>
</html>"""


# ─── MAIN ────────────────────────────────────────────────────────
def main():
    today   = date.today()
    lmon, lsun = semana_passada()
    mes_ini = inicio_mes()
    q3_ini, q3_fim = q3_range()
    ano_ini = inicio_ano()

    print(f"Semana: {lmon} → {lsun}")
    print(f"Mês:    {mes_ini} → {today}")
    print(f"Q3:     {q3_ini} → {q3_fim}")

    print("Buscando atividades da semana passada...")
    s_w_total, s_w_meet, s_w_calls, s_w_daily = fetch_activities_full(STEPHANIE_ID, lmon, lsun)
    l_w_total, l_w_meet, l_w_calls, l_w_daily = fetch_activities_full(LUIS_ID, lmon, lsun)
    print(f"  Stephanie semana: {s_w_total} ativ / {s_w_meet} reun / {s_w_calls} lig")
    print(f"  Luis semana:      {l_w_total} ativ / {l_w_meet} reun / {l_w_calls} lig")

    print("Buscando atividades do mês...")
    s_m_total, s_m_meet, s_m_calls, _ = fetch_activities_full(STEPHANIE_ID, mes_ini, today)
    l_m_total, l_m_meet, l_m_calls, _ = fetch_activities_full(LUIS_ID, mes_ini, today)
    print(f"  Stephanie mês: {s_m_total} ativ / {s_m_meet} reun / {s_m_calls} lig")
    print(f"  Luis mês:      {l_m_total} ativ / {l_m_meet} reun / {l_m_calls} lig")

    print("Buscando deals ganhos Q3...")
    s_q3_deals = fetch_won_deals_detail(STEPHANIE_ID, q3_ini, q3_fim)
    l_q3_deals = fetch_won_deals_detail(LUIS_ID, q3_ini, q3_fim)
    s_q3_count      = len(s_q3_deals)
    s_q3_value      = sum(d["value"] for d in s_q3_deals)
    s_q3_commission = sum(d["commission"] for d in s_q3_deals)
    l_q3_count      = len(l_q3_deals)
    l_q3_value      = sum(d["value"] for d in l_q3_deals)
    l_q3_commission = sum(d["commission"] for d in l_q3_deals)
    print(f"  Stephanie Q3: {s_q3_count} deals / {brl_k(s_q3_value)} / comissão {brl_full(s_q3_commission)}")
    print(f"  Luis Q3:      {l_q3_count} deals / {brl_k(l_q3_value)} / comissão {brl_full(l_q3_commission)}")

    print("Buscando estágios do pipeline...")
    stages = fetch_pipeline_stages()
    ref_id  = next((v for k, v in stages.items() if "refinamento" in k), None)
    prop_id = next((v for k, v in stages.items() if "proposta" in k), None)

    s_ref  = count_open_deals(STEPHANIE_ID, ref_id)
    s_prop = count_open_deals(STEPHANIE_ID, prop_id)
    l_ref  = count_open_deals(LUIS_ID, ref_id)
    l_prop = count_open_deals(LUIS_ID, prop_id)

    # Won 2026 (para funil)
    won_2026_s = fetch_won_deals_detail(STEPHANIE_ID, ano_ini, today)
    won_2026_l = fetch_won_deals_detail(LUIS_ID, ano_ini, today)
    s_won_2026 = len(won_2026_s)
    l_won_2026 = len(won_2026_l)

    # Funil manual via config.json — você informa as quantidades, o script calcula os %
    cfg = {}
    try:
        with open("config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        pass

    def build_funil(cfg_person, api_ref, api_prop, api_ganho):
        fp = cfg_person.get("funil", {})
        ref   = fp["refinamento"] if fp.get("refinamento") is not None else api_ref
        prop  = fp["proposta"]    if fp.get("proposta")    is not None else api_prop
        ganho = fp["ganho"]       if fp.get("ganho")       is not None else api_ganho
        conv_rp = round(prop  / ref  * 100) if ref  > 0 else 0
        conv_pg = round(ganho / prop * 100) if prop > 0 else 0
        return {"ref": ref, "prop": prop, "ganho": ganho,
                "conv_rp": conv_rp, "conv_pg": conv_pg}

    s_funil = build_funil(cfg.get("stephanie", {}), s_ref, s_prop, s_won_2026)
    l_funil = build_funil(cfg.get("luis",      {}), l_ref, l_prop, l_won_2026)
    s_conv  = s_funil["conv_pg"]
    l_conv  = l_funil["conv_pg"]
    print(f"  Funil Stephanie: ref={s_funil['ref']} prop={s_funil['prop']} ganho={s_funil['ganho']} -> {s_conv}%")
    print(f"  Funil Luis:      ref={l_funil['ref']} prop={l_funil['prop']} ganho={l_funil['ganho']} -> {l_conv}%")

    # Arrays diários para gráficos
    week_days = [lmon + timedelta(days=i) for i in range(7)]
    day_names = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]
    day_labels = [f"{day_names[i]} {(lmon + timedelta(days=i)).strftime('%d')}" for i in range(7)]

    def daily_arr(daily_dict, key):
        return [daily_dict.get(d.isoformat(), {}).get(key, 0) for d in week_days]

    data = {
        "today": today, "lmon": lmon, "lsun": lsun, "mes_ini": mes_ini,
        "s_w_total": s_w_total, "s_w_meet": s_w_meet, "s_w_calls": s_w_calls,
        "l_w_total": l_w_total, "l_w_meet": l_w_meet, "l_w_calls": l_w_calls,
        "s_m_total": s_m_total, "s_m_meet": s_m_meet, "s_m_calls": s_m_calls,
        "l_m_total": l_m_total, "l_m_meet": l_m_meet, "l_m_calls": l_m_calls,
        "s_q3_deals": s_q3_deals, "s_q3_count": s_q3_count,
        "s_q3_value": s_q3_value, "s_q3_commission": s_q3_commission,
        "l_q3_deals": l_q3_deals, "l_q3_count": l_q3_count,
        "l_q3_value": l_q3_value, "l_q3_commission": l_q3_commission,
        "s_funil": s_funil, "s_conv": s_conv,
        "l_funil": l_funil, "l_conv": l_conv,
        "day_labels":     day_labels,
        "s_daily_total":  daily_arr(s_w_daily, "total"),
        "s_daily_calls":  daily_arr(s_w_daily, "calls"),
        "s_daily_meet":   daily_arr(s_w_daily, "meetings"),
        "l_daily_total":  daily_arr(l_w_daily, "total"),
        "l_daily_calls":  daily_arr(l_w_daily, "calls"),
        "l_daily_meet":   daily_arr(l_w_daily, "meetings"),
    }

    html = gerar_html(data)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html gerado com sucesso.")


if __name__ == "__main__":
    main()
