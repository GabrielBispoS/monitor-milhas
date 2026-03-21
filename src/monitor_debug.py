"""
Monitor de Milhas — modo DEBUG
Salva HTML e screenshot de cada página em /tmp/debug/
para inspecionar seletores reais.
Rode UMA VEZ no Actions para coletar os HTMLs.
"""

import os, re, logging
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

EVOLUTION_API_URL  = os.environ["EVOLUTION_API_URL"]
EVOLUTION_API_KEY  = os.environ["EVOLUTION_API_KEY"]
EVOLUTION_INSTANCE = os.environ["EVOLUTION_INSTANCE"]
WHATSAPP_NUMBER    = os.environ["WHATSAPP_NUMBER"]

BROWSER_ARGS = ["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--disable-gpu","--disable-blink-features=AutomationControlled"]
USER_AGENT   = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

os.makedirs("/tmp/debug", exist_ok=True)

def debug_page(page, nome: str):
    """Salva HTML + screenshot + todos os textos visíveis."""
    html = page.content()
    with open(f"/tmp/debug/{nome}.html", "w") as f:
        f.write(html)
    page.screenshot(path=f"/tmp/debug/{nome}.png", full_page=True)

    # Logar TODOS os textos que contêm R$ ou %
    all_texts = page.locator("*").all_text_contents()
    reais  = [t.strip() for t in all_texts if "R$" in t and len(t.strip()) < 30]
    pcts   = [t.strip() for t in all_texts if "%" in t and len(t.strip()) < 30]
    progs  = [t.strip() for t in all_texts if any(p in t.lower() for p in ["smiles","latam","azul","tap","gol"]) and len(t.strip()) < 60]

    log.info(f"  [{nome}] textos com R$: {reais[:10]}")
    log.info(f"  [{nome}] textos com %: {pcts[:10]}")
    log.info(f"  [{nome}] programas: {progs[:10]}")

    # Logar classes únicas que aparecem no HTML (para achar seletores)
    import re as _re
    classes = list(set(_re.findall(r'class="([^"]{3,30})"', html)))[:30]
    log.info(f"  [{nome}] amostra de classes CSS: {classes[:20]}")

    return html

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
    ctx = browser.new_context(
        user_agent=USER_AGENT, locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        viewport={"width": 1280, "height": 900},
    )
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    page = ctx.new_page()

    # ── Google Flights ────────────────────────────────────────
    url_flights = (
        "https://www.google.com/travel/flights"
        "#flt=UDI.GIG.2026-08-14*GIG.UDI.2026-08-17"
        ";c:BRL;e:2;sd:1;t:f;tt:o"
    )
    log.info("Abrindo Google Flights...")
    try:
        page.goto(url_flights, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        html_flights = debug_page(page, "google_flights")
        log.info(f"  HTML salvo: {len(html_flights)} chars")
    except Exception as e:
        log.error(f"  Google Flights erro: {e}")

    # ── Livelo transferência ───────────────────────────────────
    log.info("Abrindo Livelo transferência...")
    try:
        page.goto("https://www.livelo.com.br/ganhe-pontos/viagens/transferencia-de-pontos",
                  wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(4000)
        html_livelo = debug_page(page, "livelo_transferencia")
        log.info(f"  HTML salvo: {len(html_livelo)} chars")
    except Exception as e:
        log.error(f"  Livelo erro: {e}")

    browser.close()

# Enviar resumo do debug via WhatsApp
msg = (
    "🔍 *DEBUG Monitor de Milhas*\n\n"
    "HTMLs salvos em /tmp/debug/ como artefato do Actions.\n"
    "Verifique: google_flights.html e livelo_transferencia.html\n"
    "para identificar os seletores corretos."
)
try:
    r = requests.post(
        f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}",
        json={"number": WHATSAPP_NUMBER, "textMessage": {"text": msg}},
        headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
        timeout=15,
    )
    log.info("✅ WhatsApp debug enviado")
except Exception as e:
    log.error(f"WhatsApp erro: {e}")
