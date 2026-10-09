# BinaryMaster ⚡ — Sinal M1/M5 por confluência + análise de print

> **Acesse:** `/binary` (ex.: http://localhost:8000/binary)
> **O que faz:** você cola o **print do gráfico** e o motor devolve a ordem para a **próxima vela** — `CALL 🟢` / `PUT 🔴` / `SEM ENTRADA 🟡` — com expiração **M1 ou M5**, score, plano de entrada e gestão.

## Expectativa honesta (leia primeiro)

- Binárias têm payout < 100%: com payout 82%, você precisa de **~55% de acerto só para empatar** (fórmula: `1/(1+payout)`). Com payout 70%, precisa de ~59%.
- Nenhum app, guru ou "IA" do mundo sustenta 90% em binárias. Backtests sérios de price-action filtrado ficam em **55–62%**. Este motor trava a estimativa em **68%** de propósito: acima disso é fantasia.
- O lucro vem de **payout alto + filtro rigoroso + gestão**. O app entrega os três; a disciplina é sua.
- **Opere no DEMO primeiro.** Nunca arrisque dinheiro que você não pode perder. Binárias são proibidas/reguladas em vários países — verifique sua corretora e legislação.

## Como usar (2 minutos)

1. Abra o gráfico da corretora (Quotex, IQ, Pocket, Exnova, TradingView…), maximize os **candles** com ~30–60 velas visíveis e tire o print.
2. No BinaryMaster, aperte **Ctrl+V** (ou arraste o arquivo). A visão detecta velas, tendência, volatilidade e posição no range sozinha.
3. Complete o contexto (payout, RSI, suporte/resistência, sessão…). **Cada campo preenchido aumenta a precisão** — mas funciona só com o print.
4. Clique em **⚡ ANALISAR PRÓXIMA VELA**.
5. Siga o **plano de entrada**: direção, expiração (M1/M5), timing (início da vela), invalidação e stake (% da banca).

## O motor: 12 escolas votando

| # | Módulo | Escola | Peso |
|---|--------|--------|------|
| 1 | Price Action & Candles (engolfo, pinbar, martelo, doji, fluxo) | 🇯🇵 Japonesa + 🇧🇷 BR | 20 |
| 2 | Tendência & EMAs 9/21/50 | 🇺🇸 Americana (trend) | 15 |
| 3 | Reversão RSI + Estocástico | 🇪🇺 Europeia (mean-reversion) | 12 |
| 4 | Bollinger & Volatilidade | 🇺🇸 Americana (vol) | 10 |
| 5 | SMC / ICT (BOS, CHOCH, Order Block, FVG, sweep, premium/discount) | 🇺🇸 Americana (ICT) | 18 |
| 6 | Suporte / Resistência (breakout, falso rompimento, flip, topos/fundos duplos) | Universal | 14 |
| 7 | Momentum MACD + Volume | 🇺🇸 Americana | 8 |
| 8 | Figuras + Fibonacci (bandeira, OCO, zona ouro 61.8–79%) | 🇪🇺 Clássica | 6 |
| 9 | Sessão & Killzone (overlap Londres/NY, filtro Ásia/madrugada) | 🌏 Asiática + ICT | 7 |
| 10 | Filtro Quant & OTC (payout mínimo, veto matemático) | 🇷🇺 Russa (probabilidade) | 5 |
| 11 | Exaustão & Fluxo M1 (streak 6+, reversão confirmada) | 🇧🇷 BR (scalping) | 8 |
| 12 | Gestão & Disciplina (Kelly/4, stop win/loss, anti-martingale) | 🇨🇳 Chinesa (disciplina) | 4 |

**Score −100…+100:** ≥+55 CALL FORTE · ≥+25 CALL · −25…+25 SEM ENTRADA · ≤−25 PUT · ≤−55 PUT FORTE.
**Vetos automáticos:** notícia de alto impacto, payout <70%, EV negativa, volatilidade extrema.
**Expiração:** reversão em extremo → **M1** · continuação a favor do fluxo → **M5** · sem direção → sem operação.

## As 4 abas

- **📊 Analisar** — print + contexto → ordem da próxima vela, 12 votos explicados, checklist pré-entrada.
- **📚 Estratégias Mestre** — 12 setups documentados (setup, entrada, filtro, porquê), filtráveis por escola.
- **💰 Gestão** — calculadora de banca + Monte Carlo de 300 operações + régua de break-even + regras de sobrevivência.
- **📝 Diário** — registro de operações (localStorage, com backup/restauração JSON) + winrate e P/L reais.

## API

- `POST /api/binary/analyze` — JSON com `image_base64` (data-URL ou base64 puro, máx 12MB) + campos manuais → decisão completa.
- `GET /api/binary/strategies` — biblioteca de setups.

## Arquivos

```
app/binary_engine.py   # visão (PIL+numpy, sem OpenCV) + motor de confluência
app/binary_api.py      # endpoints /api/binary/*
static/binary.html     # frontend (4 abas, Ctrl+V, diário, simulador)
```

## Rodar local

```bash
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# abra http://localhost:8000/binary
```

Render free: sem dependências pesadas (só `pillow` + `numpy` além do stack atual), sem workers, sem banco novo — o diário fica no navegador.
