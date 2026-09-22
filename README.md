# FutAnalytics v2.3 "Trader Free"

> **v2.3.5 (22/set/2026) — "sumiram os jogos futuros"**: (1) a football-data.org pode devolver **HTTP 200 com lista vazia** em certos problemas de plano/permissão — sem erro — e o painel mostrava "dia sem jogos" em todos os dias futuros, enquanto os passados pareciam vivos só porque ficam em cache por 7 dias. Agora o app **detecta a resposta vazia impossível** (9 dias × ~10 ligas = 0 jogos) e cai para a **rota por competição**, que devolve erro explícito quando é permissão — a causa aparece na tela em vez de silêncio; (2) **diagnóstico de etapas** na tela vazia (quantos jogos a API devolveu na janela, quantos nas ligas monitoradas) + versão do app no painel; (3) janela corrigida para **9 dias reais** (o `dateTo` da API v4 é exclusivo); (4) jogos futuros com horário `00:00:00Z` (placeholder de liga que ainda não fechou a hora) não vão mais para a véspera por causa do fuso; (5) erro definitivo de token/plano aparece na hora, sem 10 retentativas fingindo "aquecimento".

Plataforma de análise diária de jogos de futebol focada em mercados de gols (Over/Under, BTTS e totais por time), com **odds reais grátis**, seleção por **equilíbrio acerto × odd**, **verificação do trader** (checklist criterioso por jogo) e **zero gravação automática**.

## O que a v2.3 acrescenta — e por quê

1. **Odds reais sem pagar nada** (`app/odds_fd.py`). Em 2026 não existe mais API de odds de futebol realmente gratuita (The Odds API removeu soccer do plano free; API-Football free só libera temporadas antigas). O que continua grátis, sem chave e sem cota, é o arquivo público do **football-data.co.uk** com as odds das próximas rodadas das ligas europeias: **1X2 e Over/Under 2.5 da Bet365 e da Pinnacle** (a casa mais *sharp* do mundo). O app baixa o arquivo ~2x/dia (150 KB, cacheado 12h), casa os jogos por data + nome e mostra a melhor odd real por mercado com selo **real**.
2. **Linhas derivadas do consenso** (selo **derivada**). Over 1.5, BTTS e totais por time não existem no arquivo gratuito. O app faz o que as casas fazem: **inversão Poisson** — encontra os λ implícitos no 1X2 + O2.5 do mercado (devig) e projeta as demais linhas na matriz Dixon-Coles do motor, com margem padrão. Estimativa boa, não é preço de casa — confira na sua antes de registrar. Jogos fora das ligas cobertas (Brasileirão, Champions) seguem com a **odd justa do modelo**, sem disfarce.
3. **Seleção por equilíbrio acerto × odd** (modo padrão `balanced`). Perna de 92% @1.04 estraga múltipla: risco inteiro por quase nada. O score `prob^1.25 · (1−1/odd)^0.6` mantém a probabilidade no comando e deixa o preço decidir o desempate; perna abaixo de ~1.12 só entra sem alternativa. Modos "mais provável" e "valor esperado" continuam disponíveis em Configurações.
4. **Múltipla com odd-alvo e régua de selo** (padrão 2.8 e selo mínimo B, configuráveis): pernas adicionadas da mais segura para a menos segura **só até atingir o alvo** — perna além do alvo derruba sua taxa de acerto sem melhorar o retorno — e **perna com selo C da verificação nunca entra**; se nenhum ângulo do jogo passa na régua, o jogo fica de fora (e haverá dias sem múltipla, deliberadamente). Cada perna mostra de onde veio o preço (real/derivada/justa) e o selo.
5. **Verificação do trader A/B/C**: antes de cada pick, checklist objetivo — amostra, insumo (modelo conjunto/xG/gols), concordância modelo×mercado (discordar muito do mercado é alerta, não convite), descanso dos times, tendência recente alinhada à perna, H2H e perfil da liga. Nota 0–10 no card, calculada **para cada candidato a perna** (a múltipla filtra por selo, padrão: nunca C). Não é promessa de green: é a soma das evidências. A conferência de desfalques/escalações continua sendo humana — a sua.
6. **Nada é gravado sozinho** (pedido explícito do usuário do Render free): o modo sombra deixou de existir. Bilhetes, resultados e CLV só entram quando você clica em registrar. O único dado não-volátil escrito automaticamente é o **cache temporário das APIs** (expira em horas e é limpo na inicialização) — sem ele as cotas gratuitas estourariam em minutos e o app pararia.

## Um parágrafo de expectativa honesta

O backtest em ~5.200 jogos reais (`backtest_report.md`) mostra o que todo trader de 15 anos sabe: o mercado de odds é eficiente, modelo nenhum vence o consenso no agregado, e lucro vem de **disciplina, preço bom e filtragem** — não de "IA que adivinha". Este app existe para te dar as três coisas de graça: probabilidades calibradas e honestas, o melhor preço disponível sem pagar API, e um filtro criterioso que te tira das apostas ruins. Quem transforma isso em dinheiro consistente é você, com gestão de banca e registro completo. Promessa de acerto garantido seria mentira — e mentira não entra neste código.

## Rodar

```bash
pip install fastapi "uvicorn[standard]" httpx
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abra http://localhost:8000

## Backtesting (a ferramenta que valida tudo)

```bash
python3 -m app.backtest --train 2324 2425 --test 2526 --fit-calibration --report backtest_report.md
```

Mede Brier/LogLoss vs. mercado, tabela de calibração, teste de alfa, ROI das estratégias (flat e Kelly) e CLV contra a odd de fechamento. Cache em `data_cache/`.

## Fontes de dados

| Provedor | Custo | Cobertura | Odds |
|---|---|---|---|
| Demonstração | nenhum | dados simulados | simuladas |
| football-data.org | grátis (token) | Brasileirão A, Champions, top 5 Europa, Portugal, Holanda, Championship | não |
| **football-data.co.uk** (novo) | grátis, sem chave | ~22 ligas europeias (próximas rodadas) | **sim: B365 + Pinnacle** |
| API-Football (api-sports.io) | pago p/ temporada atual | Série A/B, Copa do Brasil, Libertadores + Europa | sim |
| Understat | grátis, sem chave | xG histórico: Premier, La Liga, Bundesliga, Serie A, Ligue 1 | — |

Chaves salvas localmente em SQLite e nunca saem da sua máquina além das chamadas às próprias APIs. Sem uso pago, todas as fontes do dia a cabo.

## Metodologia (resumo)

Modelo v2: dois horizontes de força por time (estrutural meia-vida ~58d + forma recente 25%), força sobre **xG do Understat** nas ligas cobertas, **modelo conjunto por liga** (regressão de Poisson ridge ajustada por adversário, sem viés de calendário), matriz Dixon-Coles (ρ = −0,09), **calibração isotônica** por mercado/modo treinada em temporadas passadas, mistura com o consenso de mercado (25%, devig proporcional, só preços reais), Kelly fracionado com desconto de incerteza. Na temporada 25/26 real: **81,1% de acerto por perna** (prometido 83,3%) na dinâmica "linha mais segura"; detalhes e métricas honestas em `backtest_report.md`.

## Estrutura

```
app/
  main.py             # API FastAPI: painel, bilhetes, CLV, backup, configurações
  model.py            # motor: forças, Poisson/Dixon-Coles, equilíbrio, checklist, múltipla
  odds_fd.py          # odds reais grátis (football-data.co.uk) + inversão Poisson
  calibration.py      # calibração isotônica (PAVA) por grupo de mercado
  understat.py        # provedor de xG (grátis, sem chave, com cache)
  joint.py            # modelo conjunto por liga (forças ajustadas por adversário)
  backtest.py         # backtesting nativo + treino das curvas (offline, no seu PC)
  provider.py         # football-data.org, API-Football, modo demo, cache
  db.py               # SQLite: settings, cache (com limpeza de expirados), bilhetes
  calibration_data.json  # curvas treinadas (gerada por app.backtest)
static/
  index.html          # frontend (painel, verificação do trader, bilhetes, metodologia)
```

## Render free: por que este app cabe (e continua cabendo)

- **Sem dependências novas** (`fastapi`, `uvicorn`, `httpx`, `pydantic`): build e RAM iguais.
- **Sem workers agendados, sem processos em segundo plano, sem banco separado**: um processo web, SQLite efêmero.
- **Sem gravação automática de dados**: o disco só vê cache temporário com expiração (e limpeza na inicialização). Bilhetes são manuais; o backup/restauração cobre o disco efêmero do free.
- **Uma única chamada externa extra** (o CSV de odds de 150 KB, 2x/dia): nada que arranhe os 750h/mês, a RAM de 512 MB ou a banda.
