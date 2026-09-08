// Lab trend lines with reference bands and intervention markers.
//
// Markers are dated events (a transfusion, a growth-factor shot) that explain a movement in the
// line. The x axis is categorical, so a marker whose date falls between two draws is placed by
// interpolating between their pixel positions rather than snapped to the nearest one.

function _xForDate(scale, labels, dateStr) {
  const t = Date.parse(dateStr);
  if (isNaN(t) || !labels.length) return null;
  const exact = labels.indexOf(dateStr);
  if (exact >= 0) return scale.getPixelForValue(exact);
  let lo = -1;
  for (let i = 0; i < labels.length; i++) {
    if (Date.parse(labels[i]) <= t) lo = i; else break;
  }
  if (lo < 0 || lo >= labels.length - 1) return null;   // outside the plotted range
  const t0 = Date.parse(labels[lo]), t1 = Date.parse(labels[lo + 1]);
  const p0 = scale.getPixelForValue(lo), p1 = scale.getPixelForValue(lo + 1);
  return t1 === t0 ? p0 : p0 + ((t - t0) / (t1 - t0)) * (p1 - p0);
}

const interventionMarkers = {
  id: 'interventionMarkers',
  afterDatasetsDraw(chart, _args, opts) {
    const marks = (opts && opts.marks) || [];
    if (!marks.length) return;
    const { ctx, chartArea: area, scales } = chart;
    const labels = chart.data.labels || [];
    ctx.save();
    for (const m of marks) {
      const x = _xForDate(scales.x, labels, m.d);
      if (x === null || x < area.left - 0.5 || x > area.right + 0.5) continue;
      ctx.globalAlpha = 0.45;              // the data line stays the primary read
      ctx.beginPath();
      ctx.setLineDash([3, 4]);
      ctx.lineWidth = 1.25;
      ctx.strokeStyle = m.color || '#b91c1c';
      ctx.moveTo(x, area.top);
      ctx.lineTo(x, area.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.beginPath();                 // a solid cap so the line reads as an event, not a gridline
      ctx.moveTo(x, area.top);
      ctx.lineTo(x - 3.5, area.top - 6);
      ctx.lineTo(x + 3.5, area.top - 6);
      ctx.closePath();
      ctx.fillStyle = m.color || '#b91c1c';
      ctx.fill();
    }
    ctx.restore();
  }
};
if (window.Chart) Chart.register(interventionMarkers);

function labChart(canvas, points, opts) {
  opts = opts || {};
  const marks = opts.markers || [];
  const labels = points.map(p => p.d);
  const data = points.map(p => p.v);
  const lo = points.length ? points[points.length - 1].lo : null;
  const hi = points.length ? points[points.length - 1].hi : null;
  const ds = [{ label: opts.label || 'value', data, borderColor: opts.color || '#2f6fed',
                backgroundColor: 'rgba(47,111,237,.12)', borderWidth: 2, pointRadius: 2.5,
                tension: .25, fill: false }];
  if (lo != null) ds.push({ data: points.map(() => lo), borderColor: 'rgba(0,0,0,.18)', borderDash: [4, 4],
                            borderWidth: 1, pointRadius: 0, fill: false });
  if (hi != null) ds.push({ data: points.map(() => hi), borderColor: 'rgba(0,0,0,.18)', borderDash: [4, 4],
                            borderWidth: 1, pointRadius: 0, fill: lo != null ? '-1' : false,
                            backgroundColor: 'rgba(4,120,87,.07)' });

  // Markers that land on a draw date are named in that point's tooltip.
  const byDate = {};
  for (const m of marks) (byDate[m.d] = byDate[m.d] || []).push(m.short || m.key);

  return new Chart(canvas, {
    type: 'line',
    data: { labels, datasets: ds },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      layout: { padding: { top: marks.length ? 9 : 0 } },
      plugins: {
        legend: { display: false },
        interventionMarkers: { marks },
        tooltip: {
          callbacks: {
            label: c => (c.datasetIndex === 0 ? `${c.parsed.y}` : null),
            afterBody: items => {
              const d = items.length ? labels[items[0].dataIndex] : null;
              return d && byDate[d] ? [byDate[d].join(', ')] : [];
            }
          }
        }
      },
      scales: {
        x: { ticks: { maxTicksLimit: 5, font: { size: 10 } }, grid: { display: false } },
        y: { ticks: { maxTicksLimit: 4, font: { size: 10 } }, beginAtZero: !!opts.zero }
      }
    }
  });
}
