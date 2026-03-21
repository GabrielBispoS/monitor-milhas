"""
Monitor de Milhas e Passagens Aéreas
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Passagens: Playwright → Google Flights (UDI→GIG, 2 adultos, agosto/2026)
Livelo:    Playwright → livelo.com.br (bônus transferência + varejo)
Envio:     WhatsApp via Evolution API
Execução:  GitHub Actions, 1x ao dia às 08h00 BRT

v4 — migração total para Playwright (zero dependência de API key paga)
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

PAR_A = [0, 2]  # dias pares
PAR_B = [1, 3]  # dias ímpares

# ──────────────────────────────────────────────────────────────
# PARÂMETROS LIVELO
# ──────────────────────────────────────────────────────────────
PROGRAMAS_AEREOS    = ["smiles", "tudo azul", "latam pass", "tap"]
PARCEIROS_VAREJO    = ["natura", "shopee", "casas bahia", "fast shop",
                       "carrefour", "magalu", "americanas"]
LIMIAR_BONUS_AEREO  = 30   # % mínimo
LIMIAR_BONUS_VAREJO = 8    # pts/R$ mínimo


# ══════════════════════════════════════════════════════════════
# BROWSER — configuração compartilhada
# ══════════════════════════════════════════════════════════════

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
]

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ══════════════════════════════════════════════════════════════
# 1. PASSAGENS — Playwright → Google Flights
# ══════════════════════════════════════════════════════════════

def _url_google_flights(datas: dict) -> str:
    """Monta URL direta do Google Flights para a rota/datas."""
    return (
        f"https://www.google.com/travel/flights/search"
        f"?hl=pt-BR&gl=BR&curr=BRL"
        f"&q=voos+de+{ORIGEM}+para+{DESTINO}"
        f"&tfs=CBwQAhoe"   # token base de ida e volta
        f"&outbound_date={datas['ida']}"
        f"&return_date={datas['volta']}"
        f"&adults={ADULTOS}"
    )


def _parse_preco(texto: str) -> float | None:
    """Extrai valor numérico de strings como 'R$\xa01.234' ou 'R$ 980'."""
    texto = texto.replace("\xa0", "").replace(" ", "").replace(".", "").replace(",", ".")
    match = re.search(r"[\d]+(?:\.\d+)?", texto)
    return float(match.group()) if match else None


def buscar_voo_playwright(datas: dict, page) -> dict | None:
    """Faz scraping do Google Flights para um par de datas."""
    label_volta = datas["volta"][8:10] + "/" + datas["volta"][5:7]

    # URL alternativa mais compatível com scraping headless
    url = (
        f"https://www.google.com/travel/flights"
        f"#flt={ORIGEM}.{DESTINO}.{datas['ida']}"
        f"*{DESTINO}.{ORIGEM}.{datas['volta']}"
        f";c:BRL;e:{ADULTOS};sd:1;t:f;tt:o"
    )

    try:
        log.info(f"  [Flights] carregando {datas['label']} ...")
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(5000)  # aguardar JS renderizar preços

        # ── Tentar capturar preços via múltiplos seletores ──────
        seletores_preco = [
            'div[class*="FpEdX"] span',       # preço principal
            'div[class*="YMlIz"] span',       # preço alternativo
            'span[class*="n5zSZb"]',          # preço compacto
            '[aria-label*="R$"]',             # acessibilidade
            'div[class*="U3gSDe"] span',      # card de voo
        ]

        textos_preco = []
        for sel in seletores_preco:
            try:
                itens = page.locator(sel).all_text_contents()
                textos_preco.extend([t for t in itens if "R$" in t or re.search(r"\d{3,}", t)])
            except Exception:
                pass

        log.info(f"  [Flights {datas['label']}] textos capturados: {textos_preco[:8]}")

        # ── Companhia e duração ────────────────────────────────
        cias = []
        for sel in ['div[class*="sSHqwe"]', 'div[class*="h1fkLb"]', '[data-airline]']:
            try:
                cias = page.locator(sel).all_text_contents()
                if cias:
                    break
            except Exception:
                pass

        duracoes = []
        for sel in ['div[class*="gvkrdb"]', 'div[class*="Ak5kof"]', '[aria-label*="hora"]']:
            try:
                duracoes = page.locator(sel).all_text_contents()
                if duracoes:
                    break
            except Exception:
                pass

        escalas_txt = []
        for sel in ['div[class*="EfT7Ae"] span', 'div[class*="ogfYpf"]']:
            try:
                escalas_txt = page.locator(sel).all_text_contents()
                if escalas_txt:
                    break
            except Exception:
                pass

        # ── Parsear menor preço encontrado ─────────────────────
        precos = []
        for t in textos_preco:
            v = _parse_preco(t)
            if v and 100 < v < 50000:   # sanity check
                precos.append(v)

        if not precos:
            log.warning(f"  [Flights {datas['label']}] nenhum preço encontrado")
            # Screenshot para debug no Actions
            try:
                page.screenshot(path=f"/tmp/debug_{datas['label'].replace('/','-')}.png")
            except Exception:
                pass
            return None

        preco = min(precos)
        cia     = cias[0].strip() if cias else "N/A"
        duracao = duracoes[0].strip() if duracoes else "N/A"

        # Contar escalas
        escalas = 0
        for t in escalas_txt:
            if "escala" in t.lower():
                m = re.search(r"(\d+)", t)
                escalas = int(m.group(1)) if m else 1
                break

        return {
            "label":       datas["label"],
            "label_volta": label_volta,
            "ida":         datas["ida"],
            "volta":       datas["volta"],
            "preco":       preco,
            "cia":         cia,
            "destino":     DESTINO,
            "escalas":     escalas,
            "duracao":     duracao,
        }

    except PWTimeout:
        log.warning(f"  [Flights {datas['label']}] timeout")
        return None
    except Exception as e:
        log.warning(f"  [Flights {datas['label']}] erro: {e}")
        return None


def buscar_passagens() -> list[dict]:
    dia_do_mes = datetime.now().day
    indices    = PAR_A if dia_do_mes % 2 == 0 else PAR_B
    par_nome   = "A (07/08 e 21/08)" if indices == PAR_A else "B (14/08 e 28/08)"
    log.info(f"Dia {dia_do_mes} → Par {par_nome}")

    resultados = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1280, "height": 900},
        )
        # Anti-detecção básica
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)
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
# 2. LIVELO — Playwright → livelo.com.br
# ══════════════════════════════════════════════════════════════

def buscar_bonus_transferencia(page) -> list[dict]:
    """Scrapa página de transferência de pontos da Livelo."""
    url = "https://www.livelo.com.br/ganhe-pontos/viagens/transferencia-de-pontos"
    aereas = []
    try:
        log.info("  [Livelo] carregando página de transferência ...")
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(4000)

        html = page.content().lower()

        # Seletores para cards de bônus
        seletores = [
            '[class*="bonus"]',
            '[class*="transfer"]',
            '[class*="campanha"]',
            '[class*="program"]',
            'article',
            '.card',
        ]

        texto_completo = ""
        for sel in seletores:
            try:
                itens = page.locator(sel).all_text_contents()
                texto_completo += " ".join(itens).lower()
            except Exception:
                pass

        if not texto_completo:
            texto_completo = html

        log.info(f"  [Livelo transferência] texto extraído: {len(texto_completo)} chars")

        # Detectar programas e percentuais
        for programa in PROGRAMAS_AEREOS:
            if programa in texto_completo:
                # Buscar % próximo ao programa
                idx = texto_completo.find(programa)
                trecho = texto_completo[max(0, idx-100):idx+200]
                match = re.search(r"(\d{2,3})\s*%", trecho)
                pct = int(match.group(1)) if match else 0
                if pct >= LIMIAR_BONUS_AEREO:
                    aereas.append({
                        "programa": programa.title(),
                        "bonus_pct": pct,
                    })
                    log.info(f"  🔥 {programa.title()}: +{pct}%")

    except Exception as e:
        log.warning(f"  [Livelo transferência] erro: {e}")

    return aereas


def buscar_varejo_turbinado(page) -> list[dict]:
    """Scrapa página de compras online da Livelo."""
    url = "https://www.livelo.com.br/ganhe-pontos/compras-online"
    varejo = []
    try:
        log.info("  [Livelo] carregando página de compras online ...")
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(4000)

        texto_completo = ""
        seletores = ['[class*="store"]', '[class*="parceiro"]', '[class*="loja"]',
                     '[class*="card"]', 'article', 'li']
        for sel in seletores:
            try:
                itens = page.locator(sel).all_text_contents()
                texto_completo += " ".join(itens).lower()
            except Exception:
                pass

        if not texto_completo:
            texto_completo = page.content().lower()

        log.info(f"  [Livelo varejo] texto extraído: {len(texto_completo)} chars")

        for parceiro in PARCEIROS_VAREJO:
            if parceiro in texto_completo:
                idx = texto_completo.find(parceiro)
                trecho = texto_completo[max(0, idx-50):idx+200]
                match = re.search(r"(\d+)\s*(?:pontos?|pts?)\s*/?\s*r?\$?", trecho)
                pts = int(match.group(1)) if match else 0
                if pts >= LIMIAR_BONUS_VAREJO:
                    varejo.append({
                        "loja": parceiro.title(),
                        "pontos_por_real": pts,
                    })
                    log.info(f"  ⭐ {parceiro.title()}: {pts}pts/R$")

    except Exception as e:
        log.warning(f"  [Livelo varejo] erro: {e}")

    return varejo


def buscar_promocoes_livelo() -> dict:
    resultado = {"aereas": [], "varejo": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)
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
        linhas.append("_Google Flights pode ter bloqueado. Ver logs._")
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
            linhas.append(f"⏳ *Melhor preço:* R$ {melhor['preco']:.0f} ({melhor['label']})\n   Faltam *R$ {diff:.0f}* para R$ {PRECO_ALVO:.0f}")

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
    linhas.append("_Playwright · Google Flights · Livelo · Evolution API_")

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
    log.info("  Monitor de Milhas v4 — Playwright")
    log.info("══════════════════════════════════")

    passagens = buscar_passagens()
    livelo    = buscar_promocoes_livelo()
    mensagem  = formatar_mensagem(passagens, livelo)

    log.info(f"\n{'='*40}\n{mensagem}\n{'='*40}")
    enviar_whatsapp(mensagem)

    log.info("══ Finalizado ══")


if __name__ == "__main__":
    main()
