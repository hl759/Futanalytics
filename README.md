# FutAnalytics v2

Plataforma de análise diária de jogos de futebol com foco em mercados de gols (Over/Under e BTTS), valor esperado e gestão de banca por Kelly fracionado.

**v2.1 — o app pensa apenas em GOLS e escolhe o MAIS PROVÁVEL**: sem 1X2, sem dupla chance, sem EV mandar na seleção. Cada jogo contribui com sua linha de gols mais segura (Over 1.5 é o clássico) e a múltipla do dia junta as pernas mais prováveis — repetindo linha quando ela for a mais segura em vários jogos. Sob o capô, o motor v2 (backtest em ~5.200 jogos reais): xG do Understat, dois horizontes de força e calibração isotônica — na temporada 25/26 real, a dinâmica acertou 77,3% das pernas prometendo 79,3%. Detalhes em `backtest_report.md`.

## Rodar

```bash
pip install fastapi "uvicorn[standard]" httpx
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abra http://localhost:8000

## Backtesting (a ferramenta que valida tudo)

```bash
# baixa dados (football-data.co.uk + Understat), treina calibração e avalia
python3 -m app.backtest --train 2324 2425 --test 2526 --fit-calibration --report backtest_report.md
```

Mede Brier/LogLoss vs. mercado, tabela de calibração, teste de alfa (quem acerta quando o modelo diverge do mercado), ROI das estratégias (flat e Kelly) e CLV contra a odd de fechamento. Cache dos dados em `data_cache/`.

## Fontes de dados

| Provedor | Custo | Cobertura | Odds reais |
|---|---|---|---|
| Demonstração | nenhum | dados simulados | simuladas |
| football-data.org | grátis (token) | Brasileirão A, Champions, top 5 Europa, Portugal, Holanda, Championship | não |
| API-Football (api-sports.io) | grátis, 100 req/dia* | Série A/B, Copa do Brasil, Libertadores, Champions, top 5 Europa | sim |
| Understat | grátis, sem chave | xG histórico: Premier, La Liga, Bundesliga, Serie A, Ligue 1 | — |

*o plano gratuito da API-Football atualmente só libera temporadas antigas; para jogos do dia sem pagar, use football-data.org.

Chaves são salvas localmente em SQLite (`futanalytics.db`) e nunca saem da sua máquina, além das chamadas às próprias APIs. Todo dado é cacheado para respeitar os limites gratuitos.

## Metodologia do modelo (v2)

1. **Dois horizontes de força** por time (casa/fora): estrutural (janela de até 40 jogos, decaimento lento, meia-vida ~58 dias) + forma recente (últimos 8 jogos, peso de 25%), cada um com shrinkage bayesiano próprio.
2. **xG quando disponível** (Understat): força calculada sobre gols esperados com peso 65% (configurável), modulado pela cobertura da amostra; ligas sem xG usam gols brutos.
3. Gols esperados por lado; matriz de placares Poisson com correção de Dixon-Coles (ρ = -0,09).
4. **Calibração isotônica** das probabilidades por mercado (curvas treinadas em 2023/24–2024/25, armazenadas em `app/calibration_data.json`), antes de qualquer mistura.
5. Probabilidades de Over/Under, BTTS, 1X2 e dupla chance derivadas da matriz; médias de gols por liga com shrinkage para a média global.
6. **Mistura com o consenso de mercado** (devig proporcional) com peso padrão 25% — o mercado manda, o modelo filtra divergências.
7. **Melhor mercado = o mais provável** (janela 60–95%, só gols/BTTS, preferência Over em empates); EV aparece como informação. Modo "valor esperado" continua disponível em Configurações para quem opera como trader. **Modo sombra** registra toda recomendação como bilhete de auditoria.
8. **Stake**: no modo "mais provável", stake fixa sugerida (metade do teto, 1,5% da banca no padrão). No modo EV, Kelly fracionado com desconto de incerteza (σ = √(p(1-p)/n)).
9. **CLV**: registre a odd de fechamento de cada bilhete; CLV positivo consistente (300+ apostas) é o melhor indício de edge real antes do lucro aparecer.

## Estrutura

```
app/
  main.py             # API FastAPI: painel do dia, bilhetes, CLV, sombra, configurações
  model.py            # motor estatístico (2 horizontes, xG, Poisson/Dixon-Coles, EV, Kelly)
  calibration.py      # calibração isotônica (PAVA) + aplicação por grupo de mercado
  understat.py        # provedor de xG (grátis, sem chave, com cache)
  backtest.py         # backtesting nativo + treino das curvas de calibração
  provider.py         # football-data.org, API-Football, modo demo, cache
  db.py               # SQLite: settings, cache, bilhetes, uso de API
  calibration_data.json  # curvas treinadas (gerada por app.backtest --fit-calibration)
static/
  index.html          # frontend (painel, bilhetes com CLV, configurações, metodologia)
data_cache/           # cache local do backtest (dados públicos, não versionar é opcional)
backtest_report.md    # último relatório de validação
```
