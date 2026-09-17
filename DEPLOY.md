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
- **Banco de dados (`FUTA_DB`)**: no plano **free NÃO existe disco persistente**,
  então o banco fica na raiz do projeto e é apagado a cada deploy/reinício
  (use o backup em JSON; veja os avisos abaixo). O `render.yaml` **não** define
  `FUTA_DB` de propósito: apontar para `/var/data` sem disco faria o SQLite
  falhar. Em plano **pago**, crie um **Disk** (Settings > Disks, 1 GB basta)
  montado em `/var/data` e descomente as duas linhas de `FUTA_DB` no
  `render.yaml` — aí o banco sobrevive a deploys.
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

## Plano gratuito do Render: o que a v3 consome (medido, não estimado)

Medições feitas no próprio projeto (Python 3.11, 6 jogos analisados por dia):

| Recurso | Limite do free | Consumo da v3 | Situação |
|---|---|---|---|
| RAM | 512 MB | **62 MB** com o app rodando (46 MB só de import) | folga de ~8× |
| CPU | 0,1 vCPU compartilhado | 0,3–0,5 s por dia analisado aqui (máquina rápida); no free conte 2–5 s | ok |
| Build | 500 min/mês | `pip install -r requirements.txt` (4 pacotes, sem dependência nova) | ~1–2 min por deploy |
| Instância | 750 h/mês **por workspace** | dorme após 15 min sem acesso; uso real fica bem abaixo | ok — **desde que você não use "pinger" para manter acordado** |
| Disco | **não existe no free** | banco + cache na raiz do projeto, apagados a cada deploy/reinício | ver avisos |

O que isso significa na prática:

- **O app não estoura os limites do free.** O peso é o mesmo da v2: mesmo
  build, mesma RAM, cold start igual. A v3 trocou "mais modelo" por "mais
  matemática de preço e registro" — tudo em cima do que já existia.
- **Não use serviço de "keep-alive"/pinger.** Manter o serviço acordado 24/7
  consome as 750 h do workspace; um segundo serviço acordado estoura o mês e o
  Render **suspende todos os serviços free** até o dia 1. Deixe dormir.
- **O disco é apagado a cada deploy e a cada reinício da instância:** as
  configurações voltam ao padrão e os bilhetes sombra/registrados se perdem.
  Os tokens (FD_TOKEN/AF_KEY/APP_TOKEN) não, porque são variáveis de ambiente.
  Antes de atualizar o código, use **"Baixar backup"** em Configurações e
  **"Restaurar"** depois do deploy — o JSON carrega banca, bilhetes, CLV, modo
  sombra e configurações. Faça backup com frequência; é o único seguro no free.
- **Primeira carga de cada "acordar" pode ser lenta** (30–60 s do cold start +
  a busca de xG no Understat, que fica em cache no banco — cache que o
  spin-down apaga). O app cai no histórico de gols se o Understat não responder.
- **O backtest NÃO roda no Render** (nem deve): é ferramenta de PC. Rodar
  `python -m app.backtest` no free levaria muitos minutos de CPU e pode ser
  interrompido no meio.
- **Dado operacional — o CI pegou um bug que derrubava a análise no Render:** o
  `league_xg_priors` não aceitava a tupla de 7 campos que o Understat devolve
  (`ValueError: too many values to unpack`), e isso só aparecia **com internet
  disponível** — no seu PC com Understat bloqueado ele passava batido. Corrigido
  nesta versão, com teste de regressão (`tests/test_model.py`).

## Alternativa sem GitHub: PythonAnywhere

Se preferir não usar GitHub, o pythonanywhere.com gratuito também roda o
projeto (upload manual dos arquivos + app web ASGI). O Render via GitHub é
mais simples de manter atualizado.
