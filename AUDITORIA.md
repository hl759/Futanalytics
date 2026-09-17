# AUDITORIA — FutAnalytics v3

*Revisão feita com o olhar de quem vive de aposta há 15 anos: o critério não é
"o app é bonito" nem "o modelo é moderno" — é **quanto isso coloca no bolso,
com que variância e com que evidência**. Todos os números abaixo saíram de
dados reais (football-data.co.uk para resultados e odds B365 de abertura e
fechamento; Understat para xG) e são reproduzíveis com os comandos da última
seção. Onde não há evidência, está escrito que não há.*

**Data da auditoria:** 2026-09-17 · **Versão auditada:** 3.0.0
**Amostra principal:** 4.549 jogos (Premier League, La Liga, Serie A,
Bundesliga, Ligue 1) — treino 19/20–22/23, teste 23/24–25/26.

---

## 1. Veredito para quem tem pressa

**Como ferramenta de preço, registro e auditoria, este é um app acima da
média — honestamente, muito acima da média do que se vende por aí.** Ele
calcula a margem da casa, o preço mínimo que faz uma aposta valer a pena, o
Kelly fracionado com desconto de incerteza, a múltipla com correlação e
margem explícitas, guarda o histórico de odds, registra bilhetes e mede CLV.
Ele também **recusa apostas** — e isso, para um trader, é o recurso mais
valioso de todos.

**Como gerador de lucro, com as cinco grandes ligas e a linha de abertura da
B365, o modelo de gols não demonstrou vantagem.** Não é uma opinião: é o que
os 4.549 jogos de teste dizem.

| Pergunta | Resposta dos dados |
|---|---|
| O modelo bate o preço da casa em Brier? | **Não.** Perde em todos os mercados (Over 2.5: 0,2569 contra 0,2386 do mercado). |
| O modelo sequer bate o baseline ingênuo em Over/Under? | **Não.** 0,2569 contra 0,2486 de "chutar sempre 53,7%". |
| Quando o modelo diverge do mercado, ele acerta? | **Não.** Nos dois extremos de divergência o erro é do modelo. |
| A divergência do modelo antecipa o fechamento (CLV)? | **Não — é negativa** em casa (t = −2,41) e fora (t = −3,00). Ou seja: na média, apostar na divergência do modelo é apostar contra o movimento da linha. |
| As políticas de valor dão lucro no teste? | **Sem evidência estatística.** "valor + faixa" (v3): 46 apostas, ROI −1,87%, IC 95% [−29,6%, +28,4%]. |
| O que dá dinheiro de verdade? | **O movimento da linha.** O *benchmark* "oráculo do fechamento" (só aposta o que o fechamento valida) fez **+4,18% de ROI e +9,45% de CLV em 1.230 apostas**. |
| A margem da casa aparece nos dados? | **Sim, nitidamente:** "favorito O/U 2.5" com 4.549 apostas deu **−3,87%** — é a margem pagando a conta. |

**A frase que resume:** *este app é excelente para saber **quanto vale** um
preço e para **provar em números** se você tem vantagem; ele ainda **não
tem** vantagem demonstrada para gerar os preços.* Quem usar o "⭐ Entrada de
valor" como sinal de aposta está depositando confiança em uma coisa que o
backtest não sustenta. Quem usar como disciplina de preço, gestão e
auditoria está usando uma ferramenta séria.

Isso não é motivo para jogar o app fora — é motivo para usá-lo com o
propósito correto, com sombra ligada e com CLV como métrica de decisão.

---

## 2. O que a auditoria mediu (e como)

1. **Walk-forward com blindagem de vazamento:** o modelo é reconstruído com
   os jogos disponíveis *até a data da partida* (histórico ≥ 8 jogos), inclusive
   o modelo conjunto por liga (`app/joint.py`). Nada do futuro entra no cálculo.
2. **Preço de verdade:** odds de **abertura** (o que se consegue antes do
   fechamento) e de **fechamento** (o melhor estimador disponível de
   probabilidade real). ROI é medido na abertura; CLV compara com o fechamento.
3. **Incerteza explícita:** todo ROI tem IC 95% por bootstrap (2.000
   reamostragens). ROI positivo com IC cruzando zero **não** é lucro
   demonstrado — foi assim que descartamos um resultado de +30,56% em 9
   apostas que virou −47,37% em 19 apostas fora da amostra.
4. **Teste de xG sem vazamento:** as curvas de calibração foram re-treinadas
   só em 19/20 e o teste rodado em 20/21 (96% dos jogos com xG do Understat).
   O arquivo que embarca no app foi treinado em 19/20–22/23 e também foi
   aplicado a temporadas *fora* dele neste relatório.
5. **Contraprova:** o *oráculo do fechamento* — um sistema que só aposta
   quando o preço de abertura é maior que o de fechamento desmarginalizado —
   serve para mostrar que o motor de backtest **encontra** vantagem quando
   ela existe na amostra. Ele achou (+9,45% de CLV). O modelo, não.

---

## 3. O que estava errado na v2.2 (e o que a v3 corrigiu)

A revisão do repositório encontrou problemas estruturais, não de estilo:

| Problema na v2.2 | Consequência | Correção na v3 |
|---|---|---|
| Seleção por "o mais provável", preço só como informação (`pick_best_market(..., mode="prob")`) | Aposta sem vantagem: 1368 pernas com 81,1% de acerto e ROI negativo | Seleção por **crescimento esperado de capital**, com EV e vantagem mínimos, faixa de odd e **preço-alvo** (`app/market.py`) |
| Sem remoção de margem no caminho de decisão (devig existia, mas não sustentava o valor) | Comparava probabilidade com preço sujo | Devig **proporcional, power e Shin**, com EV e vantagem em p.p. calculados sobre probabilidade limpa |
| Sem CLV, sem bilhetes sombra, sem IC | Era impossível saber se qualquer resultado era sorte | **CLV**, **sombra automática**, bootstrap, t-stat, drawdown |
| Calibração carregada uma vez e nunca recarregada | Curvas velhas em produção | Cache invalidado por `mtime` + proveniência (`_meta`) no JSON |
| Múltipla sem correlação nem margem | Odd combinada mentirosa e exposição sem teto | `app/parlay.py`: independência como cenário base, correlação como *upside*, margem embutida, teto de 8,00, escada e **cap diário** |
| Zero testes, zero CI, sem auth, sem health check | Refatorar era roleta russa; API aberta na rede | 68 testes (pytest), CI no GitHub, `APP_TOKEN`, `/api/health`, `FUTA_DB` |
| Sem dossiê de contexto | "Análise criteriosa" era só o placar mais provável | `app/dossier.py`: descanso, congestionamento, mando, forma ponderada, xG, volatilidade, bandeiras de risco |
| Backtest sem política de dinheiro | Não respondia "quanto eu ganharia?" | Backtest v3: Brier/LogLoss, quintis, alpha de CLV, 6 políticas com ROI + IC, Kelly, faixas de odd, múltiplas, devig e correlação |

A v3 é, portanto, o próprio resultado da auditoria — o diagnóstico virou
código, testes e números.

---

## 4. O que os números mostram, seção por seção

### 4.1 Poder preditivo (Brier — menor é melhor)

| Mercado | Ingênuo | Modelo | Calibrado | +mercado | Mercado |
|---|---|---|---|---|---|
| Over 2.5 | 0,2486 | 0,2569 | 0,2697 | 0,2413 | **0,2386** |
| Casa | 0,2453 | 0,2204 | 0,2279 | 0,2078 | **0,2058** |
| Fora | 0,2157 | 0,1943 | 0,2031 | 0,1840 | **0,1823** |
| Empate | 0,1895 | 0,1896 | 0,1943 | 0,1878 | **0,1870** |

Duas conclusões que doem, mas precisam estar escritas:

- **Em Over/Under o modelo cru é pior que o baseline ingênuo.** Não há como
  chamar isso de "modelo de gols" sem qualificar: sem xG, o motor de gols
  desta versão tem menos informação que a média histórica da liga.
- **A calibração, no estado atual, é fonte de erro — não de correção.** Neste
  teste ela piorou **os quatro mercados** (Over 0,2569 → 0,2697; casa 0,2204 →
  0,2279; fora 0,1943 → 0,2031; empate 0,1896 → 0,1943). A causa provável está
  na proveniência: as curvas que embarcam no app foram treinadas com **58%** de
  jogos cobertos por xG (`_meta.xg_matches = 4182` de 7203) e são aplicadas a
  um teste com **0%** de xG — mistura de populações. No teste *com* xG (seção
  4.7) a mesma calibração **melhorou** casa (0,2110 → 0,2082), fora (0,2004 →
  0,1978) e empate (0,1883 → 0,1875), e piorou apenas o Over (0,2463 →
  0,2470). Ou seja: a calibração funciona quando o regime de dados bate.
  **Corrigir isso é o item P0 da seção 8.**
- **A mistura com o mercado (25% modelo / 75% preço) é a única coisa que se
  aproxima do preço** — e é justamente porque 75% dela é o preço. Ela nunca
  bate o mercado, o que é honesto e esperado.

### 4.2 O modelo antecipa o fechamento? (alpha em CLV)

| Mercado | n | β | t | Leitura |
|---|---|---|---|---|
| Over 2.5 | 4549 | +0,0053 | 1,66 | sem alpha detectável |
| Casa | 4549 | −0,0081 | **−2,41** | **contrário** ao movimento |
| Fora | 4549 | −0,0093 | **−3,00** | **contrário** ao movimento |

O sinal negativo é a sentença mais importante desta auditoria: quando o
modelo se afasta do mercado, **o fechamento se afasta do modelo**. Em
português claro: o modelo não está encontrando valor; está encontrando o
lado errado da margem.

### 4.3 Quintis de divergência (Over 2.5)

| Quintil | Divergência (modelo − mercado) | Mercado dizia | Aconteceu |
|---|---|---|---|
| Q1 | −0,313 | 0,465 | **0,485** |
| Q5 | +0,113 | 0,504 | **0,492** |

Nos dois extremos, o mercado estava mais certo que o modelo. Um trader
competente lê isso como instrução: **não aposte na divergência do modelo
contra a linha; use a divergência para saber onde NÃO apostar.**

### 4.4 Dinheiro (odds de abertura, stake Kelly fracionado)

| Política | Apostas | Acerto | ROI | IC 95% | CLV médio |
|---|---|---|---|---|---|
| mais provável (v2) | 100 | 68,0% | −3,62% | [−16,7%, +9,0%] | +0,62% |
| casa valor+faixa (experimental) | 135 | 51,1% | −9,24% | [−25,1%, +5,5%] | −0,18% |
| **valor + faixa (v3)** | 46 | 47,8% | −1,87% | [−29,6%, +28,4%] | **+0,77%** |
| valor só-modelo (sem filtro de faixa) | 223 | 45,7% | −12,73% | [−25,4%, +0,1%] | −0,52% |
| **oráculo do fechamento** (benchmark) | 1230 | 57,9% | **+4,18%** | [−0,8%, +9,4%] | **+9,45%** |
| favorito O/U 2.5 (baseline burro) | 4549 | 58,6% | −3,87% | [−6,1%, −1,6%] | +0,18% |

Leituras obrigatórias:

- A política v3 é a **única política de modelo com CLV positivo** (+0,77%) —
  mas com 46 apostas isso é ruído, não vantagem. O IC do ROI vai de −29,6% a
  +28,4%: qualquer conclusão aqui é chute.
- "Valor só-modelo" (223 apostas) é o retrato do valor fantasma: acerto de
  45,7% e ROI de −12,73% — perdendo para a margem com folga.
- O único número grande e consistente da tabela é o do oráculo: **+9,45% de
  CLV em 1.230 apostas**. É onde está o dinheiro.
- O baseline "favorito" mostra que este backtest não é ingênuo: ele **acha**
  o −3,87% que a margem deveria produzir em 4.549 apostas.

### 4.5 Múltiplas

| Pernas | Bilhetes | Acerto | Odd média | ROI |
|---|---|---|---|---|
| 2 | 7 | 14,3% | 4,10 | −37,00% |
| 3 | 1 | 0,0% | 10,27 | −100,00% |

Amostras ridículas — e é exatamente o ponto: **múltipla é onde o apostador
perde dinheiro com mais frequência e mais rápido.** A contribuição real do
app aqui não é "achar a múltipla do dia"; é **mostrar a margem efetiva e
recusar combinações ruins** (teto de 8,00, cenário base de independência,
correlação só como upside, exposição diária máxima de 6% da banca). Uma
múltipla de 4 pernas com ρ de 0,06 rende menos do que o apostador imagina —
o app mostra isso antes de você clicar.

### 4.6 Correlação intra-rodada

- 8.322 pares de jogos: P(Over 2.5) = 0,551; produto das marginais = 0,29891;
  frequência conjunta observada = 0,30233 → **ρ(φ) = +0,0138**.

É pequeno, mas é positivo e consistente com o que se vê na prática (rodadas
de gols acontecem em bloco). Para múltipla, o efeito é *upside* pequeno; para
quem monta várias múltiplas no mesmo dia, é exposição correlacionada — e é
por isso que o app limita a exposição diária.

### 4.7 A única luz no fim do túnel: xG (teste sem vazamento)

Teste em 20/21 (1.425 jogos, 96% com xG), treino apenas em 19/20:

| Mercado | Modelo | Calibrado | +mercado | Mercado |
|---|---|---|---|---|
| Over 2.5 | 0,2463 | 0,2470 | 0,2423 | **0,2421** |
| Casa | 0,2110 | 0,2082 | 0,2015 | **0,2016** |
| Fora | 0,2004 | 0,1978 | 0,1928 | **0,1929** |
| Empate | 0,1883 | 0,1875 | 0,1861 | **0,1860** |

Com xG, o modelo **empata com o mercado** em todos os quatro mercados
(diferenças na quarta casa decimal, a favor e contra). E o mais interessante:

- **CLV alpha em casa: β = +0,0257, t = 2,94** — o modelo passou a
  *antecipar* o movimento da linha no mando de campo. Fora e Over: sem alpha.
- Dinheiro nesse recorte: "valor só-modelo" 48 apostas, +12,77% de ROI
  (IC [−13,9%, +39,6%]); "casa valor+faixa" 11 apostas, +20,09%
  (IC [−34,5%, +71,5%]); oráculo 348 apostas, +9,38%, CLV +10,71%.

**Como um trader lê isso:** a informação de xG é o que separa "modelo pior
que a média histórica" de "modelo empatado com o mercado". Não é prova de
vantagem (amostra pequena, um único teste, IC largo), mas é a **direção
certa** — e é a única pista positiva de todo o backtest. Prioridade número
um do roadmap: **xG completo e atual**, depois re-teste.

---

## 5. O que este app faz melhor que a média (e que deve ser preservado)

1. **Recusa.** O estado normal do app é "sem entrada". Isso é raríssimo em
   app de apostas e é a diferença entre método e vício.
2. **Preço, não palpite.** Preço-alvo (`(1+EV)/p`), odd de equilíbrio,
   vantagem em pontos de probabilidade e margem efetiva da múltipla.
3. **Disciplina de risco embutida:** Kelly fracionado (0,25) com quantil 0,30
   da posterior Beta (desconta amostra pequena), teto de 3% por aposta e 6%
   por dia, teto de odd combinada de 8,00.
4. **CLV como métrica central**, com registro da odd de fechamento e
   bilhetes-sombra automáticos para toda recomendação — inclusive as
   recusadas, que é o que permite medir o filtro.
5. **Dossiê criterioso** (`app/dossier.py`): descanso e congestionamento,
   splits de mando, forma ponderada, gols acima/abaixo do xG (quem marca mais
   do que cria tende a regredir — o item mais subestimado em análise de
   futebol), volatilidade, "jogo travado", força do calendário, bandeiras de
   risco e nota de confiança com motivos.
6. **Reprodutibilidade:** o backtest é o próprio produto da auditoria —
   qualquer promessa nova pode ser medida no mesmo terreno, com IC e com
   contraprova (o oráculo).
7. **Engenharia honesta:** 68 testes, CI, token de API, health check, SQLite
   com WAL, caminho de disco persistente documentado, proveniência da
   calibração gravada no JSON.

---

## 6. O que **não** foi demonstrado (limitações que precisam constar)

- **Não há vantagem demonstrada** para apostar nas recomendações do modelo
  nas cinco grandes ligas contra a linha de abertura da B365.
- **xG sozinho não basta:** a cobertura disponível no espelho de dados é
  histórica (até 2021/22 parcial). Sem xG atual, o motor roda no modo "gols",
  que é justamente o que perde para o baseline ingênuo em Over.
- **Só uma casa (B365) e só mercados de gols/1X2** foram testados. Não há
  odds de mercados de time (marca sim/não, totais por time), nem comparação
  entre casas — ou seja, **não há teste de line shopping**, que é metade do
  trabalho de um trader profissional.
- **Modelo não vê escalação, lesão, suspensão, viagem, clima, arbitragem nem
  motivação.** A bandeira de risco do dossiê sinaliza; ela não resolve.
- **ρ intra-rodada é medido no nível de desfecho** (±1 dia, mesma liga).
  Números pequenos, medidos em 8.322 pares, servem para dimensionar risco de
  carteira — não para "prever" jogos.
- **Calibração é fonte de erro no estado atual** (ver 4.1) — e está ligada por
  padrão no app. Ver P0 abaixo.
- **Modo demonstração é circular:** os preços nascem do próprio modelo.
  Serve para conhecer a interface; **nunca** para concluir que há valor.
- Toda estatística aqui é de uma janela específica (23/24–25/26). Regime
  muda: janela de gols, estilo de jogo, regras. Nada disso é profecia.

---

## 7. Como usar hoje sem se machucar (regras de operação)

1. **Objetivo do app:** descobrir o **preço certo** e **provar vantagem**. Não
   é sinal de compra. A "⭐ Entrada de valor" é uma hipótese, não uma ordem.
2. **Cole sempre a odd da sua casa** (a que você realmente consegue). Sem
   preço, não existe avaliação de valor.
3. **Só aposte acima do preço-alvo** mostrado no cartão. Se o preço-alvo está
   acima do que a casa oferece, a resposta é não apostar.
4. **Registre a odd de fechamento de todo bilhete.** A decisão de aumentar,
   manter ou parar o método é tomada pelo **CLV médio em 300+ apostas**, nunca
   pelo ROI de 20.
5. **Sombra sempre ligada.** Ela é o seu arquivo de provas: comparar acerto
   prometido × realizado é o único jeito de descobrir se o filtro funciona.
6. **Múltipla: no máximo 2 pernas, e só quando o app mostra margem efetiva
   baixa.** Os dados desta auditoria (2 pernas: −37%) não autorizam
   otimismo.
7. **Stake do app é o teto, não a meta.** Kelly 0,25 já é conservador; se a
   sua banca é o que você não pode perder, aposte menos.
8. **Suspeite de EV alto.** O app recusa acima de 25% de EV justamente porque,
   sem notícia de escalação, EV alto costuma ser erro de dado ou jogo
   manipulado, não ouro.

---

## 8. Checklist priorizado (o que fazer para o app virar lucro)

### P0 — sem isso, o resto é conversa

1. **xG completo e recente** (Understat por temporada, atualizado). É o único
   fator que levou o modelo de "pior que o baseline" a "empatado com o
   mercado" e que produziu CLV alpha positivo (casa, t = 2,94). Re-treinar
   calibração com esse dado e re-rodar o walk-forward.
2. **Corrigir a calibração.** (a) Treinar e servir curvas **por regime de
   disponibilidade** (com xG / sem xG / com modelo conjunto), em vez de
   aplicar curvas treinadas com 58% de xG num teste com 0%; (b) se a curva
   não melhorar o Brier fora da amostra, **desligar a calibração para aquele
   mercado** em vez de servi-la; (c) registrar no app qual curva foi usada em
   cada ficha (rastreabilidade já existe no `_meta` — falta mostrar na ficha).
3. **CLV como KPI de produto.** Meta: 300 bilhetes com CLV médio > 0. Hoje a
   política v3 tem +0,77% em 46 apostas — promissor, longe de prova.
4. **Line shopping real:** no mínimo duas fontes de odds (uma casa asiática e
   uma europeia) e regra de pegar o melhor preço. Sem isso, metade do CLV
   possível é perdida antes de qualquer modelo.

### P1 — aumenta a chance de vantagem existir

5. **Modelo com xG como base** (e gols como fallback), com pesos aprendidos —
   hoje o blend é fixo em 65% quando há cobertura.
6. **Estender o teste a mercados de time** (marca sim/não, totais por time) e
   a ligas menores, onde a margem é maior e o modelo pode ter mais espaço.
7. **Calibrar as probabilidades de mercado com o próprio preço de fechamento**
   (a linha de fechamento é o melhor professor disponível): usar fechamento
   como alvo de calibração, não só como métrica.
8. **Detecção de movimento anômalo** (steam) como alerta: se o preço cai mais
   de X% depois de você apostar, o mercado sabe algo — revisar o bilhete.
9. **Filtros de contexto no sinal:** só recomendar quando o dossiê traz
   descanso/congestionamento favorável e nenhuma bandeira de risco grave.

### P2 — qualidade de operação

10. **Relatório de recusas:** quantas apostas o filtro recusou e como elas se
    saíram (o bilhete-sombra já grava; falta o relatório dedicado).
11. **Exportar CSV/JSON de bilhetes e CLV** para planilha/contabilidade.
12. **Múltiplas com correlação estimada por liga e por família de mercado**
    (hoje 0,06 fixo para mesma liga/família) — pequeno, mas honesto.
13. **Deploy com banca única e backup automático** (hoje: backup manual em
    JSON + `FUTA_DB` em disco persistente; o plano gratuito do Render
    hiberna).

---

## 9. Reprodução (comandos exatos)

```bash
# amostra principal desta auditoria (relatório + resumo JSON)
python3 -m app.backtest --divs E0 SP1 I1 D1 F1 \
    --train 1920 2021 2122 2223 --test 2324 2425 2526 \
    --report backtest_report.md --json backtest_summary.json

# re-treinar as curvas de calibração nas temporadas de treino (grava _meta)
python3 -m app.backtest --train 1920 2021 2122 2223 --fit-calibration

# teste com xG SEM vazamento: treino só em 19/20, teste em 20/21
python3 -m app.backtest --train 1920 --test 2021 --calib-file <curvas_treinadas_em_1920.json>

# espelho local (roda offline)
FUTA_DATA_DIR=/caminho/<temporada>/<DIV>.csv FUTA_XG_DIR=/caminho/understat.csv \
    python3 -m app.backtest ... 
```

Testes e lint: `pip install -e ".[dev]" && ruff check app tests && pytest -q` (68 testes).

---

## 10. Conclusão do auditor

Eu não colocaria dinheiro sério nas recomendações deste modelo **hoje**.
Colocaria dinheiro — e já coloquei em sistemas piores — na **disciplina** que
ele impõe: só apostar acima do preço justo, registrar o fechamento, medir CLV,
limitar exposição, escrever a razão de cada recusa.

Se a pergunta é *"isso pode virar um sistema lucrativo?"*, a resposta é:
**pode, mas não por causa do motor de gols atual — e sim pelo caminho que a
auditoria abriu.** O dado de xG é o primeiro sinal positivo que apareceu em
todo o backtest. Ele e o line shopping são a diferença entre um app que
**explica** preço e um app que **ganha** dinheiro.

Enquanto isso: sombra ligada, CLV no comando, e nenhuma aposta que o preço não
autorize.
