# FutAnalytics

Plataforma de análise diária de jogos de futebol com foco em mercados de gols (Over/Under e BTTS), valor esperado e gestão de banca por Kelly fracionado.

## Rodar

```bash
pip install fastapi "uvicorn[standard]" httpx
cd futanalytics
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abra http://localhost:8000

## Fontes de dados

| Provedor | Custo | Cobertura | Odds reais |
|---|---|---|---|
| Demonstração | nenhum | dados simulados | simuladas |
| football-data.org | grátis (token) | Brasileirão A, Champions, top 5 Europa, Portugal, Holanda, Championship | não |
| API-Football (api-sports.io) | grátis, 100 req/dia | Série A/B, Copa do Brasil, Libertadores, Champions, top 5 Europa | sim |

Chaves são salvas localmente em SQLite (`futanalytics.db`) e nunca saem da sua máquina, além das chamadas às próprias APIs. Todo dado é cacheado para respeitar os limites gratuitos.

## Metodologia do modelo

1. Força de ataque/defesa por time, separada casa/fora, com decaimento exponencial no tempo.
2. Shrinkage bayesiano para a média da liga em amostras pequenas.
3. Gols esperados por lado; matriz de placares Poisson com correção de Dixon-Coles.
4. Probabilidades de Over/Under, BTTS, 1X2 e dupla chance derivadas da matriz.
5. Melhor mercado por jogo escolhido por EV (com odds reais) ou por probabilidade calibrada.
6. Stake por Kelly fracionado (padrão 0,25) com teto por aposta (padrão 3% da banca); múltiplas com fração reduzida.

## Estrutura

```
app/
  main.py      # API FastAPI: painel do dia, bilhetes, configurações
  model.py     # motor estatístico (Poisson/Dixon-Coles, EV, Kelly)
  provider.py  # football-data.org, API-Football, modo demo, cache
  db.py        # SQLite: settings, cache, bilhetes, uso de API
static/
  index.html   # frontend completo (painel, bilhetes, configurações, metodologia)
```
