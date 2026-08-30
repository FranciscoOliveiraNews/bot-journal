# Bot de assinatura do Telegram — canal pago com Pix

Bot que vende acesso mensal/anual a um canal fechado do Telegram, cobra por Pix
via Asaas, libera o acesso sozinho quando o pagamento cai e remove quem não
renovou. Sem depender de plataforma de terceiros: a base de assinantes é sua.

## O que ele faz

- **Checkout por Pix** — QR Code + copia e cola dentro da conversa, sem sair do Telegram
- **Liberação automática** — link de convite pessoal, de uso único, que vence em 24h
- **Renovação** — aviso um dia antes do vencimento, com botão de renovar
- **Tolerância de 2 dias** — quem atrasa não é removido na hora
- **Remoção automática** — passou a tolerância, sai do canal (e pode voltar quando pagar)
- **Recuperação de carrinho** — 3 mensagens para quem gerou Pix e não pagou (15min, 2h, 24h com 30% off)
- **Reembolso automático** — dentro de 7 dias, estorna e remove o acesso sozinho; fora do prazo, vira fila de aprovação
- **Chargeback** — remove e bloqueia na hora, sem chance de recompra
- **Painel de vendas** — faturamento, ticket médio, conversão do funil, export CSV

## Antes de começar, você precisa de

1. **Um bot** criado no [@BotFather](https://t.me/BotFather) → guarde o token
2. **O bot como admin do canal**, com as permissões `Adicionar assinantes` e `Banir usuários` ligadas
3. **O ID do canal** — veja as três formas abaixo. Começa com `-100`
4. **Seu ID de usuário** — mande `/id` para o seu próprio bot depois que ele subir
5. **Uma conta no [Asaas](https://www.asaas.com)** — aceita CPF, sem mensalidade, Pix a R$ 0,49 fixo

## Como descobrir o ID do canal

Três formas, da mais simples para a mais chata. **Nenhuma delas precisa de bot
de terceiro** — evite entregar acesso ao seu canal para bots públicos de leitura
de ID.

**1. Canal público? Não precisa de ID.** Se o canal tem `@nomedocanal`, é só pôr
isso em `CHANNEL_ID`. O bot aceita `@nomedocanal` e `-1001234567890` igualmente.

**2. Pelo navegador, antes de qualquer deploy.** Publique um post no canal (o bot
já tem que ser admin) e abra:

```
https://api.telegram.org/bot<SEU_TOKEN>/getUpdates
```

Procure no JSON por `"chat":{"id":-100...,"title":"..."}`. Esse número é o
`CHANNEL_ID`. O token vai na própria URL, então não abra isso com alguém vendo a
tela e não cole o endereço em lugar nenhum. Se vazar, `/revoke` no @BotFather
gera outro. Só funciona antes do bot estar rodando — em polling, o processo
consome as atualizações.

**3. Pelo próprio bot, depois do deploy.** Suba com `CHANNEL_ID` vazio: enquanto
ele estiver em branco, todo post publicado no canal faz o bot mandar o ID no seu
privado. Ou encaminhe um post do canal para o bot, que ele responde com o ID.
Assim que você preencher a variável, esse aviso para sozinho.

E `/id`, em qualquer conversa, devolve o seu ID de usuário e o ID do chat atual —
é assim que você preenche `ADMIN_IDS`.

## Deploy no Railway (o caminho mais curto)

1. Suba esta pasta para um repositório no GitHub
2. No [Railway](https://railway.app): **New Project → Deploy from GitHub repo**
3. Adicione o plugin **PostgreSQL** ao projeto (`New → Database → PostgreSQL`)
4. Em **Variables**, cole as variáveis do `.env.example`. Para o banco, use a
   referência do plugin: `DATABASE_URL = ${{Postgres.DATABASE_URL}}`
5. Em **Settings → Networking**, clique em **Generate Domain**. Você recebe algo
   como `seu-bot.up.railway.app` — é essa a URL do webhook, você não precisa
   comprar domínio nenhum
6. **Mantenha `numReplicas` em 1.** Com duas réplicas o bot faz polling duplicado
   e dispara cada cobrança e cada remoção duas vezes

### Configurar o webhook no Asaas

No painel do Asaas, em **Integrações → Webhooks → Adicionar**:

| Campo | Valor |
|---|---|
| URL | `https://seu-bot.up.railway.app/webhook/asaas` |
| Token de autenticação | o mesmo valor de `ASAAS_WEBHOOK_TOKEN` |
| Versão da API | v3 |
| Eventos | todos os de **Cobranças** |

Sem esse token o endpoint devolve 401 e ignora a chamada — é o que impede
qualquer um de mandar "pagamento confirmado" para o seu bot e entrar de graça.

## Rodar localmente

```bash
cp .env.example .env      # preencha os valores
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Com `DATABASE_URL` apontando para SQLite, funciona sem instalar banco nenhum.
Use `ASAAS_ENV=sandbox` enquanto testa — o Asaas tem ambiente de testes com
chave própria e Pix que você "paga" pelo painel.

```bash
pytest        # 29 testes cobrindo o fluxo inteiro
```

## Mudar preços e planos

Tudo em `plans.json`, sem tocar em código:

```json
{ "code": "mensal", "name": "Mensal", "price": 27.90, "days": 30,
  "highlight": false, "label": "R$ 27,90/mes" }
```

`highlight: true` põe uma estrela no botão. Depois de editar, faça um novo deploy.
Os textos do bot ficam todos em `app/bot/texts.py`.

## Comandos

**Assinante:** `/start` · `/status` · `/renovar` · `/reembolso` · `/ajuda` · `/id`

**Admin (só os IDs em `ADMIN_IDS`):**

- `/painel` — relatório de vendas com botões de período, fila de reembolso e CSV
- `/diagnostico` — checa se o bot está admin do canal e com as permissões certas. **Rode este primeiro depois do deploy**
- `/avisar <texto>` — mensagem para todos os assinantes ativos
- Encaminhar um post do canal para o bot — ele responde com o `CHANNEL_ID`

## Rastrear as campanhas do Meta Ads

Use deep link no destino do anúncio:

```
https://t.me/SEU_BOT?start=meta_criativo_a
```

O que vem depois de `start=` é gravado como origem do lead e sai na coluna
`origem` do CSV. Um por criativo e você descobre qual anúncio traz assinante
que renova, não só quem clica.

## Modo silêncio

`QUIET_MODE=true` faz o bot recusar novas assinaturas com um aviso educado,
sem afetar quem já é assinante. Serve para as 48h antes e 24h depois da votação,
quando propaganda política paga é proibida, ou para qualquer pausa na captação.

## Estrutura

```
app/
  config.py          variáveis de ambiente e planos
  models.py          tabelas
  db.py              conexão
  main.py            sobe API + bot + agendador em um processo só
  webhook.py         recebe os eventos do Asaas
  bot/
    texts.py         todo o copy, para você editar
    keyboards.py     botões
    handlers/        checkout, comandos do assinante, painel admin
  jobs/
    expiry.py        aviso D-1, tolerância, remoção
    recovery.py      recuperação de carrinho
  services/
    asaas.py         cliente do gateway
    access.py        convite e remoção no canal
    billing.py       regras de negócio
    reports.py       relatórios e CSV
tests/               29 testes de ponta a ponta
```

## Limitações conhecidas

- **Sem CNPJ não dá para usar Pix Automático** (o débito recorrente do Banco
  Central). Toda renovação exige o assinante pagar um Pix novo, o que aumenta
  o churn passivo. Quando abrir empresa, vale migrar
- **O `MemoryStorage` do FSM** perde o estado a cada deploy. Na prática significa
  que quem estava digitando o CPF no exato momento do deploy precisa recomeçar.
  Para volume alto, troque por Redis
- **Uma réplica só.** O agendador não tem lock distribuído
