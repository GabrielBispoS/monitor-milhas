"""
Monitor de Milhas e Passagens Aéreas
Livelo (bônus de transferência + pontuação varejo) + Passagens UDI→RIO
Roda via GitHub Actions às 08h10 (BRT) todos os dias
"""

import os
import json
import requests
from datetime import datetime, date
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# CONFIGURAÇÕES
# ──────────────────────────────────────────────
AMADEUS_CLIENT_ID     = os.environ["AMADEUS_CLIENT_ID"]
AMADEUS_CLIENT_SECRET = os.environ["AMADEUS_CLIENT_SECRET"]

EVOLUTION_API_URL     = os.environ["EVOLUTION_API_URL"]      # ex: https://sua-evolution.com
EVOLUTION_API_KEY     = os.environ["EVOLUTION_API_KEY"]
EVOLUTION_INSTANCE    = os.environ["EVOLUTION_INSTANCE"]      # nome da instância
WHATSAPP_NUMBER       = os.environ["WHATSAPP_NUMBER"]         # ex: 5534999999999

# Parâmetros de viagem
ORIGEM        = "UDI"
DESTINO_LIST  = ["SDU", "GIG"]
ADULTOS       = 2
PRECO_ALVO    = 1500.00   # abaixo disso → alerta de compra imediata

DATAS_VIAGEM = [
    {"ida": "2026-08-07", "volta": "2026-08-10"},
    {"ida": "2026-08-14", "volta": "2026-08-17"},
    {"ida": "2026-08-21", "volta": "2026-08-24"},
    {"ida": "2026-08-28", "volta": "2026-08-31"},
]

# Parceiros Livelo monitorados — palavras-chave buscadas nos blogs/agregadores
PARCEIROS_VAREJO = [
    "natura", "shopee", "casas bahia", "fast shop",
    "carrefour", "magalu", "insider", "americanas"
]

BONUS_AEREAS = {
    "smiles": ["smiles", "gol"],
    "tudo azul": ["azul", "tudo azul"],
    "latam pass": ["latam pass", "latam"],
    "tap miles&go": ["tap"],
}

LIMIAR_BONUS_AEREO   = 40    # % mínimo para alertar
LIMIAR_BONUS_VAREJO  = 8     # pontos/R$ mínimo para alertar

# ──────────────────────────────────────────────
# AMADEUS — TOKEN
# ──────────────────────────────────────────────
def get_amadeus_token() -> str:
    r = requests.post(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": AMADEUS_CLIENT_ID,
            "client_secret": AMADEUS_CLIENT_SECRET,
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]


# ──────────────────────────────────────────────
# AMADEUS — BUSCA DE VOOS
# ──────────────────────────────────────────────
def buscar_voos(token: str, ida: str, volta: str, destino: str) -> dict | None:
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "originLocationCode":      ORIGEM,
        "destinationLocationCode": destino,
        "departureDate":           ida,
        "returnDate":              volta,
        "adults":                  ADULTOS,
        "currencyCode":            "BRL",
        "max":                     5,
    }
    try:
        r = requests.get(
            "https://test.api.amadeus.com/v2/shopping/flight-offers",
            headers=headers,
            params=params,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("data"):
            oferta = data["data"][0]
            preco  = float(oferta["price"]["grandTotal"])
            cia    = oferta["validatingAirlineCodes"][0] if oferta.get("validatingAirlineCodes") else "N/A"
            return {"preco": preco, "cia": cia, "destino": destino, "ida": ida, "volta": volta}
    except Exception as e:
        log.warning(f"Erro buscando {ORIGEM}→{destino} em {ida}: {e}")
    return None


def buscar_todas_passagens(token: str) -> list[dict]:
    resultados = []
    for datas in DATAS_VIAGEM:
        melhor = None
        for dest in DESTINO_LIST:
            r = buscar_voos(token, datas["ida"], datas["volta"], dest)
            if r and (melhor is None or r["preco"] < melhor["preco"]):
                melhor = r
        if melhor:
            resultados.append(melhor)
    return resultados


# ──────────────────────────────────────────────
# LIVELO — WEB SCRAPING VIA BUSCA (sem credenciais)
# Usa a API pública do Google Custom Search / DuckDuckGo Instant
# como fallback gratuito para detectar promoções
# ──────────────────────────────────────────────
def buscar_promocoes_livelo() -> dict:
    """
    Busca notícias/posts sobre promoções Livelo via DuckDuckGo Instant Answer API.
    Retorna dicionário com achados por categoria.
    """
    resultado = {
        "aereas": [],
        "varejo": [],
        "raw_snippets": [],
    }

    queries = [
        "site:livelo.com.br bônus transferência pontos 2026",
        "livelo bônus smiles latam azul transferência hoje",
        "livelo pontuação turbinada shopee casas bahia natura hoje",
    ]

    for q in queries:
        try:
            r = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": q, "format": "json", "no_html": 1, "skip_disambig": 1},
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            data = r.json()
            abstract = data.get("AbstractText", "").lower()
            related  = " ".join([t.get("Text", "") for t in data.get("RelatedTopics", [])]).lower()
            texto    = abstract + " " + related
            resultado["raw_snippets"].append(texto[:500])

            # Detectar bônus aéreos
            for programa, keywords in BONUS_AEREAS.items():
                for kw in keywords:
                    if kw in texto:
                        # Tenta extrair percentual
                        import re
                        match = re.search(r"(\d+)%", texto)
                        pct = int(match.group(1)) if match else 0
                        if pct >= LIMIAR_BONUS_AEREO:
                            resultado["aereas"].append({
                                "programa": programa.upper(),
                                "bonus_pct": pct,
                                "fonte": q,
                            })

            # Detectar varejo turbinado
            for parceiro in PARCEIROS_VAREJO:
                if parceiro in texto:
                    import re
                    match = re.search(r"(\d+)\s*(?:pontos?|pts?)\s*/?\s*r\$", texto)
                    pts = int(match.group(1)) if match else 0
                    if pts >= LIMIAR_BONUS_VAREJO:
                        resultado["varejo"].append({
                            "loja": parceiro.title(),
                            "pontos_por_real": pts,
                            "fonte": q,
                        })
        except Exception as e:
            log.warning(f"Erro na busca Livelo '{q}': {e}")

    # Deduplica
    resultado["aereas"] = list({v["programa"]: v for v in resultado["aereas"]}.values())
    resultado["varejo"] = list({v["loja"]: v for v in resultado["varejo"]}.values())
    return resultado


# ──────────────────────────────────────────────
# FORMATAÇÃO DA MENSAGEM WHATSAPP
# ──────────────────────────────────────────────
def formatar_mensagem(passagens: list[dict], livelo: dict) -> str:
    hoje = datetime.now().strftime("%d/%m/%Y %H:%M")
    linhas = [f"🤖 *Monitor de Milhas — {hoje}*\n"]

    # ── PASSAGENS ──
    linhas.append("✈️ *PASSAGENS UDI → RIO (2 adultos)*")
    melhor_geral = None
    for p in passagens:
        emoji = "🟢" if p["preco"] <= PRECO_ALVO else "🟡"
        linhas.append(
            f"{emoji} {p['ida']} → {p['volta']} | *R$ {p['preco']:.0f}* | {p['cia']} → {p['destino']}"
        )
        if melhor_geral is None or p["preco"] < melhor_geral["preco"]:
            melhor_geral = p

    if melhor_geral:
        if melhor_geral["preco"] <= PRECO_ALVO:
            linhas.append(f"\n🚨 *COMPRA RECOMENDADA!* Casal abaixo de R$ {PRECO_ALVO:.0f}!")
        else:
            linhas.append(f"\n⏳ Melhor preço: R$ {melhor_geral['preco']:.0f} — aguardar queda para R$ {PRECO_ALVO:.0f}")

    linhas.append("")

    # ── LIVELO AÉREAS ──
    linhas.append("🏦 *BÔNUS DE TRANSFERÊNCIA LIVELO*")
    if livelo["aereas"]:
        for a in livelo["aereas"]:
            linhas.append(f"🔥 {a['programa']}: *+{a['bonus_pct']}% de bônus* — TRANSFERIR HOJE!")
    else:
        linhas.append("😴 Sem bônus relevantes detectados. Aguardar.")

    linhas.append("")

    # ── LIVELO VAREJO ──
    linhas.append("🛍️ *PONTUAÇÃO TURBINADA — VAREJO*")
    if livelo["varejo"]:
        for v in livelo["varejo"]:
            linhas.append(f"⭐ {v['loja']}: *{v['pontos_por_real']}pts/R$*")
    else:
        linhas.append("📊 Sem promoções de varejo acima de {LIMIAR_BONUS_VAREJO}pts/R$ hoje.")

    linhas.append("\n_Monitor automático via GitHub Actions_")
    return "\n".join(linhas)


# ──────────────────────────────────────────────
# ENVIO WHATSAPP — EVOLUTION API
# ──────────────────────────────────────────────
def enviar_whatsapp(mensagem: str) -> bool:
    url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    payload = {
        "number": WHATSAPP_NUMBER,
        "text": mensagem,
    }
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        log.info("✅ WhatsApp enviado com sucesso!")
        return True
    except Exception as e:
        log.error(f"❌ Erro ao enviar WhatsApp: {e}")
        return False


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    log.info("=== Iniciando Monitor de Milhas ===")

    # 1. Passagens
    log.info("Buscando passagens via Amadeus...")
    token = get_amadeus_token()
    passagens = buscar_todas_passagens(token)
    log.info(f"Passagens encontradas: {len(passagens)}")

    # 2. Livelo
    log.info("Buscando promoções Livelo...")
    livelo = buscar_promocoes_livelo()
    log.info(f"Bônus aéreos: {len(livelo['aereas'])} | Varejo: {len(livelo['varejo'])}")

    # 3. Montar e enviar mensagem
    mensagem = formatar_mensagem(passagens, livelo)
    log.info(f"\n{mensagem}")
    enviar_whatsapp(mensagem)

    log.info("=== Monitor finalizado ===")


if __name__ == "__main__":
    main()
