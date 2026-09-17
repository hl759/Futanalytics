# FutAnalytics v3

Painel de decisão para apostas de futebol com foco em **valor, matemática de
preço e disciplina**. O app estima probabilidades de gols (Poisson +
Dixon-Coles, xG do Understat, modelo conjunto por liga), mostra **o preço que
faz a aposta valer a pena** (`odd` mínima aceitável e odd de equilíbrio), monta
a múltipla do dia com a matemática de correlação e margem explícitas, registra
seus bilhetes e mede **CLV** contra a odd de fechamento — o único indicador
honesto de vantagem antes do lucro aparecer.

> **Leia o `AUDITORIA.md` antes de apostar um centavo.** Ele traz a avaliação
> honesta (com números de 4.549 jogos reais) do que este motor demonstrou e do
> que **não** demonstrou. Resumo: o app é excelente como ferramenta de preço,
> registro e auditoria; o modelo, nas cinco grandes ligas e contra a linha de
> abertura da B365, **não apresentou vantagem estatisticamente demonstrável**.
> Quem tem pressa de lucro vai se machucar usando qualquer coisa que prometa o
> contrário — inclusive isto aqui.

## O que o backtest mostra (números reais, sem maquiagem)

Rodado em 4.549 jogos de Premier League, La Liga, Serie A, Bundesliga e Ligue 1
(treino 19/20–22/23, teste 23/24–25/26), odds de abertura e de fechamento da
B365, com bootstrap de 2.000 reamostragens. Relatório completo em
`backtest_report.md`; a discussão crítica está em `AUDITORIA.md`.

| O que foi medido | Resultado |
|---|---|
| Brier do modelo / misturado / mercado (Over 2.5) | 0,2569 / 0,2413 / **0,2386** |
| Brier do modelo / mercado (1X2 casa) | 0,2204 / **0,2058** |
| Baseline ingênuo de Over 2.5 (frequência-base) | 0,2486 — ou seja, **sem xG o modelo é pior que "chutar a média"** |
| Política v2 ("mais provável", 100 apostas) | −3,62% de ROI, IC 95% [−16,7%, +9,0%] |
| Política v3 ("valor + faixa", 46 apostas) | −1,87% de ROI, IC [−29,6%, +28,4%], **CLV +0,77%** |
| "Valor" usando só o modelo (223 apostas) | **−12,73%** de ROI, IC [−25,4%, +0,1%] |
| Baseline "favorito O/U 2.5" (4.549 apostas) | **−3,87%** (é a margem da casa aparecendo) |
| *Benchmark* "oráculo do fechamento" (1.230 apostas) | **+4,18%** de ROI e CLV +9,45% |
| Alpha de CLV do modelo (1X2 casa / fora) | **negativo** (t = −2,41 / −3,00): o modelo é contrário ao movimento |
| Correlação entre Overs da mesma rodada (ρ) | +0,0138 (8322 pares) |

Com **xG do Understat** e teste sem vazamento (treino 19/20, teste 20/21, 96%
de cobertura), o quadro muda de figura: o modelo **empata com o mercado** em
Brier nos quatro mercados (Over 2.5: 0,2463 contra 0,2421; casa: 0,2110
contra 0,2016) e o **CLV alpha em casa fica positivo** (β = +0,0257,
t = 2,94). Ainda não é prova de vantagem — é o único sinal positivo do
backtest inteiro, e é por isso que o roadmap em `AUDITORIA.md` começa por
xG completo e line shopping.


Leitura de trader: **o dinheiro disponível está no movimento da linha**
(abertura → fechamento), não em adivinhar o placar. O modelo de gols serve
para *posicionar preço* e para recusar aposta ruim; a vantagem real vem de
pegar o preço certo antes de o mercado se ajustar — e de provar isso no CLV.

## Rodar localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # ou: pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abra `http://localhost:8000`. O app começa em **modo demonstração** (jogos e
preços simulados) para você explorar o fluxo sem chave nenhuma.

Provedores de dados, em Configurações:

| Provedor | Custo | Odds | xG |
|---|---|---|---|
| Demonstração | — | simuladas (não use para validar nada) | — |
| football-data.org | grátis, com token | não | — |
| API-Football (api-sports.io) | grátis (100 req/dia) | sim | — |
| Understat | grátis, sem chave | — | Premier, La Liga, Bundesliga, Serie A, Ligue 1 |

## Backtest (a ferramenta que valida qualquer promessa)

```bash
# baixa/usa cache dos jogos e roda o walk-forward completo
python3 -m app.backtest --train 1920 2021 2122 2223 --test 2324 2425 2526 \
    --report backtest_report.md --json backtest_summary.json

# treinar as curvas de calibração nas temporadas de treino (grava proveniência)
python3 -m app.backtest --train 1920 2021 2122 2223 --fit-calibration

# espelho local (roda offline, sem tocar na internet)
FUTA_DATA_DIR=/caminho/<temporada>/<DIV>.csv python3 -m app.backtest ...
```

O backtest mede: Brier/LogLoss contra o mercado, quintis de divergência, **alpha
de CLV** (o modelo antecipa o movimento da linha?), ROI com IC bootstrap e
drawdown de Kelly, ROI por faixa de odd, múltiplas, comparação entre métodos de
devig e correlação intra-rodada. A aba **Auditoria** do app mostra o último
`backtest_summary.json` gerado.

## Como usar no dia a dia (o fluxo que dá dinheiro)

1. **Painel do dia** — cada jogo mostra a entrada de valor (se existir), o
   **preço-alvo** (`aceite só acima de @X`), a odd de equilíbrio e o dossiê dos
   dois times: descanso/congestionamento, rendimento por mando, forma ponderada,
   execução vs. xG (quem marca mais do que cria tende a regredir), volatilidade,
   índice de "jogo travado", força do calendário e bandeiras de risco.
2. **Cole a odd da SUA casa** na tabela do jogo (não dá para avaliar preço sem
   preço). O app recalcula EV, vantagem e alvo, e guarda o histórico do
   movimento da linha.
3. **Se não houver entrada, não há aposta.** A recusa é o estado normal do app
   — no backtest, o filtro de valor rejeita a quase totalidade dos jogos.
4. **Registre o bilhete e depois a odd de fechamento.** CLV médio positivo em
   300+ apostas é o que separa método de sorte; ROI positivo em 20 apostas não
   significa nada.
5. **Sombra sempre ligada**: o app grava toda recomendação do modelo, comparando
   acerto prometido × realizado sem arriscar dinheiro.

## Método (v3)

1. **Forças por time** em dois horizontes (estrutural + forma), com shrinkage
   bayesiano e blend de xG (peso 65% quando há cobertura).
2. **Modelo conjunto por liga** (`app/joint.py`): Poisson log-linear
   (`log λ = μ ± h + ataque + defesa`), ridge 8,0, meia-vida de 90 dias,
   resolvido por IRLS esparso. Elimina o viés de calendário e roda dentro do
   backtest com blindagem contra vazamento (só jogos anteriores à data).
3. **Matriz de placares** Poisson com correção de Dixon-Coles (ρ = −0,09) e
   mercados derivados (Over/Under 0,5–3,5, BTTS, totais por time, 1X2).
4. **Matemática de preço** (`app/market.py`): devig proporcional/power/Shin,
   EV, vantagem em pontos de probabilidade, `odd` mínima aceitável
   `(1+EV)/p`, odd de equilíbrio `1/p`, Kelly fracionado com desconto de
   incerteza (quantil 0,30 da posterior Beta) e ordenação por **crescimento
   esperado de capital** — não por "probabilidade de acertar".
5. **Sem o outro lado do mercado** o app não consegue separar margem de valor:
   nesse caso ele exige 2 pontos percentuais extras de EV (e diz isso na ficha).
6. **Faixas de operação** (`POLICY`): simples com p ∈ [50%, 85%] e odd ∈
   [1,45, 3,20]; pernas de múltipla com p ∈ [62%, 88%] e odd ∈ [1,32, 2,00];
   EV mínimo e vantagem mínima configuráveis (padrão 3,0% e 2,0 p.p.).
7. **Múltipla** (`app/parlay.py`): decisão no cenário conservador
   (independência), cenário correlacionado apenas como *upside*
   (ρ = 0,06 mesma liga/família; 0,03 mesma liga), margem embutida, odd
   combinada com teto de 8,00, escada de 1 a 4 pernas e exposição diária
   limitada (padrão 6% da banca).
8. **Calibração isotônica** (PAVA) por modo e mercado, aplicada antes da
   mistura; `app/calibration_data.json` carrega a proveniência (temporadas,
   ligas, data) e o app recarrega as curvas quando o arquivo muda.
9. **Dossiê criterioso** (`app/dossier.py`): contexto numérico dos dois times —
   descanso, congestionamento, splits de mando, forma ponderada, xG a favor e
   contra, índice de finalização, taxas de Over/BTTS/jogo travado, força do
   calendário, ambiente da liga, roteiro do jogo, bandeiras de risco e nota de
   confiança com motivos explícitos.

## Operação

| Variável | Para que serve |
|---|---|
| `APP_TOKEN` | protege `/api/*`: sem ele, qualquer pessoa com o link pode apagar seus bilhetes. Com ele, o app pede o token uma vez e guarda no navegador. |
| `FUTA_DB` | caminho do SQLite (disco persistente só existe em plano pago; no free o banco é apagado a cada deploy — veja `DEPLOY.md`) |
| `FD_TOKEN` | token do football-data.org |
| `AF_KEY` | chave da API-Football (odds reais) |
| `FUTA_DATA_DIR` | espelho local de CSVs do football-data.co.uk para o backtest offline |
| `FUTA_XG_DIR` | diretório (ou arquivo único) de CSVs de chutes do Understat para o xG offline |
| `FUTA_XG_SEASONS` | temporadas de xG a carregar (a coluna `season` do Understat é o ano de **início**) |

## Estrutura

```
app/
  main.py             # API FastAPI: painel do dia, odds, bilhetes, CLV, sombra, backup
  model.py            # motor estatístico (forças, Poisson/Dixon-Coles, políticas de seleção)
  market.py           # matemática de preço (devig, EV, alvo, Kelly, crescimento)
  dossier.py          # dossiê criterioso dos times (descanso, splits, xG, risco)
  parlay.py           # múltiplas: correlação, margem, escada, exposição
  joint.py            # modelo conjunto por liga (Poisson ridge, IRLS esparso)
  calibration.py      # calibração isotônica + proveniência das curvas
  understat.py        # xG (grátis, com cache)
  provider.py         # football-data.org, API-Football, demo
  db.py               # SQLite (WAL): settings, cache, bilhetes, histórico de odds
  backtest.py         # walk-forward: Brier/CLV/ROI/IC, políticas, devig, correlação
  calibration_data.json  # curvas treinadas (com _meta de proveniência)
static/index.html     # frontend (5 abas: painel, bilhetes, auditoria, config, metodologia)
tests/                # 68 testes (pytest): mercado, modelo, parlay, joint, calibração, API, backtest
backtest_report.md    # último relatório walk-forward
AUDITORIA.md          # avaliação crítica honesta + checklist de melhorias
```

## Testes

```bash
pip install -e ".[dev]"
ruff check app tests
pytest -q
```

## Avisos

- **Nada aqui é recomendação de investimento.** A aposta é sua, o risco é seu.
- O modo demonstração cria preços a partir do próprio modelo: ele serve para
  conhecer a interface, **nunca** para concluir que o modelo tem vantagem.
- Estatística de amostra pequena não é evidência: ROI positivo com IC cruzando
  zero é sorte até prova em contrário, e a prova é CLV em amostra grande.
- O plano gratuito do Render hiberna e apaga o disco a cada deploy: use
  `FUTA_DB` em disco persistente e o backup em JSON da aba Configurações.
