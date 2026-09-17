# Como colocar o FutAnalytics no ar com link fixo (grátis, ~10 min)

> **Antes de mais nada — leia o `AUDITORIA.md`.** O que o app faz muito bem é
> disciplina, matemática de preço e auditoria (CLV). O que o backtest **não**
> demonstrou é vantagem sistemática contra a linha de abertura da B365 nas
> cinco grandes ligas. Trate este deploy como um painel de decisão e de
> registro — não como uma máquina de imprimir dinheiro.

Resultado final: um endereço permanente tipo `https://futanalytics.onrender.com`
que você abre em qualquer celular ou computador e salva na tela inicial como app.

## Passo 1: baixar o projeto

Nesta conversa do Arena, baixe a pasta `futanalytics` (botão de download no
visualizador de arquivos). Você precisa destes arquivos:

```
futanalytics/
  app/            (main.py, model.py, provider.py, db.py, joint.py,
                   calibration.py, understat.py, backtest.py,
                   calibration_data.json)   ← obrigatório: curvas de calibração
  static/         (index.html)
  requirements.txt
  render.yaml
```

**NÃO suba** para o GitHub/Render (pesa no repositório e não serve lá):
`futanalytics.db` (seu banco local) e `data_cache/` (cache de backtest, ~10 MB).

**Novidades da v3 no deploy:**
- `requirements.txt` NÃO mudou: zero dependência nova, o build fica igual.
- **Token de escrita (`APP_TOKEN`)**: o `render.yaml` já gera um. A primeira vez
  que você abrir o app, ele pede o token (o Render mostra o valor em
  Environment Variables); o app guarda no navegador. Sem isso, qualquer pessoa
  com o link podia apagar seus bilhetes e ler suas configurações.
- **Banco em disco persistente (`FUTA_DB`)**: o `render.yaml` aponta para
  `/var/data/futanalytics.db`. Para isso valer, crie um **Disk** no Render
  (Settings > Disks, 1 GB é o suficiente) montado em `/var/data`. Sem disco, o
  banco é apagado a cada deploy — e o backup/restauração continua sendo o
  caminho.
- O xG vem do Understat (grátis, sem chave). Nas ligas com cobertura (Premier,
  La Liga, Serie A, Bundesliga, Ligue 1) a carga inicial é rápida (1 requisição
  cacheada por liga). Fora delas o app cai no histórico de gols brutos.
- O `app/backtest.py` é ferramenta OFFLINE para rodar no SEU computador
  (`python -m app.backtest ...`). No Render ele não roda.
- A calibração já vem pronta em `app/calibration_data.json` (9 KB, com
  proveniência: temporadas, ligas e data do ajuste). Regenere no seu PC quando
  tiver temporadas novas:
  `python -m app.backtest --train 1920 2021 2122 2223 --fit-calibration`
- **Backtest de verdade** (não confie em opinião): este comando baixa os jogos
  do football-data.co.uk, mede Brier/LogLoss/ROI/CLV contra o mercado e gera o
  `backtest_report.md`. Para rodar desconectado, aponte um espelho local:
  `FUTA_DATA_DIR=/caminho/com/csvs python -m app.backtest ...`.

## Passo 2: subir para o GitHub

1. Crie uma conta gratuita em github.com (se não tiver).
2. Crie um repositório novo, por exemplo `futanalytics` (pode ser privado).
3. Envie os arquivos: na página do repositório, "Add file" > "Upload files"
   e arraste a pasta inteira. Confirme com "Commit changes".

## Passo 3: publicar no Render

1. Crie conta gratuita em render.com (pode entrar com o GitHub, sem cartão).
2. Clique em "New +" > "Web Service" e conecte o repositório `futanalytics`.
3. O Render lê o `render.yaml` sozinho. Se pedir os campos manualmente:
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Plan: Free
4. Em "Environment Variables", adicione:
   - `FD_TOKEN` = seu token da football-data.org
   (assim o token sobrevive a redeploys; recomendo gerar um token NOVO,
   porque o atual apareceu numa captura de tela)
5. Clique em "Create Web Service" e aguarde o deploy (2 a 5 min).

## Passo 4: usar no celular

1. Abra o link `https://SEU-APP.onrender.com` no Chrome do celular.
2. Menu do Chrome (três pontos) > "Adicionar à tela inicial".
3. Vira um ícone que abre como aplicativo.

## Avisos do plano gratuito do Render

- O serviço "dorme" após 15 min sem uso; a primeira visita do dia demora
  ~1 min para acordar. Depois fica rápido. (Isso é o Render free, não o app —
  e acontece igual na v1 e na v2.)
- O disco é apagado a cada deploy: as configurações voltam ao padrão e os
  bilhetes registrados são perdidos. O token não, se estiver na variável
  FD_TOKEN. Para o resto, use o botão "Baixar backup" em Configurações antes
  de atualizar o código e "Restaurar" depois do deploy — o arquivo carrega
  banca, bilhetes, CLV, modo sombra e configurações.
- Custo da v2 comparado à v1 no free: +9 KB de arquivo estático e, no pior
  caso (Understat fora do ar), +10 s na primeira carga antes do fallback
  automático. Nada a mais: mesmo build, mesma RAM, mesmo cold start.

## Alternativa sem GitHub: PythonAnywhere

Se preferir não usar GitHub, o pythonanywhere.com gratuito também roda o
projeto (upload manual dos arquivos + app web ASGI). O Render via GitHub é
mais simples de manter atualizado.
