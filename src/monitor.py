"""
Monitor de Milhas e Passagens Aéreas — v6
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Passagens: Amadeus Flight Offers Search API (GDS oficial, dados reais)
Livelo:    Playwright → livelo.com.br (bônus transferência + varejo)
Envio:     WhatsApp via Evolution API
Execução:  GitHub Actions, 1x ao dia às 08h00 BRT

Secrets necessários no GitHub:
  AMADEUS_CLIENT_ID      → API Key do app em developers.amadeus.com
  AMADEUS_CLIENT_SECRET  → API Secret do app em developers.amadeus.com
  EVOLUTION_API_URL      → http://137.131.236.152:8080
  EVOLUTION_API_KEY      → monitor-milhas-key
  EVOLUTION_INSTANCE     → monitor-milhas
  WHATSAPP_NUMBER        → 55349XXXXXXXX
"""

import os
import re
import logging
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# SECRETS
# ──────────────────────────────────────────────────────────────
AMADEUS_CLIENT_ID     = os.environ["AMADEUS_CLIENT_ID"]
AMADEUS_CLIENT_SECRET = os.environ["AMADEUS_CLIENT_SECRET"]
EVOLUTION_API_URL     = os.environ["EVOLUTION_API_URL"]
EVOLUTION_API_KEY     = os.environ["EVOLUTION_API_KEY"]
EVOLUTION_INSTANCE    = os.environ["EVOLUTION_INSTANCE"]
WHATSAPP_NUMBER       = os.environ["WHATSAPP_NUMBER"]

# ──────────────────────────────────────────────────────────────
# PARÂMETROS DE VIAGEM
# ──────────────────────────────────────────────────────────────
ORIGEM     = "UDI"
DESTINO    = "GIG"
ADULTOS    = 2
PRECO_ALVO = 1500.00

DATAS_VIAGEM = [
    {"ida": "2026-08-07", "volta": "2026-08-10", "label": "07/08"},
    {"ida": "2026-08-14", "volta": "2026-08-17", "label": "14/08"},
    {"ida": "2026-08-21", "volta": "2026-08-24", "label": "21/08"},
    {"ida": "2026-08-28", "volta": "2026-08-31", "label": "28/08"},
]
PAR_A = [0, 2]  # dias pares
PAR_B = [1, 3]  # dias ímpares

# ──────────────────────────────────────────────────────────────
# LIVELO
# ──────────────────────────────────────────────────────────────
PROGRAMAS_TRANSFER = [
    {"nome": "Smiles",     "url": "https://www.livelo.com.br/livelo-para-parceiros/smiles/SMLTransfer"},
    {"nome": "Tudo Azul",  "url": "https://www.livelo.com.br/livelo-para-parceiros/azul/AZLTransfer"},
    {"nome": "Latam Pass", "url": "https://www.livelo.com.br/livelo-para-parceiros/latam/MTPTransfer"},
    {"nome": "Copa Miles", "url": "https://www.livelo.com.br/livelo-para-parceiros/copa/COPTransfer"},
]
LOJAS_VAREJO = [
    ("Shopee",           "https://www.livelo.com.br/juntar-pontos/parceiros/shopee/PEE"),
    ("Natura",           "https://www.livelo.com.br/juntar-pontos/parceiros/natura/NTR"),
    ("Magalu",           "https://www.livelo.com.br/juntar-pontos/parceiros/magalu/MZL"),
    ("Carrefour Mercado","https://www.livelo.com.br/juntar-pontos/parceiros/carrefour/CRM"),
    ("Casas Bahia",      "https://www.livelo.com.br/juntar-pontos/parceiros/casas-bahia/CSB"),
    ("Fast Shop",        "https://www.livelo.com.br/juntar-pontos/parceiros/fast-shop/FST"),
]
LIMIAR_BONUS_AEREO  = 30
LIMIAR_BONUS_VAREJO = 5   # baixado de 8 para capturar Carrefour(7) e Casas Bahia(5)

BROWSER_ARGS = [
    "--no-sandbox", "--disable-setuid-sandbox",
    "--disable-dev-shm-usage", "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
]
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ══════════════════════════════════════════════════════════════
# 1. PASSAGENS — Amadeus Flight Offers Search API
# ══════════════════════════════════════════════════════════════

def amadeus_get_token() -> str | None:
    """
    Obtém access token OAuth2 da Amadeus.
    Token dura 30 minutos — suficiente para o script inteiro.
    """
    try:
        r = requests.post(
            "https://test.api.amadeus.com/v1/security/oauth2/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     AMADEUS_CLIENT_ID,
                "client_secret": AMADEUS_CLIENT_SECRET,
            },
            timeout=15,
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        log.info("  [Amadeus] token obtido ✅")
        return token
    except Exception as e:
        log.error(f"  [Amadeus] erro ao obter token: {e}")
        return None


def buscar_voo_amadeus(datas: dict, token: str) -> dict | None:
    """
    Busca o voo mais barato UDI→GIG para um par de datas via Amadeus.
    Endpoint: GET /v2/shopping/flight-offers
    Retorna preço real em BRL, cia aérea, escalas e duração.
    """
    label_volta = datas["volta"][8:10] + "/" + datas["volta"][5:7]

    try:
        log.info(f"  [Amadeus] buscando {datas['label']} ...")
        r = requests.get(
            "https://test.api.amadeus.com/v2/shopping/flight-offers",
            params={
                "originLocationCode":      ORIGEM,
                "destinationLocationCode": DESTINO,
                "departureDate":           datas["ida"],
                "returnDate":              datas["volta"],
                "adults":                  ADULTOS,
                "currencyCode":            "BRL",
                "max":                     10,  # top 10 para pegar o mais barato
                "nonStop":                 "false",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )

        if r.status_code == 400:
            log.warning(f"  [Amadeus {datas['label']}] 400 — rota sem dados no sandbox: {r.text[:150]}")
            return None

        r.raise_for_status()
        data = r.json()

        ofertas = data.get("data", [])
        log.info(f"  [Amadeus {datas['label']}] {len(ofertas)} ofertas retornadas")

        if not ofertas:
            return None

        # ── Pegar a oferta mais barata ─────────────────────────
        melhor = min(ofertas, key=lambda o: float(o["price"]["grandTotal"]))
        preco  = float(melhor["price"]["grandTotal"])

        # ── Extrair dados do itinerário de ida ─────────────────
        itinerario_ida   = melhor["itineraries"][0]
        segments_ida     = itinerario_ida["segments"]
        primeiro_seg     = segments_ida[0]
        ultimo_seg       = segments_ida[-1]

        cia              = primeiro_seg["carrierCode"]
        escalas          = len(segments_ida) - 1
        duracao_raw      = itinerario_ida["duration"]  # ex: "PT2H30M"
        duracao          = _formatar_duracao(duracao_raw)

        # Nome da cia via dicionário (cias brasileiras principais)
        cias_nomes = {
            "G3": "Gol", "LA": "LATAM", "AD": "Azul",
            "JJ": "LATAM", "O6": "Decolar", "TP": "TAP",
        }
        cia_nome = cias_nomes.get(cia, cia)

        log.info(f"  [Amadeus {datas['label']}] R$ {preco:.0f} | {cia_nome} | {escalas} escala(s) | {duracao}")

        return {
            "label":       datas["label"],
            "label_volta": label_volta,
            "ida":         datas["ida"],
            "volta":       datas["volta"],
            "preco":       preco,
            "cia":         cia_nome,
            "destino":     ultimo_seg["arrival"]["iataCode"],
            "escalas":     escalas,
            "duracao":     duracao,
        }

    except Exception as e:
        log.warning(f"  [Amadeus {datas['label']}] erro: {e}")
        return None


def _formatar_duracao(iso: str) -> str:
    """Converte 'PT2H30M' → '2h30'."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return iso
    horas  = int(m.group(1) or 0)
    minutos = int(m.group(2) or 0)
    return f"{horas}h{minutos:02d}"


def buscar_passagens() -> list[dict]:
    dia      = datetime.now().day
    indices  = PAR_A if dia % 2 == 0 else PAR_B
    par_nome = "A (07/08 e 21/08)" if indices == PAR_A else "B (14/08 e 28/08)"
    log.info(f"Dia {dia} → Par {par_nome}")

    token = amadeus_get_token()
    if not token:
        log.error("Sem token Amadeus — abortando busca de passagens")
        return []

    resultados = []
    for i in indices:
        voo = buscar_voo_amadeus(DATAS_VIAGEM[i], token)
        if voo:
            resultados.append(voo)
            log.info(f"  ✔ {voo['label']}: R$ {voo['preco']:.0f} ({voo['cia']})")
        else:
            log.warning(f"  ✘ {DATAS_VIAGEM[i]['label']}: sem resultado")

    return resultados


# ══════════════════════════════════════════════════════════════
# 2. LIVELO — Playwright (URLs testadas e confirmadas)
# ══════════════════════════════════════════════════════════════

def buscar_bonus_transferencia(page) -> list[dict]:
    aereas = []
    for prog in PROGRAMAS_TRANSFER:
        try:
            log.info(f"  [Livelo] {prog['nome']} ...")
            page.goto(prog["url"], wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            html  = page.content()
            texto = page.evaluate("() => document.body.innerText").lower()

            # Buscar % próximo a "bônus" no texto renderizado pelo JS
            pcts = re.findall(
                r"(?:bônus|bonus)[^\d]{0,30}(\d{2,3})\s*%"
                r"|(\d{2,3})\s*%[^\d]{0,30}(?:bônus|bonus|a mais|extra)",
                texto, re.I
            )
            pct_flat = [int(x) for pair in pcts for x in pair if x]

            # Fallback: qualquer % razoável no HTML
            if not pct_flat:
                pcts2    = re.findall(r"(\d{2,3})%", html)
                pct_flat = [int(x) for x in pcts2 if 30 <= int(x) <= 200]

            pct = max(pct_flat) if pct_flat else 0
            log.info(f"  [Livelo] {prog['nome']}: {pct}%")

            if pct >= LIMIAR_BONUS_AEREO:
                aereas.append({"programa": prog["nome"], "bonus_pct": pct})
                log.info(f"  🔥 {prog['nome']}: +{pct}%")

        except Exception as e:
            log.warning(f"  [Livelo] {prog['nome']} erro: {e}")

    return aereas


def buscar_varejo_turbinado(page) -> list[dict]:
    varejo = []
    for loja_nome, url in LOJAS_VAREJO:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            texto = page.evaluate("() => document.body.innerText").lower()
            log.info(f"  [Livelo varejo] {loja_nome}: {len(texto)} chars")

            padroes = [
                r"(\d+)\s*pontos?\s*livelo",
                r"(\d+)\s*(?:pts?|pontos?)\s*(?:/|por)\s*r?\$?\s*1?",
                r"ganhe\s*(\d+)\s*pontos?",
                r"até\s*(\d+)\s*pontos?",
            ]
            pts = 0
            for p in padroes:
                m = re.search(p, texto)
                if m:
                    pts = int(m.group(1))
                    break

            log.info(f"  [Livelo varejo] {loja_nome}: {pts} pts/R$")
            if pts >= LIMIAR_BONUS_VAREJO:
                varejo.append({"loja": loja_nome, "pontos_por_real": pts})
                log.info(f"  ⭐ {loja_nome}: {pts}pts/R$")

        except Exception as e:
            log.warning(f"  [Livelo varejo] {loja_nome} erro: {e}")

    return varejo


def buscar_promocoes_livelo() -> dict:
    resultado = {"aereas": [], "varejo": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
        ctx     = browser.new_context(
            user_agent=USER_AGENT,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()

        resultado["aereas"] = buscar_bonus_transferencia(page)
        resultado["varejo"] = buscar_varejo_turbinado(page)

        browser.close()
    return resultado


# ══════════════════════════════════════════════════════════════
# 3. FORMATAR MENSAGEM
# ══════════════════════════════════════════════════════════════

def formatar_mensagem(passagens: list[dict], livelo: dict) -> str:
    hoje   = datetime.now().strftime("%d/%m %H:%M")
    linhas = []

    linhas.append(f"🛫 *Monitor de Milhas* — {hoje}")
    linhas.append("─" * 30)
    linhas.append("")
    linhas.append("✈️ *PASSAGENS UDI → RIO*")
    linhas.append("_2 adultos · agosto/2026_")

    if not passagens:
        linhas.append("")
        linhas.append("⚠️ Nenhuma passagem encontrada.")
        linhas.append("_Verificar AMADEUS\\_CLIENT\\_ID nos Secrets._")
    else:
        melhor = min(passagens, key=lambda p: p["preco"])
        linhas.append("")
        for p in sorted(passagens, key=lambda p: p["ida"]):
            emoji = "🟢" if p["preco"] <= PRECO_ALVO else "🔴"
            esc   = "direto" if p["escalas"] == 0 else f"{p['escalas']} escala{'s' if p['escalas'] > 1 else ''}"
            linhas.append(
                f"{emoji} *{p['label']} → {p['label_volta']}*\n"
                f"   💰 *R$ {p['preco']:.0f}* (2 pax)\n"
                f"   🏢 {p['cia']}  |  🛬 {p['destino']}  |  ⏱ {p['duracao']}  |  {esc}"
            )
        linhas.append("")
        if melhor["preco"] <= PRECO_ALVO:
            linhas.append(f"🚨 *COMPRAR AGORA!*\n   {melhor['label']} por *R$ {melhor['preco']:.0f}* — abaixo da meta!")
        else:
            diff = melhor["preco"] - PRECO_ALVO
            linhas.append(f"⏳ *Melhor:* R$ {melhor['preco']:.0f} ({melhor['label']}) — faltam R$ {diff:.0f}")

    linhas.append("")
    linhas.append("─" * 30)
    linhas.append("")
    linhas.append("🏦 *BÔNUS LIVELO → AÉREAS*")
    if livelo["aereas"]:
        linhas.append("")
        for a in livelo["aereas"]:
            linhas.append(f"🔥 *{a['programa']}:* +{a['bonus_pct']}% de bônus")
        linhas.append("_⚡ Transferir hoje!_")
    else:
        linhas.append("😴 Sem bônus relevantes hoje.")

    linhas.append("")
    linhas.append("🛍️ *VAREJO TURBINADO LIVELO*")
    if livelo["varejo"]:
        linhas.append("")
        for v in livelo["varejo"]:
            linhas.append(f"⭐ *{v['loja']}:* {v['pontos_por_real']} pts/R$")
    else:
        linhas.append(f"📊 Nada acima de {LIMIAR_BONUS_VAREJO} pts/R$ hoje.")

    linhas.append("")
    linhas.append("─" * 30)
    linhas.append("_Amadeus API · Livelo · Evolution API_")
    return "\n".join(linhas)


# ══════════════════════════════════════════════════════════════
# 4. ENVIO — Evolution API
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
        log.error(f"❌ Erro WhatsApp: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    log.info("══════════════════════════════════")
    log.info("  Monitor de Milhas v6 — Amadeus")
    log.info("══════════════════════════════════")

    passagens = buscar_passagens()
    livelo    = buscar_promocoes_livelo()
    mensagem  = formatar_mensagem(passagens, livelo)

    log.info(f"\n{'='*40}\n{mensagem}\n{'='*40}")
    enviar_whatsapp(mensagem)
    log.info("══ Finalizado ══")


if __name__ == "__main__":
    main()
