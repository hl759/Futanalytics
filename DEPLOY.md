# Como colocar o SigmaDesk no ar com link fixo (grátis, ~10 min)

Resultado final: um endereço permanente tipo `https://sigmadesk.onrender.com`
que você abre em qualquer celular ou computador e salva na tela inicial como app.

> **Uma simplificação em relação ao FutAnalytics:** nenhuma das fontes de dados
> precisa de chave, token ou cadastro. Deribit, Coinbase Exchange e Kraken têm
> API pública. Não existe mais `FD_TOKEN` para configurar — o deploy ficou com um
> passo a menos.

## Passo 1: baixar o projeto

Nesta conversa do Arena, baixe a pasta do projeto (botão de download no
visualizador de arquivos). Você precisa destes arquivos:

```
sigmadesk/
  app/            (main.py, provider.py, volread.py, db.py,
                   calibration.py, fixtures_real.json)
  static/         (index.html)
  tools/          (validate_etapa0.py, smoke_api.py, bench_render_free.py,
                   capture_fixtures.py)
  requirements.txt
  render.yaml
  README.md
```

`app/fixtures_real.json` (~30 KB) é **obrigatório**: são os dados reais
capturados em 26/set/2026 que as duas suítes de validação usam. Sem ele os
testes não rodam.

`tools/` não é executado pelo servidor no Render — são ferramentas para rodar no
seu computador. Subir não custa nada e permite validar depois do deploy.

**NÃO suba** para o GitHub/Render (já protegidos pelo `.gitignore`): `*.db`
(seu banco local, pode ter seus trades), `data_cache/`, `.venv/`, `__pycache__/`.

## Passo 2: validar antes de subir (recomendado, ~1 min)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tools.validate_etapa0    #  81 checks: matemática sobre dado real
.venv/bin/python -m tools.smoke_api          # 137 checks: contrato HTTP
.venv/bin/python -m tools.bench_render_free  #  31 checks: restrições do free, medidas
```

As três rodam **offline** e devem terminar com `0 FALHAS`. Se falharem na sua
máquina, não faça deploy — o problema é anterior à rede. O benchmark simula
provedor pendurado e confirma o teto de latência; ele sozinho leva ~50 s, é o
único demorado.

## Passo 3: subir para o GitHub

1. Crie uma conta gratuita em github.com (se não tiver).
2. Crie um repositório novo, por exemplo `sigmadesk` (pode ser privado).
3. Envie os arquivos: na página do repositório, "Add file" > "Upload files"
   e arraste a pasta inteira. Confirme com "Commit changes".

## Passo 4: publicar no Render

1. Crie conta gratuita em render.com (pode entrar com o GitHub, sem cartão).
2. Clique em "New +" > "Web Service" e conecte o repositório `sigmadesk`.
3. O Render lê o `render.yaml` sozinho. Se pedir os campos manualmente:
   - Runtime: **Python 3**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Health Check Path: `/api/health`
   - Plan: **Free**
4. Aguarde o build (~2 min) e o endereço aparecer.

## Passo 5: a PRIMEIRA coisa a verificar depois do deploy

Abra `https://SEU-APP.onrender.com/api/test-provider`.

Isto é importante e eu não pude testar daqui: **a cadeia de fallback foi
validada por formato de resposta, não por uma chamada httpx real saindo de um IP
de datacenter americano.** O sandbox onde este código foi escrito não tem egresso
para Deribit/Coinbase (o proxy encerra o TLS), então o que foi confirmado é que
as APIs respondem com o formato que o parser espera — verificado com `fetch_page`,
que alcança os dois. O que **não** foi confirmado é que o Render free, cujo IP
fica nos EUA, alcança a Deribit.

Sabemos que a **Binance bloqueia** IP americano (testado: devolve "restricted
location") — por isso ela foi removida da cadeia. Deribit e Coinbase não têm esse
bloqueio documentado, mas "não documentado" não é "testado".

A resposta traz, além do estado de cada provedor, dois blocos que explicam o
comportamento do app: `_budget` (timeout de 8 s por chamada, deadline global de
25 s por request, disjuntor abre após 2 falhas com cooldown de 180 s) e
`_breakers` (qual provedor está com o disjuntor aberto e em quantos segundos ele
tenta de novo). Se o painel estiver em demo, `_breakers` diz por quê sem você
precisar abrir log de servidor.

Leia o estado dos provedores assim:

- `deribit_chain.ok: true` → cenário ideal. IV, DVOL, OHLCV e funding vêm de uma
  fonte só, com preço de opção real.
- `deribit_*.ok: false` **mas** `coinbase.ok: true` → o app funciona em modo
  degradado honesto: velas reais da Coinbase, superfície de opções em demo. O
  painel mostra isso camada por camada.
- tudo `false` → modo demo completo. Números sintéticos ancorados no último valor
  real observado, sempre rotulados.

E confirme `/api/version`: deve devolver `"version": "0.1.0-etapa0"`. Se vier
outra coisa, o deploy não pegou o código novo — *Manual Deploy → Clear build
cache & deploy*.

## Passo 6: usar no celular

Abra o endereço no navegador do celular → menu → "Adicionar à tela inicial".
Funciona como app, sem loja e sem instalação.

## Avisos do plano gratuito do Render

- **O serviço dorme** após 15 min sem uso; a primeira visita do dia demora ~1 min
  para acordar. Depois fica rápido. É o Render free, não o app.
- **O disco é apagado a cada deploy.** Configurações voltam ao padrão e os trades
  registrados são perdidos. Use **"Baixar backup"** em Configurações antes de
  atualizar o código e **"Restaurar"** depois — o arquivo leva settings, trades,
  CVL e notas. É o único caminho de persistência real num disco volátil.
- **Nada é gravado sozinho.** O único dado escrito automaticamente é cache
  temporário com TTL (cadeia 15 min, velas e DVOL 1 h), purgado na inicialização.
  Trade só entra quando você clica em registrar. Herdado do FutAnalytics, onde era
  um pedido explícito seu — e continua sendo a decisão certa.
- **Consumo de API:** ~8 chamadas a cada 15 min no pior caso, contra um pool de
  50 mil créditos da Deribit (500 créditos/chamada, refill de 10 mil/s). Margem
  enorme. A chamada cara é `get_instruments` (10 mil créditos) — por isso fica no
  cache diário, nunca no ciclo curto.
- **Custo comparado ao FutAnalytics no free:** mesmo build, mesmas quatro
  dependências, mesma RAM (~60 MB sem numpy/pandas), mesmo cold start.

## Problema: "o painel mostra tudo em demo"

Sintoma: os cards aparecem com o selo `demo` e os números não mudam entre
atualizações.

1. **Rode `/api/test-provider`** e leia qual provedor falhou e com que erro. A
   resposta traz a mensagem real de cada um, não um silêncio.
2. **`TLS/SSL connection has been closed`** em todos → é egresso bloqueado no
   ambiente, não bug do app. No Render isso não deveria acontecer; se acontecer,
   é a rede deles.
3. **`restricted location` / 451 / 403** → bloqueio geográfico do provedor. A
   Binance foi removida da cadeia exatamente por isso. Se aparecer em outro,
   me avise: a cadeia precisa de um novo fallback.
4. **`error -32602` da Deribit** → parâmetro errado. Já conhecido: 
   `get_volatility_index_data` exige `currency`, não só `index_name`. Está
   corrigido no código; se reaparecer, a API mudou.
5. **Espera de TTL.** Um momento ruim fica em cache por até 1 h (velas/DVOL) ou
   15 min (cadeia). Depois de resolver a causa, espere o TTL vencer ou mude
   qualquer configuração que afete dados (`min_oi`, `currencies`, `target_dte`)
   — isso invalida o cache de leitura imediatamente.

## Problema: "os números de volatilidade parecem errados"

Antes de suspeitar do motor, confira três coisas nesta ordem:

1. **Está em demo?** O selo diz. Números demo são ancorados no último valor real
   observado, mas o caminho é sintético.
2. **Anualização.** Cripto usa √365, não √252 — o mercado não fecha no fim de
   semana. Comparar com um site que usa √252 dá diferença de ~20% e não é bug.
3. **VRP negativo é uma leitura, não um erro.** Em 26/set/2026 o VRP real do BTC
   foi **−3,8 pontos** (IV 35,4% contra RV realizada 39,2%) e o app respondeu
   "não venda prêmio". Isso é o motor funcionando. Um motor que sempre encontra
   oportunidade não está lendo o mercado — está inventando.

Para investigar com dado real, rode `python -m tools.capture_fixtures --check`:
ele recaptura e compara com o fixture de 26/set/2026, avisando se alguma âncora
derivou mais de 20% (caso em que o modo demo precisa ser reancorado).

## Alternativa sem GitHub: PythonAnywhere

Se preferir não usar GitHub, o pythonanywhere.com gratuito também roda o projeto
(upload manual dos arquivos + app web ASGI). O Render via GitHub é mais simples
de manter atualizado.

## Aviso

Ferramenta de análise e registro. **Não é recomendação de investimento.** Opções
de criptoativos são instrumentos de risco elevado; estruturas de risco definido
limitam a perda máxima a um valor conhecido, o que é diferente de não perder.
Verifique você mesmo os termos de uso da Deribit para residentes no Brasil antes
de abrir conta — não é restrito hoje, mas termos mudam.
