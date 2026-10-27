// Cliente da API: guarda o token e transforma erros em mensagens prontas para mostrar.
const CHAVE = 'vt_token'

export const token = {
  get: () => { try { return localStorage.getItem(CHAVE) } catch { return null } },
  set: (t) => { try { t ? localStorage.setItem(CHAVE, t) : localStorage.removeItem(CHAVE) } catch { /* sem storage */ } },
}

export class ErroApi extends Error {
  constructor(msg, status) { super(msg); this.status = status }
}

let aoExpirar = () => {}
export const quandoExpirar = (f) => { aoExpirar = f }

async function pedir(metodo, url, corpo, { bruto = false } = {}) {
  const headers = {}
  const t = token.get()
  if (t) headers.Authorization = `Bearer ${t}`
  let body
  if (corpo instanceof FormData) body = corpo
  else if (corpo !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(corpo) }
  let r
  try {
    r = await fetch(url, { method: metodo, headers, body })
  } catch {
    throw new ErroApi('Não consegui falar com o servidor. Confira a conexão.', 0)
  }
  if (r.status === 401 && t) aoExpirar()
  if (!r.ok) {
    let msg = `Erro ${r.status}`
    try {
      const j = await r.json()
      msg = typeof j.detail === 'string' ? j.detail : (j.detail?.[0]?.msg || msg)
    } catch { /* corpo sem json */ }
    throw new ErroApi(msg, r.status)
  }
  return bruto ? r : r.json()
}

export const api = {
  get: (url, params) => pedir('GET', url + query(params)),
  post: (url, corpo) => pedir('POST', url, corpo),
  put: (url, corpo) => pedir('PUT', url, corpo),
  patch: (url, corpo) => pedir('PATCH', url, corpo),
  del: (url) => pedir('DELETE', url),
  // downloads passam pelo fetch (o token vai no cabeçalho, nunca na URL)
  async baixar(url, params) {
    const r = await pedir('GET', url + query(params), undefined, { bruto: true })
    const nome = /filename="([^"]+)"/.exec(r.headers.get('content-disposition') || '')?.[1] || 'arquivo'
    const blob = await r.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = nome
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  },
}

function query(params) {
  if (!params) return ''
  const q = new URLSearchParams()
  // '' vai junto: é o código da "Sem fazenda"; quem não quer o filtro passa undefined
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null) q.set(k, v) })
  const s = q.toString()
  return s ? `?${s}` : ''
}

// o backend manda datas em UTC sem fuso
export function data(s, soData = false) {
  if (!s) return '—'
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(s) ? s : s + 'Z')
  return soData ? d.toLocaleDateString('pt-BR') : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}

export const num = (v, casas = 2) => (v ?? 0).toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas })

export function area(m2) {
  return m2 >= 1000 ? `${num(m2 / 1e4)} ha` : `${num(m2, 1)} m²`
}
