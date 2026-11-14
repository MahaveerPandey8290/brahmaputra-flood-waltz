// Brahmaputra Flood Waltz — web app
(() => {
  const state = { m: null, mode: "explore", layer: "water", idx: 0, left: 0, right: 0, opacity: 0.85, playing: null, swipeX: 0.5 };
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-IN"));
  const fmtDate = (s) => new Date(s + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

  const map = L.map("map", { zoomControl: true, attributionControl: true });
  const bases = {
    imagery: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 18, attribution: "Basemap: Esri, Maxar, Earthstar Geographics" }),
    streets: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap contributors" }),
  };
  bases.imagery.addTo(map);
  map.attributionControl.addAttribution("Radar: NISAR L-band GCOV via ASF DAAC");

  map.createPane("left");  map.getPane("left").style.zIndex = 410;
  map.createPane("right"); map.getPane("right").style.zIndex = 411;
  let leftLayer = null, rightLayer = null;

  const url = (id, layer) => `data/layers/${id}_${layer}.png`;

  function setOverlay(which, src) {
    const pane = which;
    let lyr = which === "left" ? leftLayer : rightLayer;
    if (!src) { if (lyr) { map.removeLayer(lyr); } if (which === "left") leftLayer = null; else rightLayer = null; return; }
    if (lyr) { lyr.setUrl(src); lyr.setOpacity(state.opacity); }
    else {
      lyr = L.imageOverlay(src, state.m.bounds, { pane, opacity: state.opacity, interactive: false, className: "pix" }).addTo(map);
      if (which === "left") leftLayer = lyr; else rightLayer = lyr;
    }
  }

  // ---------- swipe clipping
  function clip() {
    const rp = map.getPane("right");
    if (state.mode !== "compare") { rp.style.clip = ""; return; }
    const size = map.getSize();
    const nw = map.containerPointToLayerPoint([0, 0]);
    const se = map.containerPointToLayerPoint(size);
    const x = nw.x + size.x * state.swipeX;
    rp.style.clip = `rect(${nw.y}px, ${se.x}px, ${se.y}px, ${x}px)`;
    $("swipe").style.left = (state.swipeX * 100) + "%";
  }
  map.on("move zoom resize", clip);
  (() => {
    const h = document.querySelector("#swipe .handle");
    let drag = false;
    const move = (e) => {
      if (!drag) return;
      const r = $("map").getBoundingClientRect();
      const cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
      state.swipeX = Math.min(0.98, Math.max(0.02, cx / r.width));
      clip();
    };
    h.addEventListener("mousedown", (e) => { drag = true; map.dragging.disable(); e.preventDefault(); });
    h.addEventListener("touchstart", (e) => { drag = true; map.dragging.disable(); e.preventDefault(); }, { passive: false });
    window.addEventListener("mousemove", move);
    window.addEventListener("touchmove", move, { passive: true });
    const up = () => { if (drag) { drag = false; map.dragging.enable(); } };
    window.addEventListener("mouseup", up);
    window.addEventListener("touchend", up);
  })();

  // ---------- legend
  const legends = {
    water: `<b>Open water</b><br><i style="background:#1e78ff"></i>Water (HH below threshold)`,
    change: `<b>Change since first pass</b><br><i style="background:#e62828"></i>Newly flooded<br><i style="background:#ffc800"></i>Water receded<br><i style="background:#1e5ac8"></i>Water on both dates`,
    rgb: `<b>False colour</b><br>R = HH, G = HV, B = HH/HV<br><i style="background:#000"></i>Water (dark)<br><i style="background:#5fa040"></i>Vegetation (green)<br><i style="background:#e080c0"></i>Buildings (pink/white)`,
    hh: `<b>HH backscatter</b><br><div class="ramp" style="background:linear-gradient(90deg,#000,#fff)"></div>−25 dB (smooth / water) → 0 dB (rough / urban)`,
    frequency: `<b>Flood frequency</b><br><div class="ramp" style="background:linear-gradient(90deg,#ffffff,#0066ff)"></div>Flooded once → flooded every pass`,
  };

  // ---------- render
  function render() {
    const m = state.m, d = m.dates;
    $("layerBlock").classList.toggle("hidden", state.mode === "frequency");
    $("timeBlock").classList.toggle("hidden", state.mode !== "explore");
    $("compareBlock").classList.toggle("hidden", state.mode !== "compare");
    $("swipe").classList.toggle("hidden", state.mode !== "compare");

    if (state.mode === "frequency") {
      setOverlay("left", "data/layers/flood_frequency.png"); setOverlay("right", null);
      $("legend").innerHTML = legends.frequency;
    } else if (state.mode === "compare") {
      setOverlay("left", url(d[state.left].id, state.layer));
      setOverlay("right", url(d[state.right].id, state.layer));
      $("legend").innerHTML = legends[state.layer] + `<br><span class="hint">Left: ${fmtDate(d[state.left].date)} · Right: ${fmtDate(d[state.right].date)}</span>`;
    } else {
      setOverlay("left", url(d[state.idx].id, state.layer)); setOverlay("right", null);
      $("legend").innerHTML = legends[state.layer];
      $("dateLabel").textContent = fmtDate(d[state.idx].date);
    }
    clip();
    renderStats();
    chart.update("none");
  }

  function renderStats() {
    const m = state.m, s = m.summary;
    const cur = state.mode === "compare" ? m.dates[state.right] : m.dates[state.idx];
    const tiles = state.mode === "frequency"
      ? [[s.ever_flooded_km2, "km² flooded at least once"], [s.seasonal_only_km2, "km² seasonal flood only"],
         [s.permanent_water_km2, "km² water on every pass"], [s.peak_water_km2, `km² peak (${fmtDate(s.peak_date)})`]]
      : [[cur.water_km2, `km² water on ${fmtDate(cur.date)}`], [cur.new_water_km2, "km² newly flooded vs first pass"],
         [cur.receded_km2, "km² water receded vs first pass"], [s.peak_water_km2, `km² season peak (${fmtDate(s.peak_date)})`]];
    $("stats").innerHTML = tiles.map(([v, t]) => `<div class="stat"><b>${fmt(v)}</b><span>${t}</span></div>`).join("");
  }

  // ---------- chart
  let chart;
  function buildChart() {
    const m = state.m;
    const css = getComputedStyle(document.documentElement);
    const ink = css.getPropertyValue("--muted").trim(), grid = css.getPropertyValue("--line").trim(), acc = css.getPropertyValue("--accent").trim();
    const marker = {
      id: "marker",
      afterDatasetsDraw(c) {
        const i = state.mode === "compare" ? state.right : state.idx;
        const pt = c.getDatasetMeta(0).data[i]; if (!pt || state.mode === "frequency") return;
        const g = c.ctx; g.save(); g.strokeStyle = acc; g.setLineDash([4, 3]);
        g.beginPath(); g.moveTo(pt.x, c.chartArea.top); g.lineTo(pt.x, c.chartArea.bottom); g.stroke(); g.restore();
      },
    };
    chart = new Chart($("chart"), {
      type: "line",
      data: {
        labels: m.dates.map((r) => fmtDate(r.date).replace(/ \d{4}$/, "")),
        datasets: [
          { label: "Water (km²)", data: m.dates.map((r) => r.water_km2), borderColor: acc, backgroundColor: acc + "33", fill: true, tension: 0.3, pointRadius: 3 },
          { label: "Newly flooded (km²)", data: m.dates.map((r) => r.new_water_km2), borderColor: "#e62828", borderDash: [5, 3], pointRadius: 2, tension: 0.3 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { color: ink, boxWidth: 12, font: { size: 11 } } } },
        scales: { x: { ticks: { color: ink, font: { size: 10 }, maxRotation: 0, autoSkip: true }, grid: { color: grid } },
                  y: { ticks: { color: ink, font: { size: 10 } }, grid: { color: grid }, beginAtZero: true } },
        onClick: (e, els) => { if (els.length && state.mode === "explore") { state.idx = els[0].index; $("dateSlider").value = state.idx; render(); } },
      },
      plugins: [marker],
    });
  }

  // ---------- story
  function story() {
    const m = state.m, s = m.summary, d = m.dates;
    const first = d[0], last = d[d.length - 1];
    const grow = s.peak_water_km2 - first.water_km2;
    $("story").innerHTML = `
      <p><b>What the radar shows.</b> On the first NISAR pass of the season (${fmtDate(first.date)}), about <b>${fmt(first.water_km2)} km²</b> of the study area was open water: the braided Brahmaputra channel plus flooded fields and wetlands beside it. ${s.peak_date === first.date
        ? `That was the widest water extent of the season.`
        : `Water peaked on <b>${fmtDate(s.peak_date)}</b> at <b>${fmt(s.peak_water_km2)} km²</b>${grow > 0 ? ` (${fmt(grow)} km² more)` : ""}.`} It stayed high through August as new monsoon pulses arrived, then drained. By ${fmtDate(last.date)} only <b>${fmt(last.water_km2)} km²</b> was left, ${fmt(100 * (1 - last.water_km2 / s.peak_water_km2))}% less than the peak.</p>
      <p><b>Why it happens.</b> Monsoon rain over the eastern Himalaya and Assam, plus snow and glacier melt upstream, swells the Brahmaputra and its tributaries. The low, flat floodplain and silt-raised riverbed let water spill sideways into fields, wetlands (beels) and Kaziranga's grasslands. ${fmt(s.seasonal_only_km2)} km² flooded only for part of the season. That pulse refills wetlands and fertilises farmland, but it also hits homes, roads and wildlife.</p>
      <p class="hint">Water threshold: HH &lt; ${m.threshold_db} dB · ${d.length} NISAR passes · grid ${Math.round(m.pixel_deg * 111000)} m</p>`;
    $("dataList").innerHTML = d.map((r) => `<li>${r.date}: ${r.granules.map((g) => `<code>${g}</code>`).join(", ")}</li>`).join("") +
      `<li>Basemaps: Esri World Imagery, OpenStreetMap</li>`;
  }

  // ---------- controls
  function segment(id, key, cb) {
    $(id).addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      [...$(id).children].forEach((x) => x.classList.toggle("on", x === b));
      cb(b.dataset[key]);
    });
  }
  segment("modeSeg", "mode", (v) => { stop(); state.mode = v; render(); });
  segment("layerSeg", "layer", (v) => { state.layer = v; render(); });
  segment("baseSeg", "base", (v) => { Object.values(bases).forEach((b) => map.removeLayer(b)); bases[v].addTo(map); });
  $("dateSlider").addEventListener("input", (e) => { state.idx = +e.target.value; render(); });
  $("opacity").addEventListener("input", (e) => {
    state.opacity = e.target.value / 100; $("opLabel").textContent = e.target.value + "%";
    [leftLayer, rightLayer].forEach((l) => l && l.setOpacity(state.opacity));
  });
  $("leftSel").addEventListener("change", (e) => { state.left = +e.target.value; render(); });
  $("rightSel").addEventListener("change", (e) => { state.right = +e.target.value; render(); });
  function stop() { if (state.playing) { clearInterval(state.playing); state.playing = null; $("playBtn").textContent = "▶"; } }
  $("playBtn").addEventListener("click", () => {
    if (state.playing) return stop();
    $("playBtn").textContent = "❚❚";
    state.playing = setInterval(() => { state.idx = (state.idx + 1) % state.m.dates.length; $("dateSlider").value = state.idx; render(); }, 1200);
  });
  $("aboutBtn").addEventListener("click", () => $("about").showModal());

  // ---------- load
  fetch("data/manifest.json").then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }).then((m) => {
    state.m = m;
    const n = m.dates.length;
    $("dateSlider").max = n - 1;
    const opts = m.dates.map((r, i) => `<option value="${i}">${fmtDate(r.date)}</option>`).join("");
    $("leftSel").innerHTML = opts; $("rightSel").innerHTML = opts;
    state.left = 0;
    state.right = m.dates.findIndex((r) => r.date === m.summary.peak_date);
    if (state.right <= 0) state.right = n - 1;
    $("leftSel").value = state.left; $("rightSel").value = state.right;
    map.fitBounds(m.bounds);
    // preload images so the animation is smooth
    m.dates.forEach((r) => ["water", "change", "rgb", "hh"].forEach((l) => { new Image().src = url(r.id, l); }));
    buildChart(); story(); render();
    $("status").classList.add("hidden");
  }).catch((e) => {
    map.setView([26.75, 93.8], 9);
    $("status").textContent = "NISAR layers not processed yet (data/manifest.json missing).";
    console.error(e);
  });
})();
