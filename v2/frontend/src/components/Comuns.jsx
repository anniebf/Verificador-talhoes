import { useRef, useState } from 'react'
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

export const STATUS = {
  pendente: { texto: 'Pendente', cls: 'st-pendente' },
  aceita: { texto: 'Aceitar correção', cls: 'st-aceita' },
  rejeitada: { texto: 'Manter original', cls: 'st-rejeitada' },
  aplicada: { texto: 'Corrigida', cls: 'st-aplicada' },
  mantida: { texto: 'Mantida', cls: 'st-mantida' },
}

export function nomeFazenda(f) {
  if (!f) return ''
  if (f.codigo === '') return 'Sem fazenda'
  return f.nome ? `${f.codigo} · ${f.nome}` : f.codigo
}
