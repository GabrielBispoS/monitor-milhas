"""
Monitor de Milhas e Passagens Aéreas
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Passagens: UDI → RIO, 2 adultos, agosto/2026
API voos:  SerpApi / Google Flights (2 req/dia → ~62/mês, free tier = 100)
Livelo:    DuckDuckGo (gratuito, sem API key)
Envio:     WhatsApp via Evolution API
Execução:  GitHub Actions, 1x ao dia às 08h00 BRT
"""

import os
import re
import requests
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# SECRETS (configurados nos GitHub Secrets do repositório)
# ──────────────────────────────────────────────────────────────
SERPAPI_KEY        = os.environ["SERPAPI_KEY"]
EVOLUTION_API_URL  = os.environ["EVOLUTION_API_URL"]    # ex: https://minha-evolution.com
EVOLUTION_API_KEY  = os.environ["EVOLUTION_API_KEY"]
EVOLUTION_INSTANCE = os.environ["EVOLUTION_INSTANCE"]   # nome da instância
WHATSAPP_NUMBER    = os.environ["WHATSAPP_NUMBER"]       # ex: 5534999999999

# ──────────────────────────────────────────────────────────────
# PARÂMETROS DE VIAGEM
# ──────────────────────────────────────────────────────────────
ORIGEM     = "UDI"
ADULTOS    = 2
PRECO_ALVO = 1500.00  # R$ — abaixo disso dispara alerta de compra imediata

# 4 finais de semana de agosto/2026 (sexta → segunda)
DATAS_VIAGEM = [
    {"ida": "2026-08-07", "volta": "2026-08-10", "label": "07/08"},
    {"ida": "2026-08-14", "volta": "2026-08-17", "label": "14/08"},
    {"ida": "2026-08-21", "volta": "2026-08-24", "label": "21/08"},
    {"ida": "2026-08-28", "volta": "2026-08-31", "label": "28/08"},
]

# Alternância de pares por paridade do dia do mês (2 req/dia)
# Dia par   → Par A: 07/08 e 21/08
# Dia ímpar → Par B: 14/08 e 28/08
PAR_A = [0, 2]
PAR_B = [1, 3]

# ──────────────────────────────────────────────────────────────
# PARÂMETROS LIVELO
# ──────────────────────────────────────────────────────────────
BONUS_AEREAS = {
    "SMILES":     ["smiles", "gol"],
    "TUDO AZUL":  ["tudo azul", "azul"],
    "LATAM PASS": ["latam pass", "latam"],
    "TAP":        ["tap miles", "tap"],
}
PARCEIROS_VAREJO    = ["natura", "shopee", "casas bahia", "fast shop",
                       "carrefour", "magalu", "insider", "americanas"]
LIMIAR_BONUS_AEREO  = 40   # % mínimo para alertar
LIMIAR_BONUS_VAREJO = 8    # pontos/R$ mínimo para alertar


# ══════════════════════════════════════════════════════════════
# 1. PASSAGENS — SerpApi / Google Flights
# ══════════════════════════════════════════════════════════════

def buscar_voo(datas: dict) -> dict | None:
    """Busca o voo mais barato UDI→RIO para um par de datas. Usa 1 req SerpApi."""
    params = {
        "engine":        "google_flights",
        "departure_id":  ORIGEM,
        "arrival_id":    "RIO",        # abrange GIG e SDU automaticamente
        "outbound_date": datas["ida"],
        "return_date":   datas["volta"],
        "adults":        ADULTOS,
        "currency":      "BRL",
        "hl":            "pt",
        "type":          "1",          # 1 = ida e volta
        "api_key":       SERPAPI_KEY,
    }
    try:
        r = requests.get("https://serpapi.com/search", params=params, timeout=25)
        r.raise_for_status()
        data = r.json()

        melhor = None
        for grupo in ["best_flights", "other_flights"]:
            for voo in data.get(grupo, []):
                preco = voo.get("price")
                if preco and (melhor is None or preco < melhor["preco"]):
                    legs    = voo.get("flights", [{}])
                    destino = legs[-1].get("arrival_airport", {}).get("id", "RIO")
                    cia     = legs[0].get("airline", "N/A")
                    melhor  = {
                        "label":   datas["label"],
                        "ida":     datas["ida"],
                        "volta":   datas["volta"],
                        "preco":   float(preco),
                        "cia":     cia,
                        "destino": destino,
                    }
        return melhor

    except Exception as e:
        log.warning(f"Erro SerpApi [{datas['label']}]: {e}")
        return None


def buscar_passagens() -> list[dict]:
    """
    Escolhe o par do dia (paridade do dia do mês) e faz 2 buscas.
    Dia par → Par A (07/08 e 21/08) | Dia ímpar → Par B (14/08 e 28/08)
    """
    dia_do_mes = datetime.now().day
    indices    = PAR_A if dia_do_mes % 2 == 0 else PAR_B
    par_nome   = "A (07/08 e 21/08)" if indices == PAR_A else "B (14/08 e 28/08)"
    log.info(f"Dia {dia_do_mes} → Par {par_nome}")

    resultados = []
    for i in indices:
        voo = buscar_voo(DATAS_VIAGEM[i])
        if voo:
            resultados.append(voo)
            log.info(f"  ✔ {voo['label']}: R$ {voo['preco']:.0f} ({voo['cia']} → {voo['destino']})")
        else:
            log.warning(f"  ✘ {DATAS_VIAGEM[i]['label']}: sem resultado")

    return resultados


# ══════════════════════════════════════════════════════════════
# 2. LIVELO — DuckDuckGo (gratuito, sem API key)
# ══════════════════════════════════════════════════════════════

def buscar_promocoes_livelo() -> dict:
    resultado = {"aereas": [], "varejo": []}

    queries = [
        "livelo bonus transferencia smiles latam azul hoje 2026",
        "livelo pontuacao turbinada shopee natura casas bahia hoje",
    ]

    texto_total = ""
    for q in queries:
        try:
            r = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": q, "format": "json", "no_html": 1, "skip_disambig": 1},
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            data     = r.json()
            abstract = data.get("AbstractText", "").lower()
            related  = " ".join(t.get("Text", "") for t in data.get("RelatedTopics", [])).lower()
            texto_total += " " + abstract + " " + related
        except Exception as e:
            log.warning(f"DDG falhou em '{q}': {e}")

    # Detectar bônus aéreos
    for programa, keywords in BONUS_AEREAS.items():
        for kw in keywords:
            if kw in texto_total:
                match = re.search(r"(\d+)\s*%", texto_total)
                pct   = int(match.group(1)) if match else 0
                if pct >= LIMIAR_BONUS_AEREO:
                    if not any(a["programa"] == programa for a in resultado["aereas"]):
                        resultado["aereas"].append({"programa": programa, "bonus_pct": pct})
                        log.info(f"  🔥 {programa}: +{pct}%")

    # Detectar varejo turbinado
    for parceiro in PARCEIROS_VAREJO:
        if parceiro in texto_total:
            match = re.search(r"(\d+)\s*(?:pontos?|pts?)\s*/?\s*r\$", texto_total)
            pts   = int(match.group(1)) if match else 0
            if pts >= LIMIAR_BONUS_VAREJO:
                if not any(v["loja"] == parceiro.title() for v in resultado["varejo"]):
                    resultado["varejo"].append({"loja": parceiro.title(), "pontos_por_real": pts})
                    log.info(f"  ⭐ {parceiro.title()}: {pts}pts/R$")

    return resultado


# ══════════════════════════════════════════════════════════════
# 3. FORMATAR MENSAGEM WHATSAPP
# ══════════════════════════════════════════════════════════════

def formatar_mensagem(passagens: list[dict], livelo: dict) -> str:
    hoje       = datetime.now().strftime("%d/%m/%Y %H:%M")
    dia_do_mes = datetime.now().day
    par_nome   = "A (07/08 e 21/08)" if dia_do_mes % 2 == 0 else "B (14/08 e 28/08)"

    linhas = [f"🤖 *Monitor de Milhas — {hoje}*\n"]

    # ── Passagens ──────────────────────────────────────────
    linhas.append(f"✈️ *PASSAGENS UDI → RIO* _(par {par_nome})_")

    if not passagens:
        linhas.append("⚠️ Nenhuma passagem encontrada hoje.")
    else:
        melhor = min(passagens, key=lambda p: p["preco"])
        for p in sorted(passagens, key=lambda p: p["ida"]):
            emoji = "🟢" if p["preco"] <= PRECO_ALVO else "🟡"
            linhas.append(
                f"{emoji} {p['label']} → {p['volta'][8:]} ago | "
                f"*R$ {p['preco']:.0f}* | {p['cia']} ({p['destino']})"
            )

        linhas.append("")
        if melhor["preco"] <= PRECO_ALVO:
            linhas.append(
                f"🚨 *COMPRA RECOMENDADA!*\n"
                f"   {melhor['label']} por *R$ {melhor['preco']:.0f}* (2 pessoas)"
            )
        else:
            diff = melhor["preco"] - PRECO_ALVO
            linhas.append(
                f"⏳ Melhor: *R$ {melhor['preco']:.0f}* ({melhor['label']})\n"
                f"   Faltam R$ {diff:.0f} para a meta de R$ {PRECO_ALVO:.0f}"
            )

    linhas.append("")

    # ── Livelo — Transferências aéreas ─────────────────────
    linhas.append("🏦 *BÔNUS DE TRANSFERÊNCIA LIVELO*")
    if livelo["aereas"]:
        for a in livelo["aereas"]:
            linhas.append(f"🔥 {a['programa']}: *+{a['bonus_pct']}% de bônus* — AGIR HOJE!")
    else:
        linhas.append("😴 Sem bônus relevantes. Aguardar.")

    linhas.append("")

    # ── Livelo — Varejo ────────────────────────────────────
    linhas.append("🛍️ *PONTUAÇÃO TURBINADA — VAREJO*")
    if livelo["varejo"]:
        for v in livelo["varejo"]:
            linhas.append(f"⭐ {v['loja']}: *{v['pontos_por_real']}pts/R$*")
    else:
        linhas.append(f"📊 Sem ofertas acima de {LIMIAR_BONUS_VAREJO}pts/R$ hoje.")

    linhas.append("\n_GitHub Actions · SerpApi · Evolution API_")
    return "\n".join(linhas)


# ══════════════════════════════════════════════════════════════
# 4. ENVIO — Evolution API (WhatsApp)
# ══════════════════════════════════════════════════════════════

def enviar_whatsapp(mensagem: str) -> bool:
    url     = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    payload = {"number": WHATSAPP_NUMBER, "textMessage": {"text": mensagem}}
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        log.info("✅ WhatsApp enviado!")
        return True
    except Exception as e:
        log.error(f"❌ Erro ao enviar WhatsApp: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    log.info("══════════════════════════════════")
    log.info("  Monitor de Milhas — iniciando  ")
    log.info("══════════════════════════════════")

    passagens = buscar_passagens()
    livelo    = buscar_promocoes_livelo()
    mensagem  = formatar_mensagem(passagens, livelo)

    log.info(f"\n{mensagem}\n")
    enviar_whatsapp(mensagem)

    log.info("══════════════════════════════════")
    log.info("  Finalizado                      ")
    log.info("══════════════════════════════════")


if __name__ == "__main__":
    main()
