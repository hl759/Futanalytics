# Relatório de backtest — FutAnalytics v2

Gerado em 2026-09-10 · treino ['2324', '2425'] · teste ['2526'] (E0, SP1, I1, D1, F1)

---

## v1: só gols, sem calibração

*1368 jogos avaliáveis (histórico ≥ 8 jogos)*

### Métricas preditivas — v1: só gols, sem calibração

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

**PRODUÇÃO — modelo calibrado + mercado (25%):** 1 apostas · ROI flat **-100.00%** · ROI Kelly **+0.00%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| over_2.5 | 1 | -100.00% |

CLV vs. fechamento B365 (1 apostas): média **+12.72%** · bateu o fechamento em **100.0%**

**SÓ MODELO — calibrado, sem mistura com mercado:** 44 apostas · ROI flat **-15.75%** · ROI Kelly **-7.16%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| under_2.5 | 26 | -37.08% |
| over_2.5 | 17 | -1.71% |
| away | 1 | +300.00% |

CLV vs. fechamento B365 (44 apostas): média **+1.85%** · bateu o fechamento em **45.5%**

**Baseline (todo Over 2.5 na B365):** 1368 apostas · ROI flat **-2.52%**


## v1 + calibração

*1368 jogos avaliáveis (histórico ≥ 8 jogos)*

### Métricas preditivas — v1 + calibração

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

**PRODUÇÃO — modelo calibrado + mercado (25%):** 1 apostas · ROI flat **-100.00%** · ROI Kelly **+0.00%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| over_2.5 | 1 | -100.00% |

CLV vs. fechamento B365 (1 apostas): média **+12.72%** · bateu o fechamento em **100.0%**

**SÓ MODELO — calibrado, sem mistura com mercado:** 44 apostas · ROI flat **-15.75%** · ROI Kelly **-7.16%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| under_2.5 | 26 | -37.08% |
| over_2.5 | 17 | -1.71% |
| away | 1 | +300.00% |

CLV vs. fechamento B365 (44 apostas): média **+1.85%** · bateu o fechamento em **45.5%**

**Baseline (todo Over 2.5 na B365):** 1368 apostas · ROI flat **-2.52%**


## v2: xG sem calibração

*1368 jogos avaliáveis (histórico ≥ 8 jogos)*

### Métricas preditivas — v2: xG sem calibração

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

**PRODUÇÃO — modelo calibrado + mercado (25%):** nenhuma aposta passou o filtro de EV (disciplina: sem valor detectado, sem aposta).

**SÓ MODELO — calibrado, sem mistura com mercado:** 107 apostas · ROI flat **-9.39%** · ROI Kelly **-35.19%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| home | 52 | -27.15% |
| under_2.5 | 20 | -24.35% |
| over_2.5 | 19 | +34.95% |
| away | 16 | +14.37% |

CLV vs. fechamento B365 (107 apostas): média **+1.27%** · bateu o fechamento em **43.0%**

**Baseline (todo Over 2.5 na B365):** 1368 apostas · ROI flat **-2.52%**


## v2 completa: xG + calibração

*1368 jogos avaliáveis (histórico ≥ 8 jogos)*

### Métricas preditivas — v2 completa: xG + calibração

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

**PRODUÇÃO — modelo calibrado + mercado (25%):** nenhuma aposta passou o filtro de EV (disciplina: sem valor detectado, sem aposta).

**SÓ MODELO — calibrado, sem mistura com mercado:** 107 apostas · ROI flat **-9.39%** · ROI Kelly **-35.19%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| home | 52 | -27.15% |
| under_2.5 | 20 | -24.35% |
| over_2.5 | 19 | +34.95% |
| away | 16 | +14.37% |

CLV vs. fechamento B365 (107 apostas): média **+1.27%** · bateu o fechamento em **43.0%**

**Baseline (todo Over 2.5 na B365):** 1368 apostas · ROI flat **-2.52%**


## v2 completa · produção com min_ev 1% (mais volume)

### Estratégia (blend modelo 25%, min_ev 1%, Kelly c/ desconto de incerteza)

**PRODUÇÃO — modelo calibrado + mercado (25%):** nenhuma aposta passou o filtro de EV (disciplina: sem valor detectado, sem aposta).

**SÓ MODELO — calibrado, sem mistura com mercado:** 107 apostas · ROI flat **-9.39%** · ROI Kelly **-35.19%**

| Mercado | Apostas | ROI flat |
|---|---|---|
| home | 52 | -27.15% |
| under_2.5 | 20 | -24.35% |
| over_2.5 | 19 | +34.95% |
| away | 16 | +14.37% |

CLV vs. fechamento B365 (107 apostas): média **+1.27%** · bateu o fechamento em **43.0%**

**Baseline (todo Over 2.5 na B365):** 1368 apostas · ROI flat **-2.52%**

