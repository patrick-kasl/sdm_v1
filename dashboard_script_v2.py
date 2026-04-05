import os
import sys
import json
import argparse
import re
from collections import defaultdict

# --- HEURISTICS FOR CLINICAL RESOURCE TIERS ---
TIER_0_KEYWORDS = ['wait', 'watch', 'nothing', 'home', 'rest', 'ice', 'diet', 'exercis', 'delay', 'continue', 'status quo', 'conservative']
TIER_2_KEYWORDS = ['refer', 'special', 'mri', 'ct ', 'scan', 'x-ray', 'xray', 'rheumatol', 'ortho', 'surg', 'neurol', 'cardiol', 'imaging', 'procedure', 'er ', 'emergency']

def classify_tier(label):
    lbl = label.lower()
    if any(k in lbl for k in TIER_2_KEYWORDS): return 2
    if any(k in lbl for k in TIER_0_KEYWORDS): return 0
    return 1

def classify_decision_type(text):
    text = text.lower()
    if any(k in text for k in ['mri', 'ct ', 'x-ray', 'xray', 'ultrasound', 'imaging', 'scan', 'dexa']): return "Imaging/Diagnostic"
    if any(k in text for k in ['refer', 'specialist', 'consult', 'therapy', 'rheumatol', 'ortho', 'neurol', 'cardiol']): return "Referral"
    if any(k in text for k in ['lab', 'blood', 'test', 'swab', 'panel', 'urine']): return "Lab Test"
    if any(k in text for k in ['med', 'dose', 'mg', 'prescri', 'pill', 'inhaler', 'tylenol', 'ibuprofen', 'omeprazole', 'injection']): return "Med Change"
    return "Conservative / Other"

def parse_transcript_to_turns(raw_text):
    """
    Mirrors the processing pipeline exactly.

    FORMAT 1 — colon separator (speaker name < 50 chars, may be mixed case):
        "Doctor Smith: hello there"
        "PATIENT: I feel fine"

    FORMAT 2 — all-caps speaker label alone on a line, no colon (line < 40 chars):
        "DOCTOR Q131"
        "PATIENT?"

    Turn index is assigned as len(turns) *before* appending, so indices are
    0-based positional — matching the absolute turn_index values in the JSONs.
    """
    lines = raw_text.split('\n')
    turns = []
    current_turn = None

    # FORMAT 1: optional leading whitespace, colon separator, name < 50 chars
    speaker_colon_regex = re.compile(r'^([A-Z][A-Za-z0-9\.\s]+):\s*(.*)', re.IGNORECASE)
    # FORMAT 2: all-caps (digits/spaces/? allowed), no colon, short line
    speaker_nocolon_regex = re.compile(r'^([A-Z][A-Z0-9\s]*\??)$')

    for line in lines:
        clean = line.strip()
        if not clean:
            continue

        is_speaker = False
        speaker_name = ""
        spoken_text = ""

        match_colon = speaker_colon_regex.match(clean)
        if match_colon and len(match_colon.group(1)) < 50:
            is_speaker = True
            speaker_name = match_colon.group(1).strip().upper()
            spoken_text = match_colon.group(2).strip()
        else:
            match_nocolon = speaker_nocolon_regex.match(clean)
            if match_nocolon and len(clean) < 40:
                is_speaker = True
                speaker_name = clean.upper()
                spoken_text = ""

        if is_speaker:
            if current_turn:
                turns.append(current_turn)
            current_turn = {
                "index": len(turns),   # absolute index — matches JSON turn_index values
                "speaker": speaker_name,
                "text": spoken_text
            }
        else:
            if current_turn:
                current_turn["text"] += ("\n" + clean) if current_turn["text"] else clean
            else:
                current_turn = {"index": 0, "speaker": "METADATA", "text": clean}

    if current_turn:
        turns.append(current_turn)

    return turns


def generate_dashboard(jsons_dir, transcripts_dir, output_file):
    print(f"Scanning JSONs in: {jsons_dir}")
    if transcripts_dir:
        print(f"Looking for transcripts in: {transcripts_dir}")

    global_stats = {
        "total_files": 0,
        "files_with_sdm": 0,
        "total_regions": 0,
        "tier_distribution": {0: 0, 1: 0, 2: 0},
        "sdm_by_tier": {0: 0, 1: 0, 2: 0},
        "decision_types": defaultdict(int),
        "cross_tier_count": 0,
        "sdm_present_count": 0,
        "pref_integration_count": 0,
        "behavior_counts_by_tier": {0: defaultdict(int), 1: defaultdict(int), 2: defaultdict(int)},
        "avg_coverage_by_tier": {0: [], 1: [], 2: []}
    }
    files_data = []

    for filename in sorted(os.listdir(jsons_dir)):
        if not filename.endswith('.json'):
            continue

        filepath = os.path.join(jsons_dir, filename)
        base_name = filename.replace('.json', '')

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            global_stats["total_files"] += 1
            regions = data.get("regions", [])
            has_sdm = len(regions) > 0

            # Load matching transcript if available
            turns = []
            if transcripts_dir:
                txt_path = os.path.join(transcripts_dir, base_name + '.txt')
                if os.path.exists(txt_path):
                    with open(txt_path, 'r', encoding='utf-8') as tf:
                        turns = parse_transcript_to_turns(tf.read())
                    print(f"  {filename}: {len(turns)} turns parsed")
                else:
                    print(f"  {filename}: no matching .txt found")

            file_highest_tier = -1

            for r in regions:
                options = r.get('options_discussed', [])
                summary_text = r.get('summary', '')
                decision_text = summary_text + " " + " ".join([opt['label'] for opt in options])

                opt_tiers = {opt['option_id']: classify_tier(opt['label']) for opt in options}
                r['option_tiers'] = opt_tiers
                decision_type = classify_decision_type(decision_text)

                tiers_present = set(opt_tiers.values())
                highest_tier = max(tiers_present) if tiers_present else 1
                is_cross_tier = len(tiers_present) > 1

                if file_highest_tier == -1 or highest_tier > file_highest_tier:
                    file_highest_tier = highest_tier

                behaviors_present = set()
                for ct in r.get('classified_turns', []):
                    for b in ct.get('behaviors', []):
                        behaviors_present.add(b['behavior_name'])
                        global_stats["behavior_counts_by_tier"][highest_tier][b['behavior_name']] += 1

                sdm_present = ("Options Implied" in behaviors_present) and len(behaviors_present) > 1
                coverage = len(behaviors_present)
                pref_int = "Preference Integration" in behaviors_present

                r['analytics'] = {
                    'highest_tier': highest_tier,
                    'is_cross_tier': is_cross_tier,
                    'sdm_present': sdm_present,
                    'coverage_score': coverage,
                    'preference_integration': pref_int,
                    'decision_type': decision_type,
                    'behaviors_list': sorted(list(behaviors_present))
                }

                global_stats["total_regions"] += 1
                global_stats["tier_distribution"][highest_tier] += 1
                global_stats["decision_types"][decision_type] += 1
                if is_cross_tier: global_stats["cross_tier_count"] += 1
                if sdm_present:
                    global_stats["sdm_present_count"] += 1
                    global_stats["sdm_by_tier"][highest_tier] += 1
                if pref_int: global_stats["pref_integration_count"] += 1
                global_stats["avg_coverage_by_tier"][highest_tier].append(coverage)

            if has_sdm:
                global_stats["files_with_sdm"] += 1

            files_data.append({
                "filename": base_name,
                "has_sdm": has_sdm,
                "region_count": len(regions),
                "highest_tier": file_highest_tier if file_highest_tier != -1 else None,
                "regions": regions,
                "turns": turns
            })

        except Exception as e:
            print(f"  [ERROR] Failed to process {filename}: {e}")
            import traceback; traceback.print_exc()

    # Finalise averages
    for t in [0, 1, 2]:
        arr = global_stats["avg_coverage_by_tier"][t]
        global_stats["avg_coverage_by_tier"][t] = round(sum(arr) / len(arr), 1) if arr else 0

    # Sort: files with SDM first, then by region count desc, then alphabetically
    files_data.sort(key=lambda x: (-x["region_count"], x["filename"]))

    # Serialise decision_types defaultdict for JSON
    global_stats["decision_types"] = dict(global_stats["decision_types"])
    for t in [0, 1, 2]:
        global_stats["behavior_counts_by_tier"][t] = dict(global_stats["behavior_counts_by_tier"][t])

    payload = {"global_stats": global_stats, "files": files_data}

    # -------------------------------------------------------------------------
    # HTML TEMPLATE
    # -------------------------------------------------------------------------
    html_template = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SDM Clinical Analytics Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { height: 100vh; display: flex; overflow: hidden; background-color: #f8fafc; font-family: 'Inter', sans-serif; }
        .sidebar { width: 330px; display: flex; flex-direction: column; background: #0f172a; color: #f1f5f9; flex-shrink: 0; }
        .main-content { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .scrollable { overflow-y: auto; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
        .sidebar ::-webkit-scrollbar-thumb { background: #475569; }
        .file-item { cursor: pointer; transition: all 0.2s; }
        .file-item:hover { background: #1e293b; }
        .file-item.active { background: #3b82f6; color: white; border-left: 4px solid #60a5fa !important; }
        .chart-container { position: relative; height: 350px; width: 100%; }
        .badge { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 9999px; font-size: 0.7rem; font-weight: 600; }
        .badge-t0 { background: #dcfce7; color: #166534; border: 1px solid #bbf7d0; }
        .badge-t1 { background: #e0f2fe; color: #075985; border: 1px solid #bae6fd; }
        .badge-t2 { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }
        .transcript-pane { font-family: 'Courier New', Courier, monospace; line-height: 1.6; }

        /* Highlight flash animation */
        @keyframes flash { 0%,100% { background-color: inherit; } 30% { background-color: #fef08a; } }
        .flash { animation: flash 1.2s ease; }
    </style>
</head>
<body>

<div class="sidebar shadow-xl z-20">
    <div class="p-4 border-b border-slate-700">
        <h1 class="text-lg font-bold text-white mb-2">SDM Tier Analytics</h1>
        <input type="text" id="searchInput" placeholder="Search transcripts..."
               class="w-full bg-slate-800 text-white placeholder-slate-400 border border-slate-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
               oninput="filterFiles()">
    </div>
    <div class="p-3 border-b border-slate-700 flex justify-between items-center bg-slate-800 cursor-pointer hover:bg-slate-700 transition"
         onclick="showGlobalDashboard()">
        <span class="font-semibold text-blue-400">&#x1F4CA; Global Aggregates</span>
    </div>
    <div class="scrollable flex-1 p-2" id="fileList"></div>
</div>

<div class="main-content relative z-10">
    <header class="bg-white border-b border-slate-200 px-6 py-4 shadow-sm flex justify-between items-center flex-shrink-0">
        <div>
            <h2 id="viewTitle" class="text-2xl font-bold text-slate-800">Global Overview</h2>
            <p id="viewSubtitle" class="text-sm text-slate-500 mt-1">Cross-tier analytics and resource utilization.</p>
        </div>
    </header>
    <div class="scrollable flex-1 bg-slate-50" id="viewContainer"></div>
</div>

<script>
const DB = __DATA_PAYLOAD__;
let globalCharts = [];

// ── Stable colour from string hash ──────────────────────────────────────────
function getColor(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
    const c = (hash & 0x00FFFFFF).toString(16).toUpperCase();
    const hex = "000000".substring(0, 6 - c.length) + c;
    const r = parseInt(hex.substr(0, 2), 16);
    const g = parseInt(hex.substr(2, 2), 16);
    const b = parseInt(hex.substr(4, 2), 16);
    return `rgba(${Math.floor((r + 255) / 2)}, ${Math.floor((g + 255) / 2)}, ${Math.floor((b + 255) / 2)}, 0.9)`;
}

function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Speaker classification (mirrors Python get_speaker_type) ─────────────────
function speakerClass(speakerLine) {
    const s = (speakerLine || '').toUpperCase();
    if (/DOC|CLIN|PROV/.test(s)) return 'clinician';
    if (/PAT/.test(s))           return 'patient';
    return 'other';
}

// ── Init ─────────────────────────────────────────────────────────────────────
function init() { renderSidebar(); showGlobalDashboard(); }

function renderSidebar() {
    const list = document.getElementById('fileList');
    list.innerHTML = '';
    DB.files.forEach((file, idx) => {
        const div = document.createElement('div');
        div.className = 'file-item rounded p-3 mb-1 flex justify-between items-center border-l-4 border-transparent';
        div.id = `sidebar-file-${idx}`;
        div.setAttribute('data-filename', file.filename.toLowerCase());

        const icon = file.has_sdm ? '&#x1F7E2;' : '&#x26AA;';
        const dimClass = file.has_sdm ? 'text-slate-100' : 'text-slate-500';

        let metaTags = "";
        if (file.has_sdm) {
            let tColor = 'bg-slate-700 text-slate-300';
            if (file.highest_tier === 2) tColor = 'bg-red-900 text-red-200';
            else if (file.highest_tier === 1) tColor = 'bg-blue-900 text-blue-200';
            else if (file.highest_tier === 0) tColor = 'bg-green-900 text-green-200';
            metaTags = `
                <div class="flex gap-1 flex-shrink-0 ml-2">
                    <span class="text-[10px] bg-slate-700 px-1.5 py-0.5 rounded text-slate-300">${file.region_count} evt</span>
                    <span class="text-[10px] ${tColor} px-1.5 py-0.5 rounded">Max T${file.highest_tier}</span>
                </div>`;
        }

        div.innerHTML = `
            <div class="truncate pr-2 ${dimClass}">
                <span class="text-xs mr-1">${icon}</span>${escHtml(file.filename)}
            </div>${metaTags}`;
        div.onclick = () => showFileDetails(idx);
        list.appendChild(div);
    });
}

function filterFiles() {
    const q = document.getElementById('searchInput').value.toLowerCase();
    document.querySelectorAll('.file-item').forEach(el => {
        el.style.display = el.getAttribute('data-filename').includes(q) ? 'flex' : 'none';
    });
}

function clearSidebarActive() {
    document.querySelectorAll('.file-item').forEach(el => el.classList.remove('active'));
}

function destroyCharts() { globalCharts.forEach(c => c.destroy()); globalCharts = []; }

// ── Global dashboard ─────────────────────────────────────────────────────────
function showGlobalDashboard() {
    clearSidebarActive();
    destroyCharts();
    const vc = document.getElementById('viewContainer');
    vc.className = 'scrollable flex-1 bg-slate-50 p-6';
    document.getElementById('viewTitle').innerText = "Clinical Resource Utilization & SDM";
    document.getElementById('viewSubtitle').innerText = "Analysis of Treatment Tiers, Conservative vs Escalation, and Process Behaviors";

    const s = DB.global_stats;
    const sdmRate = t => s.tier_distribution[t]
        ? ((s.sdm_by_tier[t] / s.tier_distribution[t]) * 100).toFixed(1) : 0;

    vc.innerHTML = `
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
            <div class="bg-white p-4 rounded shadow-sm border border-slate-200">
                <div class="text-slate-500 text-xs font-bold uppercase">Decisions Extracted</div>
                <div class="text-2xl font-bold text-slate-800 mt-1">${s.total_regions}</div>
            </div>
            <div class="bg-white p-4 rounded shadow-sm border border-slate-200">
                <div class="text-slate-500 text-xs font-bold uppercase">Cross-Tier Options</div>
                <div class="text-2xl font-bold text-indigo-600 mt-1">${s.cross_tier_count}</div>
            </div>
            <div class="bg-white p-4 rounded shadow-sm border border-slate-200">
                <div class="text-slate-500 text-xs font-bold uppercase">SDM Process Verified</div>
                <div class="text-2xl font-bold text-emerald-600 mt-1">${s.sdm_present_count}</div>
            </div>
            <div class="bg-white p-4 rounded shadow-sm border border-slate-200">
                <div class="text-slate-500 text-xs font-bold uppercase">Pref. Integration Used</div>
                <div class="text-2xl font-bold text-amber-600 mt-1">${s.pref_integration_count}</div>
            </div>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
            <div class="bg-white p-5 rounded shadow-sm border border-slate-200">
                <h3 class="text-sm font-bold text-slate-800 mb-4 uppercase">Decision Categories</h3>
                <div class="chart-container" style="height:250px;"><canvas id="typeChart"></canvas></div>
            </div>
            <div class="bg-white p-5 rounded shadow-sm border border-slate-200">
                <h3 class="text-sm font-bold text-slate-800 mb-4 uppercase">Outcome Resource Tier</h3>
                <div class="chart-container" style="height:250px;"><canvas id="tierChart"></canvas></div>
            </div>
            <div class="bg-white p-5 rounded shadow-sm border border-slate-200">
                <h3 class="text-sm font-bold text-slate-800 mb-2 uppercase">% of Decisions Using SDM</h3>
                <div class="chart-container" style="height:220px;"><canvas id="sdmRateChart"></canvas></div>
            </div>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <div class="bg-white p-5 rounded shadow-sm border border-slate-200">
                <h3 class="text-sm font-bold text-slate-800 mb-4 uppercase">Avg Behavior Coverage Score by Tier</h3>
                <div class="chart-container" style="height:350px;"><canvas id="coverageChart"></canvas></div>
            </div>
            <div class="bg-white p-5 rounded shadow-sm border border-slate-200">
                <h3 class="text-sm font-bold text-slate-800 mb-4 uppercase">Behavior Process Frequencies</h3>
                <div class="chart-container" style="height:350px;"><canvas id="processChart"></canvas></div>
            </div>
        </div>`;

    const c1 = new Chart(document.getElementById('tierChart'), {
        type: 'doughnut',
        data: {
            labels: ['Tier 0 (Conservative)', 'Tier 1 (Meds/Labs)', 'Tier 2 (Escalation)'],
            datasets: [{ data: [s.tier_distribution[0], s.tier_distribution[1], s.tier_distribution[2]], backgroundColor: ['#22c55e', '#3b82f6', '#ef4444'] }]
        },
        options: { maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
    });

    const cType = new Chart(document.getElementById('typeChart'), {
        type: 'pie',
        data: {
            labels: Object.keys(s.decision_types),
            datasets: [{ data: Object.values(s.decision_types), backgroundColor: ['#8b5cf6','#f59e0b','#ec4899','#14b8a6','#64748b'] }]
        },
        options: { maintainAspectRatio: false, plugins: { legend: { position: 'right', labels: { boxWidth: 10 } } } }
    });

    const cSDM = new Chart(document.getElementById('sdmRateChart'), {
        type: 'bar',
        data: {
            labels: ['Tier 0', 'Tier 1', 'Tier 2'],
            datasets: [{
                label: '% Displaying SDM',
                data: [sdmRate(0), sdmRate(1), sdmRate(2)],
                backgroundColor: ['#bbf7d0','#bfdbfe','#fecaca'],
                borderColor: ['#22c55e','#3b82f6','#ef4444'],
                borderWidth: 2
            }]
        },
        options: { maintainAspectRatio: false, scales: { y: { beginAtZero: true, max: 100 } } }
    });

    const c2 = new Chart(document.getElementById('coverageChart'), {
        type: 'bar',
        data: {
            labels: ['Tier 0', 'Tier 1', 'Tier 2'],
            datasets: [{ label: 'Avg Unique SDM Behaviors', data: [s.avg_coverage_by_tier[0], s.avg_coverage_by_tier[1], s.avg_coverage_by_tier[2]], backgroundColor: '#6366f1' }]
        },
        options: { maintainAspectRatio: false, scales: { y: { beginAtZero: true } } }
    });

    const behaviors = ["Options Implied","Explaining Benefits","Explaining Risks","Explaining Burdens/Practicalities","Values Exploration","Patient Values Expressed","Preferences Elicited","Preference Integration","Decision Stated"];
    const d0 = behaviors.map(b => s.behavior_counts_by_tier[0][b] || 0);
    const d1 = behaviors.map(b => s.behavior_counts_by_tier[1][b] || 0);
    const d2 = behaviors.map(b => s.behavior_counts_by_tier[2][b] || 0);
    const c3 = new Chart(document.getElementById('processChart'), {
        type: 'bar',
        data: {
            labels: behaviors,
            datasets: [
                { label: 'Tier 0', data: d0, backgroundColor: '#22c55e' },
                { label: 'Tier 1', data: d1, backgroundColor: '#3b82f6' },
                { label: 'Tier 2', data: d2, backgroundColor: '#ef4444' }
            ]
        },
        options: { maintainAspectRatio: false, indexAxis: 'y' }
    });

    globalCharts.push(c1, cType, cSDM, c2, c3);
}

// ── File detail view ─────────────────────────────────────────────────────────
function showFileDetails(idx) {
    clearSidebarActive();
    destroyCharts();
    document.getElementById(`sidebar-file-${idx}`).classList.add('active');

    const file = DB.files[idx];
    document.getElementById('viewTitle').innerText = escHtml(file.filename);
    document.getElementById('viewSubtitle').innerText = "Detailed Segment View & Inline Behavior Mapping";

    const vc = document.getElementById('viewContainer');
    vc.className = 'flex-1 overflow-hidden bg-slate-50';

    if (!file.has_sdm) {
        vc.innerHTML = `<div class="p-10 flex justify-center text-slate-400"><h3>No SDM Events Detected</h3></div>`;
        return;
    }

    // ------------------------------------------------------------------
    // Build lookup maps keyed by ABSOLUTE turn_index (as stored in JSON)
    // ------------------------------------------------------------------
    const turnHighlightMap  = {};   // turn_index → region id
    const turnBehaviorsMap  = {};   // turn_index → [{behavior + region info}]
    const regionSpanMap     = {};   // region id  → {start, end, summary}

    file.regions.forEach(r => {
        regionSpanMap[r.id] = { start: r.start_turn_index, end: r.end_turn_index, summary: r.summary };

        // Shade the whole region span
        for (let i = r.start_turn_index; i <= r.end_turn_index; i++) {
            turnHighlightMap[i] = r.id;
        }

        // Map classified turns
        (r.classified_turns ||[]).forEach(ct => {
            // FIX: Indices in JSON are already absolute. Do NOT add r.start_turn_index!
            const absIdx = ct.turn_index;   
            
            if (!turnBehaviorsMap[absIdx]) turnBehaviorsMap[absIdx] =[];
            ct.behaviors.forEach(b => {
                turnBehaviorsMap[absIdx].push({
                    behavior_name: b.behavior_name,
                    confidence_score: b.confidence_score,
                    related_option_id: b.related_option_id,
                    region_id: r.id
                });
            });
            // Ensure annotated turns are also highlighted even if not in span
            if (!(absIdx in turnHighlightMap)) turnHighlightMap[absIdx] = r.id;
        });
    });

    // ── Left pane: region cards ──────────────────────────────────────────────
    let regionsHtml = `<div class="space-y-4">`;
    file.regions.forEach(r => {
        const an = r.analytics;
        const tClass = an.highest_tier === 2 ? 'badge-t2' : (an.highest_tier === 0 ? 'badge-t0' : 'badge-t1');
        const crossBadge = an.is_cross_tier
            ? `<span class="badge bg-purple-100 text-purple-700 border border-purple-200 ml-1">Cross-Tier</span>` : '';
        const sdmBadge = an.sdm_present
            ? `<span class="badge bg-emerald-100 text-emerald-700 ml-1">SDM &#x2713;</span>`
            : `<span class="badge bg-slate-200 text-slate-500 ml-1">SDM &#x2717;</span>`;

        const optsHtml = (r.options_discussed ||[]).map(opt => {
            const oT = r.option_tiers[opt.option_id];
            const oC = oT === 2 ? 'bg-red-50 text-red-700 border-red-200'
                     : oT === 0 ? 'bg-green-50 text-green-700 border-green-200'
                                : 'bg-blue-50 text-blue-700 border-blue-200';
            return `<div class="text-xs border rounded px-2 py-1 mb-1 ${oC}">
                        <b>${escHtml(opt.option_id)} (T${oT}):</b> ${escHtml(opt.label)}
                    </div>`;
        }).join('');

        // NEW: Pre-calculate which turn index to jump to when a behavior badge is clicked
        const behaviorToTurn = {};
        (r.classified_turns ||[]).forEach(ct => {
            ct.behaviors.forEach(b => {
                if (behaviorToTurn[b.behavior_name] === undefined) {
                    behaviorToTurn[b.behavior_name] = ct.turn_index;
                }
            });
        });

        // NEW: Added cursor-pointer, hover effects, and onclick to scroll directly to the transcript line
        const bTagsHtml = an.behaviors_list && an.behaviors_list.length > 0
            ? an.behaviors_list.map(b => {
                const targetIdx = behaviorToTurn[b] !== undefined ? behaviorToTurn[b] : r.start_turn_index;
                return `<span class="inline-block px-2 py-0.5 rounded text-[10px] font-bold text-slate-800 shadow-sm border border-black/10 cursor-pointer hover:opacity-80 hover:ring-2 hover:ring-blue-400 transition-all"
                       style="background-color:${getColor(b)};"
                       title="Jump to turn ${targetIdx}"
                       onclick="event.stopPropagation(); scrollToTurn(${targetIdx})">${escHtml(b)}</span>`;
              }).join('<span class="mx-0.5"></span>')
            : `<span class="text-xs italic text-slate-400">No process behaviors logged</span>`;

        regionsHtml += `
            <div class="bg-white rounded shadow-sm border border-slate-200 p-4 cursor-pointer hover:border-blue-400 transition"
                 onclick="scrollToTurn(${r.start_turn_index})">
                 ...

    // ── Left pane: region cards ──────────────────────────────────────────────
    let regionsHtml = `<div class="space-y-4">`;
    file.regions.forEach(r => {
        const an = r.analytics;
        const tClass = an.highest_tier === 2 ? 'badge-t2' : (an.highest_tier === 0 ? 'badge-t0' : 'badge-t1');
        const crossBadge = an.is_cross_tier
            ? `<span class="badge bg-purple-100 text-purple-700 border border-purple-200 ml-1">Cross-Tier</span>` : '';
        const sdmBadge = an.sdm_present
            ? `<span class="badge bg-emerald-100 text-emerald-700 ml-1">SDM &#x2713;</span>`
            : `<span class="badge bg-slate-200 text-slate-500 ml-1">SDM &#x2717;</span>`;

        const optsHtml = (r.options_discussed || []).map(opt => {
            const oT = r.option_tiers[opt.option_id];
            const oC = oT === 2 ? 'bg-red-50 text-red-700 border-red-200'
                     : oT === 0 ? 'bg-green-50 text-green-700 border-green-200'
                                : 'bg-blue-50 text-blue-700 border-blue-200';
            return `<div class="text-xs border rounded px-2 py-1 mb-1 ${oC}">
                        <b>${escHtml(opt.option_id)} (T${oT}):</b> ${escHtml(opt.label)}
                    </div>`;
        }).join('');

        const bTagsHtml = an.behaviors_list && an.behaviors_list.length > 0
            ? an.behaviors_list.map(b =>
                `<span class="inline-block px-2 py-0.5 rounded text-[10px] font-bold text-slate-800 shadow-sm border border-black/10"
                       style="background-color:${getColor(b)};">${escHtml(b)}</span>`
              ).join('<span class="mx-0.5"></span>')
            : `<span class="text-xs italic text-slate-400">No process behaviors logged</span>`;

        regionsHtml += `
            <div class="bg-white rounded shadow-sm border border-slate-200 p-4 cursor-pointer hover:border-blue-400 transition"
                 onclick="scrollToTurn(${r.start_turn_index})">
                <div class="flex justify-between items-start mb-2">
                    <h3 class="font-bold text-slate-800 text-sm">${escHtml(r.id)} — ${escHtml(an.decision_type)}</h3>
                    <span class="text-[10px] text-slate-400 ml-2 flex-shrink-0">Turns ${r.start_turn_index}–${r.end_turn_index}</span>
                </div>
                <div class="mb-3 flex flex-wrap gap-1">
                    <span class="badge ${tClass}">Result: Tier ${an.highest_tier}</span>
                    ${crossBadge}${sdmBadge}
                    <span class="badge bg-slate-100 text-slate-600 border border-slate-200">Coverage: ${an.coverage_score}</span>
                </div>
                <div class="text-xs text-slate-600 mb-3 bg-slate-50 p-2 rounded italic">${escHtml(r.summary)}</div>
                <div class="mb-3">
                    <div class="text-xs font-bold text-slate-400 uppercase mb-1">Process Behaviors</div>
                    <div class="flex flex-wrap gap-1">${bTagsHtml}</div>
                </div>
                <div class="text-xs font-bold text-slate-400 uppercase mb-1">Mapped Options</div>
                ${optsHtml}
            </div>`;
    });
    regionsHtml += `</div>`;

    // ── Right pane: transcript ───────────────────────────────────────────────
    let transcriptHtml;
    if (file.turns && file.turns.length > 0) {
        let turnsHtml = '';
        file.turns.forEach(t => {
            const regionId = turnHighlightMap[t.index];
            const inRegion = regionId != null;
            const borderClass = inRegion
                ? 'border-l-4 border-yellow-400 bg-yellow-50/40'
                : 'border-l-4 border-transparent border-b border-slate-100';

            const behaviors = turnBehaviorsMap[t.index] || [];
            const tagsHtml = behaviors.map(b =>
                `<span class="inline-block px-2 py-0.5 rounded text-[10px] font-bold text-slate-800 mr-2 mb-1 shadow-sm border border-black/10"
                       style="background-color:${getColor(b.behavior_name)};"
                       title="${b.related_option_id ? 'Option: ' + escHtml(b.related_option_id) : ''}">
                    ${escHtml(b.behavior_name)}
                    <span class="opacity-50 ml-1 font-normal">(${b.confidence_score}%)</span>
                 </span>`
            ).join('');

            const sc = speakerClass(t.speaker);
            const speakerColor = sc === 'clinician' ? 'text-blue-600' : sc === 'patient' ? 'text-amber-600' : 'text-slate-500';

            turnsHtml += `
                <div class="p-3 ${borderClass} transition-colors" id="turn-${t.index}">
                    <div class="flex gap-3">
                        <div class="w-28 flex-shrink-0 text-right">
                            <div class="font-bold text-xs uppercase ${speakerColor}">${escHtml(t.speaker)}</div>
                            <div class="text-[9px] text-slate-400 mt-0.5">[#${t.index}]</div>
                            ${inRegion ? `<div class="text-[9px] text-yellow-600 mt-0.5">${escHtml(regionId)}</div>` : ''}
                        </div>
                        <div class="flex-1 min-w-0">
                            ${tagsHtml ? `<div class="mb-1">${tagsHtml}</div>` : ''}
                            <div class="whitespace-pre-wrap text-sm text-slate-700">${escHtml(t.text)}</div>
                        </div>
                    </div>
                </div>`;
        });

        transcriptHtml = `
            <div class="bg-white border border-slate-200 shadow-sm rounded h-full overflow-y-auto transcript-pane pb-10"
                 id="transcriptBox">
                ${turnsHtml}
            </div>`;
    } else {
        transcriptHtml = `
            <div class="p-10 text-center border-2 border-dashed border-slate-300 text-slate-400 rounded">
                Transcript text not available.<br>Ensure .txt files are in the designated directory.
            </div>`;
    }

    vc.innerHTML = `
        <div class="flex h-full w-full gap-4 p-4">
            <div class="w-1/3 overflow-y-auto pr-2 pb-10" style="height:calc(100vh - 100px);">
                <h3 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Identified Decisions</h3>
                ${regionsHtml}
            </div>
            <div class="w-2/3 pb-10" style="height:calc(100vh - 100px);">
                <h3 class="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Raw Transcript</h3>
                ${transcriptHtml}
            </div>
        </div>`;
}

// ── Scroll + flash a turn ────────────────────────────────────────────────────
window.scrollToTurn = function(startIdx) {
    const box    = document.getElementById('transcriptBox');
    const target = document.getElementById('turn-' + startIdx);
    if (!box || !target) return;
    box.scrollTo({ top: target.offsetTop - box.offsetTop - 20, behavior: 'smooth' });
    // Flash the target row
    target.classList.remove('flash');
    void target.offsetWidth;  // force reflow to restart animation
    target.classList.add('flash');
    setTimeout(() => target.classList.remove('flash'), 1400);
};

init();
</script>
</body>
</html>
"""

    json_payload = json.dumps(payload)
    final_html = html_template.replace('__DATA_PAYLOAD__', json_payload)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(final_html)

    print(f"\nDashboard saved: {output_file}")
    print(f"  Transcripts processed : {global_stats['total_files']}")
    print(f"  With SDM regions      : {global_stats['files_with_sdm']}")
    print(f"  Total regions found   : {global_stats['total_regions']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate SDM Tier Analytics Dashboard")
    parser.add_argument("--jsons",       required=True,                        help="Directory containing processed .json files")
    parser.add_argument("--transcripts", default=None,                         help="Directory containing raw .txt files (optional)")
    parser.add_argument("--output",      default="sdm_tier_dashboard.html",    help="Output HTML file path")
    args = parser.parse_args()
    generate_dashboard(args.jsons, args.transcripts, args.output)