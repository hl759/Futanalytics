# SigmaDesk — Etapa 0

> **Linhagem:** FutAnalytics v2.4.4 → **SigmaDesk**. Mesma arquitetura preditiva,
> mesmo repositório, mesmo histórico de git — mercado trocado.
>
> **Por que a troca:** apostas esportivas foram proibidas no Brasil (MP assinada
> em 25/set/2026; encerramento em 06/out/2026). O motor não foi descartado: a
> matemática que lia λ de gols, xG, devig e CLV lê σ implícita, volatilidade
> realizada, variância model-free e CVL. Trading de criptoativos é legal no Brasil
> (Lei 14.478/2022) e não foi afetado pela proibição.
>
> **Etapa 0 (26/set/2026) — camada de dados validada contra dado real.** Nada de
> motor de recomendação ainda. O que existe: leitura de volatilidade completa,
> diário de trades com CVL, painel e **duas suítes de validação que rodam sobre
> dados capturados das APIs de verdade**. Ver "Validação" abaixo, incluindo os 5 bugs
> que só dado real pegou.

Painel de leitura de **volatilidade de criptoativos** (opções de BTC e ETH na
Deribit), focado no prêmio de risco de volatilidade (VRP), em estruturas de
**risco definido** e no registro disciplinado de cada operação com **CVL** —
o análogo exato do CLV que o FutAnalytics usava contra a odd de fechamento.

## O que a Etapa 0 entrega — e o que ela NÃO entrega

**Entrega:**

1. **Camada de dados com cadeia de fallback** (`app/provider.py`).
   `deribit → coinbase → kraken → demo`. A Deribit resolve IV, DVOL, OHLCV e
   funding numa API pública **sem autenticação**. A Coinbase entra para histórico
   longo de velas (BTC-USD desde 2015). O modo demo nunca deixa o painel vazio —
   e é sempre rotulado como demo, em cada camada, nunca confundido com real.
2. **Leitura de volatilidade** (`app/volread.py`). RV em dois horizontes com EWMA
   (meia-vida 58d/11d, peso curto 25% — os `DECAY_LONG`/`DECAY_SHORT`/`SHORT_WEIGHT`
   herdados intactos do `model.py` do FutAnalytics) + estimadores de amplitude
   Parkinson/Garman-Klass/Yang-Zhang (5–8× mais eficientes que close-to-close —
   é o papel que o xG tinha: um estimador melhor do mesmo fenômeno) + separação
   de salto por bipower variation (o papel do ρ de Dixon-Coles). Superfície de IV:
   ATM por vencimento, estrutura a termo, forward variance, variância model-free
   pela fórmula do VIX, skew 25-delta, put/call por open interest. Previsão
   prospectiva por Ornstein-Uhlenbeck. E o **VRP** = IV − RV prevista, com banda
   e ação.
3. **Diário de trades com CVL** (`app/db.py`, `/api/trades`). Risco definido por
   padrão (`defined_risk_only: true`, `naked_short_enabled: false`) — venda de
   prêmio sem proteção fica desligada até existir histórico de CVL que a justifique.
4. **Painel** (`static/index.html`): radar BTC/ETH, browser de superfície, diário,
   diagnóstico de provedores, configurações e aba de metodologia.

**Não entrega ainda (está nas Etapas 1–4, nesta ordem):** motor de estruturas com
payoff e gregas, sizing por perda em estresse, checklist A/B/C de 11 itens,
backtest sobre histórico real, cross-section de vol sistemática vs. idiossincrática,
calibração PAVA aplicada à previsão de RV. O `calibration.py` do FutAnalytics foi
**mantido sem alteração** porque a regressão isotônica PAVA é reaproveitável
~100% — muda só o rótulo do eixo.

## Um parágrafo de expectativa honesta

O VRP existe e é documentado há décadas: em 30 anos de ações, a implícita ficou
acima da realizada em ~70–75% dos dias, com média de 2–4 pontos. Em cripto o
prêmio é maior e mais persistente, porque o comprador típico de opção paga por
convexidade sem precificá-la. **Isso não é promessa de lucro.** É uma assimetria
estatística que some exatamente quando você mais precisa dela — em abril de 2025 o
VRP de ações foi a −17 pontos num único mês, e vender prêmio nesse regime quebra
conta. Três coisas separam isso de perder dinheiro: preço de entrada (só vender
quando a banda diz que está rico), risco definido (asas compradas sempre, para o
pior caso ser um número conhecido e não uma surpresa), e registro completo com CVL
(para você saber se o seu edge é real ou se foi sorte num trimestre de vol baixa).
E note o que a leitura real de 26/set/2026 disse: **VRP negativo, −3,8 pontos** —
IV de 35,4% contra RV realizada de 39,2%. O app respondeu "não venda prêmio".
Um motor que nunca diz não não é um motor, é um gerador de operações. Promessa de
retorno garantido seria mentira, e mentira não entra neste código.

## Rodar

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abra http://localhost:8000

Quatro dependências, **as mesmas do FutAnalytics** — a migração não acrescentou
nenhuma: `fastapi`, `uvicorn`, `httpx`, `pydantic`. Toda a matemática é Python
puro da stdlib. Sem numpy, pandas, scipy ou sklearn.

## Validação

Duas suítes, ambas offline e determinísticas:

```bash
.venv/bin/python -m tools.validate_etapa0   # 81 verificações: MATEMÁTICA sobre dado real
.venv/bin/python -m tools.smoke_api         # 133 verificações: CONTRATO HTTP, banco descartável
```

`tools.validate_etapa0` roda sobre `app/fixtures_real.json`: 30 velas diárias
reais da Coinbase, 17 opções reais da Deribit e a série oficial de DVOL
(jun–set/2026 + janela histórica de 2024), tudo capturado em 26/set/2026.

```bash
python -m tools.capture_fixtures            # recaptura os fixtures (rode FORA do sandbox)
python -m tools.capture_fixtures --check    # compara com o existente e avisa sobre drift
```

### Os 5 bugs que só dado real pegou

Nenhum destes apareceria em teste com dado sintético. É o argumento pela Etapa 0
antes do motor:

1. **Dia de vencimento sem zero à esquerda.** A Deribit emite `BTC-9OCT26`, não
   `BTC-09OCT26`. O parser assumia 2 dígitos e perdia **silenciosamente** todo
   vencimento de dia 1 a 9 — justamente os semanais, a parte mais líquida da
   superfície. Corrigido lendo o código ancorado pela direita.
2. **O demo validava a si mesmo.** `demo_chain` montava nomes com
   `strftime('%d%b%y')`, que zero-pads o dia. Ou seja: o gerador produzia
   `02OCT26` e o parser aceitava `02OCT26` — um par fechado que nunca encontraria
   o nome real. Regra que ficou: **o modo demo emite a mesma convenião da
   exchange**, senão ele não testa nada.
3. **`hash(str)` é salgado por processo.** O "demo determinístico" usava
   `hash(ccy) & 0xFFFF` como seed. Em CPython isso muda a cada processo, então o
   demo gerava uma história **diferente a cada restart** — medido: o close de 30
   dias atrás saía 78.673 num processo e 92.535 noutro. Quebra reprodutibilidade,
   invalida cache e faz o painel mostrar números que mudaram sem o mercado mudar.
   Só apareceu rodando a mesma função em dois processos.
4. **`INSERT` de trades nasceu quebrado.** `sqlite3.OperationalError: 21 values
   for 20 columns` — `created_at` era contado duas vezes (já entrava como literal
   `datetime('now')` e ainda ganhava placeholder). Nenhum trade jamais foi
   gravado; só apareceu quando o endpoint foi chamado de verdade.
5. **Configuração de risco sem limite nenhum.** `POST /api/settings` gravava
   `bankroll: -50`. Como `stake_pct = notional/bankroll×100`, o sizing do trade
   seguinte saiu **−3108%**; com bankroll zero haveria divisão por zero no
   portfólio. Agora valida com limites declarativos e é **all-or-nothing** com
   422: perfil de risco é uma combinação (capital × teto de vega × Kelly × DTE),
   então aplicar metade de um payload com erro criaria um perfil que você nunca
   revisou.

Corrigido de passagem: `demo_dvol` estava ancorado em DVOL 19,0 quando o índice
oficial era **34,88** — o demo fingia um mercado 16 pontos mais barato que o real,
e qualquer teste de VRP sobre ele mentiria. E o handler de settings tinha um
`for ...: pass` no lugar da invalidação de cache: trocar o universo continuava
servindo a superfície antiga até o TTL vencer.

### O cross-check que dá confiança nos números

Duas medições **independentes** de IV, no mesmo dia, concordando em 0,50 ponto:

| Medição | Valor | Fonte |
|---|---|---|
| DVOL BTC oficial | 34,88% | índice da Deribit |
| IV 30d interpolada | 35,38% | cadeia de opções real, interpolada em variância total |
| Spot | 84.003,06 | Coinbase `BTC-USD` |
| Spot (conferência) | 84.003,27 | Deribit `estimated_delivery_price` — Δ de 21 centavos |
| RV 30d realizada | 39,18% | velas Coinbase, blend com estimadores de amplitude |
| **VRP** | **−3,8 pts** | NEGATIVO → "não venda prêmio" |

## Decisões de escopo (todas confirmadas com o usuário)

| Decisão | Valor | Consequência no código |
|---|---|---|
| Mercado | volatilidade de cripto (opções BTC/ETH) | `provider.py`, `volread.py` |
| Execução | Deribit, caminho completo com estruturas | Etapas 1–4 |
| Legado | reescrita no lugar, git preservado | módulos de futebol removidos, `calibration.py` mantido |
| Nome | **SigmaDesk** | — |
| Capital de risco | configurável, padrão editável | `settings.bankroll` |
| Perfil de risco | **só risco definido** no início | `defined_risk_only: true`, `naked_short_enabled: false` |
| Instrumentos | **BTC + ETH apenas** | `provider.CURRENCIES`; `/api/trades` recusa SOL com 400 |
| Ordem | **Etapa 0 primeiro** | este README |

Venda de prêmio sem proteção (short straddle/strangle sem asas) permanece
**desligada por padrão** até haver histórico de CVL acumulado que justifique
ligá-la. É uma chave explícita nas configurações, não um ajuste fino.

## Fontes de dados

| Provedor | Custo | Auth | Fornece | Papel |
|---|---|---|---|---|
| **Deribit** | grátis | **nenhuma** (API pública) | cadeia de IV completa, DVOL, OHLCV, funding, forward | **Principal** |
| **Coinbase Exchange** | grátis | nenhuma | OHLCV diário profundo (BTC-USD desde 2015) | **Fallback 1** |
| **Kraken** | grátis | nenhuma | OHLCV | **Fallback 2** |
| Demonstração | nenhum | — | superfície sintética ancorada em valor real | Último recurso (sempre funciona) |
| ~~Binance~~ | — | — | — | **REMOVIDA**: geo-bloqueada no IP do Render free (EUA). Confirmado por teste: devolve "restricted location" |

`get_book_summary_by_currency` devolve, **numa única chamada**, `mark_iv`,
`open_interest`, `volume`, `bid_price`/`ask_price` e `underlying_price` de todas
as opções de uma moeda. Orçamento medido: ~8 chamadas a cada 15 minutos, contra
um pool de 50 mil créditos (500 créditos/chamada, refill de 10 mil/s). `get_instruments`
custa 10 mil créditos — por isso entra no cache diário, não no ciclo de 15 minutos.

**Restrições conhecidas, medidas:** `get_volatility_index_data` exige o parâmetro
`currency` (com só `index_name` devolve erro −32602) e tem piso duro em
2024-01-01. `get_historical_volatility` só devolve ~2–3 semanas de dados horários
— não serve para histórico longo de RV. O formato de vela da Coinbase é
`[time, LOW, HIGH, open, close, volume]`, com **LOW antes de HIGH** e resposta em
ordem **descendente**; normalizar errado inverte a amplitude e corrompe todo
estimador de range sem levantar erro.

## Metodologia (resumo)

O mapeamento completo de cada componente do motor de futebol para o seu
equivalente em volatilidade está em `PLANO_SIGMADESK.md`. Os que já estão
implementados:

| FutAnalytics | SigmaDesk | Status |
|---|---|---|
| λ_home/λ_away (Poisson) | σ_forecast (EWMA 2 horizontes) | ✅ Etapa 0 |
| xG (estimador melhor que gols) | Parkinson/GK/Yang-Zhang (melhor que close-to-close) | ✅ Etapa 0 |
| ρ de Dixon-Coles (−0,09) | correção de salto por bipower variation | ✅ Etapa 0 |
| devig (remove margem da casa) | variância model-free (fórmula VIX) + half-spread | ✅ Etapa 0 |
| CLV vs odd de fechamento | **CVL** vs IV de fechamento | ✅ Etapa 0 |
| regressão xG vs gols | reversão OU de vol | ✅ Etapa 0 |
| fadiga (jogos 7/14d → λ×0,92) | densidade de eventos (FOMC/CPI/expiração → σ×1,12) | Etapa 1 |
| calibração PAVA | PAVA sobre previsão de RV | mantido, aplica na Etapa 2 |
| `kelly_stake(p, odd, bankroll)` | sizing por perda em estresse | Etapa 3 |
| `build_multiple` (penalidade fixa −8/−15%) | `build_portfolio` com correlação **medida** | Etapa 4 — upgrade real |
| checklist do trader (9 itens) | checklist de vol (11 itens) | Etapa 3 |

Anualização em cripto usa **√365**, não √252 — o mercado não fecha no fim de
semana. Errar isso subestima toda volatilidade anualizada em ~20%.

## Estrutura

```
app/
  provider.py       Deribit/Coinbase/Kraken/demo: fallback, pacing, diagnóstico
  volread.py        RV, superfície de IV, forward variance, OU, VRP
  db.py             SQLite: settings, cache com TTL, trades (com CVL), api_usage
  calibration.py    PAVA isotônico — herdado INTACTO do FutAnalytics
  fixtures_real.json  dados reais capturados em 26/set/2026 (validação offline)
  main.py           FastAPI: 23 rotas
static/index.html   painel
tools/
  validate_etapa0.py    81 verificações sobre dado real
  smoke_api.py          133 verificações do contrato HTTP
  capture_fixtures.py   recaptura os fixtures
PLANO_SIGMADESK.md  plano de migração completo (770 linhas)
```

## Render free: por que este app cabe

- **512 MB de RAM:** sem numpy/pandas, o processo fica em ~60 MB.
- **Sem worker em background:** todo refresh é sob demanda, com TTL de cache
  (`chain` 15 min, `candles`/`dvol` 1 h). Nenhum processo separado.
- **Sem dependência nova:** as mesmas quatro do FutAnalytics.
- **SQLite efêmero:** o disco do Render free é volátil, então `db.conn()`
  verifica e recria o schema a cada conexão (auto-reparo herdado). Persistência
  real é `/api/backup` — exportar/importar JSON.
- **Nenhuma gravação automática de dado de usuário:** o único dado não-volátil
  escrito sozinho é cache temporário com TTL, purgado na inicialização. Trades
  só entram quando você clica em registrar. Herdado do FutAnalytics porque era um
  pedido explícito, e continua sendo a decisão certa.
- **Health check em `/api/health`:** não toca em nenhuma API externa, então
  responde 200 mesmo com Deribit e Coinbase fora do ar (o app cai no demo em vez
  de morrer).

## Aviso

Ferramenta de análise e registro. **Não é recomendação de investimento.** Opções
de criptoativos são instrumentos de risco elevado; estruturas de risco definido
limitam a perda máxima a um valor conhecido, o que é diferente de não perder.
Deribit não restringe residentes do Brasil, mas exige KYC para operar e os termos
mudam — verifique você mesmo antes de abrir conta. Você é o único responsável
pelas suas operações.
