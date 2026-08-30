"""Todo o copy do bot em um lugar so, para voce editar sem mexer em logica."""
from __future__ import annotations

CANAL_NOME = "o Jornal"

WELCOME = (
    "*Bem-vindo.*\n\n"
    "Todo dia de manha voce recebe, direto aqui no Telegram, o resumo do que "
    "realmente importou na politica brasileira nas ultimas 24 horas. Sem timeline, "
    "sem grito, sem perder uma hora do seu dia.\n\n"
    "Escolha seu plano para entrar:"
)

ASK_CPF = (
    "Preciso do seu *CPF* para emitir a cobranca.\n\n"
    "Digite so os numeros. Ele e usado exclusivamente para gerar o Pix e "
    "processar reembolso, se voce pedir."
)

CPF_INVALID = "Esse CPF nao e valido. Confere e digita de novo, so os numeros."

ASK_EMAIL = (
    "Agora seu *e-mail* para o comprovante.\n\n"
    "Se preferir pular, e so mandar /pular."
)

GENERATING = "Gerando seu Pix..."

PIX_READY = (
    "*Pix gerado — {plan_name}*\n"
    "Valor: *R$ {value}*\n\n"
    "Aponte a camera do seu banco para o QR Code acima, ou use o codigo "
    "copia e cola na mensagem seguinte.\n\n"
    "_Assim que o pagamento cair, eu te mando o link de acesso automaticamente. "
    "Costuma levar menos de 10 segundos._"
)

PIX_COPIA_COLA = "Toque no codigo abaixo para copiar:"

NOT_PAID_YET = (
    "Ainda nao identifiquei seu pagamento.\n\n"
    "Se voce acabou de pagar, espera uns 30 segundos e tenta de novo. "
    "O Pix costuma cair na hora, mas as vezes o banco demora."
)

PAYMENT_OK = (
    "*Pagamento confirmado.* Seja bem-vindo.\n\n"
    "Seu acesso vale ate *{until}*.\n\n"
    "Toque no botao abaixo para entrar no canal. O link e pessoal, so funciona "
    "uma vez e vence em 24 horas."
)

RENEWED = (
    "*Renovacao confirmada.*\n\n"
    "Seu acesso agora vale ate *{until}*. Nao precisa fazer mais nada, "
    "voce continua no canal."
)

WARN_EXPIRING = (
    "Seu acesso ao {canal} vence *amanha* ({date}).\n\n"
    "Renove agora para nao perder o acesso — voce tem {grace} dias de tolerancia "
    "depois do vencimento, e depois disso a saida do canal e automatica."
)

REMOVED = (
    "Seu acesso ao {canal} venceu e voce foi removido do canal.\n\n"
    "Quando quiser voltar, e so tocar no botao abaixo. Sua conta continua aqui."
)

RECOVERY_1 = (
    "Seu Pix de *R$ {value}* ainda esta valido.\n\n"
    "E so terminar o pagamento que eu libero seu acesso na hora."
)

RECOVERY_2 = (
    "Enquanto voce pensa, quem ja esta dentro leu hoje: o que mudou em Brasilia "
    "nas ultimas 24h, quem ganhou e quem perdeu, e o que isso significa pra semana.\n\n"
    "Seu Pix de *R$ {value}* ainda esta de pe."
)

RECOVERY_3 = (
    "Ultima chamada — e vou facilitar.\n\n"
    "*{discount}% de desconto* no seu primeiro periodo. "
    "Sai por *R$ {new_value}* em vez de R$ {old_value}.\n\n"
    "Toque abaixo que eu gero o Pix novo com o desconto ja aplicado."
)

REFUND_OK = (
    "Reembolso processado. O valor volta pra sua conta em ate 1 dia util, "
    "no mesmo Pix que voce usou.\n\n"
    "Seu acesso ao canal foi encerrado. Obrigado por ter testado."
)

REFUND_QUEUED = (
    "Seu pedido de reembolso foi registrado e vai ser analisado.\n\n"
    "A janela de reembolso automatico e de {days} dias apos a compra, "
    "e a sua ja passou desse prazo. Voce recebe uma resposta aqui em breve."
)

REFUND_DENIED_LIMIT = (
    "Voce ja usou o reembolso automatico uma vez nessa conta. "
    "Seu pedido foi encaminhado para analise manual."
)

BLOCKED = (
    "Nao consigo processar novas compras nessa conta. "
    "Se voce acha que houve engano, responda aqui que um humano analisa."
)

QUIET_MODE = (
    "As novas assinaturas estao temporariamente pausadas e voltam em breve. "
    "Se voce ja e assinante, seu acesso segue normal."
)

NO_SUB = "Voce ainda nao tem uma assinatura ativa. Toque abaixo para assinar."

STATUS = (
    "*Sua assinatura*\n\n"
    "Plano: *{plan}*\n"
    "Situacao: *{status}*\n"
    "Vence em: *{until}*\n"
    "Renovacoes: *{renewals}*"
)
