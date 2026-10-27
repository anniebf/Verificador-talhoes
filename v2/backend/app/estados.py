"""Estados das ocorrências (seção 4 da documentação funcional).

Aprovar uma correção não é o mesmo que resolver o problema: só vira `corrigida` depois que a
etapa 3 executa a operação e a validação passa; se a validação reprova, vira `falha`.
"""

ESTADOS = {
    'identificada': 'Identificada',      # registrada pela auditoria
    'em_revisao': 'Em revisão',          # aberta para avaliação na etapa 2
    'aprovada': 'Aprovada',              # correção autorizada pelo usuário
    'rejeitada': 'Rejeitada',            # proposta de correção rejeitada
    'pendente': 'Pendente',              # usuário deixou sem decisão de propósito
    'corrigida': 'Corrigida',            # executada e validada
    'falha': 'Falha na correção',        # operação não concluída ou reprovada na validação
    'nao_corrigivel': 'Não corrigível automaticamente',  # exige intervenção manual
}

# ainda no fluxo (podem ser decididas e voltam a ser detectadas na reauditoria)
ABERTOS = ('identificada', 'em_revisao', 'aprovada', 'rejeitada', 'pendente', 'falha', 'nao_corrigivel')
# o que o usuário pode escolher na revisão
DECISOES = {'aprovar': 'aprovada', 'rejeitar': 'rejeitada', 'pendente': 'pendente'}

CLASSIFICACOES = {'confirmado': 'Confirmado', 'suspeito': 'Suspeito', 'inconclusivo': 'Inconclusivo'}


def inicial(op):
    return 'identificada' if op is not None else 'nao_corrigivel'
