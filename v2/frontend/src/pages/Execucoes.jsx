// Histórico das execuções (seção 7): cada auditoria e cada correção tem identificador próprio e relatório.
import { Fragment, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, data } from '../api.js'
import { Aviso } from '../components/Comuns.jsx'

const resumo = (e) => {
  const r = e.resumo || {}
  if (e.tipo === 'auditoria') return `${r.feicoes ?? 0} feições, ${r.ocorrencias ?? 0} ocorrência(s), ${r.novas ?? 0} nova(s)`
  return `${r.corrigidas ?? 0} corrigida(s), ${r.falhas ?? 0} falha(s), ${r.pendentes ?? 0} pendente(s), ${r.semSolucao ?? 0} sem solução`
}

export default function Execucoes() {
  const [lista, setLista] = useState(null)
  const [lote, setLote] = useState('')
  const [aberta, setAberta] = useState(null)
  const [erro, setErro] = useState('')

  const buscar = (l = lote) => api.get('/api/execucoes', { lote_id: l || undefined }).then(setLista).catch((e) => setErro(e.message))
  useEffect(() => { buscar() }, [])
  const baixar = (e, f) => api.baixar(`/api/execucoes/${e.id}/relatorio/${f}`).catch((x) => setErro(x.message))

  return (
    <div className="pagina larga">
      <h1>Execuções</h1>
      <p className="sub">Cada auditoria e cada correção automática tem um identificador próprio, com a base de entrada, as regras usadas, o resultado e a base de saída.</p>
      <Aviso aoFechar={() => setErro('')}>{erro}</Aviso>
      <div className="cartao filtros">
        <label>Lote<input type="number" min="1" value={lote} onChange={(e) => setLote(e.target.value)} /></label>
        <div className="botoes"><button className="btn primario" onClick={() => buscar()}>Filtrar</button>
          <button className="btn" onClick={() => { setLote(''); buscar('') }}>Limpar</button></div>
      </div>
      <div className="cartao">
        {!lista ? <p className="sub">Carregando…</p> : (
          <table className="tabela">
            <thead><tr><th>#</th><th>Tipo</th><th>Lote</th><th>Início</th><th>Usuário</th><th>Entrada → saída</th><th>Resultado</th><th>Situação</th><th /></tr></thead>
            <tbody>
              {lista.map((e) => (
                <Fragment key={e.id}>
                  <tr>
                    <td>{e.id}</td>
                    <td><span className={`etiqueta acao-${e.tipo === 'auditoria' ? 'auditoria' : 'correcao'}`}>{e.tipo === 'auditoria' ? 'Auditoria' : 'Correção'}</span></td>
                    <td><Link to={`/lotes/${e.loteId}`}>{e.loteId}</Link></td>
                    <td className="nowrap">{data(e.inicio)}</td>
                    <td>{e.usuario}</td>
                    <td>v{e.versaoEntrada} → v{e.versaoSaida ?? '?'}</td>
                    <td>{resumo(e)}</td>
                    <td>{e.status === 'concluida' ? 'Concluída' : e.status === 'erro' ? <span className="erro">Erro</span> : 'Executando'}</td>
                    <td className="dir">
                      <button className="btn pequeno" onClick={() => setAberta(aberta === e.id ? null : e.id)}>{aberta === e.id ? 'Fechar' : 'Detalhes'}</button>
                      {e.status !== 'executando' && <><button className="btn pequeno" onClick={() => baixar(e, 'pdf')}>PDF</button>
                        <button className="btn pequeno" onClick={() => baixar(e, 'csv')}>CSV</button></>}
                    </td>
                  </tr>
                  {aberta === e.id && (
                    <tr className="detalhe-linha"><td colSpan={9}>
                      <dl className="dl">
                        <dt>Entrada</dt><dd>{e.entrada}</dd>
                        <dt>Saída</dt><dd>{e.saida || '—'}</dd>
                        <dt>Fazendas</dt><dd>{e.fazendas.length ? e.fazendas.join(', ') : 'todas'}</dd>
                        <dt>{e.tipo === 'auditoria' ? 'Regras executadas' : 'Operações'}</dt>
                        <dd>{e.tipo === 'auditoria' ? Object.keys(e.regras).join(', ') : Object.values(e.regras).join(', ')}</dd>
                        {e.erro && <><dt>Erro</dt><dd className="erro">{e.erro}</dd></>}
                        <dt>Fim</dt><dd>{data(e.fim)}</dd>
                      </dl>
                    </td></tr>
                  )}
                </Fragment>
              ))}
              {!lista.length && <tr><td colSpan={9} className="vazio">Nenhuma execução.</td></tr>}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
