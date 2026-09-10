# Como colocar o FutAnalytics no ar com link fixo (grátis, ~10 min)

Resultado final: um endereço permanente tipo `https://futanalytics.onrender.com`
que você abre em qualquer celular ou computador e salva na tela inicial como app.

## Passo 1: baixar o projeto

Nesta conversa do Arena, baixe a pasta `futanalytics` (botão de download no
visualizador de arquivos). Você precisa destes arquivos:

```
futanalytics/
  app/            (main.py, model.py, provider.py, db.py,
                   calibration.py, understat.py, backtest.py,
                   calibration_data.json)   ← obrigatório: curvas de calibração
  static/         (index.html)
  requirements.txt
  render.yaml
```

**NÃO suba** para o GitHub/Render (pesa no repositório e não serve lá):
`futanalytics.db` (seu banco local) e `data_cache/` (cache de backtest, ~10 MB).

**Novidades da v2 no deploy:**
- `requirements.txt` NÃO mudou: zero dependência nova, o build fica igual.
- O xG vem do Understat (grátis, sem chave, sem configuração). Nas ligas com
  cobertura (Premier, La Liga, Serie A, Bundesliga, Ligue 1) a 1ª carga do dia
  fica até MAIS RÁPIDA que a v1: o histórico de todos os times da liga chega em
  1 requisição cacheada por 12h, em vez de 1 requisição por time na
  football-data (que limita 10/min). No Brasileirão o comportamento é o mesmo
  da v1 (o app detecta a ausência de xG e cai para o histórico normal).
- O `app/backtest.py` é ferramenta OFFLINE para rodar no SEU computador
  (`python -m app.backtest ...`). No Render ele não é executado pelo servidor
  e não consome nada — é só mais um arquivo parado.
- A calibração já vem pronta em `app/calibration_data.json` (9 KB). Você só
  precisa regenerá-la se quiser retreinar com temporadas novas — no seu PC.

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
