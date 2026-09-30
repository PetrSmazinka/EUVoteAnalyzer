/**
 * @file app.js
 * Main application script for the EUVoteAnalyzer web front-end.
 *
 * Fetches pre-generated JSON analysis files for a selected parliamentary term and
 * renders interactive Chart.js charts and DataTables across all dashboard tabs.
 *
 * Tabs and their primary render functions:
 *   Faction cohesion      – renderParty()
 *   Country cohesion      – renderCountry()
 *   Inter-faction         – renderInter()
 *   MEP loyalty           – renderLoyalty()
 *   MEP participation     – renderParticipation()
 *   Correlation           – initCorrTab(), renderCorrChart()
 *   MEP comparison        – setupMepPicker(), runComparison(), renderComparison()
 *   Topic analysis        – initTopicTab(), renderTopicHeatmap(), renderAssocRules(),
 *                           renderDeviationRules(), renderOppositionRules()
 *   MEP anomaly           – renderAnomalyResult(), renderMemberStats(), renderSubjectStats()
 *
 * Dependencies: Chart.js (with ChartDataLabels plugin), DataTables (jQuery),
 *               library.js (Dictionary).
 */
'use strict';

// Register ChartDataLabels globally (disabled by default; enabled per-chart via options)
Chart.register(ChartDataLabels);
Chart.defaults.plugins.datalabels = { display: false };

// ── Country colour map (ISO3) ─────────────────────────────────────────────────
const COUNTRY_COLORS = [
    '#1565C0','#C62828','#2E7D32','#E65100','#6A1B9A',
    '#00838F','#AD1457','#558B2F','#4527A0','#00695C',
    '#F57F17','#283593','#BF360C','#1B5E20','#880E4F',
    '#33691E','#827717','#006064','#4A148C','#01579B',
    '#37474F','#3E2723','#263238','#1A237E','#B71C1C',
    '#212121','#004D40',
];

// ── State ────────────────────────────────────────────────────────────────────
const charts = {};
const dtInstances = {};
let currentTerm = null;
let pairIndexCache = {};   // term → { meps: {id: {...}}, categories: [...] }
let pairDataCache  = {};   // `${term}/${viewKey}` → array of pair objects

// ── Helpers ──────────────────────────────────────────────────────────────────
/**
 * Fetch a JSON file and return a Promise that resolves to the parsed object.
 * @param {string} url - URL of the JSON resource.
 * @returns {Promise<*>} Parsed JSON value.
 * @throws {Error} When the HTTP response is not OK.
 */
function fetchJSON(url) {
    return fetch(url).then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
        return r.json();
    });
}

/**
 * Destroy a Chart.js instance stored under `key` in the `charts` registry.
 * @param {string} key - Registry key used when the chart was created.
 */
function destroyChart(key) {
    if (charts[key]) { charts[key].destroy(); delete charts[key]; }
}

/**
 * Destroy a DataTable instance and empty its tbody.
 * @param {string} id - The `id` attribute of the `<table>` element.
 */
function destroyTable(id) {
    if (dtInstances[id]) { dtInstances[id].destroy(); delete dtInstances[id]; }
    $(`#${id} tbody`).empty();
}

/**
 * Map a normalised value (0–1) to an HSL colour ranging from red through yellow to green.
 * @param {number} val - Normalised score in the range [0, 1].
 * @returns {string} CSS `hsl(...)` colour string.
 */
function hslFromValue(val) {
    // val 0..1 → red(0°) → yellow(60°) → green(120°)
    const hue = Math.round(val * 120);
    const sat = 70;
    const lig = val < 0.5 ? 45 : 38;
    return `hsl(${hue},${sat}%,${lig}%)`;
}

/**
 * Look up a translation key in the active locale (shorthand for Dictionary.term).
 * @param {string} term - Dot-separated i18n key.
 * @returns {string} Translated string.
 */
function translation(term){
    return Dictionary.term(term);
}

/**
 * Wrap a translation in a `<span>` with a `data-text` attribute so it is
 * automatically re-translated when the language changes.
 * @param {string} term - Dot-separated i18n key.
 * @returns {string} HTML string.
 */
function translatable(term){
    return `<span data-text="${term}">${translation(term)}</span>`;
}

// ── Renderers ─────────────────────────────────────────────────────────────────

/**
 * Render the faction cohesion bar chart.
 * @param {Array<{zkratka: string, ai: number, color: string, sessions: number}>} data
 *   One object per political group; `ai` is the Agreement Index in percent.
 */
function renderParty(data) {
    destroyChart('party');
    const labels = data.map(d => d.zkratka);
    const values = data.map(d => d.ai);
    const colors = data.map(d => d.color || '#9E9E9E');
    const avg = (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1);
    document.getElementById('party-stat').textContent = `${translation("tabs.factionCohesion.avg")} ${avg}%`;

    charts.party = new Chart(document.getElementById('chart-party'), {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: translation("tabs.factionCohesion.agreementIndex"),
                data: values,
                backgroundColor: colors,
                borderRadius: 4,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: ctx => ` ${translation("tabs.factionCohesion.agreementIndex")}: ${ctx.raw.toFixed(2)}%   (${data[ctx.dataIndex].sessions} ${Dictionary.term("tabs.factionCohesion.sessions")})`
                    }
                }
            },
            scales: {
                x: {
                    min: 0, max: 100,
                    title: { display: true, text: translation("tabs.factionCohesion.agreementIndex") },
                    grid: { color: '#e5e7eb' }
                },
                y: { grid: { display: false } }
            }
        }
    });
}

/**
 * Render the country cohesion horizontal bar chart, coloured by AI value.
 * @param {Array<{country: string, ai: number, mep_count: number, votes: number}>} data
 */
function renderCountry(data) {
    destroyChart('country');
    const labels  = data.map(d => d.country);
    const values  = data.map(d => d.ai);
    const colors  = values.map(v => hslFromValue(v / 100));
    const avg = (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1);
    document.getElementById('country-stat').textContent = `${translation("tabs.countryCohesion.avg")} ${avg}%`;

    charts.country = new Chart(document.getElementById('chart-country'), {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: translation("tabs.countryCohesion.agreementIndex"),
                data: values,
                backgroundColor: colors,
                borderRadius: 4,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const d = data[ctx.dataIndex];
                            return ` ${translation("tabs.countryCohesion.agreementIndexAbbr")}: ${ctx.raw.toFixed(2)}%   (${d.mep_count} ${translation("tabs.countryCohesion.meps")}, ${d.votes.toLocaleString()} ${translation("tabs.countryCohesion.votes")})`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    min: 0, max: 100,
                    title: { display: true, text: translation("tabs.countryCohesion.agreementIndex") },
                    grid: { color: '#e5e7eb' }
                },
                y: { grid: { display: false }, ticks: { font: { size: 11 } } }
            }
        }
    });
}

/**
 * Render the inter-faction cohesion heatmap as an HTML table.
 * @param {Array<{subj1_id: number, subj1_zkratka: string, subj1_color: string,
 *                subj2_id: number, subj2_zkratka: string, subj2_color: string,
 *                agreement: number}>} data
 */
function renderInter(data) {
    // Collect unique factions preserving order of appearance
    const factionMap = new Map();
    data.forEach(d => {
        if (!factionMap.has(d.subj1_id)) factionMap.set(d.subj1_id, { id: d.subj1_id, name: d.subj1_zkratka, color: d.subj1_color || '#9E9E9E' });
        if (!factionMap.has(d.subj2_id)) factionMap.set(d.subj2_id, { id: d.subj2_id, name: d.subj2_zkratka, color: d.subj2_color || '#9E9E9E' });
    });
    const factions = Array.from(factionMap.values());

    // Build symmetric matrix
    const matrix = {};
    factions.forEach(f => {
        matrix[f.id] = {};
        factions.forEach(g => { matrix[f.id][g.id] = (f.id === g.id) ? 1.0 : null; });
    });
    data.forEach(d => {
        matrix[d.subj1_id][d.subj2_id] = d.agreement;
        matrix[d.subj2_id][d.subj1_id] = d.agreement;
    });

    let html = '<table class="table table-bordered mb-0" style="min-width:420px;border-collapse:collapse">';
    // Header row
    html += '<thead><tr><th style="background:#fff"></th>';
    factions.forEach(f => {
        html += `<th class="heatmap-header text-center" style="background:${f.color};padding:8px 4px">${f.name}</th>`;
    });
    html += '</tr></thead><tbody>';

    factions.forEach(f => {
        html += `<tr><td class="heatmap-header" style="background:${f.color};padding:6px 10px;white-space:nowrap">${f.name}</td>`;
        factions.forEach(g => {
            const val = matrix[f.id][g.id];
            if (val === null) {
                html += `<td class="heatmap-cell" style="background:#eee;color:#aaa">—</td>`;
            } else {
                const bg = hslFromValue(val);
                const pct = (val * 100).toFixed(1);
                html += `<td class="heatmap-cell" style="background:${bg};color:white" title="${f.name} ↔ ${g.name}: ${pct}%">${pct}%</td>`;
            }
        });
        html += '</tr>';
    });
    html += '</tbody></table>';
    document.getElementById('heatmap-container').innerHTML = html;
}

/**
 * Render the MEP loyalty distribution histogram and sortable DataTable.
 * @param {Array<{jmeno: string, prijmeni: string, obcanstvi: string,
 *                zkratka: string, color: string,
 *                total: number, loyal_votes: number, loyalty: number}>} data
 */
function renderLoyalty(data) {
    destroyChart('loyaltyDist');
    destroyTable('table-loyalty');

    // Histogram (10 bins: 0–10 … 90–100%)
    const bins = Array(10).fill(0);
    data.forEach(d => bins[Math.min(9, Math.floor(d.loyalty * 10))]++);
    const binLabels = ['0–10','10–20','20–30','30–40','40–50','50–60','60–70','70–80','80–90','90–100'];
    document.getElementById('loyalty-stat').textContent = `${data.length} ${translation("tabs.mepLoyalty.meps")}`;

    charts.loyaltyDist = new Chart(document.getElementById('chart-loyalty-dist'), {
        type: 'bar',
        data: {
            labels: binLabels,
            datasets: [{ label: translation("tabs.mepLoyalty.mepsAxis"), data: bins, backgroundColor: '#1565C0', borderRadius: 3 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { title: { display: true, text: translation("tabs.mepLoyalty.loyalty") }, grid: { display: false } },
                y: { title: { display: true, text: translation("tabs.mepLoyalty.mepsAxis") }, grid: { color: '#e5e7eb' } }
            }
        }
    });

    // DataTable
    const tbody = document.querySelector('#table-loyalty tbody');
    data.forEach(d => {
        const color = d.color || '#9E9E9E';
        const pct = (d.loyalty * 100).toFixed(1);
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escHtml(d.jmeno)} ${escHtml(d.prijmeni)}</td>
            <td>${d.obcanstvi}</td>
            <td><span class="party-badge" style="background:${color}">${escHtml(d.zkratka)}</span></td>
            <td>${d.total}</td>
            <td>${d.loyal_votes}</td>
            <td data-sort="${d.loyalty}">${pct}%</td>`;
        tbody.appendChild(row);
    });

    dtInstances['table-loyalty'] = $('#table-loyalty').DataTable({
        pageLength: 25,
        order: [[5, 'desc']],
        columnDefs: [{ targets: 5, type: 'num' }],
        language: {
            search: translation("datatables.search"),
            lengthMenu: translation("datatables.lengthMenu"),
            info: translation("datatables.info"),
            emptyTable: translation("datatables.emptyTable"),
            infoEmpty: translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });
}

/**
 * Render the Government/Opposition Loyalty tab: a grouped bar chart comparing
 * average loyalty (government vs. opposition) per EP group, plus a per-MEP
 * detail table. Data is per-MEP x government-status rows from
 * government_opposition_loyalty.json.
 * @param {Array<Object>} data - Per-MEP government/opposition loyalty rows.
 */
function renderGovLoyalty(data) {
    destroyChart('govLoyalty');
    destroyTable('table-gov-loyalty');

    document.getElementById('gov-loyalty-stat').textContent = `${data.length} ${translation("tabs.mepLoyalty.meps")}`;

    // An MEP appears once per government-status bucket they cast votes in, so an
    // MEP whose national party changed status mid-term shows up as two rows —
    // the status filter lets the reader isolate one bucket instead of reading
    // that as a duplicate.
    const filterSelect = document.getElementById('gov-loyalty-status-filter');
    filterSelect.value = 'all';

    // Aggregate client-side by EP group x government status (vote-weighted loyalty)
    const byGroup = {};
    data.forEach(d => {
        const key = d.zkratka || String(d.ck_subjekt);
        const g = byGroup[key] || (byGroup[key] = { color: d.color, gov: { loyal: 0, total: 0 }, opp: { loyal: 0, total: 0 } });
        const bucket = d.in_government ? g.gov : g.opp;
        bucket.loyal += d.loyal_votes;
        bucket.total += d.total;
    });
    const labels = Object.keys(byGroup);
    const govPct = labels.map(k => byGroup[k].gov.total ? 100 * byGroup[k].gov.loyal / byGroup[k].gov.total : null);
    const oppPct = labels.map(k => byGroup[k].opp.total ? 100 * byGroup[k].opp.loyal / byGroup[k].opp.total : null);

    charts.govLoyalty = new Chart(document.getElementById('chart-gov-loyalty'), {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: translation("tabs.govLoyalty.government"), data: govPct, backgroundColor: '#1565C0', borderRadius: 3 },
                { label: translation("tabs.govLoyalty.opposition"), data: oppPct, backgroundColor: '#C62828', borderRadius: 3 },
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { grid: { display: false } },
                y: { min: 0, max: 100, title: { display: true, text: translation("tabs.govLoyalty.loyalty") }, grid: { color: '#e5e7eb' } }
            }
        }
    });

    // Per-MEP detail table
    const tbody = document.querySelector('#table-gov-loyalty tbody');
    data.forEach(d => {
        const color = d.color || '#9E9E9E';
        const nationalColor = d.national_color || '#9E9E9E';
        const pct = (d.loyalty * 100).toFixed(1);
        const statusText = translation(d.in_government ? "tabs.govLoyalty.government" : "tabs.govLoyalty.opposition");
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escHtml(d.jmeno)} ${escHtml(d.prijmeni)}</td>
            <td>${d.obcanstvi}</td>
            <td><span class="party-badge" style="background:${color}">${escHtml(d.zkratka)}</span></td>
            <td><span class="party-badge" style="background:${nationalColor}">${escHtml(d.national_zkratka)}</span></td>
            <td>${escHtml(statusText)}</td>
            <td>${d.total}</td>
            <td>${d.loyal_votes}</td>
            <td data-sort="${d.loyalty}">${pct}%</td>`;
        tbody.appendChild(row);
    });

    dtInstances['table-gov-loyalty'] = $('#table-gov-loyalty').DataTable({
        pageLength: 25,
        order: [[7, 'desc']],
        columnDefs: [{ targets: 7, type: 'num' }],
        language: {
            search: translation("datatables.search"),
            lengthMenu: translation("datatables.lengthMenu"),
            info: translation("datatables.info"),
            emptyTable: translation("datatables.emptyTable"),
            infoEmpty: translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });

    const table = dtInstances['table-gov-loyalty'];
    filterSelect.onchange = () => {
        const val = filterSelect.value;
        const needle = val === 'government' ? `^${escRegex(translation("tabs.govLoyalty.government"))}$`
                     : val === 'opposition' ? `^${escRegex(translation("tabs.govLoyalty.opposition"))}$`
                     : '';
        table.column(4).search(needle, true, false).draw();
    };
}

/**
 * Render the MEP participation distribution histogram and sortable DataTable.
 * @param {Array<{jmeno: string, prijmeni: string, obcanstvi: string,
 *                zkratka: string, color: string,
 *                celkem: number, pritomen: number, participace: number}>} data
 */
function renderParticipation(data) {
    destroyChart('partDist');
    destroyTable('table-participation');

    const bins = Array(10).fill(0);
    data.forEach(d => {
        const p = parseFloat(d.participace);
        bins[Math.min(9, Math.floor(p * 10))]++;
    });
    const binLabels = ['0–10','10–20','20–30','30–40','40–50','50–60','60–70','70–80','80–90','90–100'];
    document.getElementById('part-stat').textContent = `${data.length} ${translation("tabs.mepParticipation.meps")}`;

    charts.partDist = new Chart(document.getElementById('chart-part-dist'), {
        type: 'bar',
        data: {
            labels: binLabels,
            datasets: [{ label: translation("tabs.mepParticipation.mepsAxis"), data: bins, backgroundColor: '#FFB300', borderRadius: 3 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { title: { display: true, text: translation("tabs.mepParticipation.participation") }, grid: { display: false } },
                y: { title: { display: true, text: translation("tabs.mepParticipation.mepsAxis") }, grid: { color: '#e5e7eb' } }
            }
        }
    });

    const tbody = document.querySelector('#table-participation tbody');
    data.forEach(d => {
        const p = parseFloat(d.participace);
        const pct = (p * 100).toFixed(1);
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escHtml(d.jmeno)} ${escHtml(d.prijmeni)}</td>
            <td>${d.obcanstvi}</td>
            <td>${d.pritomen}</td>
            <td>${d.celkem}</td>
            <td data-sort="${p}">${pct}%</td>`;
        tbody.appendChild(row);
    });

    dtInstances['table-participation'] = $('#table-participation').DataTable({
        pageLength: 25,
        order: [[4, 'desc']],
        columnDefs: [{ targets: 4, type: 'num' }],
        language: {
            search: translation("datatables.search"),
            lengthMenu: translation("datatables.lengthMenu"), 
            info: translation("datatables.info"),
            emptyTable: translation("datatables.emptyTable"),
            infoEmpty: translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });
}

/**
 * Escape a string for safe insertion into HTML content.
 * @param {*} s - Value to escape (coerced to string).
 * @returns {string} HTML-safe string.
 */
function escHtml(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/**
 * Escape a string for safe insertion into a regular expression.
 * @param {*} s - Value to escape (coerced to string).
 * @returns {string} Regex-safe string.
 */
function escRegex(s) {
    return String(s ?? '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Fetch all pre-generated JSON files for `term` and render every primary tab.
 * Shows a loading overlay while requests are in flight.
 * @param {string|number} term - Parliamentary term identifier (directory name under `data/`).
 */
async function loadTerm(term) {
    document.getElementById('loading-overlay').style.display = 'flex';
    try {
        const base = `data/${term}`;
        const [party, country, inter, loyalty, govLoyalty, participation] = await Promise.all([
            fetchJSON(`${base}/party_cohesion.json`),
            fetchJSON(`${base}/country_cohesion.json`),
            fetchJSON(`${base}/inter_faction_cohesion.json`),
            fetchJSON(`${base}/mep_loyalty.json`),
            fetchJSON(`${base}/government_opposition_loyalty.json`),
            fetchJSON(`${base}/mep_participation.json`),
        ]);
        renderParty(party);
        renderCountry(country);
        renderInter(inter);
        renderLoyalty(loyalty);
        renderGovLoyalty(govLoyalty);
        renderParticipation(participation);
    } catch (err) {
        alert(`Failed to load data for ${term}: ${err.message}`);
    } finally {
        document.getElementById('loading-overlay').style.display = 'none';
    }
}

// ── Category Correlations ────────────────────────────────────────────────────

let corrCache = {};   // keyed by term

/**
 * Convert a p-value to an APA-style significance star string.
 * @param {number|null} p - p-value from a Pearson correlation test.
 * @returns {string} `"***"`, `"**"`, `"*"`, `"n.s."`, or `""` when `p` is null.
 */
function sigStars(p) {
    if (p == null) return '';
    if (p < 0.001) return '***';
    if (p < 0.01)  return '**';
    if (p < 0.05)  return '*';
    return 'n.s.';
}

/**
 * Compute a simple linear regression line from a set of {x, y} points.
 * @param {Array<{x: number, y: number}>} points - Scatter-plot data points.
 * @returns {Array<{x: number, y: number}>|null} Two endpoint objects for the regression
 *   line (slightly extended past the data range), or `null` when regression is undefined.
 */
function linRegression(points) {
    const n = points.length;
    if (n < 2) return null;
    const xs = points.map(p => p.x);
    const ys = points.map(p => p.y);
    const mx = xs.reduce((a, b) => a + b, 0) / n;
    const my = ys.reduce((a, b) => a + b, 0) / n;
    const num = xs.reduce((s, x, i) => s + (x - mx) * (ys[i] - my), 0);
    const den = xs.reduce((s, x) => s + (x - mx) ** 2, 0);
    if (den === 0) return null;
    const slope = num / den;
    const intercept = my - slope * mx;
    const xMin = Math.min(...xs);
    const xMax = Math.max(...xs);
    return [{ x: xMin - 0.02, y: intercept + slope * (xMin - 0.02) },
            { x: xMax + 0.02, y: intercept + slope * (xMax + 0.02) }];
}

// ── Correlation state ────────────────────────────────────────────
let corrData   = null;   // loaded JSON
let corrPoints = [];     // current scatter points — referenced by datalabels formatter

/**
 * Populate the category and indicator `<select>` elements for the correlation tab.
 * Preserves the currently selected values when data is refreshed for a new term.
 * @param {Object} data - Parsed `category_correlations.json` object.
 */
function populateCorrSelectors(data) {
    corrData = data;
    const catSel = document.getElementById('corr-category');
    const indSel = document.getElementById('corr-indicator');
    const cats = Object.entries(data.categories).sort((a, b) => a[0].localeCompare(b[0]));

    // Preserve current selection when reloading for a new term
    const prevCat = catSel.value;
    catSel.innerHTML = '';
    cats.forEach(([code, cat]) => {
        const opt = document.createElement('option');
        opt.value = code;
        opt.textContent = `${code} — ${Dictionary.term(`categories.${code}`)}`;
        opt.dataset.prefix = `${code} — `;
        opt.dataset.content = `categories.${code}`;
        catSel.appendChild(opt);
    });
    if (prevCat && data.categories[prevCat]) catSel.value = prevCat;

    refreshCorrIndicators();
}

/**
 * Repopulate the indicator `<select>` based on the currently selected category,
 * then re-render the correlation chart.
 */
function refreshCorrIndicators() {
    if (!corrData) return;
    const catSel = document.getElementById('corr-category');
    const indSel = document.getElementById('corr-indicator');
    const catCode = catSel.value;
    const inds = Object.keys(corrData.categories[catCode]?.indicators || {});
    const prevInd = indSel.value;
    indSel.innerHTML = '';
    inds.forEach(ind => {
        const opt = document.createElement('option');
        opt.value = ind;
        opt.textContent = Dictionary.term(`indicators.${ind}`) || ind;
        opt.dataset.content = `indicators.${ind}`;
        indSel.appendChild(opt);
    });
    if (prevInd && inds.includes(prevInd)) indSel.value = prevInd;
    renderCorrChart();
}

/** Attach change-event listeners to the correlation category and indicator selectors. */
function setupCorrListeners() {
    document.getElementById('corr-category').addEventListener('change', refreshCorrIndicators);
    document.getElementById('corr-indicator').addEventListener('change', renderCorrChart);
}

/**
 * Render or update the Pearson-correlation scatter chart for the selected
 * category and indicator combination.  Updates the chart in-place on subsequent
 * calls to avoid canvas-reuse errors.
 */
function renderCorrChart() {
    const data = corrData;
    if (!data) return;
    const catCode = document.getElementById('corr-category').value;
    const indCode = document.getElementById('corr-indicator').value;
    const catObj  = data.categories[catCode];
    if (!catCode || !catObj) return;
    const indObj  = catObj.indicators[indCode];
    if (!indCode || !indObj) return;

    // Update module-level ref — datalabels formatter reads this
    corrPoints = indObj.points.map((p, i) => ({
        x: p.vote_avg, y: p.indicator,
        label: p.country,
        color: COUNTRY_COLORS[i % COUNTRY_COLORS.length],
    }));

    const regLine  = linRegression(corrPoints);
    const sig      = sigStars(indObj.p_value);

    document.getElementById('corr-stats-body').innerHTML =
        `<tr><td class="text-muted" data-text="tabs.categoryCorrelations.table.category">${translation("tabs.categoryCorrelations.table.category")}</td><td><strong>${catCode}</strong> \u2014 <span data-text="categories.${catCode}">${translation(`categories.${catCode}`)}</span></td></tr>` +
        `<tr><td class="text-muted" data-text="tabs.categoryCorrelations.table.votes">${translation("tabs.categoryCorrelations.table.votes")}</td><td>${catObj.vote_count.toLocaleString()}</td></tr>` +
        `<tr><td class="text-muted" data-text="tabs.categoryCorrelations.table.indicator">${translation("tabs.categoryCorrelations.table.indicator")}</td><td data-text="indicators.${indCode}">${translation(`indicators.${indCode}`)}</td></tr>` +
        `<tr><td class="text-muted" data-text="tabs.categoryCorrelations.table.countries">${translation("tabs.categoryCorrelations.table.countries")}</td><td>${indObj.n_countries}</td></tr>` +
        `<tr><td class="text-muted" data-text="tabs.categoryCorrelations.table.pearsonR">${translation("tabs.categoryCorrelations.table.pearsonR")}</td><td><strong>${indObj.pearson_r != null ? indObj.pearson_r.toFixed(4) : 'n/a'}</strong> <span class="text-danger">${sig}</span></td></tr>` +
        `<tr><td class="text-muted" data-text="tabs.categoryCorrelations.table.pValue">${translation("tabs.categoryCorrelations.table.pValue")}</td><td>${indObj.p_value != null ? indObj.p_value.toFixed(5) : 'n/a'}</td></tr>`;

    // Update in place — no destroy/recreate (avoids canvas-reuse errors)
    if (charts.corr) {
        const ds = charts.corr.data.datasets;
        ds[0].data            = corrPoints.map(p => ({ x: p.x, y: p.y }));
        ds[0].backgroundColor = corrPoints.map(p => p.color);
        if (regLine && ds.length > 1)  { ds[1].data = regLine; }
        else if (regLine)              { ds.push(buildRegDataset(regLine)); }
        else if (ds.length > 1)        { ds.splice(1); }
        charts.corr.options.scales.x.title.text = translation(`tabs.categoryCorrelations.description`);
        charts.corr.options.scales.y.title.text = translation(`indicators.${indCode}`);
        charts.corr.update('none');
        return;
    }

    // First render
    const datasets = [{
        label: translation(`tabs.categoryCorrelations.table.countries`),
        data: corrPoints.map(p => ({ x: p.x, y: p.y })),
        backgroundColor: corrPoints.map(p => p.color),
        pointRadius: 8, pointHoverRadius: 11,
    }];
    if (regLine) datasets.push(buildRegDataset(regLine));

    charts.corr = new Chart(document.getElementById('chart-corr'), {
        type: 'scatter',
        data: { datasets },
        options: {
            animation: false,
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    filter: item => item.datasetIndex === 0,
                    callbacks: {
                        label: ctx => {
                            const p = corrPoints[ctx.dataIndex];
                            if (!p) return '';
                            return [' ' + p.label,
                                    ` ${translation(`tabs.categoryCorrelations.voteAvg`)}: ` + (p.x * 100).toFixed(1) + '%',
                                    ' ' + indCode + ': ' + p.y.toLocaleString(undefined, { maximumFractionDigits: 2 })];
                        },
                    },
                },
                datalabels: {
                    display: ctx => ctx.datasetIndex === 0,
                    formatter: (_, ctx) => corrPoints[ctx.dataIndex] ? corrPoints[ctx.dataIndex].label : '',
                    color:     ctx => corrPoints[ctx.dataIndex] ? corrPoints[ctx.dataIndex].color : '#333',
                    font:  { size: 11, weight: 'bold' },
                    align: 'top', offset: 4,
                    textShadowColor: 'rgba(255,255,255,0.8)',
                    textShadowBlur: 4,
                },
            },
            scales: {
                x: {
                    title: { display: true, text: translation(`tabs.categoryCorrelations.description`) },
                    min: 0, max: 1,
                    grid: { color: '#e5e7eb' },
                    ticks: { callback: v => (v * 100).toFixed(0) + '%' },
                },
                y: {
                    title: { display: true, text: translation(`indicators.${indCode}`) },
                    grid: { color: '#e5e7eb' },
                },
            },
        },
    });
}

/**
 * Build a Chart.js dataset descriptor for an OLS regression line.
 * @param {Array<{x: number, y: number}>} regLine - Two-point line from `linRegression`.
 * @returns {Object} Chart.js dataset object.
 */
function buildRegDataset(regLine) {
    return {
        label: 'OLS regression',
        data: regLine,
        type: 'line',
        borderColor: 'rgba(100,100,100,0.55)',
        borderWidth: 2,
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false,
        tension: 0,
        datalabels: { display: false },
    };
}

/**
 * Initialise the correlation tab for `term`: load JSON data (or use cache) and
 * populate the category/indicator selectors.
 * @param {string} term - Term directory name (e.g. `"term_10"`).
 */
async function initCorrTab(term) {
    // On term change, destroy the old chart so next render recreates it with fresh data
    destroyChart('corr');

    if (corrCache[term]) {
        populateCorrSelectors(corrCache[term]);
        return;
    }
    const loadingEl = document.getElementById('corr-loading');
    loadingEl.style.display = 'block';
    try {
        const data = await fetchJSON(`data/${term}/category_correlations.json`);
        corrCache[term] = data;
        populateCorrSelectors(data);
    } catch (err) {
        loadingEl.textContent = `Error loading data: ${err.message}`;
        return;
    } finally {
        loadingEl.style.display = 'none';
    }
}

// ── MEP Comparison ───────────────────────────────────────────────────────────

let compareIndexCache = {};
const compareSelected = { mep1: null, mep2: null };

/**
 * Wire up a live-search input that filters a paired `<select>` element,
 * and track the selected MEP in `compareSelected[key]`.
 * @param {string} inputId  - ID of the search `<input>` element.
 * @param {string} selectId - ID of the `<select>` element to filter.
 * @param {string} key      - Key in `compareSelected` (`"mep1"` or `"mep2"`).
 */
function setupMepPicker(inputId, selectId, key) {
    const input = document.getElementById(inputId);
    const sel   = document.getElementById(selectId);

    input.addEventListener('input', () => {
        const q = input.value.toLowerCase();
        Array.from(sel.options).forEach(opt => {
            opt.hidden = q.length > 0 && !opt.textContent.toLowerCase().includes(q);
        });
    });

    sel.addEventListener('change', () => {
        const opt = sel.options[sel.selectedIndex];
        if (!opt || opt.hidden) return;
        compareSelected[key] = { id: parseInt(opt.value), label: opt.textContent.trim() };
        document.getElementById('btn-compare').disabled =
            !compareSelected.mep1 || !compareSelected.mep2;
    });
}

/**
 * Populate both MEP `<select>` elements from a freshly loaded index array.
 * @param {Array<{id: number, jmeno: string, prijmeni: string, obcanstvi: string, zkratka: string}>} index
 */
function populateMepSelectors(index) {
    ['sel-mep1', 'sel-mep2'].forEach(id => {
        const sel = document.getElementById(id);
        sel.innerHTML = '';
        index.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m.id;
            const party = m.zkratka ? ` · ${m.zkratka}` : '';
            opt.textContent = `${m.prijmeni} ${m.jmeno}  (${m.obcanstvi || '?'}${party})`;
            sel.appendChild(opt);
        });
        sel.selectedIndex = -1;
    });
    compareSelected.mep1 = null;
    compareSelected.mep2 = null;
    document.getElementById('btn-compare').disabled = true;
    document.getElementById('compare-result').style.display = 'none';
    ['search-mep1', 'search-mep2'].forEach(id => { document.getElementById(id).value = ''; });
}

/**
 * Initialise the MEP comparison tab: load the MEP list for `term` via the API
 * (or reuse cache) and populate both picker selectors.
 * @param {string} term - Term directory name (e.g. `"term_10"`).
 */
async function initCompareTab(term) {
    if (compareIndexCache[term]) {
        populateMepSelectors(compareIndexCache[term]);
        return;
    }
    // Extract numeric term id from "term_11" → 11
    const termId = term.replace('term_', '');
    const loadEl  = document.getElementById('compare-loading');
    const resultEl = document.getElementById('compare-result');
    loadEl.style.display  = 'block';
    resultEl.style.display = 'none';
    try {
        const response = await Request.POST({request: "mep_list", term: termId});
        if(response.status == "ok"){
            const index = response.data;
            compareIndexCache[term] = index;
            populateMepSelectors(index);
        }
        else{
            throw new Error(response.data);
        }
    } catch (err) {
        resultEl.innerHTML = `<div class="alert alert-warning mb-0"><span data-text="tabs.mepComparison.couldNotLoad">${translation("tabs.mepComparison.couldNotLoad")}</span>${escHtml(err.message)}</div>`;
        resultEl.style.display = 'block';
    } finally {
        loadEl.style.display = 'none';
    }
}

/**
 * Build the HTML for a single MEP identity card shown in the comparison banner.
 * @param {{jmeno: string, prijmeni: string, zkratka: string, color: string,
 *           nat_party: string, nat_color: string, obcanstvi: string}} mep
 * @returns {string} HTML string.
 */
function renderMepCard(mep) {
    const epColor  = mep.color     || '#9E9E9E';
    const natColor = mep.nat_color || '#9E9E9E';
    const natBadge = mep.nat_party
        ? `<span class="party-badge ms-1" style="background:${natColor}">${escHtml(mep.nat_party)}</span>`
        : '';
    return `<div class="text-center p-3">
        <div class="fw-bold fs-5 mb-1">${escHtml(mep.jmeno)} ${escHtml(mep.prijmeni)}</div>
        <div>
            <span class="party-badge" style="background:${epColor}">${escHtml(mep.zkratka || '—')}</span>
            ${natBadge}
            <span class="text-muted ms-2 small">${escHtml(mep.obcanstvi || '')}</span>
        </div>
    </div>`;
}

/**
 * Render the MEP pairwise comparison result: overall agreement banner plus a
 * per-policy-category breakdown table.
 * @param {{mep1: Object, mep2: Object,
 *           overall: {pct: number, agree: number, total: number},
 *           categories: Object.<string, {pct: number, total: number}>}} data
 */
function renderComparison(data) {
    const ov = data.overall;

    if (ov.total === 0) {
        document.getElementById('compare-result').innerHTML =
            `<div class="alert alert-info mb-0" data-text="tabs.mepComparison.noCommonVotes">${translation("tabs.mepComparison.noCommonVotes")}</div>`;
        document.getElementById('compare-result').style.display = 'block';
        return;
    }

    const pctBg  = v => v != null ? hslFromValue(v / 100) : '#aaa';
    const pctFmt = v => v != null ? v.toFixed(1) + '%' : 'n/a';

    // Overall banner
    const ovBg = pctBg(ov.pct);
    let html = `
        <div class="row g-0 mb-3 align-items-center border rounded overflow-hidden">
            <div class="col-5">${renderMepCard(data.mep1)}</div>
            <div class="col-2 text-center p-2">
                <div style="background:${ovBg};color:white;border-radius:8px;padding:8px 4px">
                    <div style="font-size:1.5rem;font-weight:700">${pctFmt(ov.pct)}</div>
                    <div style="font-size:0.75rem;opacity:0.9" data-text="tabs.mepComparison.overall">${translation("tabs.mepComparison.overall")}</div>
                    <div style="font-size:0.72rem;opacity:0.8">${ov.agree.toLocaleString()} / ${ov.total.toLocaleString()} <span data-text="tabs.mepComparison.votes">${translation("tabs.mepComparison.votes")}</span></div>
                </div>
            </div>
            <div class="col-5">${renderMepCard(data.mep2)}</div>
        </div>`;

    // Category breakdown table
    const cats = Object.entries(data.categories);
    if (cats.length > 0) {
        html += `<table class="table table-sm table-borderless mb-0" style="font-size:0.88rem">
            <thead><tr>
                <th class="text-end pe-3 text-muted" style="width:40%" data-text="tabs.mepComparison.policyArea">${translation(`tabs.mepComparison.policyArea`)}</th>
                <th class="text-center" style="width:20%" data-text="tabs.mepComparison.agreement">${translation(`tabs.mepComparison.agreement`)}</th>
                <th class="text-start ps-3 text-muted" style="width:40%" data-text="tabs.mepComparison.votesInCommon">${translation(`tabs.mepComparison.votesInCommon`)}</th>
            </tr></thead><tbody>`;
        cats.forEach(([kat, d]) => {
            const label = translation(`categories.${kat}`) || kat;
            const bg    = pctBg(d.pct);
            html += `<tr>
                <td class="text-end pe-3" data-text="categories.${kat}">${escHtml(label)}</td>
                <td class="text-center">
                    <span style="display:inline-block;background:${bg};color:white;border-radius:10px;padding:1px 12px;font-weight:600">
                        ${pctFmt(d.pct)}
                    </span>
                </td>
                <td class="text-start ps-3 text-muted">${d.total.toLocaleString()}</td>
            </tr>`;
        });
        html += '</tbody></table>';
    }

    const el = document.getElementById('compare-result');
    el.innerHTML = html;
    el.style.display = 'block';
}

/**
 * Submit the MEP comparison request to the back-end API and render the result.
 * Reads `compareSelected.mep1` and `compareSelected.mep2` for the selected MEP IDs.
 */
async function runComparison() {
    const { mep1, mep2 } = compareSelected;
    if (!mep1 || !mep2) return;
    if (mep1.id === mep2.id) {
        alert('Please select two different MEPs.');
        return;
    }

    document.getElementById('compare-loading').style.display = 'block';
    document.getElementById('compare-result').style.display  = 'none';
    try {
        const termId = currentTerm.replace('term_', '');
        Request.POST({request: "compare", term: termId, mep1: mep1.id, mep2: mep2.id}).then(response => {
            let data = [];
            if(response.status === "ok"){
                data = response.data;
            }
            else{
                throw new Error(response.data);
            }
            renderComparison(data);
        });
    } catch (err) {
        const el = document.getElementById('compare-result');
        el.innerHTML = `<div class="alert alert-danger mb-0">${escHtml(err.message)}</div>`;
        el.style.display = 'block';
    } finally {
        document.getElementById('compare-loading').style.display = 'none';
    }
}

// ── Topic Profiles ───────────────────────────────────────────────────────────

let topicProfileCache = {};   // term → { profile, rules }
let _topicProfile     = null; // currently displayed profile data

/**
 * Populate the EP-group and country filter selectors for the topic profile tab.
 * Preserves the previously selected values when the profile is refreshed.
 * @param {{parties: Array<{ep_group: string, country: string}>}} profile
 */
function setupTopicFilters(profile) {
    const groups    = [...new Set(profile.parties.map(p => p.ep_group).filter(Boolean))].sort();
    const countries = [...new Set(profile.parties.map(p => p.country).filter(Boolean))].sort();

    const groupSel   = document.getElementById('topic-filter-group');
    const countrySel = document.getElementById('topic-filter-country');

    const prevGroup   = groupSel.value;
    const prevCountry = countrySel.value;

    groupSel.innerHTML   = `<option value="all" data-content="tabs.topicProfiles.allGroups">${translation("tabs.topicProfiles.allGroups")}</option>`;
    countrySel.innerHTML = `<option value="all" data-content="tabs.topicProfiles.allCountries">${translation("tabs.topicProfiles.allCountries")}</option>`;

    groups.forEach(g => {
        const o = document.createElement('option');
        o.value = g; o.textContent = g;
        groupSel.appendChild(o);
    });
    countries.forEach(c => {
        const o = document.createElement('option');
        o.value = c; o.textContent = c;
        countrySel.appendChild(o);
    });

    if (prevGroup   && groups.includes(prevGroup))     groupSel.value   = prevGroup;
    if (prevCountry && countries.includes(prevCountry)) countrySel.value = prevCountry;
}

/**
 * Render the party-by-topic heatmap table, applying the active group/country filters.
 * Each cell is coloured green/red by the faction's Yes fraction for that topic category.
 * @param {{parties: Array, topics: string[], topic_labels: Object,
 *           ep_group_colors: Object}} profile
 */
function renderTopicHeatmap(profile) {
    const groupFilter   = document.getElementById('topic-filter-group').value;
    const countryFilter = document.getElementById('topic-filter-country').value;

    let parties = profile.parties;
    if (groupFilter   !== 'all') parties = parties.filter(p => p.ep_group === groupFilter);
    if (countryFilter !== 'all') parties = parties.filter(p => p.country  === countryFilter);

    document.getElementById('topics-heatmap-stat').innerHTML = `${parties.length} ${translatable("tabs.topicProfiles.parties")}`;

    const topics      = profile.topics;
    const topicLabels = profile.topic_labels;

    let html = '<table class="table table-bordered mb-0" style="border-collapse:collapse;font-size:0.78rem;white-space:nowrap">';

    // Header row
    html += '<thead style="position:sticky;top:0;z-index:1;background:#fff"><tr>';
    html += `<th style="min-width:100px" data-text="tabs.topicProfiles.table.party">${translation("tabs.topicProfiles.table.party")}</th>`;
    html += `<th style="min-width:55px" data-text="tabs.topicProfiles.table.country">${translation("tabs.topicProfiles.table.country")}</th>`;
    html += `<th style="min-width:70px" data-text="tabs.topicProfiles.table.epGroup">${translation("tabs.topicProfiles.table.epGroup")}</th>`;
    topics.forEach(t => {
        html += `<th class="text-center" style="min-width:52px;padding:4px 2px" title="${translation(`categories.${t}`)}" data-title="categories.${t}">${t}</th>`;
    });
    html += '</tr></thead><tbody>';

    // Data rows
    const epGroupColors = profile.ep_group_colors || {};
    parties.forEach(p => {
        const epColor = epGroupColors[p.ep_group] || '#9E9E9E';
        html += '<tr>';
        html += `<td style="font-weight:600;padding:3px 6px" title="${escHtml(p.zkratka)}">${escHtml(p.zkratka)}</td>`;
        html += `<td style="padding:3px 6px">${p.country || '—'}</td>`;
        html += `<td style="padding:3px 4px"><span class="party-badge" style="background:${epColor};font-size:0.7rem">${escHtml(p.ep_group || '—')}</span></td>`;
        topics.forEach(t => {
            const td = p.topics[t];
            if (!td) {
                html += '<td style="background:#f5f5f5;color:#bbb;text-align:center;padding:3px 2px">—</td>';
            } else {
                const bg  = hslFromValue(td.yes_frac);
                const pct = Math.round(td.yes_frac * 100);
                const tip = `${translation(`categories.${t}`)}: ${pct}% ${translation("tabs.topicProfiles.table.yes")} (${td.n_votes} ${translation("tabs.topicProfiles.table.votes")})`;
                const tipTemplate = `{{categories.${t}}}: ${pct}% {{tabs.topicProfiles.table.yes}} (${td.n_votes} {{tabs.topicProfiles.table.votes}})`;
                html += `<td style="background:${bg};color:white;text-align:center;padding:3px 2px;cursor:default" title="${tip}" data-title-template="${tipTemplate}">${pct}%</td>`;
            }
        });
        html += '</tr>';
    });

    html += '</tbody></table>';
    document.getElementById('topic-heatmap-container').innerHTML = html;
}

/**
 * Classify a party's Yes-fraction for one topic as `"YES"`, `"NO"`, or `"ABSTAIN"`.
 * Thresholds mirror `party_association_rules()` in analyser.py.
 * @param {number} yes_frac - Fraction of Yes votes in [0, 1].
 * @returns {"YES"|"NO"|"ABSTAIN"}
 */
function partyItemDir(yes_frac) {
    return yes_frac > 0.65 ? 'YES' : (yes_frac < 0.35 ? 'NO' : 'ABSTAIN');
}

/**
 * Return `true` if a party's topic profile matches all `"topic:DIR"` items in a rule.
 * @param {{topics: Object.<string, {yes_frac: number}>}} party
 * @param {string[]} items - Array of `"topic:DIRECTION"` strings.
 * @returns {boolean}
 */
function partyMatchesItems(party, items) {
    return items.every(item => {
        const colonIdx = item.lastIndexOf(':');
        const topic = item.slice(0, colonIdx);
        const dir   = item.slice(colonIdx + 1);
        const td    = party.topics && party.topics[topic];
        if (!td) return false;
        return partyItemDir(td.yes_frac) === dir;
    });
}

/**
 * Format a single `"topic:DIRECTION"` rule item as a colour-coded HTML badge.
 * @param {string} item        - `"topic:YES"`, `"topic:NO"`, or `"topic:ABSTAIN"`.
 * @param {Object} topicLabels - Map of topic code → human-readable label.
 * @returns {string} HTML badge string.
 */
function formatRuleItem(item, topicLabels) {
    const colonIdx = item.lastIndexOf(':');
    const topic    = item.slice(0, colonIdx);
    const dir      = item.slice(colonIdx + 1);
    const label    = (topicLabels && topicLabels[topic]) || topic;
    const bg       = dir === 'YES' ? '#2E7D32' : dir === 'NO' ? '#C62828' : '#E65100';
    const symbol   = dir === 'YES' ? '✔' : dir === 'NO' ? '✘' : '~';
    return `<span style="display:inline-block;background:${bg};color:#fff;border-radius:3px;padding:1px 7px;font-size:0.78rem;white-space:nowrap">${escHtml(label)} <strong>${symbol}</strong></span>`;
}

const RULES_DISPLAY_LIMIT = 30;

/**
 * Render the FP-Growth association rules table (capped at `RULES_DISPLAY_LIMIT`).
 * Each rule row shows antecedents → consequents with support, confidence, lift,
 * and a collapsible list of matching parties.
 * @param {Array<{antecedents: string[], consequents: string[],
 *                support: number, confidence: number, lift: number}>} rules
 * @param {{n_transactions: number, parameters: {min_support: number}}} meta
 * @param {Object} topicLabels - Map of topic code → human-readable label.
 */
function renderAssocRules(rules, meta, topicLabels) {
    destroyTable('table-rules');
    const total    = rules.length;
    const displayed = rules.slice(0, RULES_DISPLAY_LIMIT);
    const statEl   = document.getElementById('rules-stat');
    statEl.textContent = total > RULES_DISPLAY_LIMIT
        ? `top ${RULES_DISPLAY_LIMIT} of ${total} rules · ${meta.n_transactions} parties · support ≥${(meta.parameters.min_support * 100).toFixed(0)}%`
        : `${total} rule${total !== 1 ? 's' : ''} · ${meta.n_transactions} parties · support ≥${(meta.parameters.min_support * 100).toFixed(0)}%`;

    const allItems   = r => [...r.antecedents, ...r.consequents];
    const profileParties = (_topicProfile && _topicProfile.parties) || [];

    const tbody = document.querySelector('#table-rules tbody');
    displayed.forEach(r => {
        const row  = document.createElement('tr');
        const fmt  = i => formatRuleItem(i, topicLabels);
        const ant  = r.antecedents.map(fmt).join(' <span class="text-muted mx-1">∧</span> ');
        const cons = r.consequents.map(fmt).join(' <span class="text-muted mx-1">∧</span> ');

        const matching = profileParties.filter(p => partyMatchesItems(p, allItems(r)));
        const epGroupColors = (_topicProfile && _topicProfile.ep_group_colors) || {};
        const badgesHtml = matching.map(p => {
            const epColor = epGroupColors[p.ep_group] || '#9E9E9E';
            const tip = `${escHtml(p.zkratka)} · ${escHtml(p.country || '?')} · ${escHtml(p.ep_group || '?')}`;
            return `<span title="${tip}" style="display:inline-block;background:${epColor};color:#fff;border-radius:3px;padding:1px 5px;font-size:0.72rem;margin:1px;white-space:nowrap">${escHtml(p.zkratka)}</span>`;
        }).join('');
        const partiesHtml = `<details><summary style="cursor:pointer;font-size:0.8rem">${matching.length} parties</summary><div style="padding-top:4px">${badgesHtml}</div></details>`;

        row.innerHTML = `
            <td>${ant}</td>
            <td>${cons}</td>
            <td data-sort="${r.support}">${(r.support * 100).toFixed(1)}%</td>
            <td data-sort="${r.confidence}">${(r.confidence * 100).toFixed(1)}%</td>
            <td data-sort="${r.lift}"><strong>${r.lift.toFixed(3)}</strong></td>
            <td style="min-width:160px">${partiesHtml}</td>`;
        tbody.appendChild(row);
    });

    dtInstances['table-rules'] = $('#table-rules').DataTable({
        pageLength: 25,
        order: [[4, 'desc']],
        columnDefs: [{ targets: [2, 3, 4], type: 'num' }],
        language: {
            search: translation("datatables.search"),
            lengthMenu: translation("datatables.lengthMenu"), 
            info: translation("datatables.info"),
            emptyTable: translation("datatables.emptyTable"),
            infoEmpty: translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });
}

/**
 * Format a `"topic:ABOVE|BELOW"` deviation rule item as an HTML badge.
 * @param {string} item - `"topic:ABOVE"` or `"topic:BELOW"`.
 * @returns {string} HTML badge string.
 */
function formatDeviationItem(item) {
    const colonIdx = item.lastIndexOf(':');
    const topic    = item.slice(0, colonIdx);
    const dir      = item.slice(colonIdx + 1);
    const label    = translatable(`categories.${topic}`);
    const bg       = dir === 'ABOVE' ? '#2E7D32' : '#C62828';
    const symbol   = dir === 'ABOVE' ? '▲' : '▼';
    return `<span style="display:inline-block;background:${bg};color:#fff;border-radius:3px;padding:1px 7px;font-size:0.78rem;white-space:nowrap">${label} <strong>${symbol}</strong></span>`;
}

/**
 * Render the topic deviation rules DataTable.
 * Rules describe combinations of above/below-average voting behaviour across topics.
 * @param {{rules: Array, n_transactions: number, parameters: Object,
 *           ep_group_means: Object}} data
 */
function renderDeviationRules(data) {
    destroyTable('table-deviation');
    const rules      = (data.rules || []).slice(0, RULES_DISPLAY_LIMIT);
    const total      = (data.rules || []).length;
    const profileParties = (_topicProfile && _topicProfile.parties) || [];

    const statEl = document.getElementById('deviation-stat');
    if (total === 0) {
        statEl.innerHTML = `0 ${translatable("tabs.deviationRules.noRules.rules")} · ${data.n_transactions || 0} ${translatable("tabs.deviationRules.noRules.parties")}`;
        document.querySelector('#table-deviation tbody').innerHTML =
            `<tr><td colspan="6" class="text-center text-muted py-3">${translatable("tabs.deviationRules.noRules.description")}</td></tr>`;
        return;
    }
    statEl.innerHTML = total > RULES_DISPLAY_LIMIT
        ? `top ${RULES_DISPLAY_LIMIT} ${translatable("tabs.deviationRules.table.of")} ${total} ${translatable("tabs.deviationRules.table.rules")} · ${data.n_transactions} ${translatable("tabs.deviationRules.table.partiesLow")} · ${translatable("tabs.deviationRules.table.deviation")} ≥${((data.parameters||{}).deviation_threshold||0.1)*100|0}%`
        : `${total} ${translatable("tabs.deviationRules.table.rules")} · ${data.n_transactions} ${translatable("tabs.deviationRules.table.partiesLow")}`;

    const tbody = document.querySelector('#table-deviation tbody');
    rules.forEach(r => {
        const row = document.createElement('tr');
        const fmt = i => formatDeviationItem(i);
        const ant = r.antecedents.map(fmt).join(' <span class="text-muted mx-1">∧</span> ');
        const con = r.consequents.map(fmt).join(' <span class="text-muted mx-1">∧</span> ');

        const allItems   = [...r.antecedents, ...r.consequents];
        const matching   = profileParties.filter(p => {
            const grp   = p.ep_group;
            const means = ((data.ep_group_means || {})[grp]) || {};
            const thresh = (data.parameters || {}).deviation_threshold || 0.10;
            return allItems.every(item => {
                const ci     = item.lastIndexOf(':');
                const topic  = item.slice(0, ci);
                const dir    = item.slice(ci + 1);
                const td     = p.topics && p.topics[topic];
                const mean   = means[topic];
                if (!td || mean == null) return false;
                const dev    = td.yes_frac - mean;
                return dir === 'ABOVE' ? dev > thresh : dev < -thresh;
            });
        });
        const epGroupColors = (_topicProfile && _topicProfile.ep_group_colors) || {};
        const badgesHtml = matching.map(p => {
            const epColor = epGroupColors[p.ep_group] || '#9E9E9E';
            return `<span title="${escHtml(p.zkratka)} · ${escHtml(p.country||'?')} · ${escHtml(p.ep_group||'?')}" style="display:inline-block;background:${epColor};color:#fff;border-radius:3px;padding:1px 5px;font-size:0.72rem;margin:1px;white-space:nowrap">${escHtml(p.zkratka)}</span>`;
        }).join('');
        const partiesHtml = `<details><summary style="cursor:pointer;font-size:0.8rem">${matching.length} ${translatable("tabs.deviationRules.table.partiesLow")}</summary><div style="padding-top:4px">${badgesHtml}</div></details>`;

        row.innerHTML = `
            <td>${ant}</td>
            <td>${con}</td>
            <td data-sort="${r.support}">${(r.support*100).toFixed(1)}%</td>
            <td data-sort="${r.confidence}">${(r.confidence*100).toFixed(1)}%</td>
            <td data-sort="${r.lift}"><strong>${r.lift.toFixed(3)}</strong></td>
            <td style="min-width:160px">${partiesHtml}</td>`;
        tbody.appendChild(row);
    });

    dtInstances['table-deviation'] = $('#table-deviation').DataTable({
        pageLength: 25,
        order: [[4, 'desc']],
        columnDefs: [{ targets: [2, 3, 4], type: 'num' }],
        language: {
            search: translation("datatables.search"),
            lengthMenu: translation("datatables.lengthMenu"), 
            info: translation("datatables.info"),
            emptyTable: translation("datatables.emptyTable"),
            infoEmpty: translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });
}

/**
 * Render a single faction+direction item as a coloured inline badge.
 *
 * The *item* string has the form `"GROUP:DIRECTION"` where DIRECTION is one of
 * `FOR`, `AGAINST`, or `ABSTAIN`.  The badge background uses the EP group colour
 * from *groupColors* when available, falling back to the direction's default colour.
 *
 * @param {string} item        - Encoded `"GROUP:DIRECTION"` antecedent/consequent token.
 * @param {Object} groupColors - Map of EP group abbreviation → hex colour string.
 * @returns {string} HTML string for the badge `<span>`.
 */
function formatOppositionItem(item, groupColors) {
    const colonIdx = item.lastIndexOf(':');
    const group    = item.slice(0, colonIdx);
    const dir      = item.slice(colonIdx + 1);
    const dirColor = dir === 'FOR' ? '#2E7D32' : dir === 'AGAINST' ? '#C62828' : '#E65100';
    const symbol   = dir === 'FOR' ? '✔' : dir === 'AGAINST' ? '✘' : '~';
    const epColor  = (groupColors && groupColors[group]) || dirColor;
    return `<span style="display:inline-block;background:${epColor};color:#fff;border-radius:3px;padding:1px 7px;font-size:0.78rem;white-space:nowrap;">${escHtml(group)} <strong>${symbol}</strong></span>`;
}

/**
 * Render faction opposition association rules into the `#table-opposition` DataTable.
 *
 * Displays a summary stat line (rule count, vote count, faction count) and populates
 * the table with up to {@link RULES_DISPLAY_LIMIT} rules ordered by lift descending.
 * Each antecedent/consequent cell is built from {@link formatOppositionItem} badges.
 *
 * @param {{ rules: Array, n_transactions: number, n_groups: number, group_colors: Object }} data
 *   - API response from the faction opposition rules endpoint.
 */
function renderOppositionRules(data) {
    destroyTable('table-opposition');
    const rules = (data.rules || []).slice(0, RULES_DISPLAY_LIMIT);
    const total = (data.rules || []).length;

    const statEl = document.getElementById('opposition-stat');
    if (total === 0) {
        statEl.textContent = `0 ${translation("tabs.oppositionRules.noRules.rules")} · ${data.n_transactions || 0} ${translation("tabs.oppositionRules.noRules.votes")}`;
        document.querySelector('#table-opposition tbody').innerHTML =
            `<tr><td colspan="5" class="text-center text-muted py-3">${translation("tabs.oppositionRules.noRules.description")}</td></tr>`;
        return;
    }
    statEl.textContent = total > RULES_DISPLAY_LIMIT
        ? `top ${RULES_DISPLAY_LIMIT} ${translation("tabs.oppositionRules.table.of")} ${total} ${translation("tabs.oppositionRules.table.rules")} · ${data.n_transactions} ${translation("tabs.oppositionRules.table.votes")} · ${data.n_groups} ${translation("tabs.oppositionRules.table.factions")}`
        : `${total} ${translation("tabs.oppositionRules.table.rules")} · ${data.n_transactions} ${translation("tabs.oppositionRules.table.votes")} · ${data.n_groups} ${translation("tabs.oppositionRules.table.factions")}`;

    const groupColors = data.group_colors || {};
    const tbody = document.querySelector('#table-opposition tbody');
    rules.forEach(r => {
        const row = document.createElement('tr');
        const fmtOpp = item => formatOppositionItem(item, groupColors);
        const ant = r.antecedents.map(fmtOpp).join(' <span class="text-muted mx-1">∧</span> ');
        const con = r.consequents.map(fmtOpp).join(' <span class="text-muted mx-1">∧</span> ');
        row.innerHTML = `
            <td>${ant}</td>
            <td>${con}</td>
            <td data-sort="${r.support}">${(r.support*100).toFixed(1)}%</td>
            <td data-sort="${r.confidence}">${(r.confidence*100).toFixed(1)}%</td>
            <td data-sort="${r.lift}"><strong>${r.lift.toFixed(3)}</strong></td>`;
        tbody.appendChild(row);
    });

    dtInstances['table-opposition'] = $('#table-opposition').DataTable({
        pageLength: 25,
        order: [[4, 'desc']],
        columnDefs: [{ targets: [2, 3, 4], type: 'num' }],
        language: {
            search: translation("datatables.search"),
            lengthMenu: translation("datatables.lengthMenu"), 
            info: translation("datatables.info"),
            emptyTable: translation("datatables.emptyTable"),
            infoEmpty: translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });
}

/**
 * Orchestrate all rendering for the Topic Profile tab family.
 *
 * Saves *profile* to the module-level `_topicProfile` reference (so filter
 * change handlers can re-render without a network round-trip), then delegates
 * to the three sub-renderers and reveals the heatmap card.
 *
 * @param {{ profile: Object, deviation: Object, opposition: Object }} payload
 *   - Destructured result from {@link initTopicTab}.
 */
function renderTopicTab({ profile, deviation, opposition }) {
    _topicProfile = profile;
    setupTopicFilters(profile);
    renderTopicHeatmap(profile);
    renderDeviationRules(deviation);
    renderOppositionRules(opposition);
    document.getElementById('topics-heatmap-card').style.display = '';
}

/**
 * Initialise the Topic Profile tab for the given parliamentary term.
 *
 * Fetches `party_topic_profile.json`, `topic_deviation_rules.json`, and
 * `faction_opposition_rules.json` in parallel on first load, caches the
 * combined result in `topicProfileCache`, and calls {@link renderTopicTab}.
 * Subsequent calls for the same term are served instantly from the cache.
 *
 * @param {string} term - Term key (e.g. `"term_10"`).
 * @returns {Promise<void>}
 */
async function initTopicTab(term) {
    if (topicProfileCache[term]) {
        renderTopicTab(topicProfileCache[term]);
        return;
    }

    const loadEl = document.getElementById('topics-loading');
    loadEl.style.display = 'block';
    document.getElementById('topics-heatmap-card').style.display = 'none';

    try {
        const [profile, deviation, opposition] = await Promise.all([
            fetchJSON(`data/${term}/party_topic_profile.json`),
            fetchJSON(`data/${term}/topic_deviation_rules.json`),
            fetchJSON(`data/${term}/faction_opposition_rules.json`),
        ]);
        topicProfileCache[term] = { profile, deviation, opposition };
        renderTopicTab({ profile, deviation, opposition });
    } catch (err) {
        loadEl.innerHTML = `<span class="text-danger">Error loading topic data: ${escHtml(err.message)}</span>`;
        return;
    } finally {
        loadEl.style.display = 'none';
    }
}

// ── Member Statistics ─────────────────────────────────────────────────────────

let mstatsCache = {};

/**
 * Render the Member Statistics tab: a "FOR %" histogram and a per-MEP DataTable.
 *
 * Bins each MEP's `za / celkem` ratio into one of 10 decile buckets (0–10 % …
 * 90–100 %) and draws a Chart.js bar chart.  A DataTable beneath it lists every
 * MEP with their raw vote counts, sorted by total descending.
 *
 * @param {Array<Object>} data - Array of MEP vote-count records from the API.
 */
function renderMemberStats(data) {
    destroyChart('mstatsDist');
    destroyTable('table-mstats');

    const bins = Array(10).fill(0);
    data.forEach(d => {
        const total = parseInt(d.celkem);
        if (total === 0) return;
        bins[Math.min(9, Math.floor(parseInt(d.za) / total * 10))]++;
    });
    const binLabels = ['0–10','10–20','20–30','30–40','40–50','50–60','60–70','70–80','80–90','90–100'];
    document.getElementById('mstats-stat').textContent = `${data.length} ${translation("tabs.memberStats.meps")}`;

    charts.mstatsDist = new Chart(document.getElementById('chart-mstats'), {
        type: 'bar',
        data: {
            labels: binLabels,
            datasets: [{ label: translation("tabs.memberStats.mepsAxis"), data: bins, backgroundColor: '#2E7D32', borderRadius: 3 }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { title: { display: true, text: `${translation("tabs.memberStats.table.for")} (%)` }, grid: { display: false } },
                y: { title: { display: true, text: translation("tabs.memberStats.mepsAxis") }, grid: { color: '#e5e7eb' } }
            }
        }
    });

    const tbody = document.querySelector('#table-mstats tbody');
    data.forEach(d => {
        const color = d.color || '#9E9E9E';
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escHtml(d.jmeno)} ${escHtml(d.prijmeni)}</td>
            <td>${escHtml(d.obcanstvi || '')}</td>
            <td><span class="party-badge" style="background:${color}">${escHtml(d.zkratka || '—')}</span></td>
            <td>${d.za}</td>
            <td>${d.proti}</td>
            <td>${d.zdrzeni}</td>
            <td data-sort="${d.celkem}">${d.celkem}</td>`;
        tbody.appendChild(row);
    });

    dtInstances['table-mstats'] = $('#table-mstats').DataTable({
        pageLength: 25,
        order: [[6, 'desc']],
        columnDefs: [{ targets: [3, 4, 5, 6], type: 'num' }],
        language: {
            search: translation("datatables.search"),
            lengthMenu: translation("datatables.lengthMenu"),
            info: translation("datatables.info"),
            emptyTable: translation("datatables.emptyTable"),
            infoEmpty: translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });

    document.getElementById('mstats-content').style.display = '';
}

/**
 * Fetch and render the Member Statistics tab for the given parliamentary term.
 *
 * POSTs a `member_stats` request to the API, caches the result in `mstatsCache`,
 * and delegates rendering to {@link renderMemberStats}.  Subsequent calls for the
 * same term are served from the cache without a network request.
 *
 * @param {string} term - Term key (e.g. `"term_10"`).
 * @returns {Promise<void>}
 */
async function initMemberStatsTab(term) {
    if (mstatsCache[term]) {
        renderMemberStats(mstatsCache[term]);
        return;
    }
    const termId = term.replace('term_', '');
    const loadEl = document.getElementById('mstats-loading');
    loadEl.style.display = 'block';
    document.getElementById('mstats-content').style.display = 'none';
    try {
        const response = await Request.POST({ request: 'member_stats', term: termId });
        if (response.status === 'ok') {
            mstatsCache[term] = response.data;
            renderMemberStats(response.data);
        } else {
            throw new Error(response.message || 'Error loading data');
        }
    } catch (err) {
        loadEl.innerHTML = `<span class="text-danger">${escHtml(err.message)}</span>`;
        return;
    } finally {
        loadEl.style.display = 'none';
    }
}

// ── Subject Statistics ────────────────────────────────────────────────────────

let sstatsCache = {};

/**
 * Render the Subject Statistics tab: a stacked horizontal bar chart for EP groups
 * and a per-national-party DataTable.
 *
 * Separates *data* into `POLITICAL_GROUP` entries (rendered as a stacked 100 %
 * bar chart showing FOR / ABSTAIN / AGAINST percentages) and national parties
 * (listed in the DataTable with raw counts and FOR %).
 *
 * @param {Array<Object>} data - Array of political subject vote-count records from the API.
 */
function renderSubjectStats(data) {
    destroyChart('sstatsFactions');
    destroyTable('table-sstats');

    const factions = data.filter(d => d.typ === 'POLITICAL_GROUP');
    const parties  = data.filter(d => d.typ !== 'POLITICAL_GROUP');

    document.getElementById('sstats-factions-stat').textContent = `${factions.length} ${translation("tabs.subjectStats.factions")}`;
    document.getElementById('sstats-parties-stat').textContent  = `${parties.length} ${translation("tabs.subjectStats.parties")}`;

    const labels      = factions.map(d => d.zkratka);
    const totals      = factions.map(d => parseInt(d.celkem));
    const forPcts     = factions.map((d, i) => totals[i] > 0 ? parseFloat((parseInt(d.za)      / totals[i] * 100).toFixed(1)) : 0);
    const abstainPcts = factions.map((d, i) => totals[i] > 0 ? parseFloat((parseInt(d.zdrzeni) / totals[i] * 100).toFixed(1)) : 0);
    const againstPcts = factions.map((d, i) => totals[i] > 0 ? parseFloat((parseInt(d.proti)   / totals[i] * 100).toFixed(1)) : 0);
    const rawKeys = ['za', 'zdrzeni', 'proti'];

    charts.sstatsFactions = new Chart(document.getElementById('chart-sstats-factions'), {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: translation("tabs.subjectStats.table.for"),     data: forPcts,     backgroundColor: '#2E7D32', borderRadius: 2 },
                { label: translation("tabs.subjectStats.table.abstain"), data: abstainPcts, backgroundColor: '#E65100', borderRadius: 2 },
                { label: translation("tabs.subjectStats.table.against"), data: againstPcts, backgroundColor: '#C62828', borderRadius: 2 },
            ]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: true, position: 'top' },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const d = factions[ctx.dataIndex];
                            const raw = parseInt(d[rawKeys[ctx.datasetIndex]]);
                            return ` ${ctx.dataset.label}: ${ctx.raw}% (${raw.toLocaleString()})`;
                        }
                    }
                }
            },
            scales: {
                x: { stacked: true, min: 0, max: 100, title: { display: true, text: '%' }, grid: { color: '#e5e7eb' } },
                y: { stacked: true, grid: { display: false } }
            }
        }
    });

    const tbody = document.querySelector('#table-sstats tbody');
    parties.forEach(d => {
        const color = d.color || '#9E9E9E';
        const total = parseInt(d.celkem);
        const forPct = total > 0 ? (parseInt(d.za) / total * 100).toFixed(1) : '0.0';
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><span class="party-badge" style="background:${color}">${escHtml(d.zkratka || '—')}</span></td>
            <td>${d.za}</td>
            <td>${d.proti}</td>
            <td>${d.zdrzeni}</td>
            <td data-sort="${d.celkem}">${d.celkem}</td>
            <td data-sort="${forPct}">${forPct}%</td>`;
        tbody.appendChild(row);
    });

    dtInstances['table-sstats'] = $('#table-sstats').DataTable({
        pageLength: 25,
        order: [[5, 'desc']],
        columnDefs: [{ targets: [1, 2, 3, 4, 5], type: 'num' }],
        language: {
            search: translation("datatables.search"),
            lengthMenu: translation("datatables.lengthMenu"),
            info: translation("datatables.info"),
            emptyTable: translation("datatables.emptyTable"),
            infoEmpty: translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });

    document.getElementById('sstats-content').style.display = '';
}

/**
 * Fetch and render the Subject Statistics tab for the given parliamentary term.
 *
 * POSTs a `subject_stats` request to the API, caches the result in `sstatsCache`,
 * and delegates rendering to {@link renderSubjectStats}.  Subsequent calls for the
 * same term return immediately from cache.
 *
 * @param {string} term - Term key (e.g. `"term_10"`).
 * @returns {Promise<void>}
 */
async function initSubjectStatsTab(term) {
    if (sstatsCache[term]) {
        renderSubjectStats(sstatsCache[term]);
        return;
    }
    const termId = term.replace('term_', '');
    const loadEl = document.getElementById('sstats-loading');
    loadEl.style.display = 'block';
    document.getElementById('sstats-content').style.display = 'none';
    try {
        const response = await Request.POST({ request: 'subject_stats', term: termId });
        if (response.status === 'ok') {
            sstatsCache[term] = response.data;
            renderSubjectStats(response.data);
        } else {
            throw new Error(response.message || 'Error loading data');
        }
    } catch (err) {
        loadEl.innerHTML = `<span class="text-danger">${escHtml(err.message)}</span>`;
        return;
    } finally {
        loadEl.style.display = 'none';
    }
}

// ── Anomaly Detection ─────────────────────────────────────────────────────────

let anomalyIndexCache  = {};
let anomalyResultCache = {};
let anomalySelected    = null;

const ANOMALY_VOTE_LABEL = { 1: 'for', 2: 'against', 3: 'abstain' };
const ANOMALY_VOTE_COLOR = { 1: '#2E7D32', 2: '#C62828', 3: '#E65100' };

/**
 * Populate the MEP `<select>` in the Anomaly Detection tab.
 *
 * Replaces all existing `<option>` elements with one per MEP from *index*,
 * resets the search box, clears the current selection, and disables the
 * "Run" button until a new selection is made.
 *
 * @param {Array<{id: number, jmeno: string, prijmeni: string, obcanstvi: string, zkratka: string}>} index
 *   - List of MEP summary records, typically the full term roster.
 */
function populateAnomalySelector(index) {
    const sel = document.getElementById('anomaly-sel');
    sel.innerHTML = '';
    index.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        const party = m.zkratka ? ` · ${m.zkratka}` : '';
        opt.textContent = `${m.prijmeni} ${m.jmeno}  (${m.obcanstvi || '?'}${party})`;
        sel.appendChild(opt);
    });
    sel.selectedIndex = -1;
    anomalySelected = null;
    document.getElementById('btn-anomaly').disabled = true;
    document.getElementById('anomaly-result').style.display = 'none';
    document.getElementById('anomaly-search').value = '';
}

/**
 * Attach the live-search filter and selection handler for the anomaly MEP picker.
 *
 * Wires an `input` listener on `#anomaly-search` that hides non-matching `<option>`
 * elements, and a `change` listener on `#anomaly-sel` that stores the selected MEP
 * in `anomalySelected` and enables the "Run" button.  Called once on page load.
 */
function setupAnomalyPicker() {
    const input = document.getElementById('anomaly-search');
    const sel   = document.getElementById('anomaly-sel');
    input.addEventListener('input', () => {
        const q = input.value.toLowerCase();
        Array.from(sel.options).forEach(opt => {
            opt.hidden = q.length > 0 && !opt.textContent.toLowerCase().includes(q);
        });
    });
    sel.addEventListener('change', () => {
        const opt = sel.options[sel.selectedIndex];
        if (!opt || opt.hidden) return;
        anomalySelected = { id: parseInt(opt.value), label: opt.textContent.trim() };
        document.getElementById('btn-anomaly').disabled = false;
    });
}

/**
 * Initialise the Anomaly Detection tab for the given parliamentary term.
 *
 * Fetches the full MEP roster via a `mep_list` API request, caches it in
 * `anomalyIndexCache`, and calls {@link populateAnomalySelector}.  If the
 * term was already loaded the roster is reused without a network request.
 *
 * @param {string} term - Term key (e.g. `"term_10"`).
 * @returns {Promise<void>}
 */
async function initAnomalyTab(term) {
    if (anomalyIndexCache[term]) { populateAnomalySelector(anomalyIndexCache[term]); return; }
    const termId   = term.replace('term_', '');
    const loadEl   = document.getElementById('anomaly-loading');
    const resultEl = document.getElementById('anomaly-result');
    loadEl.style.display   = 'block';
    resultEl.style.display = 'none';
    try {
        const response = await Request.POST({ request: 'mep_list', term: termId });
        if (response.status === 'ok') {
            anomalyIndexCache[term] = response.data;
            populateAnomalySelector(response.data);
        } else {
            throw new Error(response.message || 'Error');
        }
    } catch (err) {
        resultEl.innerHTML = `<div class="alert alert-warning mb-0"><span data-text="tabs.anomalyDetection.couldNotLoad">${translation("tabs.anomalyDetection.couldNotLoad")}</span>${escHtml(err.message)}</div>`;
        resultEl.style.display = 'block';
    } finally {
        loadEl.style.display = 'none';
    }
}

/**
 * Render anomaly detection results for a selected MEP.
 *
 * Injects a summary panel (total votes, anomalous count, anomaly rate) into
 * `#anomaly-result`, draws a horizontal bar chart of anomalous vote counts by
 * policy category, and populates `#table-anomaly` with one row per anomalous
 * vote (date, category, MEP vote, party majority vote, vote subject).
 *
 * @param {{ total: number, anomalous_count: number, votes: Array<Object> }} data
 *   - Anomaly payload loaded from a pre-computed JSON file.
 */
function renderAnomalyResult(data) {
    destroyChart('anomaly');
    destroyTable('table-anomaly');
    const resultEl = document.getElementById('anomaly-result');

    if (data.anomalous_count === 0) {
        resultEl.innerHTML = `<div class="alert alert-info mb-0"><span data-text="tabs.anomalyDetection.noAnomaly">${translation("tabs.anomalyDetection.noAnomaly")}</span></div>`;
        resultEl.style.display = 'block';
        return;
    }

    const rate = data.total > 0 ? (data.anomalous_count / data.total * 100).toFixed(1) : '0.0';

    // Anomalous vote count per policy category
    const byCat = {};
    data.votes.forEach(v => { const c = v.kategorie || 'OTHER'; byCat[c] = (byCat[c] || 0) + 1; });
    const catKeys   = Object.keys(byCat).sort((a, b) => byCat[b] - byCat[a]);
    const catValues = catKeys.map(c => byCat[c]);

    resultEl.innerHTML = `
        <div class="row g-3 mb-4">
            <div class="col-auto">
                <div class="p-3 rounded border text-center" style="min-width:110px">
                    <div class="fw-bold fs-4">${data.total.toLocaleString()}</div>
                    <div class="text-muted small" data-text="tabs.anomalyDetection.totalVotes">${translation("tabs.anomalyDetection.totalVotes")}</div>
                </div>
            </div>
            <div class="col-auto">
                <div class="p-3 rounded border text-center" style="min-width:110px;background:#fff3e0">
                    <div class="fw-bold fs-4" style="color:#E65100">${data.anomalous_count.toLocaleString()}</div>
                    <div class="text-muted small" data-text="tabs.anomalyDetection.anomalousVotes">${translation("tabs.anomalyDetection.anomalousVotes")}</div>
                </div>
            </div>
            <div class="col-auto">
                <div class="p-3 rounded border text-center" style="min-width:110px">
                    <div class="fw-bold fs-4">${rate}%</div>
                    <div class="text-muted small" data-text="tabs.anomalyDetection.anomalyRate">${translation("tabs.anomalyDetection.anomalyRate")}</div>
                </div>
            </div>
        </div>
        <div class="row g-4">
            <div class="col-lg-4">
                <div class="card h-100">
                    <div class="card-header py-3">
                        <strong data-text="tabs.anomalyDetection.chartTitle">${translation("tabs.anomalyDetection.chartTitle")}</strong>
                    </div>
                    <div class="card-body p-4">
                        <div class="chart-wrap" style="height:280px"><canvas id="chart-anomaly"></canvas></div>
                    </div>
                </div>
            </div>
            <div class="col-lg-8">
                <div class="card">
                    <div class="card-header py-3">
                        <strong data-text="tabs.anomalyDetection.tableTitle">${translation("tabs.anomalyDetection.tableTitle")}</strong>
                    </div>
                    <div class="card-body p-3">
                        <table id="table-anomaly" class="table table-sm table-striped table-hover" style="width:100%">
                            <thead><tr>
                                <th data-text="tabs.anomalyDetection.table.date">${translation("tabs.anomalyDetection.table.date")}</th>
                                <th data-text="tabs.anomalyDetection.table.category">${translation("tabs.anomalyDetection.table.category")}</th>
                                <th data-text="tabs.anomalyDetection.table.mepVote">${translation("tabs.anomalyDetection.table.mepVote")}</th>
                                <th data-text="tabs.anomalyDetection.table.partyVote">${translation("tabs.anomalyDetection.table.partyVote")}</th>
                                <th data-text="tabs.anomalyDetection.table.subject">${translation("tabs.anomalyDetection.table.subject")}</th>
                            </tr></thead>
                            <tbody></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>`;
    resultEl.style.display = 'block';

    charts.anomaly = new Chart(document.getElementById('chart-anomaly'), {
        type: 'bar',
        data: {
            labels: catKeys.map(c => translation(`categories.${c}`) || c),
            datasets: [{ data: catValues, backgroundColor: '#E65100', borderRadius: 4 }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, datalabels: { display: false } },
            scales: {
                x: { ticks: { stepSize: 1 }, grid: { color: '#e5e7eb' } },
                y: { grid: { display: false } }
            }
        }
    });

    const tbody = document.querySelector('#table-anomaly tbody');
    data.votes.forEach(v => {
        const cat      = v.kategorie ? (translation(`categories.${v.kategorie}`) || v.kategorie) : '—';
        const mepLabel = translation(`tabs.anomalyDetection.${ANOMALY_VOTE_LABEL[v.mep_vote]}`)   || String(v.mep_vote);
        const ptyLabel = translation(`tabs.anomalyDetection.${ANOMALY_VOTE_LABEL[v.party_vote]}`) || String(v.party_vote);
        const date     = v.cas ? v.cas.substring(0, 10) : '';
        const row      = document.createElement('tr');
        row.innerHTML = `
            <td>${escHtml(date)}</td>
            <td>${escHtml(cat)}</td>
            <td><span class="party-badge" style="background:${ANOMALY_VOTE_COLOR[v.mep_vote]   || '#555'}">${escHtml(mepLabel)}</span></td>
            <td><span class="party-badge" style="background:${ANOMALY_VOTE_COLOR[v.party_vote] || '#555'}">${escHtml(ptyLabel)}</span></td>
            <td>${escHtml(v.predmet || '')}</td>`;
        tbody.appendChild(row);
    });
    dtInstances['table-anomaly'] = $('#table-anomaly').DataTable({
        pageLength: 25,
        order: [[0, 'desc']],
        columnDefs: [{ targets: [2, 3], orderable: false, searchable: false }],
        language: {
            search:       translation("datatables.search"),
            lengthMenu:   translation("datatables.lengthMenu"),
            info:         translation("datatables.info"),
            emptyTable:   translation("datatables.emptyTable"),
            infoEmpty:    translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });
}

/**
 * Fetch and display anomaly detection results for the currently selected MEP.
 *
 * Constructs the cache key from `currentTerm` and `anomalySelected.id`.
 * On a cache miss, attempts to load a pre-computed JSON file from
 * `data/<term>/anomalous_votes/<id>.json`; a 404 is treated as "no anomalies"
 * and shown as a success message rather than an error.  Results are cached
 * in `anomalyResultCache` for instant re-display without a repeat fetch.
 *
 * @returns {Promise<void>}
 */
async function runAnomalyDetection() {
    if (!anomalySelected) return;
    const cacheKey = `${currentTerm}__${anomalySelected.id}`;
    document.getElementById('anomaly-loading').style.display = 'block';
    document.getElementById('anomaly-result').style.display  = 'none';
    try {
        let data;
        if (anomalyResultCache[cacheKey]) {
            data = anomalyResultCache[cacheKey];
        } else {
            const url = `data/${currentTerm}/anomalous_votes/${anomalySelected.id}.json`;
            try {
                data = await fetchJSON(url);
            } catch (fetchErr) {
                if (fetchErr.message && fetchErr.message.includes('404')) {
                    const el = document.getElementById('anomaly-result');
                    el.innerHTML = `<div class="alert alert-success mb-0">${escHtml(translation('tabs.anomalyDetection.noAnomaly'))}</div>`;
                    el.style.display = 'block';
                    return;
                }
                throw fetchErr;
            }
            anomalyResultCache[cacheKey] = data;
        }
        renderAnomalyResult(data);
    } catch (err) {
        const el = document.getElementById('anomaly-result');
        el.innerHTML = `<div class="alert alert-danger mb-0">${escHtml(err.message)}</div>`;
        el.style.display = 'block';
    } finally {
        document.getElementById('anomaly-loading').style.display = 'none';
    }
}

// ── MEP Pair Rankings ─────────────────────────────────────────────────────────
/**
 * Initialise the MEP Pair Rankings panel for the given parliamentary term.
 *
 * Loads `data/<term>/mep_comparison/index.json` to discover available category
 * views, populates the `#pair-view-select` dropdown, and loads the default
 * `overall` view.  Subsequent calls for the same term skip the index fetch.
 *
 * @param {string} term - Term key (e.g. `"term_10"`).
 * @returns {Promise<void>}
 */
async function initPairRankings(term) {
    const selEl   = document.getElementById('pair-view-select');
    const loading = document.getElementById('pair-rankings-loading');
    const nodata  = document.getElementById('pair-rankings-nodata');
    const content = document.getElementById('pair-rankings-content');

    selEl.innerHTML = '';
    destroyTable('table-pair-rankings');
    content.style.display = 'none';
    nodata.style.display  = 'none';
    loading.style.display = 'block';

    try {
        if (!pairIndexCache[term]) {
            const idx = await fetchJSON(`data/${term}/mep_comparison/index.json`);
            pairIndexCache[term] = idx;
        }
        const index = pairIndexCache[term];

        const overallOpt = document.createElement('option');
        overallOpt.value = 'overall';
        overallOpt.textContent = translation('tabs.mepComparison.rankingsOverall');
        selEl.appendChild(overallOpt);

        for (const cat of (index.categories || [])) {
            const opt = document.createElement('option');
            const safe = cat.replace(/\//g, '_').replace(/ /g, '_');
            opt.value = `cat_${safe}`;
            opt.textContent = cat;
            selEl.appendChild(opt);
        }

        await loadPairView(term, 'overall');
    } catch (_) {
        loading.style.display = 'none';
        nodata.style.display  = 'block';
    }
}

/**
 * Load and render a specific pair-rankings view (overall or per-category).
 *
 * Fetches `data/<term>/mep_comparison/<viewKey>.json` on first access and
 * stores the result in `pairDataCache`.  The MEP index (loaded during
 * {@link initPairRankings}) is used by {@link renderPairRankingsTable} to
 * resolve MEP IDs to names and party badges.
 *
 * @param {string} term    - Term key (e.g. `"term_10"`).
 * @param {string} viewKey - `"overall"` or a `"cat_<slug>"` category key.
 * @returns {Promise<void>}
 */
async function loadPairView(term, viewKey) {
    const loading = document.getElementById('pair-rankings-loading');
    const content = document.getElementById('pair-rankings-content');
    const nodata  = document.getElementById('pair-rankings-nodata');

    loading.style.display = 'block';
    content.style.display = 'none';

    const cacheKey = `${term}/${viewKey}`;
    try {
        if (!pairDataCache[cacheKey]) {
            const file = viewKey === 'overall' ? 'overall' : viewKey;
            pairDataCache[cacheKey] = await fetchJSON(
                `data/${term}/mep_comparison/${file}.json`
            );
        }
        const pairs = pairDataCache[cacheKey];
        const meps  = (pairIndexCache[term] || {}).meps || {};
        renderPairRankingsTable(pairs, meps);
        content.style.display = 'block';
    } catch (_) {
        nodata.style.display = 'block';
    } finally {
        loading.style.display = 'none';
    }
}

/**
 * Render the MEP pair-similarity ranking DataTable.
 *
 * Each row displays two MEPs (with nationality, EP group, and national party badges),
 * their agreement percentage, and the total number of shared votes.  The inner
 * `mepCell` helper formats a single MEP cell with coloured party badges.
 *
 * @param {Array<{mep1: number, mep2: number, pct: number, total: number}>} pairs
 *   - Ranked list of MEP pairs sorted by agreement percentage descending.
 * @param {Object} mepIndex - Map of MEP ID string → MEP metadata object.
 */
function renderPairRankingsTable(pairs, mepIndex) {
    destroyTable('table-pair-rankings');
    const tbody = document.querySelector('#table-pair-rankings tbody');
    tbody.innerHTML = '';

    function mepCell(info) {
        if (!info) return '—';
        const name  = `${info.prijmeni} ${info.jmeno}`;
        const cc    = info.obcanstvi ? ` (${info.obcanstvi})` : '';
        const ep    = info.ep_group
            ? `<span class="badge ms-1" style="background:${info.ep_color};color:#fff">${info.ep_group}</span>`
            : '';
        const nat   = info.nat_party
            ? `<span class="badge ms-1" style="background:${info.nat_color};color:#fff">${info.nat_party}</span>`
            : '';
        return `${name}${cc}${ep}${nat}`;
    }

    pairs.forEach((p, i) => {
        const infoA = mepIndex[String(p.mep1)];
        const infoB = mepIndex[String(p.mep2)];
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td data-sort="${i + 1}">${i + 1}</td>
            <td>${mepCell(infoA)}</td>
            <td>${mepCell(infoB)}</td>
            <td data-sort="${p.pct}">${p.pct}%</td>
            <td data-sort="${p.total}">${p.total}</td>
        `;
        tbody.appendChild(tr);
    });

    dtInstances['table-pair-rankings'] = $('#table-pair-rankings').DataTable({
        paging:    true,
        pageLength: 25,
        searching: true,
        order:     [[3, 'desc'], [4, 'desc']],
        columnDefs: [{ targets: [0, 3, 4], type: 'num' }],
        language: {
            search: translation("datatables.search"),
            lengthMenu: translation("datatables.lengthMenu"), 
            info: translation("datatables.info"),
            emptyTable: translation("datatables.emptyTable"),
            infoEmpty: translation("datatables.infoEmpty"),
            infoFiltered: translation("datatables.infoFiltered"),
            paginate: {
                first:    translation("datatables.paginate.first"),
                last:     translation("datatables.paginate.last"),
                next:     translation("datatables.paginate.next"),
                previous: translation("datatables.paginate.previous")
            }
        }
    });

    const stat = document.getElementById('pair-rankings-stat');
    if (stat) {
        stat.textContent = `${pairs.length} ${translation('tabs.mepComparison.rankingsCount')}`;
    }
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
/**
 * Application entry point — runs immediately on script load.
 *
 * Responsibilities:
 * - Fetches the list of available parliamentary terms and populates `#term-select`.
 * - Builds the language selector from the active locale's `locales` map.
 * - Registers all one-time event listeners: term change, language change,
 *   lazy-load triggers for each tab (correlation, compare, topic, member stats,
 *   subject stats, anomaly), pair-rankings accordion, topic filter dropdowns,
 *   and the global Chart.js resize handler.
 * - Performs the initial {@link loadTerm} for the most recent term.
 *
 * The `rerender` closure is called both on term-select change and on
 * `languageChanged` events so that all lazy-loaded tab state flags are reset
 * and any already-visible tab reloads its data in the new language context.
 */
(async function init() {
    // Discover available terms
    let terms = [];
    const response = await Request.POST({request: "terms"});
    if(response.status === "ok"){
        terms = response.data;
    }
    else{
        throw new Error(response.data);
    }

    const languageSelect = document.getElementById('language-select');
    const languageRender = async () => {
        languageSelect.innerHTML = "";
        const dict = await Dictionary.get();
        const languageKeys = Object.keys(dict.locales).sort((a, b) => {
            a = dict.locales[a];
            b = dict.locales[b];
            return a<b ? -1 : a>b ? 1 : 0;
        });
        for(const language of languageKeys){
            const value = dict.locales[language];
            const opt = document.createElement('option');
            opt.value = language;
            opt.selected = language == Dictionary.language;
            opt.textContent = value;
            opt.dataset.content = `locales.${language}`;
            languageSelect.appendChild(opt);
        }
    }

    await languageRender();

    const select = document.getElementById('term-select');
    const termKeys = Object.keys(terms);
    const termValues = Object.values(terms);
    for(const term of termKeys){
        const value = terms[term];
        const opt = document.createElement('option');
        opt.value = term;
        opt.textContent = `EP ${value.order} (${value.start.substring(0, 4)}-${value.end != null ? value.end.substring(0, 4) : translation("present")})`;
        opt.dataset.contentTemplate = `EP ${value.order} (${value.start.substring(0, 4)}-${value.end != null ? value.end.substring(0, 4) : "{{present}}"})`;
        select.appendChild(opt);
    }

    currentTerm = termKeys[termKeys.length - 1];
    select.value = currentTerm;

    const rerender = () => {
        languageRender();
        currentTerm = select.value;
        corrTabLoaded      = false;
        compareTabLoaded   = false;
        topicTabLoaded     = false;
        mstatsTabLoaded    = false;
        sstatsTabLoaded    = false;
        anomalyTabLoaded   = false;
        pairRankingsLoaded = false;
        loadTerm(currentTerm);
        if (document.getElementById('tab-corr').classList.contains('show')) {
            corrTabLoaded = true;
            initCorrTab(currentTerm);
        }
        if (document.getElementById('tab-compare').classList.contains('show')) {
            compareTabLoaded = true;
            initCompareTab(currentTerm);
            if (document.getElementById('acc-rankings').classList.contains('show')) {
                pairRankingsLoaded = true;
                initPairRankings(currentTerm);
            }
        }
        if (['tab-heatmap', 'tab-deviation', 'tab-opposition'].some(id => document.getElementById(id).classList.contains('show'))) {
            topicTabLoaded = true;
            initTopicTab(currentTerm);
        }
        if (document.getElementById('tab-mstats').classList.contains('show')) {
            mstatsTabLoaded = true;
            initMemberStatsTab(currentTerm);
        }
        if (document.getElementById('tab-sstats').classList.contains('show')) {
            sstatsTabLoaded = true;
            initSubjectStatsTab(currentTerm);
        }
        if (document.getElementById('tab-anomaly').classList.contains('show')) {
            anomalyTabLoaded = true;
            initAnomalyTab(currentTerm);
        }
    };

    select.addEventListener('change', rerender);
    languageSelect.addEventListener('change', ()=>{
        Dictionary.translate(languageSelect.value);
    })
    window.addEventListener('languageChanged', rerender);

    // Wire correlation selectors once — no duplicates ever
    setupCorrListeners();

    // Wire MEP pickers once
    setupMepPicker('search-mep1', 'sel-mep1', 'mep1');
    setupMepPicker('search-mep2', 'sel-mep2', 'mep2');
    document.getElementById('btn-compare').addEventListener('click', runComparison);

    // Lazy-load pair rankings when the accordion panel is opened
    let pairRankingsLoaded = false;
    document.getElementById('acc-rankings').addEventListener('shown.bs.collapse', () => {
        if (!pairRankingsLoaded) {
            pairRankingsLoaded = true;
            initPairRankings(currentTerm);
        }
    });
    document.getElementById('pair-view-select').addEventListener('change', () => {
        loadPairView(currentTerm, document.getElementById('pair-view-select').value);
    });

    // Wire anomaly picker once
    setupAnomalyPicker();
    document.getElementById('btn-anomaly').addEventListener('click', runAnomalyDetection);

    // Lazy-load correlation tab on first visit
    let corrTabLoaded = false;
    document.querySelector('[data-bs-target="#tab-corr"]').addEventListener('shown.bs.tab', () => {
        if (!corrTabLoaded) {
            corrTabLoaded = true;
            initCorrTab(currentTerm);
        }
    });

    // Lazy-load compare tab on first visit
    let compareTabLoaded = false;
    document.querySelector('[data-bs-target="#tab-compare"]').addEventListener('shown.bs.tab', () => {
        if (!compareTabLoaded) {
            compareTabLoaded = true;
            initCompareTab(currentTerm);
        }
    });

    // Lazy-load member/subject statistics tabs on first visit
    let mstatsTabLoaded = false;
    document.querySelector('[data-bs-target="#tab-mstats"]').addEventListener('shown.bs.tab', () => {
        if (!mstatsTabLoaded) {
            mstatsTabLoaded = true;
            initMemberStatsTab(currentTerm);
        }
    });

    let sstatsTabLoaded = false;
    document.querySelector('[data-bs-target="#tab-sstats"]').addEventListener('shown.bs.tab', () => {
        if (!sstatsTabLoaded) {
            sstatsTabLoaded = true;
            initSubjectStatsTab(currentTerm);
        }
    });

    // Lazy-load anomaly tab on first visit
    let anomalyTabLoaded = false;
    document.querySelector('[data-bs-target="#tab-anomaly"]').addEventListener('shown.bs.tab', () => {
        if (!anomalyTabLoaded) {
            anomalyTabLoaded = true;
            initAnomalyTab(currentTerm);
        }
    });

    // Lazy-load topic tabs on first visit (all three share the same data load)
    let topicTabLoaded = false;
    ['#tab-heatmap', '#tab-deviation', '#tab-opposition'].forEach(target => {
        document.querySelector(`[data-bs-target="${target}"]`).addEventListener('shown.bs.tab', () => {
            if (!topicTabLoaded) {
                topicTabLoaded = true;
                initTopicTab(currentTerm);
            }
            if (target === '#tab-deviation' && dtInstances['table-deviation']) {
                dtInstances['table-deviation'].columns.adjust().draw(false);
            }
            if (target === '#tab-opposition' && dtInstances['table-opposition']) {
                dtInstances['table-opposition'].columns.adjust().draw(false);
            }
        });
    });

    // Re-render heatmap when filters change
    document.getElementById('topic-filter-group').addEventListener('change',   () => { if (_topicProfile) renderTopicHeatmap(_topicProfile); });
    document.getElementById('topic-filter-country').addEventListener('change', () => { if (_topicProfile) renderTopicHeatmap(_topicProfile); });

    // Resize charts when switching tabs (charts rendered in hidden tabs have 0 size)
    document.querySelectorAll('[data-bs-toggle="tab"]').forEach(btn => {
        btn.addEventListener('shown.bs.tab', () => {
            Object.values(charts).forEach(c => c && c.resize());
        });
    });

    await loadTerm(currentTerm);
}());
