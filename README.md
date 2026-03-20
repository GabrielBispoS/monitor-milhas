# 🛫 Monitor de Milhas — Livelo + Passagens UDI→RIO

Roda automaticamente às **08h10 (BRT)** via GitHub Actions.  
Envia relatório diário no **WhatsApp** via Evolution API.

---

## 📦 O que monitora

| Módulo | O que verifica |
|---|---|
| ✈️ Passagens | Voos UDI→RIO (GIG/SDU) para todas as sextas de agosto/2026, 2 adultos |
| 🏦 Livelo Aéreas | Bônus de transferência para Smiles, LATAM, Azul, TAP (alerta ≥ 40%) |
| 🛍️ Livelo Varejo | Pontuação turbinada em Shopee, Natura, Fast Shop, CB, etc. (alerta ≥ 8pts/R$) |

---

## 🚀 Setup — Passo a Passo

### 1. Fork / Clone do repositório

```bash
git clone https://github.com/SEU_USUARIO/monitor-milhas
cd monitor-milhas
```

### 2. Amadeus API (passagens aéreas)

1. Acesse [developers.amadeus.com](https://developers.amadeus.com) e crie conta gratuita
2. Crie um novo app → copie `Client ID` e `Client Secret`
3. A API de test é gratuita com dados reais de voos

> ⚠️ Quando quiser ir para produção, troque a URL base de  
> `test.api.amadeus.com` → `api.amadeus.com` no `monitor.py`

### 3. Evolution API (WhatsApp)

Você precisa de uma instância rodando. Opções:

**A) Self-hosted (recomendado):**
```bash
# Via Docker
docker run -d \
  --name evolution \
  -p 8080:8080 \
  -e AUTHENTICATION_API_KEY=sua_chave_aqui \
  atendai/evolution-api:latest
```

Depois acesse `http://localhost:8080` e conecte seu WhatsApp escaneando o QR Code.

**B) VPS barata:** DigitalOcean ($6/mês), Oracle Cloud (grátis), ou qualquer VPS com Docker.

### 4. Configurar Secrets no GitHub

No seu repositório → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Valor |
|---|---|
| `AMADEUS_CLIENT_ID` | ID do app no Amadeus |
| `AMADEUS_CLIENT_SECRET` | Secret do app no Amadeus |
| `EVOLUTION_API_URL` | URL da sua instância (ex: `https://sua-evolution.com`) |
| `EVOLUTION_API_KEY` | API Key configurada no Evolution |
| `EVOLUTION_INSTANCE` | Nome da instância no Evolution |
| `WHATSAPP_NUMBER` | Seu número com DDI (ex: `5534999999999`) |

### 5. Ativar GitHub Actions

O arquivo `.github/workflows/monitor.yml` já está configurado.  
Basta garantir que Actions esteja habilitado no repo (Settings → Actions → Allow all).

### 6. Testar manualmente

No GitHub: **Actions → Monitor de Milhas → Run workflow**

Você receberá a mensagem no WhatsApp em segundos.

---

## 📱 Exemplo de mensagem recebida

```
🤖 Monitor de Milhas — 20/03/2026 08:10

✈️ PASSAGENS UDI → RIO (2 adultos)
🟡 07/08 → 10/08 | R$ 2.004 | LA → SDU
🟢 14/08 → 17/08 | R$ 1.831 | G3 → SDU
🟢 21/08 → 24/08 | R$ 1.831 | G3 → SDU
🟢 28/08 → 31/08 | R$ 1.831 | G3 → SDU

⏳ Melhor preço: R$ 1.831 — aguardar queda para R$ 1.500

🏦 BÔNUS DE TRANSFERÊNCIA LIVELO
🔥 SMILES: +80% de bônus — TRANSFERIR HOJE!

🛍️ PONTUAÇÃO TURBINADA — VAREJO
⭐ Natura: 15pts/R$

_Monitor automático via GitHub Actions_
```

---

## ⚙️ Customizações

No arquivo `src/monitor.py`, você pode ajustar:

```python
PRECO_ALVO          = 1500.00   # Valor que dispara alerta de compra imediata
LIMIAR_BONUS_AEREO  = 40        # % mínimo de bônus para alertar
LIMIAR_BONUS_VAREJO = 8         # Pontos/R$ mínimo para alertar

# Para adicionar mais datas ou rotas, edite:
DATAS_VIAGEM = [...]
DESTINO_LIST = ["SDU", "GIG"]
```

---

## 📊 Custo total

| Item | Custo |
|---|---|
| GitHub Actions | **Grátis** (usa ~1 min/dia dos 2000 grátis/mês) |
| Amadeus API | **Grátis** (até 2000 req/mês) |
| Evolution API | **Grátis** (self-hosted com Docker) |
| VPS para Evolution | Oracle Cloud Free Tier = **R$ 0** |

**Custo total: R$ 0/mês** 🎉
