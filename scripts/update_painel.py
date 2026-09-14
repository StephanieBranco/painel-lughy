#!/usr/bin/env python3
"""
update_painel.py — Painel Comercial Lughy
Gera o index.html a partir dos dados do Pipedrive via REST API.
Executado pelo GitHub Actions toda segunda-feira.

Configuração:
  - PIPEDRIVE_TOKEN: token de API do Pipedrive (GitHub Secret)
  - COMMISSION_RATE: taxa de comissão (ajuste conforme necessidade)
"""

import os
import sys
import requests
from datetime import date, timedelta

# ─── CONFIGURAÇÃO ────────────────────────────────────────────────
API_TOKEN = os.environ.get("PIPEDRIVE_TOKEN", "")
if not API_TOKEN:
    print("ERRO: variável PIPEDRIVE_TOKEN não definida.")
    sys.exit(1)

BASE_URL = "https://api.pipedrive.com/v1"
STEPHANIE_ID = 24155859
LUIS_ID = 13236195
COMMISSION_RATE = 0.005  # 0,5% — ajuste conforme a política de comissão real

VALID_TYPES = {
    "pesquisa", "task", "primeira_mensagem_linkedin", "call",
    "ligacao_nao_atendida", "whatsapp", "email", "reuniao_pre_venda_",
    "meeting", "estimativa_", "elaborar_proposta_",
    "reuniao_de_apresentacao_de", "follow_up", "no_show"
}
MEETING_TYPES = {"meeting", "reuniao_pre_venda_", "reuniao_de_apresentacao_de"}


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


# ─── ATIVIDADES ──────────────────────────────────────────────────
def fetch_activities(user_id, start_date, end_date):
    items = get_all("/activities", {
        "user_id": user_id,
        "due_date_start": start_date.isoformat(),
        "due_date_end": end_date.isoformat(),
        "done": 1,
    })
    valid = [a for a in items if (a.get("type") or "") in VALID_TYPES]
    total = len(valid)
    meetings = sum(1 for a in valid if (a.get("type") or "") in MEETING_TYPES)
    calls = sum(1 for a in valid if a.get("type") == "call")
    return total, meetings, calls


# ─── DEALS ───────────────────────────────────────────────────────
def fetch_won_deals(user_id, start_date, end_date):
    items = get_all("/deals", {"user_id": user_id, "status": "won"})
    filtered = [
        d for d in items
        if start_date.isoformat() <= (d.get("won_time") or "")[:10] <= end_date.isoformat()
    ]
    count = len(filtered)
    value = sum(d.get("value") or 0 for d in filtered)
    return count, value


def fetch_pipeline_stages():
    """Retorna dicionário {nome_lower: stage_id} do pipeline Vendas Lughy."""
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
def brl(value):
    """Formata valor em reais: R$ 1.234"""
    return f"R$ {int(value):,}".replace(",", ".")


def pct(won, prop):
    total = won + prop
    return f"{round(won / total * 100, 1)}%" if total > 0 else "—"


# ─── HTML ────────────────────────────────────────────────────────
def gerar_html(d):
    today_str = date.today().strftime("%d/%m/%Y")

    def funil_row(stage, count, max_c):
        w = min(100, round(count / max(1, max_c) * 100))
        return f"""
        <div class="funil-row">
          <span class="funil-stage">{stage}</span>
          <div class="funil-bar"><div class="funil-fill" style="width:{w}%"></div></div>
          <span class="funil-num">{count}</span>
        </div>"""

    def funil_card(nome, emoji, refin, prop, ganho, conv):
        m = max(1, refin, prop, ganho)
        return f"""
      <div class="card">
        <div class="card-name">{emoji} {nome} · Conversão <strong>{conv}</strong></div>
        {funil_row("Refinamento", refin, m)}
        {funil_row("Proposta", prop, m)}
        {funil_row("Ganho 2026", ganho, m)}
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Painel Comercial Lughy</title>
<style>
:root {{
  --bg:#0f1117; --surface:#1a1d2e; --card:#20243a;
  --accent:#6c63ff; --green:#43d9a2; --yellow:#f6c90e;
  --text:#e2e8f0; --muted:#8892a4; --border:#2d3150;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;padding:1.5rem;max-width:960px;margin:0 auto}}
h1{{font-size:1.4rem;font-weight:700;color:var(--accent);margin-bottom:.2rem}}
.sub{{color:var(--muted);font-size:.82rem;margin-bottom:1.5rem}}
.sec{{font-size:.85rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin:1.5rem 0 .75rem;border-left:3px solid var(--accent);padding-left:.6rem}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}
@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:1rem 1.25rem}}
.card-name{{font-size:.78rem;color:var(--muted);margin-bottom:.6rem;font-weight:600}}
.kpi{{display:flex;gap:1.5rem}}
.kpi-item{{text-align:center}}
.kpi-v{{font-size:1.7rem;font-weight:700;line-height:1}}
.kpi-l{{font-size:.68rem;color:var(--muted);margin-top:.2rem}}
.meet .kpi-v{{color:var(--accent)}}
.call .kpi-v{{color:var(--green)}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
th{{color:var(--muted);font-weight:600;text-align:left;padding:.4rem .6rem;border-bottom:1px solid var(--border)}}
td{{padding:.5rem .6rem}}
tr:nth-child(even) td{{background:rgba(255,255,255,.02)}}
.badge{{display:inline-block;padding:.15rem .5rem;border-radius:4px;font-size:.75rem;font-weight:600;background:rgba(108,99,255,.15);color:var(--accent)}}
.funil-row{{display:flex;align-items:center;gap:.75rem;padding:.45rem 0;border-bottom:1px solid var(--border)}}
.funil-stage{{flex:1;font-size:.83rem}}
.funil-bar{{flex:3;background:var(--border);border-radius:4px;height:7px;overflow:hidden}}
.funil-fill{{height:100%;border-radius:4px;background:var(--accent)}}
.funil-num{{font-weight:700;font-size:.88rem;width:2rem;text-align:right}}
.footer{{color:var(--muted);font-size:.72rem;margin-top:1.5rem;text-align:right}}
</style>
</head>
<body>
<h1>📊 Painel Comercial Lughy</h1>
<div class="sub">Semana {d['week_label']} · Atualizado em {today_str}</div>

<div class="sec">📅 Semana Passada ({d['week_label']})</div>
<div class="grid">
  <div class="card">
    <div class="card-name">👩 Stephanie</div>
    <div class="kpi">
      <div class="kpi-item"><div class="kpi-v">{d['s_sem_total']}</div><div class="kpi-l">Atividades</div></div>
      <div class="kpi-item meet"><div class="kpi-v">{d['s_sem_meet']}</div><div class="kpi-l">Reuniões</div></div>
      <div class="kpi-item call"><div class="kpi-v">{d['s_sem_call']}</div><div class="kpi-l">Ligações</div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-name">👨 Luis</div>
    <div class="kpi">
      <div class="kpi-item"><div class="kpi-v">{d['l_sem_total']}</div><div class="kpi-l">Atividades</div></div>
      <div class="kpi-item meet"><div class="kpi-v">{d['l_sem_meet']}</div><div class="kpi-l">Reuniões</div></div>
      <div class="kpi-item call"><div class="kpi-v">{d['l_sem_call']}</div><div class="kpi-l">Ligações</div></div>
    </div>
  </div>
</div>

<div class="sec">📆 Mês Corrente ({d['month_label']})</div>
<div class="grid">
  <div class="card">
    <div class="card-name">👩 Stephanie</div>
    <div class="kpi">
      <div class="kpi-item"><div class="kpi-v">{d['s_mes_total']}</div><div class="kpi-l">Atividades</div></div>
      <div class="kpi-item meet"><div class="kpi-v">{d['s_mes_meet']}</div><div class="kpi-l">Reuniões</div></div>
      <div class="kpi-item call"><div class="kpi-v">{d['s_mes_call']}</div><div class="kpi-l">Ligações</div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-name">👨 Luis</div>
    <div class="kpi">
      <div class="kpi-item"><div class="kpi-v">{d['l_mes_total']}</div><div class="kpi-l">Atividades</div></div>
      <div class="kpi-item meet"><div class="kpi-v">{d['l_mes_meet']}</div><div class="kpi-l">Reuniões</div></div>
      <div class="kpi-item call"><div class="kpi-v">{d['l_mes_call']}</div><div class="kpi-l">Ligações</div></div>
    </div>
  </div>
</div>

<div class="sec">💰 Comissão Trimestral — {d['q3_label']}</div>
<div class="card">
  <table>
    <thead><tr><th>Vendedor</th><th>Deals ganhos</th><th>Valor total</th><th>Comissão est.</th></tr></thead>
    <tbody>
      <tr><td>Stephanie</td><td>{d['s_deals']}</td><td>{brl(d['s_value'])}</td><td><span class="badge">{brl(d['s_commission'])}</span></td></tr>
      <tr><td>Luis</td><td>{d['l_deals']}</td><td>{brl(d['l_value'])}</td><td><span class="badge">{brl(d['l_commission'])}</span></td></tr>
    </tbody>
  </table>
</div>

<div class="sec">🔀 Funil de Conversão</div>
<div class="grid">
  {funil_card("Stephanie", "👩", d['s_refin'], d['s_prop'], d['s_ganho'], d['s_conv'])}
  {funil_card("Luis", "👨", d['l_refin'], d['l_prop'], d['l_ganho'], d['l_conv'])}
</div>

<div class="footer">Gerado automaticamente pelo GitHub Actions · {today_str}</div>
</body>
</html>"""


# ─── MAIN ────────────────────────────────────────────────────────
def main():
    today = date.today()
    last_mon, last_sun = semana_passada()
    mes_ini = inicio_mes()
    q3_ini, q3_fim = q3_range()

    print(f"Semana passada: {last_mon} a {last_sun}")
    print(f"Mês corrente: {mes_ini} a {today}")
    print(f"Q3: {q3_ini} a {q3_fim}")

    print("Buscando atividades...")
    s_sem = fetch_activities(STEPHANIE_ID, last_mon, last_sun)
    l_sem = fetch_activities(LUIS_ID, last_mon, last_sun)
    s_mes = fetch_activities(STEPHANIE_ID, mes_ini, today)
    l_mes = fetch_activities(LUIS_ID, mes_ini, today)

    print("Buscando deals...")
    s_deals, s_value = fetch_won_deals(STEPHANIE_ID, q3_ini, q3_fim)
    l_deals, l_value = fetch_won_deals(LUIS_ID, q3_ini, q3_fim)

    print("Buscando etapas do funil...")
    stages = fetch_pipeline_stages()
    refin_id = next((v for k, v in stages.items() if "refinamento" in k or "pendên" in k or "pendencia" in k), None)
    prop_id = next((v for k, v in stages.items() if "proposta" in k), None)

    s_refin = count_open_deals(STEPHANIE_ID, refin_id)
    l_refin = count_open_deals(LUIS_ID, refin_id)
    s_prop = count_open_deals(STEPHANIE_ID, prop_id)
    l_prop = count_open_deals(LUIS_ID, prop_id)

    dados = dict(
        week_label=f"{last_mon.strftime('%d/%m')} a {last_sun.strftime('%d/%m')}",
        month_label=today.strftime("%b/%Y"),
        q3_label=f"Q3 {today.year} (jul–set)",
        # semana
        s_sem_total=s_sem[0], s_sem_meet=s_sem[1], s_sem_call=s_sem[2],
        l_sem_total=l_sem[0], l_sem_meet=l_sem[1], l_sem_call=l_sem[2],
        # mês
        s_mes_total=s_mes[0], s_mes_meet=s_mes[1], s_mes_call=s_mes[2],
        l_mes_total=l_mes[0], l_mes_meet=l_mes[1], l_mes_call=l_mes[2],
        # comissão
        s_deals=s_deals, s_value=s_value, s_commission=round(s_value * COMMISSION_RATE),
        l_deals=l_deals, l_value=l_value, l_commission=round(l_value * COMMISSION_RATE),
        # funil
        s_refin=s_refin, l_refin=l_refin,
        s_prop=s_prop, l_prop=l_prop,
        s_ganho=s_deals, l_ganho=l_deals,
        s_conv=pct(s_deals, s_prop),
        l_conv=pct(l_deals, l_prop),
    )

    html = gerar_html(dados)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("✅ index.html gerado com sucesso.")


if __name__ == "__main__":
    main()
