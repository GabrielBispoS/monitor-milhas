"""
Monitor de Milhas e Passagens Aéreas
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Passagens: UDI → RIO (GIG/SDU), 2 adultos, agosto/2026
API voos:  SerpApi / Google Flights (2 req/dia → ~62/mês, free tier = 100)
Livelo:    DuckDuckGo (gratuito, sem API key)
Envio:     WhatsApp via Evolution API
Execução:  GitHub Actions, 1x ao dia às 08h00 BRT

CORREÇÕES APLICADAS (v2):
  - arrival_id alterado de "RIO" (inválido) para kgmid do Rio de Janeiro
  - deep_search=true adicionado (resultados mais precisos para rotas regionais)
  - Parsing de price robusto (int/float/None)
  - Fallback para price_insights.lowest_price
  - Formatação de mensagem WhatsApp melhorada
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
EVOLUTION_API_URL  = os.environ["EVOLUTION_API_URL"]    # ex: http://137.131.236.152:8080
EVOLUTION_API_KEY  = os.environ["EVOLUTION_API_KEY"]
EVOLUTION_INSTANCE = os.environ["EVOLUTION_INSTANCE"]   # monitor-milhas
WHATSAPP_NUMBER    = os.environ["WHATSAPP_NUMBER"]       # ex: 5534999999999

# ──────────────────────────────────────────────────────────────
# PARÂMETROS DE VIAGEM
# ──────────────────────────────────────────────────────────────
ORIGEM      = "UDI"
# CORREÇÃO: "RIO" não é código IATA válido na SerpApi.
# Usar kgmid do Rio de Janeiro para cobrir GIG e SDU automaticamente.
DESTINO     = "/m/0f2r2"   # kgmid do Rio de Janeiro (cobre GIG e SDU)
ADULTOS     = 2
PRECO_ALVO  = 1500.00      # R$ — abaixo disso dispara alerta de compra imediata

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
    """
    Busca o voo mais barato UDI→RIO para um par de datas. Usa 1 req SerpApi.

    CORREÇÕES v2:
    - arrival_id usa kgmid /m/0f2r2 (Rio de Janeiro) em vez de "RIO" (inválido)
    - deep_search=true garante resultados precisos para rotas regionais brasileiras
    - price tratado como int ou float, nunca falha em preco == 0
    - Fallback para price_insights.lowest_price quando best_flights/other_flights vazios
    """
    params = {
        "engine":        "google_flights",
        "departure_id":  ORIGEM,
        "arrival_id":    DESTINO,      # kgmid do Rio (cobre GIG e SDU)
        "outbound_date": datas["ida"],
        "return_date":   datas["volta"],
        "adults":        ADULTOS,
        "currency":      "BRL",
        "hl":            "pt",
        "gl":            "br",         # geolocalização Brasil → resultados em BRL reais
        "type":          "1",          # 1 = ida e volta
        "deep_search":   "true",       # CORREÇÃO: resultados precisos para rotas regionais
        "api_key":       SERPAPI_KEY,
    }
    try:
        r = requests.get("https://serpapi.com/search", params=params, timeout=40)
        r.raise_for_status()
        data = r.json()

        # Log para debug (aparece nos logs do GitHub Actions)
        chaves_raiz = list(data.keys())
        log.info(f"  [SerpApi] chaves na resposta: {chaves_raiz}")

        if "error" in data:
            log.warning(f"  [SerpApi] erro da API: {data['error']}")
            return None

        melhor = None

        for grupo in ["best_flights", "other_flights"]:
            for voo in data.get(grupo, []):
                # CORREÇÃO: price pode ser int, float ou ausente — nunca usar "if preco"
                preco_raw = voo.get("price")
                if preco_raw is None:
                    continue
                preco = float(preco_raw)

                if melhor is None or preco < melhor["preco"]:
                    legs        = voo.get("flights", [{}])
                    primeiro    = legs[0] if legs else {}
                    ultimo      = legs[-1] if legs else {}

                    destino_id  = (
                        ultimo.get("arrival_airport", {}).get("id")
                        or ultimo.get("arrival_airport", {}).get("name", "RIO")
                    )
                    cia         = primeiro.get("airline", "N/A")
                    escalas     = len(voo.get("layovers", []))
                    dur_min     = voo.get("total_duration", 0) or 0
                    duracao     = f"{dur_min // 60}h{dur_min % 60:02d}" if dur_min else "N/A"
                    label_volta = datas["volta"][8:10] + "/" + datas["volta"][5:7]

                    melhor = {
                        "label":       datas["label"],
                        "label_volta": label_volta,
                        "ida":         datas["ida"],
                        "volta":       datas["volta"],
                        "preco":       preco,
                        "cia":         cia,
                        "destino":     destino_id,
                        "escalas":     escalas,
                        "duracao":     duracao,
                    }

        # CORREÇÃO: fallback via price_insights se nenhum voo parseado
        if melhor is None:
            insights = data.get("price_insights", {})
            preco_insights = insights.get("lowest_price")
            if preco_insights is not None:
                label_volta = datas["volta"][8:10] + "/" + datas["volta"][5:7]
                melhor = {
                    "label":       datas["label"],
                    "label_volta": label_volta,
                    "ida":         datas["ida"],
                    "volta":       datas["volta"],
                    "preco":       float(preco_insights),
                    "cia":         "Diversas",
                    "destino":     "RIO",
                    "escalas":     0,
                    "duracao":     "N/A",
                }
                log.info(f"  [SerpApi] usando price_insights.lowest_price: R$ {preco_insights}")

        return melhor

    except requests.exceptions.Timeout:
        log.warning(f"  [SerpApi] timeout na busca [{datas['label']}] — deep_search pode demorar até 40s")
        return None
    except Exception as e:
        log.warning(f"  [SerpApi] erro [{datas['label']}]: {e}")
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
            match = re.search(r"(\d+)\s*(?:pontos?|pts?)\s*/?\\s*r\$", texto_total)
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
    """
    Formata a mensagem para o WhatsApp.
    Usa negrito (*texto*) e emojis compatíveis com WhatsApp.
    """
    hoje = datetime.now().strftime("%d/%m %H:%M")
    linhas = []

    # ── Cabeçalho ──────────────────────────────────────────────
    linhas.append(f"🛫 *Monitor de Milhas* — {hoje}")
    linhas.append("─" * 30)

    # ── Passagens ──────────────────────────────────────────────
    linhas.append("")
    linhas.append("✈️ *PASSAGENS UDI → RIO*")
    linhas.append(f"_2 adultos · agosto/2026_")

    if not passagens:
        linhas.append("")
        linhas.append("⚠️ Nenhuma passagem encontrada.")
        linhas.append("_Verifique os logs do GitHub Actions._")
    else:
        melhor = min(passagens, key=lambda p: p["preco"])
        linhas.append("")

        for p in sorted(passagens, key=lambda p: p["ida"]):
            abaixo_meta = p["preco"] <= PRECO_ALVO
            emoji_preco = "🟢" if abaixo_meta else "🔴"

            if p["escalas"] == 0:
                escala_txt = "direto"
            elif p["escalas"] == 1:
                escala_txt = "1 escala"
            else:
                escala_txt = f"{p['escalas']} escalas"

            linhas.append(
                f"{emoji_preco} *{p['label']} → {p['label_volta']}*\n"
                f"   💰 *R$ {p['preco']:.0f}* (2 pax)\n"
                f"   🏢 {p['cia']}  |  🛬 {p['destino']}  |  ⏱ {p['duracao']}  |  {escala_txt}"
            )

        linhas.append("")
        if melhor["preco"] <= PRECO_ALVO:
            linhas.append(
                f"🚨 *COMPRAR AGORA!*\n"
                f"   {melhor['label']} por *R$ {melhor['preco']:.0f}* — abaixo da meta!"
            )
        else:
            diff = melhor["preco"] - PRECO_ALVO
            linhas.append(
                f"⏳ *Melhor preço:* R$ {melhor['preco']:.0f} ({melhor['label']})\n"
                f"   Faltam *R$ {diff:.0f}* para a meta de R$ {PRECO_ALVO:.0f}"
            )

    linhas.append("")
    linhas.append("─" * 30)

    # ── Livelo — Bônus transferência ───────────────────────────
    linhas.append("")
    linhas.append("🏦 *BÔNUS LIVELO → AÉREAS*")

    if livelo["aereas"]:
        linhas.append("")
        for a in livelo["aereas"]:
            linhas.append(f"🔥 *{a['programa']}:* +{a['bonus_pct']}% de bônus")
        linhas.append("_⚡ Transferir hoje para aproveitar!_")
    else:
        linhas.append("😴 Sem bônus relevantes hoje.")

    linhas.append("")

    # ── Livelo — Varejo turbinado ──────────────────────────────
    linhas.append("🛍️ *VAREJO TURBINADO LIVELO*")

    if livelo["varejo"]:
        linhas.append("")
        for v in livelo["varejo"]:
            linhas.append(f"⭐ *{v['loja']}:* {v['pontos_por_real']} pts/R$")
    else:
        linhas.append(f"📊 Nada acima de {LIMIAR_BONUS_VAREJO} pts/R$ hoje.")

    # ── Rodapé ─────────────────────────────────────────────────
    linhas.append("")
    linhas.append("─" * 30)
    linhas.append("_GitHub Actions · SerpApi · Evolution API_")

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
        if hasattr(e, "response") and e.response is not None:
            log.error(f"   Resposta: {e.response.text[:300]}")
        return False


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    log.info("══════════════════════════════════")
    log.info("  Monitor de Milhas v2 — iniciando")
    log.info("══════════════════════════════════")

    passagens = buscar_passagens()
    livelo    = buscar_promocoes_livelo()
    mensagem  = formatar_mensagem(passagens, livelo)

    log.info(f"\n{'='*40}\nMENSAGEM FINAL:\n{mensagem}\n{'='*40}\n")
    enviar_whatsapp(mensagem)

    log.info("══════════════════════════════════")
    log.info("  Finalizado                      ")
    log.info("══════════════════════════════════")


if __name__ == "__main__":
    main()
