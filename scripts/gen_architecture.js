// Writes docs/architecture/pavilion.excalidraw: the Pavilion architecture in the hand-drawn Excalidraw style.
// Open it at excalidraw.com (menu > Open) to edit; scripts/render_architecture.html renders it to PNG.
const fs = require("fs");
let n = 0, seed = 7;
const rnd = () => (seed = (seed * 48271) % 2147483647);
const base = (type, x, y, w, h, o = {}) => ({id: "pav_" + n++, type, x, y, width: w, height: h, angle: 0,
  strokeColor: o.color || "#1e1e1e", backgroundColor: "transparent", fillStyle: "solid", strokeWidth: 1,
  strokeStyle: o.dashed ? "dashed" : "solid", roughness: 1, opacity: 100, groupIds: [], frameId: null, roundness: null,
  seed: rnd(), version: 1, versionNonce: rnd(), isDeleted: false, boundElements: [], updated: 1, link: null, locked: false});
const els = [];
const rect = (x, y, w, h, o) => els.push(base("rectangle", x, y, w, h, o));
const text = (x, y, w, s, size = 14, o = {}) => els.push({...base("text", x, y, w, Math.round(s.split("\n").length * size * 1.25), o),
  text: s, originalText: s, fontSize: size, fontFamily: 1, textAlign: "left", verticalAlign: "top", containerId: null, lineHeight: 1.25, autoResize: false});
const arrow = (x, y, dx, dy, o = {}) => els.push({...base("arrow", x, y, Math.abs(dx), Math.abs(dy), o), points: [[0, 0], [dx, dy]],
  lastCommittedPoint: null, startBinding: null, endBinding: null, startArrowhead: null, endArrowhead: "arrow", elbowed: false});
const box = (x, y, w, h, s, o = {}) => { rect(x, y, w, h, o); text(x + 10, y + 12, w - 20, s, o.size || 14, o.color ? {color: o.color} : {}); };
const G = "#555555", RED = "#e03131", L = 0, T = 0, W = 1242;

text(L, T + 16, 420, "PAVILION\nARCHITECTURE", 40);
text(L, T + 111, 420, "explanations an analyst can audit", 16, {color: G});
const IX = L + 380; rect(IX, T, 862, 175); text(IX + 20, T + 12, 822, "Inputs", 16);
box(IX + 20, T + 37, 266, 116, "Analyst question\nwhy did the number move?\ncompany-specific or sector-wide?\ne.g. Target gross margin, FY2022");
box(IX + 298, T + 37, 266, 116, "SEC EDGAR · live\nXBRL companyconcept API\nsubmissions API · SIC · FYE\n7 big-box retailers, 10-K + 10-Q");
box(IX + 575, T + 37, 266, 116, "Context data\nBLS producer price indexes (FRED)\ncompany_dns SIC hierarchy\nretail margins · freight · warehousing");
arrow(IX + 431, T + 175, 0, 48); text(IX + 443, T + 190, 260, "question + live public data", 13, {color: G});

const DY = T + 223; rect(L, DY, W, 298);
text(L + 20, DY + 12, 1202, "Data · Zetaris Lightning · Unified Semantic Layer causeway.retail_usl · the only data path the agents have", 16);
box(L + 24, DY + 45, 273, 116, "Source tables\nLIGHTNING REST TABLE × 33\nSchemaStore views · FILESTORE\nqueried live, nothing copied");
box(L + 326, DY + 45, 273, 116, "USL · 5 tables\nsector_class · company\nfinancial_fact · annual_financials\nprice_index · PK/FK in the DDL");
box(L + 628, DY + 45, 273, 116, "edgar.margin_change\nevery YoY change from ONE 10-K\nLAG over the filing\nrestatements can't fake a jump");
box(L + 930, DY + 45, 286, 116, "Data quality\nREGISTER DQ × 6 · RUN DQ\ncost < revenue · margin 5-60%\n0 invalid records");
arrow(L + 297, DY + 103, 29, 0); arrow(L + 599, DY + 103, 29, 0); arrow(L + 901, DY + 103, 29, 0);
box(L + 24, DY + 186, 273, 92, "USL as code\nzetaris_sql/usl · dq · sources\ngen_edgar_sql.py from universe.json\nany company list", {dashed: true});
box(L + 326, DY + 186, 890, 92, "Read-only by construction\nofficial JDBC driver · SELECT / WITH only · DDL and DML refused before they reach Zetaris\nthe Critic re-executes cited SQL here, in the same engine the agent used");
rect(L + 1290, DY + 33, 232, 100); text(L + 1300, DY + 45, 212, "Zetaris JDBC\nndp-jdbc-driver 2.4.3.1\nLightningDriver · port 10000", 14);
arrow(L + 1248, DY + 83, 40, 0, {dashed: true}); text(L + 1250, DY + 140, 200, "every query,\nevery recompute", 12, {color: G});
arrow(L + 727, DY + 298, 0, 50); text(L + 739, DY + 314, 220, "SQL in · rows out", 13, {color: G});

const AY = DY + 348; rect(L, AY, W, 298);
text(L + 20, AY + 12, 1202, "Agents · NVIDIA Nemotron via Meterless Nightshift · no agent may state a number it did not query", 16);
box(L + 24, AY + 45, 273, 116, "Planner\nNemotron 3.5 Lightning 30B\npeers · exclusions with reasons\n4-6 hypotheses, both sides");
box(L + 326, AY + 45, 273, 116, "Investigator\nNemotron 3 Ultra 550B\none SQL query per turn\nfindings cite query ids");
box(L + 628, AY + 45, 273, 116, "Critic\nre-runs every cited query\ngrounds every number\nUltra methodology review");
box(L + 930, AY + 45, 286, 116, "Reporter\nNemotron 3 Ultra 550B\napproved findings only\na query id on every number");
arrow(L + 297, AY + 103, 29, 0); arrow(L + 599, AY + 103, 29, 0); arrow(L + 901, AY + 103, 29, 0);
box(L + 24, AY + 186, 273, 92, "Reject loop\nrejected → back with the fix\nup to 3 rounds\nunreviewed = never approved", {dashed: true});
box(L + 326, AY + 186, 890, 92, "Fail-safe\na hung model falls back Ultra ↔ Lightning · cloud_busy / DNS drops / engine restarts are waited out\nNightshift exact cache keyed per run, so no stale answer replays as live agent work");
rect(L + 1290, AY + 33, 232, 116); text(L + 1300, AY + 45, 212, "Meterless Nightshift\nprovider contract v4\ncompute receipt per call\nrouted model logged", 14);
arrow(L + 1248, AY + 83, 40, 0, {dashed: true}); text(L + 1290, AY + 160, 200, "agents propose,\nthe Critic disposes", 12, {color: G});
arrow(L + 727, AY + 298, 0, 50); text(L + 739, AY + 314, 240, "findings + cited query ids", 13, {color: G});

const EY = AY + 348; rect(L, EY, W, 240);
text(L + 20, EY + 12, 1202, "Engines · Meterless World Model + H-MEM · deterministic checks · no LLM decides whether a number is real", 16);
box(L + 24, EY + 45, 273, 108, "World Model\nstable hash IDs · append-only log\nevery Zetaris row → facts\nwith provenance (659 per run)");
box(L + 326, EY + 45, 273, 108, "Recompute\nre-execute each cited query\nrow fingerprint must match\nelse: rejected");
box(L + 628, EY + 45, 273, 108, "Grounding\nevery number = a fact, its %,\na same-measure difference\nor a peer median");
box(L + 930, EY + 45, 286, 108, "H-MEM\nhybrid score ≥ 0.35 · top 5\nrejections → lessons (0.9)\napprovals → patterns (0.75)");
arrow(L + 297, EY + 99, 29, 0); arrow(L + 599, EY + 99, 29, 0); arrow(L + 901, EY + 99, 29, 0);
box(L + 24, EY + 172, 572, 54, "restatement trap: Dollar Tree's restated cost of sales faked +8 pts\n→ caught, fixed with the same-filing margin_change view", {dashed: true, size: 13});
box(L + 628, EY + 172, 588, 54, "hard rules: excluding a peer because it differs = cherry-picking → REJECT\nrounded numbers (4.64 → 4) · PPI index points ≠ margin points → REJECT", {dashed: true, color: RED, size: 13});
rect(L + 1290, EY + 33, 232, 140); text(L + 1300, EY + 45, 212, "Lessons recalled\nbefore the next plan\nTarget run: 8 learned\nBest Buy run: 4 recalled\n27 tests in the repo", 14);
arrow(L + 1288, EY + 99, -66, 0); text(L + 1225, EY + 180, 120, "lessons in,\nverdicts out", 12, {color: G});

arrow(L + 727, EY + 240, 0, 48);
const VY = EY + 288; box(L + 24, VY, 320, 66, "Round 1 on Target: 1 of 5 approved\nRound 3: 5 of 5 · 17 recomputes, all identical", {dashed: true, size: 13});
rect(L + 368, VY, 504, 66); text(L + 378, VY + 18, 484, "approved  ·  rejected  ·  unverified", 24);
box(L + 896, VY, 346, 66, "No secrets in the repo\n.env gitignored · keys only in Nightshift\nSEC user agent injected at run time", {dashed: true, size: 13});
arrow(L + 620, VY + 66, -278, 83); arrow(L + 620, VY + 66, 0, 83); arrow(L + 620, VY + 66, 279, 83);
text(L + 360, VY + 91, 120, "every finding", 13, {color: G}); text(L + 632, VY + 100, 140, "every memo", 13, {color: G}); text(L + 760, VY + 91, 140, "every run", 13, {color: G});
const OY = VY + 149;
box(L + 24, OY, 398, 98, "Analyst memo\none-paragraph answer · company vs sector\nTarget −4.64 vs peers −0.74 → −3.90 own\nunverified claims only under Open questions");
box(L + 446, OY, 348, 98, "Evidence log · runs/<id>/logs\nsteps · queries + rows\nmodel_calls + Nightshift receipts\nCritic verdicts per round");
box(L + 818, OY, 424, 98, "Pavilion dashboard · web/\ntimeline · Critic rounds · memo\nclick a query id → SQL + rows\nmemory recalled and learned");
rect(L + 1290, OY + 8, 232, 82); text(L + 1300, OY + 20, 212, "Analysts\ncredit + equity research\nask why, get proof", 14);
arrow(L + 1288, OY + 49, -44, 0);
text(L, OY + 147, 600, "AGENTS QUERY\nTHE CRITIC RE-RUNS\nEVERY NUMBER HAS A SOURCE\nMEMORY LEARNS FROM REJECTIONS", 28);
text(L + 626, OY + 157, 560, "question → plan → SQL → recompute → verdict → memo → lesson\nZetaris · Meterless Nightshift · NVIDIA Nemotron · World Model · H-MEM\ngithub.com/Pavilion-devs/pavilion-agent", 13, {color: G});

fs.writeFileSync(process.argv[2], JSON.stringify({type: "excalidraw", version: 2, source: "https://excalidraw.com",
  elements: els, appState: {viewBackgroundColor: "#ffffff", gridSize: null}, files: {}}, null, 1));
console.log(els.length + " elements");
