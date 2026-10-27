import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

// Fundo de satélite (Esri) com opção de mapa de ruas, como na v1.
function fundos() {
  const satelite = L.layerGroup([
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 20, maxNativeZoom: 17, attribution: 'Imagens © Esri' }),
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 20, maxNativeZoom: 17, opacity: 0.8 }),
  ])
  const ruas = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 20, maxNativeZoom: 19, attribution: '© OpenStreetMap' })
  return { 'Satélite': satelite, 'Ruas': ruas }
}

/**
 * camadas: [{ id, dados (GeoJSON), estilo (obj ou função), aoClicar(feature, layer), dica(feature) -> texto,
 *             ponto (estilo de circleMarker para pontos) }]
 *   Uma camada com o mesmo `id` e os mesmos `dados` (mesmo objeto) não é recriada: só o estilo é reaplicado.
 *   Isso deixa seleção e destaque instantâneos mesmo com milhares de talhões.
 * enquadrar: GeoJSON/array de GeoJSON ou bounds Leaflet; muda a vista quando a `chaveEnquadrar` muda.
 * aoCriar(map): para sincronizar dois mapas; pode devolver uma função chamada quando o mapa sai da tela.
 */
export default function Mapa({ camadas = [], enquadrar, chaveEnquadrar, aoCriar, legenda, rotulo, className = '', children }) {
  const div = useRef(null)
  const mapa = useRef(null)
  const grupo = useRef(null)
  const vivas = useRef({})      // id -> { dados, layer, c }
  const ultimo = useRef(null)   // último enquadramento pedido
  const mexeu = useRef(false)   // o usuário já moveu/zoom no mapa depois dele

  useEffect(() => {
    const m = L.map(div.current, { zoomControl: true, preferCanvas: true, attributionControl: true })
    const f = fundos()
    f['Satélite'].addTo(m)
    L.control.layers(f, null, { position: 'topright' }).addTo(m)
    L.control.scale({ imperial: false }).addTo(m)
    m.setView([-15.8, -47.9], 4)
    grupo.current = L.layerGroup().addTo(m)
    mapa.current = m
    const desfazer = aoCriar?.(m)
    // se o painel muda de tamanho antes de o usuário mexer no mapa, reenquadra no que foi pedido
    const obs = new ResizeObserver(() => {
      m.invalidateSize()
      if (ultimo.current && !mexeu.current) m.fitBounds(ultimo.current, { padding: [30, 30], maxZoom: 18, animate: false })
    })
    obs.observe(div.current)
    const marcar = () => { mexeu.current = true }
    for (const ev of ['mousedown', 'wheel', 'touchstart']) div.current.addEventListener(ev, marcar, { passive: true })
    return () => { obs.disconnect(); desfazer?.(); m.remove(); mapa.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const g = grupo.current
    if (!g) return
    const vistos = new Set()
    camadas.forEach((c, ordem) => {
      if (!c?.dados) return
      const id = c.id || `camada-${ordem}`
      vistos.add(id)
      const viva = vivas.current[id]
      if (viva && viva.dados === c.dados) {
        viva.c = c
        viva.layer.setStyle(c.estilo)
        return
      }
      if (viva) g.removeLayer(viva.layer)
      const nova = { dados: c.dados, c }
      // os eventos leem a camada atual (nova.c): estilos e callbacks mudam sem recriar o desenho
      nova.layer = L.geoJSON(c.dados, {
        style: c.estilo,
        pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5, weight: 1.5, color: '#fff', fillColor: '#e5383b', fillOpacity: 1, ...(c.ponto || {}) }),
        onEachFeature: (f, layer) => {
          if (c.dica) layer.bindTooltip(() => nova.c.dica?.(f) || '', { sticky: true, className: 'dica-mapa' })
          if (c.aoClicar) layer.on('click', (e) => { L.DomEvent.stopPropagation(e); nova.c.aoClicar?.(f, layer) })
        },
        interactive: !!(c.dica || c.aoClicar),
      })
      nova.layer.addTo(g)
      vivas.current[id] = nova
    })
    for (const [id, viva] of Object.entries(vivas.current)) {
      if (!vistos.has(id)) { g.removeLayer(viva.layer); delete vivas.current[id] }
    }
    // a ordem do array é a ordem de desenho (a última fica por cima)
    camadas.forEach((c, ordem) => { const v = vivas.current[c?.id || `camada-${ordem}`]; if (v && c?.dados) v.layer.bringToFront() })
  }, [camadas])

  useEffect(() => {
    const m = mapa.current
    if (!m || !enquadrar) return
    let b
    if (enquadrar instanceof L.LatLngBounds) b = enquadrar
    else {
      const lista = (Array.isArray(enquadrar) ? enquadrar : [enquadrar]).filter(Boolean)
      if (!lista.length) return
      b = L.featureGroup(lista.map((x) => L.geoJSON(x))).getBounds()
    }
    if (b.isValid()) {
      ultimo.current = b
      mexeu.current = false
      m.flyToBounds(b, { padding: [40, 40], maxZoom: 17, duration: 0.45 })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chaveEnquadrar])

  return (
    <div className={`mapa ${className}`}>
      <div ref={div} className="mapa-leaflet" />
      {rotulo && <div className="mapa-rotulo">{rotulo}</div>}
      {children}
      {legenda && (
        <div className="mapa-legenda">
          {legenda.map((l) => (
            <div key={l.texto}><span className={`leg ${l.tipo || ''}`} style={{ background: l.fundo, borderColor: l.cor }} />{l.texto}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// Liga dois mapas: mexer num move o outro.
export function sincronizar(a, b) {
  let travado = false
  const seguir = (de, para) => () => {
    if (travado) return
    travado = true
    para.setView(de.getCenter(), de.getZoom(), { animate: false })
    travado = false
  }
  a.on('move', seguir(a, b))
  b.on('move', seguir(b, a))
}

export const ESTILOS = {
  talhao: { color: '#f5d90a', weight: 1.5, fillColor: '#f5d90a', fillOpacity: 0.08 },
  comErro: { color: '#ff8c1a', weight: 2, fillColor: '#ff8c1a', fillOpacity: 0.18 },
  selecionado: { color: '#22d3ee', weight: 3, fillColor: '#22d3ee', fillOpacity: 0.2 },
  realce: { color: '#ffffff', weight: 4, fillColor: '#22d3ee', fillOpacity: 0.35 },
  vizinho: { color: '#94a3b8', weight: 1.5, fillColor: '#94a3b8', fillOpacity: 0.12, dashArray: '4 3' },
  erro: { color: '#ff1f3d', weight: 2.5, fillColor: '#ff1f3d', fillOpacity: 0.55 },
  // trecho do problema pela gravidade: alta vermelho, média laranja, baixa amarelo
  gravidade: {
    alta: { color: '#ff1f3d', weight: 2.5, fillColor: '#ff1f3d', fillOpacity: 0.55, radius: 6 },
    media: { color: '#ff7a00', weight: 2.5, fillColor: '#ff7a00', fillOpacity: 0.5, radius: 5 },
    baixa: { color: '#ffd400', weight: 2, fillColor: '#ffd400', fillOpacity: 0.6, radius: 3.5 },
  },
  original: { color: '#ff8c1a', weight: 2.5, fillColor: '#ff8c1a', fillOpacity: 0.15 },
  proposta: { color: '#3ddc84', weight: 2.5, fillColor: '#3ddc84', fillOpacity: 0.25 },
  alterado: { color: '#3ddc84', weight: 2.5, fillColor: '#3ddc84', fillOpacity: 0.3 },
  estrada: { color: '#60a5fa', weight: 3, opacity: 0.95 },
  estradaErro: { color: '#c084fc', weight: 3.5, opacity: 1 },
}
