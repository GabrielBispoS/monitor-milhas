"""
Monitor de Milhas e Passagens Aéreas — v5
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Passagens: Playwright → Google Flights (URL /search com tfs, não hash)
Livelo:    Playwright → páginas corretas identificadas via debug
  - Transferência: /livelo-para-parceiros/{prog}/{code} (por programa)
  - Varejo:        /turbo-livelo

Correções v5 (baseadas no HTML real do debug):
  - Google Flights: hash #flt= não funciona headless → usar /search com tfs
    e aguardar seletor real 'span.tVc44e' (preço em USD) + converter via cotação
    OU usar moeda BRL forçada via parâmetro curr=BRL no tfs
  - Livelo transferência: URL /ganhe-pontos/viagens/... retorna 404
    → URLs corretas: /livelo-para-parceiros/smiles/SMLTransfer etc.
  - Livelo varejo: /ganhe-pontos/compras-online retorna pouco conteúdo
    → URL correta: /turbo-livelo
"""

import os
import re
import json
import logging
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# SECRETS
# ──────────────────────────────────────────────────────────────
EVOLUTION_API_URL  = os.environ["EVOLUTION_API_URL"]
EVOLUTION_API_KEY  = os.environ["EVOLUTION_API_KEY"]
EVOLUTION_INSTANCE = os.environ["EVOLUTION_INSTANCE"]
WHATSAPP_NUMBER    = os.environ["WHATSAPP_NUMBER"]

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
PAR_A = [0, 2]
PAR_B = [1, 3]

# ──────────────────────────────────────────────────────────────
# LIVELO — URLs corretas (identificadas via debug do HTML real)
# ──────────────────────────────────────────────────────────────
PROGRAMAS_TRANSFER = [
    {"nome": "Smiles",      "url": "https://www.livelo.com.br/livelo-para-parceiros/smiles/SMLTransfer"},
    {"nome": "Tudo Azul",   "url": "https://www.livelo.com.br/livelo-para-parceiros/azul/AZLTransfer"},
    {"nome": "Latam Pass",  "url": "https://www.livelo.com.br/livelo-para-parceiros/latam/MTPTransfer"},
    {"nome": "Copa Miles",  "url": "https://www.livelo.com.br/livelo-para-parceiros/copa/COPTransfer"},
]
LIMIAR_BONUS_AEREO  = 30   # % mínimo
LIMIAR_BONUS_VAREJO = 8    # pts/R$ mínimo

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
# 1. PASSAGENS — Google Flights via /search (sem hash)
# ══════════════════════════════════════════════════════════════

def _url_flights(datas: dict) -> str:
    """
    URL de busca do Google Flights sem fragmento (#).
    Usa o formato /search com parâmetros diretos — funciona headless.
    Inclui gl=BR e curr=BRL para forçar moeda e região.
    """
    return (
        "https://www.google.com/travel/flights/search"
        f"?hl=pt-BR&gl=BR&curr=BRL"
        f"&q=voos+{ORIGEM}+{DESTINO}"
        f"+{datas['ida']}+{datas['volta']}"
        f"&adults={ADULTOS}"
    )


def _extrair_preco(texto: str) -> float | None:
    """Extrai valor numérico de 'R$ 1.234' ou 'US$ 234'."""
    texto = texto.replace("\xa0", "").replace(" ", "")
    # Remover símbolo de moeda
    texto = re.sub(r"[A-Z$R]+", "", texto)
    # Normalizar separadores brasileiros
    texto = texto.replace(".", "").replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", texto)
    return float(m.group()) if m else None


def buscar_voo_playwright(datas: dict, page) -> dict | None:
    label_volta = datas["volta"][8:10] + "/" + datas["volta"][5:7]
    url = _url_flights(datas)

    try:
        log.info(f"  [Flights] {datas['label']} → {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Aguardar preços renderizarem — tentar até 3x
        preco_sel = "span.tVc44e"
        for tentativa in range(3):
            page.wait_for_timeout(4000)
            count = page.locator(preco_sel).count()
            log.info(f"  [Flights {datas['label']}] tentativa {tentativa+1} — {count} preços")
            if count > 0:
                break

        # ── Coletar preços ─────────────────────────────────────
        precos_raw = page.locator(preco_sel).all_text_contents()
        log.info(f"  [Flights {datas['label']}] preços raw: {precos_raw[:5]}")

        precos = []
        for t in precos_raw:
            v = _extrair_preco(t)
            if v and 50 < v < 100000:
                precos.append(v)

        if not precos:
            # Fallback: qualquer span com valor monetário
            spans = page.locator("span").all_text_contents()
            for t in spans:
                if ("R$" in t or "US$" in t) and len(t) < 20:
                    v = _extrair_preco(t)
                    if v and 50 < v < 100000:
                        precos.append(v)
            log.info(f"  [Flights {datas['label']}] fallback spans: {precos[:5]}")

        if not precos:
            page.screenshot(path=f"/tmp/flights_{datas['label'].replace('/','')}.png")
            log.warning(f"  [Flights {datas['label']}] sem preços")
            return None

        # Preço base pode estar em USD — se < 500, provavelmente USD → converter
        preco = min(precos)
        moeda = "BRL"
        if preco < 300:
            # Buscar cotação do dólar
            try:
                # Frankfurter: gratuito, sem rate limit, sem API key
                cotacao = requests.get(
                    "https://api.frankfurter.app/latest?from=USD&to=BRL",
                    timeout=5
                ).json()["rates"]["BRL"]
                preco_brl = round(preco * float(cotacao) * ADULTOS, 0)
                log.info(f"  [Flights {datas['label']}] USD {preco} × {cotacao} × {ADULTOS} pax = R$ {preco_brl}")
                preco = preco_brl
                moeda = "USD→BRL"
            except Exception:
                preco = preco * ADULTOS  # estimativa sem cotação
                moeda = "USD≈BRL"

        # ── Companhia e duração ────────────────────────────────
        cias = []
        for sel in ["div.sSHqwe", "div.h1fkLb", "span[class*='airlin']"]:
            try:
                cias = [t for t in page.locator(sel).all_text_contents() if t.strip()]
                if cias: break
            except Exception:
                pass

        duracoes = []
        for sel in ["div.gvkrdb", "div.Ak5kof", "[aria-label*='hora']"]:
            try:
                duracoes = [t for t in page.locator(sel).all_text_contents() if t.strip()]
                if duracoes: break
            except Exception:
                pass

        escalas = 0
        for sel in ["div.EfT7Ae span", "div.ogfYpf"]:
            try:
                for t in page.locator(sel).all_text_contents():
                    if "escala" in t.lower():
                        m = re.search(r"(\d+)", t)
                        escalas = int(m.group(1)) if m else 1
                        break
            except Exception:
                pass

        return {
            "label":       datas["label"],
            "label_volta": label_volta,
            "ida":         datas["ida"],
            "volta":       datas["volta"],
            "preco":       preco,
            "cia":         cias[0].strip() if cias else "N/A",
            "destino":     DESTINO,
            "escalas":     escalas,
            "duracao":     duracoes[0].strip() if duracoes else "N/A",
            "moeda":       moeda,
        }

    except PWTimeout:
        log.warning(f"  [Flights {datas['label']}] timeout")
        return None
    except Exception as e:
        log.warning(f"  [Flights {datas['label']}] erro: {e}")
        return None


def buscar_passagens() -> list[dict]:
    dia = datetime.now().day
    indices  = PAR_A if dia % 2 == 0 else PAR_B
    par_nome = "A (07/08 e 21/08)" if indices == PAR_A else "B (14/08 e 28/08)"
    log.info(f"Dia {dia} → Par {par_nome}")

    resultados = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1280, "height": 900},
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()

        for i in indices:
            voo = buscar_voo_playwright(DATAS_VIAGEM[i], page)
            if voo:
                resultados.append(voo)
                log.info(f"  ✔ {voo['label']}: R$ {voo['preco']:.0f} ({voo['cia']})")
            else:
                log.warning(f"  ✘ {DATAS_VIAGEM[i]['label']}: sem resultado")

        browser.close()
    return resultados


# ══════════════════════════════════════════════════════════════
# 2. LIVELO — URLs corretas identificadas via debug
# ══════════════════════════════════════════════════════════════

def buscar_bonus_transferencia(page) -> list[dict]:
    """
    Acessa cada página de parceiro individualmente.
    URLs: /livelo-para-parceiros/{prog}/{code}
    """
    aereas = []
    for prog in PROGRAMAS_TRANSFER:
        try:
            log.info(f"  [Livelo] {prog['nome']} ...")
            page.goto(prog["url"], wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            html  = page.content()
            texto = page.evaluate("() => document.body.innerText").lower()

            # Buscar percentual de bônus no texto da página
            # Ex: "+40% de bônus", "bônus de 50%", "100% de bônus"
            pcts = re.findall(r"(?:bônus|bonus)[^\d]*(\d{2,3})\s*%|(\d{2,3})\s*%[^\d]*(?:bônus|bonus)", texto, re.I)
            pct_flat = [int(x) for pair in pcts for x in pair if x]

            # Também buscar no HTML direto
            if not pct_flat:
                pcts2 = re.findall(r"(\d{2,3})%", html)
                # Filtrar apenas valores razoáveis de bônus (30-200%)
                pct_flat = [int(x) for x in pcts2 if 30 <= int(x) <= 200]

            pct = max(pct_flat) if pct_flat else 0
            log.info(f"  [Livelo] {prog['nome']}: pct encontrado = {pct}%")

            if pct >= LIMIAR_BONUS_AEREO:
                aereas.append({"programa": prog["nome"], "bonus_pct": pct})
                log.info(f"  🔥 {prog['nome']}: +{pct}%")

        except Exception as e:
            log.warning(f"  [Livelo] {prog['nome']} erro: {e}")

    return aereas


def buscar_varejo_turbinado(page) -> list[dict]:
    """
    Acessa cada parceiro individualmente — URLs testadas e confirmadas 200 OK.
    Os dados de pts/R$ são renderizados via JS, capturados pelo Playwright.
    """
    varejo = []

    LOJAS = [
        ("Shopee",      "https://www.livelo.com.br/juntar-pontos/parceiros/shopee/PEE"),
        ("Natura",      "https://www.livelo.com.br/juntar-pontos/parceiros/natura/NTR"),
        ("Magalu",      "https://www.livelo.com.br/juntar-pontos/parceiros/magalu/MZL"),
        ("Carrefour Mercado", "https://www.livelo.com.br/juntar-pontos/parceiros/carrefour/CRM"),  # 7pts/R$ ✅
        ("Casas Bahia",       "https://www.livelo.com.br/juntar-pontos/parceiros/casas-bahia/CSB"),  # 5pts/R$ ✅
        ("Fast Shop",   "https://www.livelo.com.br/juntar-pontos/parceiros/fast-shop/FST"),
    ]

    for loja_nome, url in LOJAS:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            texto = page.evaluate("() => document.body.innerText").lower()
            log.info(f"  [Livelo varejo] {loja_nome}: {len(texto)} chars")

            # Padrões testados: "X pontos Livelo", "X pts/R$", "X pontos por real"
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
        ctx = browser.new_context(
            user_agent=USER_AGENT, locale="pt-BR",
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
        linhas.append("_Google Flights bloqueou. Ver logs._")
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
    linhas.append("_Playwright · Google Flights · Livelo_")
    return "\n".join(linhas)


# ══════════════════════════════════════════════════════════════
# 4. ENVIO
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
    log.info("  Monitor de Milhas v5")
    log.info("══════════════════════════════════")

    passagens = buscar_passagens()
    livelo    = buscar_promocoes_livelo()
    mensagem  = formatar_mensagem(passagens, livelo)

    log.info(f"\n{'='*40}\n{mensagem}\n{'='*40}")
    enviar_whatsapp(mensagem)
    log.info("══ Finalizado ══")


if __name__ == "__main__":
    main()
