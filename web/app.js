/* Surf Spot Climate Resilience Explorer — V1 skeleton frontend.
 *
 * Loads the pre-processed static surf_breaks.json, plots breaks on a Leaflet
 * map colour-coded by long-term erosion risk, and opens a profile panel showing
 * sea level rise (decadal time series) and coastal erosion risk per break.
 *
 * No build step, no API calls at runtime — all data is baked into the JSON.
 */

const DATA_URL = "../data/surf_breaks.json";

const RISK_COLOURS = {
  high: "#e4572e",
  moderate: "#f2a900",
  low: "#2a9d8f",
  unknown: "#9aa9b4",
};

const RISK_LABELS = {
  high: "Higher risk",
  moderate: "Moderate risk",
  low: "Lower / managed",
  unknown: "Not mapped nearby",
};

let map;
let selectedMarker = null;
let insetMap = null; // small recession-zone map inside the panel
let insetNfiLayer = null; // "no future intervention" comparison layer

// --------------------------------------------------------------------------- //
// Bootstrap
// --------------------------------------------------------------------------- //
init();

async function init() {
  map = L.map("map", { zoomControl: true }).setView([52.8, -2.6], 6);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; ' +
      '<a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 19,
  }).addTo(map);

  document.getElementById("panel-close").addEventListener("click", closePanel);

  let payload;
  try {
    const resp = await fetch(DATA_URL);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    payload = await resp.json();
  } catch (err) {
    document.getElementById("attribution").innerHTML =
      `<span style="color:#ffd0c4">Could not load data (${err.message}). ` +
      `Serve this folder over HTTP and run preprocess.py first.</span>`;
    return;
  }

  renderAttribution(payload.metadata);
  const markers = payload.breaks.map(addBreakMarker);

  // Fit to all markers so the full English coastline of breaks is visible.
  const group = L.featureGroup(markers);
  map.fitBounds(group.getBounds().pad(0.15));

  // Optional deep link: ?break=<id> opens that break's panel on load;
  // &nfi=1 also switches on the "no defences" comparison outline.
  const params = new URLSearchParams(location.search);
  const wanted = params.get("break");
  if (wanted != null) {
    const i = payload.breaks.findIndex((b) => String(b.id) === wanted);
    if (i !== -1) {
      markers[i].fire("click");
      if (params.get("nfi") === "1") {
        const t = document.getElementById("tgl-nfi");
        if (t) {
          t.checked = true;
          t.dispatchEvent(new Event("change"));
        }
      }
    }
  }
}

// --------------------------------------------------------------------------- //
// Markers
// --------------------------------------------------------------------------- //
function addBreakMarker(brk) {
  const risk = (brk.erosion && brk.erosion.risk_level) || "unknown";
  const marker = L.circleMarker([brk.lat, brk.lon], {
    radius: 8,
    color: "#ffffff",
    weight: 2,
    fillColor: RISK_COLOURS[risk] || RISK_COLOURS.unknown,
    fillOpacity: 0.95,
  }).addTo(map);

  marker.bindTooltip(brk.name, { direction: "top", offset: [0, -6] });
  marker.on("click", () => {
    if (selectedMarker) selectedMarker.getElement()?.classList.remove("selected");
    selectedMarker = marker;
    marker.getElement()?.classList.add("selected");
    openPanel(brk);
  });
  return marker;
}

// --------------------------------------------------------------------------- //
// Panel
// --------------------------------------------------------------------------- //
function openPanel(brk) {
  destroyInset(); // tear down any inset from a previously selected break
  document.getElementById("panel-content").innerHTML = renderPanel(brk);
  const panel = document.getElementById("panel");
  panel.hidden = false;
  // allow the browser a frame so the CSS transition runs
  requestAnimationFrame(() => panel.classList.add("open"));
  buildErosionInset(brk);
}

function closePanel() {
  document.getElementById("panel").classList.remove("open");
  destroyInset();
  if (selectedMarker) {
    selectedMarker.getElement()?.classList.remove("selected");
    selectedMarker = null;
  }
}

function renderPanel(brk) {
  return `
    <h2 class="break-name">${esc(brk.name)}</h2>
    <p class="break-region">${esc(brk.region)}</p>
    ${renderErosion(brk.erosion)}
    ${renderSeaLevel(brk.sea_level)}
    <p class="disclaimer">
      These indicators describe projected <strong>coastal and ocean conditions</strong>,
      not wave quality directly. The relationship between climate change and surfing
      conditions is complex; this tool does not predict whether a break will improve
      or degrade. Sea level uses RCP8.5 (high emissions), 50th percentile.
    </p>`;
}

// --- Coastal erosion ------------------------------------------------------- //
function renderErosion(e) {
  if (!e || e.status === "unavailable") {
    return indicatorShell(
      "Coastal Erosion Risk",
      "NCERM 2024 · Environment Agency",
      `<p class="unavailable">Erosion data could not be retrieved for this break.</p>`
    );
  }
  if (e.status === "no_nearby_frontage") {
    return indicatorShell(
      "Coastal Erosion Risk",
      "NCERM 2024 · Environment Agency",
      `<p class="unavailable">No NCERM erosion frontage is mapped near this break,
       so no erosion classification is available. This may mean the location is not
       in a mapped erosion zone rather than that it is risk-free.</p>`
    );
  }

  const risk = e.risk_level || "unknown";
  const pill = `<span class="pill risk-${risk}">${RISK_LABELS[risk] || "Unknown"}</span>`;
  const unit = e.smp_name
    ? `<p class="scenario">Policy unit: ${esc(e.smp_name)}${e.smp_pu ? " · " + esc(e.smp_pu) : ""} ·
       allowance: ${esc(e.climate_allowance || "")}</p>`
    : "";

  const rows = [
    ["Medium term (to 2055), with SMP", e.medium_term_with_smp],
    ["Long term (to 2105), with SMP", e.long_term_with_smp],
    ["Medium term, no intervention", e.medium_term_no_intervention],
    ["Long term, no intervention", e.long_term_no_intervention],
  ]
    .map(([label, cell]) => {
      if (!cell) return `<tr><td>${label}</td><td class="unavailable">—</td></tr>`;
      const policy = cell.policy ? esc(cell.policy) : null;
      const interp = cell.interpretation ? esc(cell.interpretation) : null;
      const band = cell.band != null ? `band ${cell.band}` : null;
      const text = [policy, interp].filter(Boolean).join(" · ") || band || "—";
      const bandSuffix = policy && band ? ` <span class="unavailable">(${band})</span>` : "";
      return `<tr><td>${label}</td><td>${text}${bandSuffix}</td></tr>`;
    })
    .join("");

  const body = `
    ${pill}
    ${unit}
    <p class="plain-english">
      Erosion of headlands, cliffs and beaches reshapes how waves break and can
      threaten access. The bands are NCERM recession indicators (higher = more
      projected erosion). "No intervention" shows the outlook if current sea
      defences were not maintained.
    </p>
    <table class="data"><tbody>${rows}</tbody></table>
    ${renderRecession(e)}`;
  return indicatorShell("Coastal Erosion Risk", "NCERM 2024 · Environment Agency", body);
}

// --- Recession zone inset map --------------------------------------------- //
function hasZone(cell) {
  return Boolean(cell && cell.zone);
}

function renderRecession(e) {
  const anyZone = [
    e.medium_term_with_smp,
    e.long_term_with_smp,
    e.medium_term_no_intervention,
    e.long_term_no_intervention,
  ].some(hasZone);
  if (!anyZone) return "";

  const hasNfi = hasZone(e.medium_term_no_intervention) || hasZone(e.long_term_no_intervention);
  return `
    <div class="recession">
      <div class="recession-head">
        <span class="recession-title">Projected erosion zone near this break</span>
        ${
          hasNfi
            ? `<label class="tgl"><input type="checkbox" id="tgl-nfi" /> show “no defences” outline</label>`
            : ""
        }
      </div>
      <div id="erosion-inset" class="inset-map"></div>
      <div class="inset-legend">
        <span><i class="sw mt"></i>to 2055 (with SMP)</span>
        <span><i class="sw lt"></i>to 2105 (with SMP)</span>
        ${hasNfi ? `<span id="lgd-nfi" class="nfi-only" hidden><i class="sw nfi"></i>no defences</span>` : ""}
        <span class="inset-note">shaded = land projected at risk</span>
      </div>
    </div>`;
}

function buildErosionInset(brk) {
  destroyInset();
  const el = document.getElementById("erosion-inset");
  const e = brk.erosion;
  if (!el || !e) return;

  insetMap = L.map(el, {
    zoomControl: true,
    attributionControl: false,
    scrollWheelZoom: false, // don't trap page scroll
    doubleClickZoom: true,
    dragging: true,
  });
  // A view MUST be set before adding layers, or the SVG renderer never
  // initialises and vector layers (zones, marker) silently fail to paint.
  // fit() refines this once the container has settled.
  insetMap.setView([brk.lat, brk.lon], 14);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
  }).addTo(insetMap);
  L.control.scale({ imperial: false, position: "bottomright" }).addTo(insetMap);

  const bounds = L.latLngBounds([]);
  const addZone = (cell, style) => {
    if (!hasZone(cell)) return;
    const layer = L.geoJSON(cell.zone, { style }).addTo(insetMap);
    bounds.extend(layer.getBounds());
  };
  // With-SMP fills: long term underneath (red wash), medium term on top (amber).
  addZone(e.long_term_with_smp, { color: "#e4572e", weight: 1, fillColor: "#e4572e", fillOpacity: 0.22 });
  addZone(e.medium_term_with_smp, { color: "#f2a900", weight: 1, fillColor: "#f2a900", fillOpacity: 0.45 });

  // The break itself.
  L.circleMarker([brk.lat, brk.lon], {
    radius: 6, color: "#fff", weight: 2, fillColor: "#1e6091", fillOpacity: 1,
  })
    .addTo(insetMap)
    .bindTooltip(brk.name, { permanent: false });
  bounds.extend([brk.lat, brk.lon]);

  // No-intervention outlines, hidden until toggled on.
  insetNfiLayer = L.layerGroup();
  const addNfi = (cell, style) => {
    if (hasZone(cell)) L.geoJSON(cell.zone, { style }).addTo(insetNfiLayer);
  };
  addNfi(e.long_term_no_intervention, { color: "#7a0010", weight: 2, dashArray: "4 3", fill: false });
  addNfi(e.medium_term_no_intervention, { color: "#7a0010", weight: 1, dashArray: "2 3", fill: false });

  const tgl = document.getElementById("tgl-nfi");
  if (tgl) {
    tgl.addEventListener("change", (ev) => {
      const lgd = document.getElementById("lgd-nfi");
      if (ev.target.checked) {
        insetNfiLayer.addTo(insetMap);
        if (lgd) lgd.hidden = false;
      } else {
        insetMap.removeLayer(insetNfiLayer);
        if (lgd) lgd.hidden = true;
      }
    });
  }

  // Defer sizing + fit until the container has its final layout. The panel may
  // still be sliding in, and invalidateSize MUST run before fitBounds — fitting
  // against a zero/stale size computes the wrong zoom and the zones land
  // off-screen (basemap-only symptom). Runs twice (next frame + after the open
  // transition); fitBounds is idempotent.
  const fit = () => {
    if (!insetMap) return;
    insetMap.invalidateSize();
    // Cap the zoom so short frontages aren't shown over-zoomed and cramped;
    // this keeps surrounding coastline visible for context.
    if (bounds.isValid()) insetMap.fitBounds(bounds.pad(0.35), { maxZoom: 15 });
    else insetMap.setView([brk.lat, brk.lon], 14);
  };
  requestAnimationFrame(() => requestAnimationFrame(fit));
  setTimeout(fit, 400);
}

function destroyInset() {
  if (insetMap) {
    try {
      insetMap.remove();
    } catch (err) {
      console.warn("inset teardown failed", err);
    }
    insetMap = null;
    insetNfiLayer = null;
  }
}

// --- Sea level rise -------------------------------------------------------- //
function renderSeaLevel(s) {
  if (!s || s.status !== "ok" || !s.anomaly_cm) {
    return indicatorShell(
      "Sea Level Rise",
      "Met Office UKCP18",
      `<p class="unavailable">Sea level projection could not be retrieved for this break.</p>`
    );
  }
  const years = Object.keys(s.anomaly_cm).sort();
  const latest = s.anomaly_cm[years[years.length - 1]];
  const body = `
    <p class="plain-english">
      Projected mean sea level rise of about <strong>${latest} cm by 2100</strong>
      (relative to the 1981–2000 baseline). Higher water shifts where waves break,
      most noticeably on beach breaks; reefs are generally less affected.
    </p>
    ${sparkline(s.anomaly_cm)}
    <table class="data">
      <thead><tr><th>Year</th><th>Anomaly (cm)</th></tr></thead>
      <tbody>${years
        .map((y) => `<tr><td>${y}</td><td>${s.anomaly_cm[y]}</td></tr>`)
        .join("")}</tbody>
    </table>`;
  return indicatorShell(
    "Sea Level Rise",
    `Met Office UKCP18 · ${esc(s.scenario)} · baseline ${esc(s.baseline)}`,
    body
  );
}

// --------------------------------------------------------------------------- //
// Inline SVG sparkline for the sea-level time series (no chart library).
// --------------------------------------------------------------------------- //
function sparkline(anomalyByYear) {
  const years = Object.keys(anomalyByYear).sort();
  const vals = years.map((y) => anomalyByYear[y]);
  const W = 320, H = 130, padL = 30, padR = 10, padT = 12, padB = 22;
  const maxV = Math.max(...vals);
  const minV = Math.min(0, ...vals);
  const x = (i) => padL + (i / (years.length - 1)) * (W - padL - padR);
  const y = (v) => H - padB - ((v - minV) / (maxV - minV || 1)) * (H - padT - padB);

  const linePts = vals.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const areaPts = `${x(0)},${y(minV)} ${linePts} ${x(years.length - 1)},${y(minV)}`;
  const xLabels = [years[0], years[years.length - 1]]
    .map((yr, k) => `<text x="${k === 0 ? padL : W - padR}" y="${H - 6}"
       text-anchor="${k === 0 ? "start" : "end"}">${yr}</text>`)
    .join("");

  return `
    <svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
         aria-label="Sea level rise from ${years[0]} to ${years[years.length - 1]}">
      <line class="axis" x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" />
      <line class="axis" x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" />
      <polygon class="area" points="${areaPts}" />
      <polyline class="line" points="${linePts}" />
      <text x="2" y="${y(maxV) + 3}">${maxV}</text>
      <text x="2" y="${y(minV)}">${minV}</text>
      ${xLabels}
    </svg>`;
}

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //
function indicatorShell(title, scenario, bodyHtml) {
  return `
    <section class="indicator">
      <h3>${title}</h3>
      <p class="scenario">${scenario}</p>
      ${bodyHtml}
    </section>`;
}

function renderAttribution(meta) {
  if (!meta) return;
  const sl = meta.sources?.sea_level;
  const er = meta.sources?.erosion;
  document.getElementById("attribution").innerHTML =
    `Data: ${esc(sl?.name || "UKCP18")} &amp; ${esc(er?.name || "NCERM 2024")} ` +
    `(OGL v3.0). Generated ${esc((meta.generated_at || "").slice(0, 10))}.`;
}

function esc(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
