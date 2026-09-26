# PLANO DE MIGRAÇÃO — FutAnalytics → SigmaDesk

**Da inteligência de odds esportivas para a inteligência de volatilidade em criptoativos**

Documento de arquitetura. Nada aqui foi implementado ainda — é o mapa completo da
mudança, para você aprovar antes de eu tocar no código.

Data: 26/set/2026 · Branch: `arena/01a0dba3-futanalytics`

---

## 0. Sumário executivo

O que você tem hoje não é um "app de futebol". É um **motor de comparação
probabilidade-modelo × preço-de-mercado com gestão de risco e disciplina de
entrada**. Futebol era só o domínio onde ele foi calibrado.

Essa máquina tem um equivalente financeiro exato, e não é "prever para onde vai o
preço" (isso quase ninguém consegue). É **volatilidade**: o único ativo do mercado
financeiro cuja dinâmica é genuinamente previsível, porque volatilidade agrupa
(volatility clustering) e porque existe um prêmio estrutural pago a quem a
negocia.

**A decisão de mercado: volatilidade de cripto — opções de BTC e ETH na Deribit.**

| | FutAnalytics (hoje) | SigmaDesk (novo) |
|---|---|---|
| O que o modelo prevê | distribuição de gols (λ) | distribuição de retornos e variância (σ) |
| Preço de mercado | odd da Bet365/Pinnacle | volatilidade implícita da Deribit |
| Sinal de edge | EV = p·odd − 1 | VRP = σ_implícita − σ_prevista |
| "Melhor preditor de edge" | CLV vs odd de fechamento | CVL vs IV de fechamento/expiração |
| Ruído que atrapalha | gols são barulhentos → usa xG | retorno close-to-close é barulhento → usa estimador de amplitude |
| Evento que distorce | fadiga (3 jogos em 7 dias) | densidade de eventos (FOMC/CPI/expiry em 7 dias) |
| Combinação | múltipla com penalidade de correlação **chutada** | carteira com correlação **medida** |
| Filtro | checklist A/B/C, C nunca entra | checklist A/B/C, C nunca entra |

A coluna da direita não é metáfora. Cada linha é o mesmo código, no mesmo arquivo,
com a mesma função. É o que permite uma mudança profunda sem jogar fora 12 meses
de engenharia estatística.

**Escopo aprovado nesta conversa:** reescrever tudo no lugar (futebol sai do repo),
execução via Deribit (estruturas de opção de verdade), e entregar motor + backtest +
painel completos.

---

## 1. Por que volatilidade de cripto — a tese de trader

### 1.1 O argumento central: VRP (Variance Risk Premium)

Volatilidade implícita (IV) fica, na média, **acima** da volatilidade que o mercado
de fato entrega (RV). Isso não é erro de precificação — é prêmio de seguro. Quem
compra proteção paga mais do que o custo atuarial esperado, exatamente como seguro
de incêndio. O vendedor embolsa a diferença.

Números que sustentam a escolha:

- Em índices de ações, IV supera RV em ~2–4 pontos de vol no vencimento de 30 dias,
  em **70–75% dos dias úteis ao longo de três décadas**. É um dos fatos estilizados
  mais robustos de finanças.
- Em cripto o prêmio é **persistente e maior**: o fluxo de varejo compra opção como
  bilhete de loteria, e instituições montam arb de vol em cima disso.
- **E aqui está o pulo do gato de 2026**: o VRP de ações está comprimido. Em
  18/jun/2026, SPY marcava IV ATM de 13,8% contra RV de 20 dias em 15,1% — VRP
  **negativo de −1,3 ponto**. Vender vol de ações hoje é vender seguro abaixo do
  custo. Em BTC, o DVOL rondava 42% no vencimento de US$ 10,6 bi de junho.

Ou seja: o edge existe, mas **mudou de endereço**. Um motor calibrado serve
justamente para dizer *quando* há edge e quando não há — não para vender vol
cegamente.

### 1.2 Por que cripto e não B3 ou ações americanas

| Critério | Cripto (Deribit) | B3 | Índice EUA |
|---|---|---|---|
| Superfície de IV completa, grátis, sem chave | **Sim — 1 chamada** | Não existe | Só Yahoo não-oficial (quebra sem aviso) |
| Mercado 24/7, tempo contínuo | **Sim** | Não (pregão, gaps) | Não (pregão, gaps) |
| VRP em 2026 | **Positivo e amplo** | Raso | Comprimido/negativo |
| Acessível do Brasil | **Sim** — Brasil não está na lista restrita | Sim | Sim, mas exige corretora US |
| Contraparte de varejo | Outro varejo comprando loteria | Formadores de mercado | HFT + fluxo institucional |
| Custo de dados | Zero | Zero, mas sem IV | Zero frágil ou pago |

O argumento técnico mais forte é o **tempo contínuo**. Seu motor atual usa
decaimento exponencial em dias (`DECAY_LONG = 0.012`, meia-vida ≈ 58 dias;
`DECAY_SHORT = 0.06`, meia-vida ≈ 11 dias). Isso foi desenhado para um calendário
discreto de jogos. Em cripto o dado é contínuo — 365 dias por ano, sem gap de fim
de semana, sem feriado. O estimador que você já tem funciona **melhor** nesse
regime do que funciona no futebol. Não é adaptação: é promoção.

### 1.3 Por que volatilidade e não direção

Preciso ser direto aqui, porque é onde 95% dos apps de "IA financeira" mentem.

Direção de preço de curto prazo é praticamente não-prevísivel — o mercado é
eficiente o bastante para que qualquer modelo público tenha alfa próximo de zero
depois de custos. Seu próprio `backtest_report.md` já dizia a verdade sobre odds:
*"o mercado de odds é eficiente, modelo nenhum vence o consenso no agregado"*.

Volatilidade é diferente. Ela tem **memória**. Um dia de alta vol estatisticamente
anuncia dias de alta vol; um dia calmo anuncia calma. Essa persistência é mensurável,
modelável e — o mais importante — **o mercado a precifica publicamente**, o que te
dá um número contra o qual comparar sua previsão. É exatamente a estrutura do seu
app atual: previsão honesta × preço do consenso × filtro de disciplina.

### 1.4 O contra-argumento, sem esconder

Vender volatilidade tem **skew negativo**: você coleta prêmio pequeno por meses e
toma uma perda grande num dia. É "catar moedas na frente de um rolo compressor".

- Em abril de 2025, com o choque de tarifas, a RV de SPY explodiu para ~42% enquanto
  a IV estava em 24–28%. O VRP foi a **−17 pontos, z-score −2,14**. Quem vendia
  prêmio na semana anterior foi atropelado.
- Em cripto isso é pior: o mercado não fecha, então não há "overnight" para se
  proteger — há gap de fim de semana com liquidez fina.

**Consequência de projeto (não negociável):** o dimensionamento de posição deste app
será por **perda em cenário de estresse**, nunca por prêmio recebido. O sizing
pergunta "quanto eu perco se a IV subir 3 pontos, ou se a RV dobrar, ou se o BTC
gapar 2σ?" e usa o pior dos três. Se o seu app de futebol já recusava aposta sem
EV, este vai recusar estrutura sem margem de estresse.

---

## 2. Diagnóstico do que existe hoje

Levantamento real do repo (5.489 linhas, 17 arquivos, 4 dependências):

| Arquivo | Linhas | O que faz de verdade | Transferível? |
|---|---|---|---|
| `app/model.py` | 1.002 | Dois horizontes de força, Poisson/Dixon-Coles, matriz de placar, mercados, devig, blend, fadiga, regressão xG, checklist A/B/C, múltipla, Kelly | **90%** — é o coração |
| `app/provider.py` | 996 | football-data.org + OpenLigaDB + ESPN + demo, fallback em cadeia, pacing de rate-limit, cache | **80%** — o esqueleto de provedores é idêntico |
| `app/odds_fd.py` | 450 | CSV de odds grátis, devig, inversão Poisson para derivar linhas inexistentes | **85%** — vira a camada de IV |
| `app/backtest.py` | 635 | Walk-forward sem vazamento, Brier/LogLoss, tabela de calibração, teste de alfa por quintil, ROI flat/Kelly, CLV | **75%** — o harness de validação é reaproveitável quase inteiro |
| `app/understat.py` | 372 | Provedor de xG: o estimador de melhor qualidade que substitui o dado ruidoso | **conceito 100%**, código 30% |
| `app/joint.py` | 139 | Regressão de Poisson ridge log-linear por liga, ponderada por decaimento, gradiente descendente em Python puro, à prova de vazamento | **95%** — estrutura de efeitos fixos é a mesma |
| `app/calibration.py` | 184 | PAVA (regressão isotônica), shrinkage para identidade com n < 300, por família de mercado e modo | **~100%** — reusa quase literal |
| `app/main.py` | 787 | API FastAPI, settings, backup, diário de bilhetes, CLV manual | **80%** — muda o vocabulário dos endpoints |
| `app/db.py` | 135 | SQLite: settings, cache com TTL e purga, bets, api_usage | **90%** |
| `static/index.html` | 691 | Painel, cards, checklist, múltipla, diário | **60%** — layout reusa, conteúdo troca |

**Restrições que herdo e respeito:**
- Render free: 512 MB de RAM, um processo web, sem worker agendado, disco efêmero.
- **Sem numpy, pandas ou scikit-learn.** Só `fastapi`, `uvicorn`, `httpx`,
  `pydantic` + stdlib. Toda a matemática nova (Black-Scholes, EWMA, PAVA, ridge por
  gradiente) roda em Python puro — `math.erf` é stdlib e resolve a normal acumulada.
- Nada é gravado automaticamente. Trades só entram com clique.

---

## 3. O mapeamento profundo

Esta é a seção central. Cada bloco do motor antigo tem destino definido.

### 3.1 `TeamSample` → `ReturnSample`

```python
# HOJE: (dias_atras, foi_mandante, gols_pro, gols_contra, xg_pro, xg_contra)
# NOVO: (horas_atras, log_retorno, amplitude_HL, abertura, fechamento, volume, funding)
```

A função `_normalize()` (que tolera tupla com ou sem xG) continua igual em
estrutura: tolera barra com ou sem amplitude, com ou sem funding.

### 3.2 `Strengths` → `VolState`

| Hoje | Novo |
|---|---|
| `atk_home`, `def_home` | `rv_long`, `rv_short` (variância realizada nos dois horizontes) |
| `form` (pontos nos últimos 5) | `vol_regime` (em que regime estamos, há quantos dias) |
| `n_eff` | `n_eff` (soma dos pesos — amostra efetiva, mesma fórmula) |
| `xg_atk_home` etc. | `rv_range_*` (estimadores de amplitude) |
| `n_xg` | `n_range` (barras com OHLC completo) |

### 3.3 Os dois horizontes — a transferência mais limpa de todas

`horizon(decay, shrink, max_games, use_xg)` vira `horizon(decay, shrink, max_bars, use_range)`:

```
σ² = Σ wᵢ·rᵢ² / Σ wᵢ ,  wᵢ = exp(−λ·idadeᵢ)
com shrinkage:  σ² = (Σ w·r² + prior·k) / (Σ w + k)
```

As constantes atuais (`DECAY_LONG = 0.012` → meia-vida 58d, `DECAY_SHORT = 0.06` →
meia-vida 11d, `FORM_WEIGHT = 0.25`) são, por coincidência que não é coincidência,
próximas das meia-vidas padrão de EWMA de volatilidade (RiskMetrics usa λ = 0,94
diário ≈ meia-vida 11 dias). **O desenho de dois horizontes já estava certo.**
`LEAGUE_PRIOR_STRENGTH = 60` vira `REGIME_PRIOR_STRENGTH`.

### 3.4 `poisson_pmf` + `dixon_coles_tau` + `score_matrix` → `return_matrix`

A `score_matrix` monta uma matriz 11×11 de probabilidades de placar e depois o app
**lê qualquer mercado somando células** (`p_over`, `p_btts`, `totals_dist`).
Esse é o truque mais valioso do seu código — um único objeto de distribuição e
todos os mercados derivados dele.

O herdeiro é uma matriz de probabilidade de preço no vencimento:

```
P(S_T ∈ bin_i) = Σ_{k=0..K} Pois(k; λ_J·T) · LogN(bin_i; μ + k·μ_J, σ_dif·√T, √(k)·σ_J)
```

Distribuição **log-normal com mistura de saltos de Merton**, discretizada em ~120
bins log-espaçados.

E aqui está a correspondência mais elegante do plano:

> **O ρ de Dixon-Coles (−0,09) existe para corrigir o viés conhecido do Poisson
> independente nos placares baixos.**
> **A mistura de saltos existe para corrigir o viés conhecido da log-normal nas
> caudas.**

Mesmo papel arquitetural: um termo de correção aplicado à distribuição-base porque
sabemos, empiricamente, onde ela erra. `dixon_coles_tau()` → `jump_correction()`.

### 3.5 `markets` → mercados de evento

Hoje são 19 mercados (`over_2.5`, `btts_yes`, `dc_1x`...). Novos, todos lidos da
matriz de retorno exatamente como antes:

| Chave | Significado | Análogo |
|---|---|---|
| `above_K` | P(S_T > strike K) | `over_2.5` |
| `below_K` | P(S_T < K) | `under_2.5` |
| `move_gt_X` | P(\|retorno\| > X%) | `over_1.5` |
| `move_lt_X` | P(\|retorno\| < X%) | `under_1.5` |
| `inside_range` | P(preço fica entre K1 e K2) | `btts_no` |
| `touch_barrier` | P(toca barreira intrajanela) | `ht_0.5` |
| `iv_gt_rv` | P(a implícita supera a realizada) | **o mercado-mestre** |
| `beyond_implied` | P(move real > move precificado) | `dc_12` |

`fair_odds = 1/p` → `fair_price` (preço teórico da estrutura, via Black-Scholes
com a σ prevista).

### 3.6 `_fatigue_metrics` → `_event_density_metrics`

Hoje: conta jogos em 7/14 dias + dias de descanso, níveis 0–3 (`ok`, `atenção`,
`pesado`, `extremo`), multiplica λ por 0,92–1,00.

Novo: conta **eventos à frente** em 7/14 dias, mesmos quatro níveis, multiplica σ
por 1,00–1,12:

- FOMC, CPI, PPI, NFP, GDP (calendário macro em JSON estático versionado no repo)
- Vencimentos de opções BTC/ETH (diário, semanal, mensal, trimestral)
- Eventos específicos de cripto (halving, unlocks relevantes, rebalanceio de ETF)

A sacada de custo é a mesma da v2.4.4: **os vencimentos vêm de graça da lista de
instrumentos que o app já baixou** — zero chamada extra, assim como a fadiga veio
de graça do `days_ago` que já estava no `TeamSample`.

Só que o sinal inverte: fadiga **reduz** λ (time cansado marca menos); evento
**aumenta** σ (vol sobe em torno de evento). E aparece a regra nova mais importante
do app: **não vender prêmio com evento dentro da janela.**

### 3.7 `_xg_deltas` → `_vol_deltas`

Hoje: `gols − xG` nos últimos 10 jogos. Overperformance > +3,5 gols → regressão
provável → λ × 0,94. É o "segredo Pinnacle" da v2.4.4.

Novo: `RV − IV` nas últimas janelas, mais reversão de Ornstein-Uhlenbeck:

```
σ_prevista(T) = σ̄ + (σ_agora − σ̄)·e^(−κ·T)
```

com κ (velocidade de reversão) estimado por regressão sobre a série. Isso resolve o
erro que a literatura aponta explicitamente: RV trailing é retrovisor; quem vende
vol precisa de **previsão para frente**. O análogo é exato — time que overperformou
o xG vai regredir; vol que esticou acima da sua média vai reverter.

### 3.8 `xG` → estimadores de amplitude (o mesmo movimento intelectual)

Este é o paralelo conceitual mais bonito da migração:

> No futebol, **gols são um estimador ruidoso da qualidade de chance criada**.
> A solução foi trocar por xG — menos ruído, mais sinal. Peso 0,65, modulado pela
> cobertura (`XG_MIN_COVERAGE = 6`).
>
> Em volatilidade, **retorno close-to-close é um estimador ruidoso da variância
> latente**. A solução é usar a amplitude da barra — 5 a 8 vezes mais eficiente.

```
Parkinson:     σ²_P  = (1/(4·ln2)) · média[ln(H/L)²]                  ~5× eficiente
Garman-Klass:  σ²_GK = média[0,5·ln(H/L)² − (2ln2−1)·ln(C/O)²]        ~8× eficiente
Yang-Zhang:    σ²_YZ = σ²_oc + k·σ²_c + (1−k)·σ²_RS                   drift-independente, trata gap
```

`XG_WEIGHT = 0.65` → `RANGE_WEIGHT = 0.65`. `XG_MIN_COVERAGE` → `RANGE_MIN_BARS`.
`_blend_strength()` reusa a mesma forma: `σ² = (1−w)·σ²_cc + w·σ²_YZ`, com
`w = RANGE_WEIGHT · min(1, n_range/RANGE_MIN_BARS)`.

Detecção de salto por **bipower variation**, que é robusta a saltos:

```
BV_t = (π/2)·|r_t|·|r_{t−1}|
σ²_difusão = BV ;  σ²_salto = max(0, RV − BV) ;  λ_J = contagem(|r| > 4σ_dif)/n
```

### 3.9 `de_vig_markets` → `de_spread_iv`

Hoje remove o overround da casa proporcionalmente dentro de cada grupo de mercado.

Novo tem duas camadas:

1. **Consenso limpo**: `mark_iv` da Deribit já é o ponto médio do modelo; o "vigorish"
   real aqui é o **meio-spread bid/ask**, que é o custo de atravessar. O app sempre
   mostrará IV de mercado (mark) e IV executável (bid se você vende, ask se você
   compra) — separados. Nunca misturar os dois, exatamente como o app hoje nunca
   disfarça odd derivada de odd real (`odd_kind: real | derived | fair`).
2. **Variância implícita model-free** (fórmula do VIX/CBOE) sobre a cadeia inteira:

```
σ²_Q(T) = (2/T)·Σᵢ (ΔKᵢ/Kᵢ²)·e^(R·T)·Q(Kᵢ) − (1/T)·(F/K₀ − 1)²
```

com `Q(K)` = preço da opção no strike K, `ΔK` = metade da distância aos vizinhos,
`K₀` = primeiro strike abaixo do forward, `F` = forward obtido por paridade
put-call no strike onde |call − put| é mínimo.

Isso é um **upgrade** sobre o devig antigo: em vez de limpar margem de três
resultados, integra a superfície inteira sem assumir nenhum modelo de precificação.

### 3.10 `implied_lambdas` → `implied_variance` (inversão)

`odds_fd.py` tem uma função notável: dada a odd de Over 2.5 do mercado, **inverte a
Poisson** para achar o λ que o mercado está precificando, e daí projeta as linhas
que não existem no arquivo grátis (Over 1.5, BTTS).

Herdeiro direto: dada a IV de um vencimento, derivar as que não temos —
interpolação de term structure, forward variance entre dois vencimentos:

```
σ²_fwd(t₁,t₂) = (σ²₂·T₂ − σ²₁·T₁)/(T₂ − t₁)
```

e o `derive_margin` (6% de margem padrão ao derivar linha) vira `derive_vol_margin`
ao extrapolar vencimento inexistente. O selo `derived` continua significando a mesma
coisa: *boa estimativa, não é preço de mercado — confira na sua corretora*.

### 3.11 `blend_markets(25% modelo)` → `blend_vol(25% modelo)`

Filosofia preservada integralmente, inclusive a regra sutil que você já implementou:
**só entra no blend o preço REAL**. Odds derivadas nascem do consenso e entrariam
como vício circular. No novo: só entra no blend a IV com liquidez verificada
(spread e OI mínimos); IV de strike morto fica de fora.

### 3.12 `joint.py` → `crosssection.py`

Hoje: regressão de Poisson ridge log-linear ajustada em **todos os jogos da liga de
uma vez**, com decaimento e sem vazamento:

```
log λ_casa  = μ + vantagem_casa + atk[casa]  + def[fora]
log λ_fora  = μ − vantagem_casa + atk[fora]  + def[casa]
```

O motivo declarado no docstring é o viés de calendário: quem enfrentou defesas
fracas parece melhor do que é.

Novo: a mesma estrutura de efeitos fixos, decompondo variância em sistemática e
idiossincrática:

```
log RV_i,t   = μ_t + β_i·log RV_mercado,t + ε_i,t
log σ²_Q,i,T = μ_Q,T + β^Q_i·log σ²_Q_mercado,T + ε
```

Mesmo solver: gradiente descendente full-batch em Python puro, ridge, pesos
decaindo com a idade, blindagem `if d >= on_date: continue`. Zero numpy.

**E aqui a migração melhora o produto.** No futebol, a correlação entre duas pernas
da múltipla tinha que ser chutada (`−8%` para 2 overs da mesma liga, `−15%` para 3+),
porque jogos de futebol são genuinamente difíceis de correlacionar. Em finanças,
correlação **se mede**. Vender vol de BTC e de ETH ao mesmo tempo não é
diversificação — o β para o fator comum de vol cripto é alto. O `crosssection.py`
entrega esse número real, e o `build_portfolio` usa covariância estimada em vez de
heurística. Mesmo slot de código, resposta muito melhor.

### 3.13 `trader_checklist` → checklist do trader de volatilidade

Mesma mecânica: cada item devolve `ok` (2 pts) / `warn` (1) / `bad` (0), nota 0–10,
selo A ≥ 8, B ≥ 6, C < 6. **C nunca entra na carteira.**

| # | Item hoje | Item novo |
|---|---|---|
| 1 | Amostra dos dois times | Amostra do RV (n_eff de barras) |
| 2 | Qualidade do insumo (conjunto > xG > gols) | Qualidade do insumo (YZ+intraday > Parkinson > close-to-close) |
| 3 | Modelo × mercado (divergência em p.p.) | Modelo × mercado (σ_P vs σ_Q em pontos de vol) |
| 4 | Descanso dos times | **VRP z-score** — está rico ou magro; z < 0 = não venda |
| 5 | Tendência recente × perna | Regime e persistência do clustering |
| 6 | H2H | Term structure coerente com o forward previsto |
| 7 | Perfil da liga | Skew/carry (25Δ risk reversal esticado?) |
| 8 | Densidade de jogos (fadiga) | **Densidade de eventos na janela** |
| 9 | Regressão xG vs gols | Reversão de vol (RV vs IV recente) |
| 10 | — | **Liquidez da ponta** (spread % do mark, OI, volume 24h) |
| 11 | — | **Correlação com a carteira aberta** (vega agregado real) |

O item 10 é novo e obrigatório: em cripto, strike ilíquido significa que a IV que
você vê na tela não é a IV que você consegue negociar. É o equivalente honesto de
"odd derivada não é preço de casa".

### 3.14 `leg_value_score` → `edge_score`

Hoje: `prob^1,25 · (1 − 1/odd)^0,60` — probabilidade manda, preço desempata, e
perna com odd < 1,12 (`MIN_ODD_USEFUL`) só entra sem alternativa.

Novo:
```
edge_score = P(edge)^a · (payoff/perda_estresse)^b · liquidez^c · (1 + z_VRP)^d
```
Com piso de liquidez (`MIN_LIQUIDITY_SCORE`) fazendo o papel do `MIN_ODD_USEFUL`:
estrutura sem ponta negociável não entra, por melhor que seja o sinal.

### 3.15 `build_multiple` → `build_portfolio`

| Hoje | Novo |
|---|---|
| `MAX_LEGS = 4` | `MAX_POSITIONS` (padrão 4) |
| `TARGET_PARLAY_ODD = 2.8` | `TARGET_VEGA` / alvo de prêmio líquido |
| `MAX_PER_FAMILY = 2` | `MAX_PER_UNDERLYING`, `MAX_PER_EXPIRY` |
| `ANCHOR_ODD = 1.20` | posição-âncora de baixo risco (funding carry) |
| `corr_penalty` por liga (−5/−8/−15%) | penalidade por covariância **medida** |
| `multiple_min_grade = "B"` | `portfolio_min_grade = "B"` |
| "melhor sem múltipla no dia" | "melhor sem posição no dia" |

### 3.16 `kelly_stake` → dimensionamento por estresse

Kelly fracionado com desconto de incerteza (`p − σ`, `σ = √(p(1−p)/n)`) continua
valendo para a probabilidade — e a ideia de descontar pela amostra é *ainda mais*
valiosa aqui: previsão de vol com 20 barras merece menos tamanho que com 200.

Mas Kelly puro é **errado** para opções de prêmio vendido, porque a perda não é
limitada ao stake. Novo:

```
notional = (fração_kelly · banca · edge) / perda_estresse_por_unidade

perda_estresse = pior dos três:
  (a) IV sobe 3 pontos contra você
  (b) RV realizada vem 2× a prevista
  (c) underlying gapa 2σ na direção adversa
```
Tetos: % máximo da banca em vega agregado, % máximo em prêmio líquido vendido,
perda máxima total da carteira em estresse.

### 3.17 CLV → CVL

Seu app registra a odd de fechamento e compara com a odd pega — *"o melhor preditor
de edge de longo prazo conhecido"*. Isso é verdade e é a métrica mais sofisticada
do app inteiro.

O análogo em opções existe e é igualmente bom: **IV de entrada vs IV no
fechamento/expiração**. Se você vendeu straddle a IV 45 e a IV no vencimento foi 38,
você capturou o spread — independente do resultado daquele trade específico.
Bater a IV de fechamento consistentemente é o que separa edge real de sorte.

Tabela `bets.closing_odd` → `trades.closing_iv`. Endpoint
`POST /api/bets/{id}/closing` → `POST /api/trades/{id}/closing-iv`.

### 3.18 `calibration.py` — reuso quase literal

PAVA, interpolação linear entre centroides, shrinkage para identidade com n < 300,
curvas por família e por modo. Só troca o vocabulário:

- modos `"xg" | "goals"` → `"range" | "close_to_close" | "blended"`
- mercados calibrados `"over_2.5", "btts_yes", "home", "away"` →
  `"move_gt_X", "iv_gt_rv", "above_K", "inside_range"`
- a regra de preservar soma do grupo (calibra o over, deriva o under por complemento)
  vira: calibra `move_gt_X`, deriva `move_lt_X = 1 − move_gt_X`.

`calibration_data.json` é regenerado pelo novo `backtest.py`.

---

## 4. Arquitetura nova

### 4.1 Árvore de arquivos

```
app/
  main.py           # API: radar, estruturas, carteira, diário, CVL, config, backup
  volmodel.py       # ← model.py     motor: VolState, 2 horizontes, jump-diffusion,
                    #                            matriz de retorno, mercados de evento,
                    #                            EV, sizing por estresse, checklist, portfólio
  iv_surface.py     # ← odds_fd.py   variância model-free (VIX-style), term structure,
                    #                            forward variance, skew/25Δ, devig de spread
  realized.py       # ← understat.py EWMA 2 horizontes, Parkinson/GK/Yang-Zhang,
                    #                            bipower/jumps, reversão OU
  crosssection.py   # ← joint.py     ridge: vol sistemática × idiossincrática
  structures.py     # novo           precificação BS + jump-diffusion, payoff, gregas
  calendar.py       # novo           densidade de eventos macro/expiry
  provider.py       # ← provider.py  Deribit/Binance/Coingecko/demo, fallback, pacing
  calibration.py    # reuso          PAVA isotônico (quase intacto)
  db.py             # ← db.py        SQLite: settings, cache, trades, cvl
  backtest.py       # ← backtest.py  QLIKE/MSE, calibração, teste de alfa, P&L, CVL, estresse
  calibration_data.json
static/index.html
```

### 4.2 Fluxo de dados

```
Deribit get_book_summary_by_currency(BTC, option)  ──► superfície de IV completa (1 chamada)
Deribit get_book_summary_by_currency(ETH, option)  ──► idem
Deribit get_instruments                            ──► strikes/expiries (1×/dia, é cara)
Deribit get_volatility_index_data (dvol_btc)       ──► histórico de IV (DVOL)
Deribit get_historical_volatility                  ──► série de HV
Deribit get_tradingview_chart_data / index_chart   ──► OHLCV para RV
Deribit get_funding_chart_data                     ──► funding/carry
Binance klines (fallback + histórico longo)        ──► OHLCV desde 2017, grátis
        │
        ▼
realized.py     → RV prevista (2 horizontes + amplitude + jumps + reversão OU)
crosssection.py → vol sistemática × idiossincrática, correlação real
iv_surface.py   → σ_Q model-free, term structure, forward variance, skew
        │
        ▼
volmodel.py     → matriz de retorno (log-normal + saltos) → mercados de evento
calendar.py     → ajuste por densidade de eventos
calibration.py  → probabilidade calibrada (PAVA)
volmodel.py     → blend com σ_Q (25% modelo) → edge → EV → sizing por estresse
        │
        ▼
structures.py   → estrutura concreta (strike, vencimento, pernas, prêmio, gregas)
volmodel.py     → checklist A/B/C por estrutura → portfólio com correlação real
        │
        ▼
main.py         → radar do dia + diário manual + CVL
```

### 4.3 Cadeia de fallback (mesma filosofia da v2.4.0)

```
deribit ──falha──► binance ──falha──► coingecko/stooq ──falha──► demo
```
`fixtures_with_fallback()` → `market_with_fallback()`. O painel continua mostrando
`fallback automático: pediu deribit → usou binance`. E continua distinguindo
**falha** de **vazio** (a correção da v2.4.1), com modo demo explícito e avisado.

### 4.4 Endpoints

| Hoje | Novo |
|---|---|
| `GET /api/day?day=` | `GET /api/radar?horizon=` — oportunidades de vol do momento |
| `GET /api/model-info` | `GET /api/model-info` — versão, κ, z_VRP atual, regime |
| `GET /api/test-provider?which=` | igual, com `deribit`/`binance`/`demo` |
| `POST /api/bets` | `POST /api/trades` (estrutura, pernas, IV de entrada, notional) |
| `POST /api/bets/{id}/settle` | `POST /api/trades/{id}/settle` |
| `POST /api/bets/{id}/closing` | `POST /api/trades/{id}/closing-iv` |
| `GET/POST /api/backup` | igual |
| `GET/POST /api/settings` | igual + parâmetros de vol e risco |
| `GET /api/labels` | `GET /api/labels` (estruturas e mercados de evento) |
| — | `GET /api/surface?ccy=BTC` — term structure e skew |
| — | `GET /api/portfolio` — vega/correlação agregados das posições abertas |

### 4.5 Orçamento de chamadas (Render free)

Não-autenticado na Deribit: limite por IP, sistema de créditos — 500 créditos por
chamada padrão, pool de 50.000, refill de 10.000 créditos/s (≈20 req/s sustentadas,
burst 100). `public/get_instruments` é a exceção cara: **10.000 créditos** (1 req/s).

| Chamada | Créditos | Frequência | Cache |
|---|---|---|---|
| `get_book_summary_by_currency` BTC (option) | 500 | 15 min | 15 min |
| `get_book_summary_by_currency` ETH (option) | 500 | 15 min | 15 min |
| `get_volatility_index_data` (dvol) | 500 | 1 h | 1 h |
| `get_historical_volatility` | 500 | 1 h | 1 h |
| `get_tradingview_chart_data` (OHLCV) | 500 | 1 h | 1 h |
| `get_funding_chart_data` | 500 | 6 h | 6 h |
| `get_instruments` | **10.000** | 1×/dia | 24 h |
| `get_order_book` (só a estrutura escolhida) | 500 | sob demanda | 5 min |

Pior caso: ~8 chamadas pesadas por 15 minutos. Folga enorme. Mantenho o `_fd_pace()`
como `_dr_pace()` — pacing conservador por IP, porque a documentação pública diverge
sobre o limite exato para não-autenticado (há fonte citando 20 req/**min**). Vamos
medir empiricamente na Etapa 0 e configurar o pace real.

---

## 5. A inteligência segmentada

Você pediu segmentação. Cinco segmentos, um motor mestre que decide qual ouvir.

| Segmento | Edge | Peso | Dados |
|---|---|---|---|
| **A — VRP (núcleo)** | IV > RV; vender vol quando o prêmio está rico | 40% | superfície Deribit + OHLCV |
| **B — Carry/funding** | funding anualizado > custo de capital; delta-neutro | 20% | funding history (grátis) |
| **C — Term structure & skew** | forward variance vs prevista; risk reversal esticado | 20% | superfície + DVOL |
| **D — Regime macro** | VIX/DXY/juros filtrando risk-on/risk-off | 10% | CBOE (grátis, CSV) |
| **E — Fluxo/posicionamento** | put-call OI, max pain, funding extremo = crowding | 10% | OI da cadeia (já baixado) |

O motor mestre faz o que o `pick_mode` faz hoje: decide o modo (agressivo /
balanceado / defensivo) conforme o regime, e **pode devolver "hoje não há
oportunidade"** — que é a resposta mais valiosa que um sistema desses produz.

Sobre o segmento B: em 2026 o basis de cripto comprimiu para 5–15% a.a. (era
30–50% em 2021). Ainda é o único retorno da lista que não depende de prever nada —
por isso entra como posição-âncora de baixo risco, no papel que a `ANCHOR_ODD = 1.20`
tinha na múltipla.

---

## 6. Backtest e validação

O harness atual (`backtest.py`, 635 linhas) é bom e a maior parte sobrevive.

**Preservado:**
- Walk-forward sem vazamento (`if d >= on_date: continue`)
- Tabela de calibração por faixa (previsto → frequência real → desvio)
- **Teste de alfa por quintil de divergência** — quando o modelo discorda do mercado,
  quem tem razão? Este é *o* teste decisivo, e ele se aplica sem nenhuma mudança
  conceitual: quintil de (σ_prevista − σ_implícita) → RV realmente realizada.
- ROI flat e Kelly, com e sem blend de mercado
- CLV → **CVL**
- Cache em `data_cache/`, relatório em Markdown

**Novo:**
- **QLIKE**: `média[RV/h − ln(RV/h) − 1]` — a função de perda padrão para previsão
  de variância, robusta ao ruído do proxy de RV. Junto com MSE, substitui
  Brier/LogLoss como métrica primária de qualidade de previsão.
- Hit rate de `P(IV > RV)` por bucket de z_VRP
- P&L por estrutura com **custo realista**: meio-spread + fee de opção da Deribit
- **Métricas de cauda** (obrigatórias, porque o risco mudou de natureza):
  max drawdown, pior perda diária, skew e curtose do P&L, número de dias com perda
  > 2× o prêmio médio coletado
- **Testes de estresse por replay**: rodar o motor sobre mar/2020, abr/2025
  (tarifas, VRP −17 pts), mai/2021 e nov/2022 (crashes cripto) e responder
  "quanto eu teria perdido, e o detector de regime teria me tirado antes?"
- Backtest do **kill-switch**: em quantos dos eventos acima o filtro teria zerado a
  exposição antes do estouro

### 6.1 ⚠️ O risco técnico nº 1 do projeto — e a mitigação

Backtest de VRP precisa de **histórico de IV**. A RV é fácil (Binance publica klines
grátis desde 2017). A IV é o problema:

- `get_historical_volatility` da Deribit dá HV, não IV.
- `get_volatility_index_data` dá DVOL (que **é** IV), mas a profundidade histórica
  por chamada é limitada — precisa ser verificada empiricamente.
- Fontes com IV por strike histórica (Laevitas, Amberdata, Tardis, Kaiko) são pagas.

**Plano de mitigação, em ordem:**
1. **Backtest nível-índice (grátis)**: DVOL vs RV calculada de klines da Binance.
   Isso já valida o núcleo da tese — IV supera RV, com que frequência, em que
   magnitude, com que z-score. Cobre o segmento A inteiro.
2. **Reconstrução de superfície** onde houver DVOL por vencimento + paridade
   put-call, para term structure histórica.
3. **Construtor de dataset local** (script offline, opt-in, roda na sua máquina e
   não no Render): faz snapshot da superfície a cada N minutos e acumula o seu
   próprio histórico de IV por strike. Em 3–6 meses você tem um dataset que ninguém
   dá de graça. Respeita a regra de "nada é gravado sozinho" em produção — é
   ferramenta explícita de pesquisa, como o `backtest.py` já é.
4. Se nada disso bastar: o app **nasce com o motor e a leitura ao vivo**, e o
   backtest de estrutura por strike amadurece com o dataset acumulado. Prefiro te
   dizer isso agora a prometer um backtest que não consigo alimentar.

Este ponto é a única parte do plano que depende de uma verificação empírica que não
fiz ainda — e não fiz porque o sandbox onde estou não tem saída de rede para testar
as APIs (a validação real acontece na Etapa 0, no seu ambiente/Render).

---

## 7. O que NÃO muda

- Stack: FastAPI + SQLite + httpx + pydantic. **Zero dependência nova** — toda a
  matemática (Black-Scholes via `math.erf`, EWMA, PAVA, ridge por gradiente) é
  Python puro, como já é hoje.
- Render free: um processo, sem worker, sem banco externo, 512 MB.
- **Nada é gravado sozinho.** Trades, resultados e CVL só entram com clique. O único
  dado escrito automaticamente continua sendo o cache temporário com TTL e purga.
- Backup/restauração para o disco efêmero.
- A filosofia do "parágrafo de expectativa honesta" do README: probabilidades
  calibradas e honestas, melhor preço disponível sem pagar API, e um filtro
  criterioso que te tira das operações ruins. Promessa de ganho garantido continua
  sendo mentira, e mentira continua não entrando no código.

## 8. O que muda de vocabulário (e por quê importa)

| Hoje | Novo |
|---|---|
| jogo, partida, fixture | ativo, instrumento, janela |
| mandante/visitante | spot/derivativo, call/put |
| odd | volatilidade implícita, prêmio |
| odd justa do modelo | preço teórico da estrutura |
| λ (lambda) | σ (sigma), variância |
| gol | ponto de volatilidade |
| múltipla | carteira / book de vol |
| perna | posição / perna da estrutura |
| banca | capital de risco |
| bilhete | trade |
| green | trade no lucro |
| CLV | CVL |

---

## 9. Nome

Herdeiro natural: **SigmaDesk** (Vol + Analytics, mesma linhagem de Fut + Analytics).
Alternativas: `SigmaDesk`, `VolEdge`, `VarianceIQ`.
O rename (README, `render.yaml`, título do painel, docstrings) acontece na última
etapa, para não poluir o diff durante a migração. **Me diga se prefere outro nome.**

---

## 10. Plano de execução

| Etapa | Entrega | Validação |
|---|---|---|
| **0** | Scaffold, `provider.py` Deribit/Binance, pacing medido, modo demo, limpeza do futebol | chamadas reais funcionando; orçamento de créditos confirmado |
| **1** | `realized.py` + `iv_surface.py` → leitura de vol: IV vs RV, VRP, z-score, term structure, skew | números batem com o site da Deribit e com o DVOL publicado |
| **2** | `volmodel.py`: 2 horizontes, amplitude, jumps, matriz de retorno, mercados de evento, reversão OU, `calendar.py` + `calibration.py` | QLIKE/MSE melhor que baseline EWMA e que a própria IV |
| **3** | `structures.py`: precificação, payoff, gregas, EV, sizing por estresse, checklist A/B/C | preço teórico dentro do bid-ask em estruturas líquidas |
| **4** | `crosssection.py` + `build_portfolio` com correlação medida | vega agregado e perda em estresse da carteira coerentes |
| **5** | `backtest.py` completo + `volanalytics_report.md` honesto, incluindo replay de estresse | relatório com números reais, inclusive os ruins |
| **6** | `static/index.html`: radar, cards de estrutura, gauge IV×RV, checklist, carteira, diário, CVL | app usável de ponta a ponta |
| **7** | Rename, README, `DEPLOY.md`, `render.yaml`, `.gitignore`, remoção total do legado | deploy no Render free funcionando |

Cada etapa é commit próprio na branch `arena/01a0dba3-futanalytics`.

---

## 11. Riscos do projeto (todos, sem omitir)

**De mercado**
1. VRP pode ficar negativo por meses (aconteceu em 2026 em ações). Mitigação: o
   motor só opera com z_VRP positivo e calibrado; caso contrário recomenda ficar de
   fora ou comprar vol.
2. Skew negativo de vender vol: perda rara e grande. Mitigação: sizing por estresse,
   teto de vega agregado, preferência por estruturas de risco definido (iron condor,
   spreads) no começo.
3. Liquidez rasa fora de BTC/ETH e perto de strikes extremos. Mitigação: item 10 do
   checklist é eliminatório.
4. Risco de contraparte/exchange (Deribit FZE, Dubai, regulada pela VARA; KYC
   obrigatório). Mitigação: o app **não executa ordem nenhuma** — você decide e
   opera manualmente, como já é hoje.

**Técnicos**
5. Histórico de IV grátis limitado (seção 6.1) — o maior risco do projeto.
6. Deribit pode mudar endpoints ou limites sem aviso. Mitigação: cadeia de fallback.
7. Python puro sem numpy: a superfície tem ~300 instrumentos por moeda; o custo é
   aceitável, mas a integral model-free precisa ser escrita com cuidado para não
   estourar tempo de resposta. Mitigação: cache de 15 min, como hoje.

**Regulatórios e fiscais**
8. A MP de 25/set/2026 proíbe **apostas de quota fixa e cassinos online**. Negociar
   criptoativos e derivativos em exchange **não é aposta** e segue legal no Brasil
   (Lei 14.478/2022 e regulação do BCB). Ainda assim: isto não é aconselhamento
   jurídico — vale confirmar sua situação.
9. Tributação de derivativos de cripto no exterior para residente brasileiro tem
   tratamento específico (ganho de capital, DARF, e as regras de cripto mudaram nos
   últimos anos). **Consulte um contador** — não vou escrever regra fiscal no código
   sem fonte primária confirmada.
10. O app é ferramenta de análise, não recomendação de investimento. Isso vai estar
    escrito na tela, como a expectativa honesta já está no README.

---

## 12. Decisões em aberto (preciso de você)

1. **Nome**: SigmaDesk, ou outro?
2. **Capital de risco** que vai aparecer como padrão no painel (hoje é
   `settings.bankroll`) — só para o sizing nascer calibrado na sua realidade.
3. **Perfil de risco inicial**: começo o app recomendando apenas estruturas de
   **risco definido** (iron condor, spreads, calendar) e mantenho venda de prêmio
   descoberta desligada por padrão até você ter CVL acumulado? É o que eu faria com
   a minha própria banca.
4. **Moedas no radar**: BTC + ETH (o líquido de verdade) ou acrescento SOL e outros
   já na primeira versão, sabendo que a ponta é mais fina?
5. **Etapa 0 primeiro?** Posso começar e te mostrar o `provider.py` Deribit
   funcionando com dados reais antes de seguir para o motor — assim você valida a
   camada de dados, que é onde mora o risco nº 5, antes de eu construir em cima.
