import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

export function Etapas({ atual, loteId, fazenda }) {
  const q = fazenda !== undefined && fazenda !== null ? `?fazenda=${encodeURIComponent(fazenda)}` : ''
  const etapas = [
    { n: 1, nome: 'Auditoria', url: `/lotes/${loteId}${q}` },
    { n: 2, nome: 'Revisão visual', url: null },
    { n: 3, nome: 'Correção e envio', url: null },
  ]
  return (
    <ol className="etapas">
      {etapas.map((e) => (
        <li key={e.n} className={e.n === atual ? 'atual' : e.n < atual ? 'feita' : ''}>
          {e.url && e.n < atual ? <Link to={e.url}><b>{e.n}</b> {e.nome}</Link> : <span><b>{e.n}</b> {e.nome}</span>}
        </li>
      ))}
    </ol>
  )
}

export const ACEITOS = '.zip,.rar,.shp,.dbf,.shx,.prj,.cpg'

// Área de arrastar e soltar: aceita .zip, .rar ou os arquivos soltos do shapefile.
export function SoltarArquivos({ arquivos, setArquivos, compacto = false }) {
  const input = useRef(null)
  const [em, setEm] = useState(false)
  const juntar = (lista) => {
    const novos = [...arquivos]
    for (const f of lista) if (!novos.some((x) => x.name === f.name && x.size === f.size)) novos.push(f)
    setArquivos(novos)
  }
  return (
    <div
      className={`soltar ${em ? 'em' : ''} ${compacto ? 'compacto' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setEm(true) }}
      onDragLeave={() => setEm(false)}
      onDrop={(e) => { e.preventDefault(); setEm(false); juntar(e.dataTransfer.files) }}
      onClick={() => input.current.click()}
    >
      <input ref={input} type="file" multiple accept={ACEITOS} hidden onChange={(e) => { juntar(e.target.files); e.target.value = '' }} />
      {arquivos.length ? (
        <ul className="lista-arquivos" onClick={(e) => e.stopPropagation()}>
          {arquivos.map((f) => (
            <li key={f.name + f.size}>
              <span>{f.name}</span> <small>{(f.size / 1024 / 1024).toFixed(2)} MB</small>
              <button className="btn-link" onClick={() => setArquivos(arquivos.filter((x) => x !== f))}>remover</button>
            </li>
          ))}
          <li><button className="btn-link" onClick={() => input.current.click()}>+ adicionar mais arquivos</button></li>
        </ul>
      ) : (
        <>
          <div className="soltar-icone">⇪</div>
          <div><b>Arraste os arquivos aqui</b> ou clique para escolher</div>
          <small>.zip, .rar ou os arquivos do shapefile (.shp, .dbf, .shx, .prj). Vários shapefiles são mesclados no mesmo lote.</small>
        </>
      )}
    </div>
  )
}

export function Aviso({ tipo = 'erro', children, aoFechar }) {
  if (!children) return null
  return (
    <div className={`aviso-faixa ${tipo}`}>
      <span>{children}</span>
      {aoFechar && <button className="btn-link" onClick={aoFechar}>fechar</button>}
    </div>
  )
}

export const SEVERIDADE = { alta: 'Alta', media: 'Média', baixa: 'Baixa' }

// estados das ocorrências (seção 4 da documentação funcional)
export const STATUS = {
  identificada: { texto: 'Identificada', cls: 'st-identificada' },
  em_revisao: { texto: 'Em revisão', cls: 'st-em_revisao' },
  aprovada: { texto: 'Aprovada', cls: 'st-aprovada' },
  rejeitada: { texto: 'Rejeitada', cls: 'st-rejeitada' },
  pendente: { texto: 'Pendente', cls: 'st-pendente' },
  corrigida: { texto: 'Corrigida', cls: 'st-corrigida' },
  falha: { texto: 'Falha na correção', cls: 'st-falha' },
  nao_corrigivel: { texto: 'Não corrigível automaticamente', cls: 'st-nao_corrigivel' },
}
export const Estado = ({ s }) => <span className={`etiqueta ${STATUS[s]?.cls || ''}`}>{STATUS[s]?.texto || s}</span>

export const CLASSIFICACAO = { confirmado: 'Confirmado', suspeito: 'Suspeito', inconclusivo: 'Inconclusivo' }
export const Classe = ({ c }) => <span className={`etiqueta cl-${c}`} title="Classificação da ocorrência">{CLASSIFICACAO[c] || c}</span>

// talhões sem código, mas com nome de fazenda, vêm do servidor na fazenda "#nome"
export const semCodigo = (codigo) => String(codigo || '').startsWith('#')

export function nomeFazenda(f) {
  if (!f) return ''
  if (f.codigo === '') return f.nome || 'Sem fazenda'
  if (semCodigo(f.codigo)) return `${f.nome || f.codigo.slice(1)} (sem código)`
  return f.nome ? `${f.codigo} · ${f.nome}` : f.codigo
}

// Botão com menu suspenso: tira da frente ações que se usa pouco (downloads, campos, versões).
export function Menu({ rotulo, itens, alinhar = 'dir', primario = false }) {
  const [aberto, setAberto] = useState(false)
  const caixa = useRef(null)
  useEffect(() => {
    if (!aberto) return
    const fora = (e) => { if (!caixa.current?.contains(e.target)) setAberto(false) }
    document.addEventListener('mousedown', fora)
    return () => document.removeEventListener('mousedown', fora)
  }, [aberto])
  return (
    <div className="menu" ref={caixa}>
      <button className={`btn ${primario ? 'primario' : ''}`} onClick={() => setAberto(!aberto)}>{rotulo} <span className="seta">▾</span></button>
      {aberto && (
        <div className={`menu-lista ${alinhar}`}>
          {itens.filter(Boolean).map((i) => i.titulo ? <div key={i.titulo} className="menu-titulo">{i.titulo}</div> : (
            <button key={i.texto} disabled={i.desabilitado} onClick={() => { setAberto(false); i.acao() }}>
              <span>{i.texto}</span>{i.dica && <small>{i.dica}</small>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Dica de primeiro uso, que some quando o usuário fecha (lembrada neste navegador).
export function Dica({ id, children }) {
  const chave = `vt_dica_${id}`
  const [visivel, setVisivel] = useState(() => { try { return !localStorage.getItem(chave) } catch { return true } })
  if (!visivel) return null
  return (
    <div className="dica">
      <span className="dica-icone">?</span><div>{children}</div>
      <button className="btn-link" onClick={() => { try { localStorage.setItem(chave, '1') } catch { /* sem storage */ } setVisivel(false) }}>Entendi</button>
    </div>
  )
}

// Lista de fazendas agrupadas por aglomerado, com busca.
export function ListaFazendas({ fazendas, atual, aoEscolher, total, todas = true }) {
  const [busca, setBusca] = useState('')
  const [fechados, setFechados] = useState(new Set())
  const b = busca.trim().toLowerCase()
  const filtradas = fazendas.filter((f) => !b || `${f.codigo} ${f.nome} ${f.aglomerado}`.toLowerCase().includes(b))
  const grupos = {}
  for (const f of filtradas) (grupos[f.aglomerado || ''] ||= []).push(f)
  const nomes = Object.keys(grupos).sort((x, y) => (x === '') - (y === '') || x.localeCompare(y, 'pt-BR'))
  const alternar = (g) => { const n = new Set(fechados); n.has(g) ? n.delete(g) : n.add(g); setFechados(n) }
  return (
    <div className="lista-fazendas-wrap">
      <input className="busca" placeholder="Buscar fazenda, código ou aglomerado…" value={busca} onChange={(e) => setBusca(e.target.value)} />
      <div className="lista-fazendas">
        {todas && (
          <button className={`faz-item ${atual === '*' ? 'ativo' : ''}`} onClick={() => aoEscolher('*')}>
            <span className="faz-nome">Visão geral do lote</span>
            {total > 0 && <span className="badge erro">{total}</span>}
          </button>
        )}
        {nomes.map((g) => {
          const fs = grupos[g]
          const erros = fs.reduce((s, f) => s + f.nErros, 0)
          const fechado = fechados.has(g) && !b
          return (
            <div key={g} className="faz-grupo">
              <button className="faz-grupo-titulo" onClick={() => alternar(g)}>
                <span className="seta">{fechado ? '▸' : '▾'}</span><span>{g || 'Sem aglomerado'}</span>
                <small>{fs.length} faz.</small>{erros > 0 && <span className="badge erro">{erros}</span>}
              </button>
              {!fechado && fs.map((f) => (
                <button key={f.codigo} className={`faz-item ${atual === f.codigo ? 'ativo' : ''}`} onClick={() => aoEscolher(f.codigo)}>
                  <span className="faz-nome">{nomeFazenda(f)}</span>
                  <small>{f.talhoes ? `${f.talhoes} talhões` : 'linhas'}</small>
                  {f.nErros > 0 ? <span className="badge erro">{f.nErros}</span> : <span className="badge ok">✓</span>}
                </button>
              ))}
            </div>
          )
        })}
        {!nomes.length && <p className="sub vazio">Nenhuma fazenda encontrada.</p>}
      </div>
    </div>
  )
}
