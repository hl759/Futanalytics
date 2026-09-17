# Relatório de backtest — FutAnalytics v3

Gerado em 2026-09-17 · teste ['2324', '2425', '2526'] · treino ['1920', '2021', '2122', '2223'] · ligas E0, SP1, I1, D1, F1

*4549 jogos avaliáveis (histórico ≥ 8 jogos) · cobertura de xG no teste: 0% · devig: power · peso do modelo na mistura: 25%*

**Como ler este relatório.** ROI medido nas odds de **abertura** da B365 (o preço que se consegue de verdade antes do fechamento), com intervalo de confiança de 95% por bootstrap. CLV = quanto a odd pega bateu a odd de fechamento — o melhor indicador antecedente de vantagem real. Amostra pequena com IC largo **não** é evidência.

---

## 1. Poder preditivo

### Brier e LogLoss (modelo / calibrado / misturado / mercado)

| Mercado | Brier ingênuo | Brier modelo | Brier calibrado | Brier +mercado | Brier mercado | LogLoss modelo | LogLoss mercado |
|---|---|---|---|---|---|---|---|
| over_2.5 | 0.2486 | 0.2569 | 0.2697 | 0.2413 | 0.2386 | 0.7126 | 0.6698 |
| home | 0.2453 | 0.2204 | 0.2279 | 0.2078 | 0.2058 | 0.6337 | 0.5973 |
| away | 0.2157 | 0.1943 | 0.2031 | 0.1840 | 0.1823 | 0.5744 | 0.5429 |
| draw | 0.1895 | 0.1896 | 0.1943 | 0.1878 | 0.1870 | 0.5674 | 0.5593 |

*Baseline ingênuo (frequência base): 0.537 de Over 2.5 na amostra.*

**Teste de alfa (Over 2.5):** quando o modelo diverge do mercado, quem acerta?

| Quintil | Divergência (modelo−mercado) | Mercado diz | Real |
|---|---|---|---|
| Q1 | -0.313 | 0.465 | 0.485 |
| Q2 | -0.099 | 0.585 | 0.595 |
| Q3 | -0.024 | 0.572 | 0.560 |
| Q4 | +0.035 | 0.545 | 0.554 |
| Q5 | +0.113 | 0.504 | 0.492 |

### O modelo antecipa o fechamento? (alpha em CLV)

| Mercado | n | β | erro t | R² | Leitura |
|---|---|---|---|---|---|
| over_2.5 | 4549 | +0.005 | +1.66 | 0.001 | sem alpha detectável |
| home | 4549 | -0.008 | -2.41 | 0.001 | o modelo é sistematicamente CONTRÁRIO ao movimento |
| away | 4549 | -0.009 | -3.00 | 0.002 | o modelo é sistematicamente CONTRÁRIO ao movimento |

Referência: |t| < 2 significa que a divergência do modelo não prevê o fechamento — apostar nela é, na média, pagar a margem.

## 2. Políticas em dinheiro

### Políticas em dinheiro (odds de abertura; stake Kelly fracionado)

| Política | Apostas | Acerto | ROI | IC 95% do ROI | ROI Kelly | Drawdown máx | CLV médio | Bateu fechamento |
|---|---|---|---|---|---|---|---|---|
| mais provável (v2) | 100 | 68.0% | -3.62% | [-16.7%, +9.0%] | +0.00% | 0.0% | +0.62% | 45.0% (100) |
| casa valor+faixa (exp.) | 135 | 51.1% | -9.24% | [-25.1%, +5.5%] | -20.46% | 28.6% | -0.18% | 41.5% (135) |
| valor+faixa (v3) | 46 | 47.8% | -1.87% | [-29.6%, +28.4%] | -1.73% | 6.0% | +0.77% | 47.8% (46) |
| valor só-modelo | 223 | 45.7% | -12.73% | [-25.4%, +0.1%] | -20.70% | 21.4% | -0.52% | 38.1% (223) |
| oráculo do fechamento | 1230 | 57.9% | +4.18% | [-0.8%, +9.4%] | +1.73% | 0.3% | +9.45% | 100.0% (1230) |
| favorito O/U 2.5 | 4549 | 58.6% | -3.87% | [-6.1%, -1.6%] | +0.00% | 0.0% | +0.18% | 42.6% (4549) |

**ROI por faixa de odd (política v3, odds de abertura)** — onde o dinheiro vive:

| Faixa de odd | Apostas | Acerto | ROI | IC 95% |
|---|---|---|---|---|
| 1.80–2.20 | 39 | 48.7% | -1.64% | [-32.8%, +29.5%] |
| 2.20–3.00 | 7 | 42.9% | -3.14% | [-68.6%, +91.1%] |

**Múltiplas (pernas elegíveis por dia, montadas por crescimento esperado):**

| Pernas | Bilhetes | Acerto | Odd média | ROI | Margem efetiva média |
|---|---|---|---|---|---|
| 2 | 7 | 14.3% | 4.10 | -37.00% [-100.0%, +89.0%] | +0.00% |
| 3 | 1 | 0.0% | 10.27 | -100.00% — | +0.00% |

### Métodos de devig (qual descreve melhor o mercado?)

| Método | Brier home | Brier draw | Brier away | Brier Over 2.5 |
|---|---|---|---|---|
| proportional | 0.2060 | 0.1869 | 0.1827 | 0.2387 |
| power | 0.2058 | 0.1870 | 0.1823 | 0.2386 |
| shin | 0.2059 | 0.1869 | 0.1824 | 0.2386 |

## 3. Correlação entre jogos da mesma rodada

- Pares de jogos analisados: **8322**
- P(Over 2.5) individual: **0.551** · produto das marginais: **0.29891** · probabilidade conjunta observada: **0.30233**
- ρ (phi) implícito: **+0.0138**

## 4. Limitações (leia antes de usar)

- Odds de abertura B365 apenas: não há comparação entre casas nem odds de mercados de time (ambas marcam sim/não, totais por time).
- A amostra de cada liga é de ~1.400 jogos por temporada; ROI com IC que cruza zero **não** é lucro demonstrado.
- A calibração é treinada em temporadas passadas e aplicada adiante; mudanças de regime (regras, estilo de jogo) reduzem sua validade.
- Modelo não vê escalação, lesão, motivação ou clima: EV alto suspeito deve ser conferido antes de virar aposta.
