# Relatório de backtest — FutAnalytics v2

Gerado em 2026-09-11 · treino ['2324', '2425'] · teste ['2526'] (E0, SP1, I1, D1, F1)

---

## v2.0: gols, estimador por time

*1368 jogos avaliáveis (histórico ≥ 8 jogos)*

### Métricas preditivas — v2.0: gols, estimador por time

| Mercado | Brier modelo | Brier mercado | Brier ingênuo | LogLoss modelo | LogLoss mercado |
|---|---|---|---|---|---|
| over_2.5 | 0.2492 | 0.2421 | 0.2488 | 0.6932 | 0.6768 |
| home | 0.2443 | 0.2115 | 0.2458 | 0.6816 | 0.6102 |
| away | 0.2145 | 0.1875 | 0.2142 | 0.6204 | 0.5561 |
| draw | 0.1884 | 0.1879 | 0.1897 | 0.5637 | 0.5622 |

**Calibração Over 2.5** (prob. média prevista → frequência real):

| Faixa | Previsto | Real | n | Desvio |
|---|---|---|---|---|
| 20%–40% | 0.333 | 0.375 | 16 | +0.042 |
| 40%–60% | 0.508 | 0.532 | 1173 | +0.024 |
| 60%–80% | 0.631 | 0.568 | 176 | -0.063 |
| 80%–100% | 0.952 | 0.667 | 3 | -0.285 |

**Teste de alfa (Over 2.5):** ao divergir do mercado, quem tem razão?

| Quintil | Divergência (mod-mkt) | Mercado diz | Real |
|---|---|---|---|
| Q1 | -0.118 | 0.628 | 0.652 |
| Q2 | -0.041 | 0.554 | 0.542 |
| Q3 | +0.004 | 0.520 | 0.542 |
| Q4 | +0.039 | 0.486 | 0.473 |
| Q5 | +0.107 | 0.436 | 0.467 |

### Estratégia (blend modelo 25%, min_ev 3%, Kelly c/ desconto de incerteza)

**PRODUÇÃO — modelo calibrado + mercado (25%):** nenhuma aposta passou o filtro de EV (disciplina: sem valor detectado, sem aposta).

**SÓ MODELO — calibrado, sem mistura com mercado:** 1 apostas · ROI flat **+20.00%** · ROI Kelly **+0.60%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| over_2.5 | 1 | +20.00% |

CLV vs. fechamento B365 (1 apostas): média **+2.56%** · bateu o fechamento em **100.0%**

**Baseline (todo Over 2.5 na B365):** 1368 apostas · ROI flat **-2.52%**


## v2.1: xG + calibração

*1368 jogos avaliáveis (histórico ≥ 8 jogos)*

### Métricas preditivas — v2.1: xG + calibração

| Mercado | Brier modelo | Brier mercado | Brier ingênuo | LogLoss modelo | LogLoss mercado |
|---|---|---|---|---|---|
| over_2.5 | 0.2478 | 0.2421 | 0.2488 | 0.6885 | 0.6768 |
| home | 0.2326 | 0.2115 | 0.2458 | 0.6586 | 0.6102 |
| away | 0.2105 | 0.1875 | 0.2142 | 0.6108 | 0.5561 |
| draw | 0.1905 | 0.1879 | 0.1897 | 0.5693 | 0.5622 |

**Calibração Over 2.5** (prob. média prevista → frequência real):

| Faixa | Previsto | Real | n | Desvio |
|---|---|---|---|---|
| 20%–40% | 0.387 | 0.397 | 73 | +0.011 |
| 40%–60% | 0.522 | 0.538 | 1158 | +0.016 |
| 60%–80% | 0.655 | 0.576 | 132 | -0.079 |
| 80%–100% | 0.910 | 0.800 | 5 | -0.110 |

**Teste de alfa (Over 2.5):** ao divergir do mercado, quem tem razão?

| Quintil | Divergência (mod-mkt) | Mercado diz | Real |
|---|---|---|---|
| Q1 | -0.105 | 0.615 | 0.601 |
| Q2 | -0.032 | 0.547 | 0.575 |
| Q3 | +0.009 | 0.518 | 0.604 |
| Q4 | +0.043 | 0.495 | 0.462 |
| Q5 | +0.105 | 0.448 | 0.435 |

### Estratégia (blend modelo 25%, min_ev 3%, Kelly c/ desconto de incerteza)

**PRODUÇÃO — modelo calibrado + mercado (25%):** 2 apostas · ROI flat **+22.50%** · ROI Kelly **+0.00%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| over_2.5 | 2 | +22.50% |

CLV vs. fechamento B365 (2 apostas): média **+2.51%** · bateu o fechamento em **100.0%**

**SÓ MODELO — calibrado, sem mistura com mercado:** 2 apostas · ROI flat **+59.00%** · ROI Kelly **+2.79%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| over_2.5 | 1 | +25.00% |
| under_2.5 | 1 | +93.00% |

CLV vs. fechamento B365 (2 apostas): média **+2.61%** · bateu o fechamento em **100.0%**

**Baseline (todo Over 2.5 na B365):** 1368 apostas · ROI flat **-2.52%**


## v2.2: modelo conjunto por adversário + xG + calibração

*1368 jogos avaliáveis (histórico ≥ 8 jogos)*

### Métricas preditivas — v2.2: modelo conjunto por adversário + xG + calibração

| Mercado | Brier modelo | Brier mercado | Brier ingênuo | LogLoss modelo | LogLoss mercado |
|---|---|---|---|---|---|
| over_2.5 | 0.2460 | 0.2421 | 0.2488 | 0.6859 | 0.6768 |
| home | 0.2256 | 0.2115 | 0.2458 | 0.6464 | 0.6102 |
| away | 0.1997 | 0.1875 | 0.2142 | 0.5908 | 0.5561 |
| draw | 0.1920 | 0.1879 | 0.1897 | 0.5769 | 0.5622 |

**Calibração Over 2.5** (prob. média prevista → frequência real):

| Faixa | Previsto | Real | n | Desvio |
|---|---|---|---|---|
| 20%–40% | 0.391 | 0.500 | 42 | +0.109 |
| 40%–60% | 0.532 | 0.523 | 1106 | -0.009 |
| 60%–80% | 0.658 | 0.583 | 204 | -0.075 |
| 80%–100% | 0.933 | 0.875 | 16 | -0.058 |

**Teste de alfa (Over 2.5):** ao divergir do mercado, quem tem razão?

| Quintil | Divergência (mod-mkt) | Mercado diz | Real |
|---|---|---|---|
| Q1 | -0.074 | 0.606 | 0.612 |
| Q2 | -0.008 | 0.540 | 0.579 |
| Q3 | +0.027 | 0.525 | 0.505 |
| Q4 | +0.062 | 0.495 | 0.469 |
| Q5 | +0.124 | 0.457 | 0.511 |

### Estratégia (blend modelo 25%, min_ev 3%, Kelly c/ desconto de incerteza)

**PRODUÇÃO — modelo calibrado + mercado (25%):** 5 apostas · ROI flat **+25.60%** · ROI Kelly **+0.00%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| over_2.5 | 5 | +25.60% |

CLV vs. fechamento B365 (5 apostas): média **+1.33%** · bateu o fechamento em **80.0%**

**SÓ MODELO — calibrado, sem mistura com mercado:** 5 apostas · ROI flat **+29.80%** · ROI Kelly **+2.32%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| over_2.5 | 5 | +29.80% |

CLV vs. fechamento B365 (5 apostas): média **+0.74%** · bateu o fechamento em **60.0%**

**Baseline (todo Over 2.5 na B365):** 1368 apostas · ROI flat **-2.52%**


## v2.2 · dinâmica 'mais provável' (produção atual)

### Dinâmica v2.1 — 'linha mais segura de gols por jogo'

**1368 pernas** · acerto real **81.1%** · prob média prometida 83.3% (desvio -2.2 p.p.)

| Mercado | Pernas | Acerto |
|---|---|---|
| ht_0.5 | 642 | 83.3% |
| over_1.5 | 417 | 77.9% |
| under_3.5 | 157 | 78.3% |
| at_0.5 | 136 | 83.1% |
| over_2.5 | 9 | 100.0% |
| ht_1.5 | 4 | 50.0% |
| over_3.5 | 2 | 50.0% |
| btts_yes | 1 | 100.0% |

**Múltipla do dia (até 4 pernas):** 101 dias · green total **49.5%** · 3 pernas: **58.7%** (121 dias)

