// Small wrapper around Chart.js for lab trend lines with reference bands.
function labChart(canvas, points, opts) {
  opts = opts || {};
  const labels = points.map(p => p.d);
  const data = points.map(p => p.v);
  const lo = points.length ? points[points.length - 1].lo : null;
  const hi = points.length ? points[points.length - 1].hi : null;
  const ds = [{ data, borderColor: opts.color || '#2f6fed', backgroundColor: 'rgba(47,111,237,.12)', borderWidth: 2, pointRadius: 2.5, tension: .25, fill: false }];
  if (lo != null) ds.push({ data: points.map(() => lo), borderColor: 'rgba(0,0,0,.18)', borderDash: [4, 4], borderWidth: 1, pointRadius: 0, fill: false });
  if (hi != null) ds.push({ data: points.map(() => hi), borderColor: 'rgba(0,0,0,.18)', borderDash: [4, 4], borderWidth: 1, pointRadius: 0, fill: lo != null ? '-1' : false, backgroundColor: 'rgba(4,120,87,.07)' });
  new Chart(canvas, { type: 'line', data: { labels, datasets: ds }, options: {
    animation: false, responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => c.dataset === ds[0] ? `${c.parsed.y}` : null } } },
    scales: { x: { ticks: { maxTicksLimit: 5, font: { size: 10 } }, grid: { display: false } }, y: { ticks: { maxTicksLimit: 4, font: { size: 10 } }, beginAtZero: !!opts.zero } }
  } });
}
