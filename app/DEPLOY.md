# Como colocar o FutAnalytics no ar com link fixo (grátis, ~10 min)

Resultado final: um endereço permanente tipo `https://futanalytics.onrender.com`
que você abre em qualquer celular ou computador e salva na tela inicial como app.

## Passo 1: baixar o projeto

Nesta conversa do Arena, baixe a pasta `futanalytics` (botão de download no
visualizador de arquivos). Você precisa destes arquivos:

```
futanalytics/
  app/            (main.py, model.py, provider.py, db.py)
  static/         (index.html)
  requirements.txt
  render.yaml
```

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
  ~1 min para acordar. Depois fica rápido.
- O disco é apagado a cada deploy: as configurações voltam ao padrão e os
  bilhetes registrados são perdidos. O token não, se estiver na variável
  FD_TOKEN. Se o histórico de bilhetes for importante para você (e para
  medir ROI ele é), me peça que eu adiciono exportação/importação de
  backup ou banco externo gratuito.

## Alternativa sem GitHub: PythonAnywhere

Se preferir não usar GitHub, o pythonanywhere.com gratuito também roda o
projeto (upload manual dos arquivos + app web ASGI). O Render via GitHub é
mais simples de manter atualizado.
