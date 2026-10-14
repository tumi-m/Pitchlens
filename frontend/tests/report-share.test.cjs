const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

// lib/review/reportShare.ts and its one runtime import (visionStats.ts), run in a vm.
function load(file, context) {
  const source = fs.readFileSync(path.join(__dirname, '../lib/review', file), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: context.require });
  return exports;
}
const stats = load('visionStats.ts', { require: () => ({}) });
const { reportSummary } = load('reportShare.ts', { require: (name) => (name === './visionStats' ? stats : {}) });

const count = (value, confirmed = 0, extra = {}) => ({ value, confirmed, pending: value - confirmed, ...extra });

function analysis({ reviewed = false } = {}) {
  const team = (i) => ({
    controlSeconds: 1, possessionSeconds: 1,
    possession: i ? 40.5 : 59.5,
    possessions: 3, averagePossession: 2,
    passes: reviewed
      ? count(i ? 83 : 105, i ? 5 : 8, { checked: 10, estimate: i ? 83 : 105, estimateRange: i ? [51, 98] : [66, 123] })
      : count(i ? 41 : 70),
    passesComplete: reviewed ? count(i ? 42 : 73, i ? 1 : 3, { checked: 5 }) : count(i ? 30 : 50),
    passAccuracy: null,
    shots: null,
    shotsOnTarget: null,
    goals: reviewed ? { value: i ? 1 : 2, candidates: null } : { value: 0, candidates: null },
    interceptions: count(i ? 2 : 3),
    tackles: null,
    fieldTilt: null,
  });
  return {
    schemaVersion: 1, calibrated: false, template: null, directions: null, events: [],
    enteredScore: null,
    stats: {
      teams: [team(0), team(1)],
      coverage: { possessionPercent: 72.4, possessionShown: true, possessionMissingBounds: [45.2, 69.1], possessionInterval: null, controlPercent: 60, ballStatePercent: 80, calibratedPercent: 0, inPlaySeconds: 600, deadBallSeconds: 0, contestedSeconds: 0, looseSeconds: 0, unknownSeconds: 0, shotsBasis: 'no-pitch' },
      momentum: [], heatmaps: null, averagePositions: null, shotMap: null, tracks: { fragments: 1, players: 1 },
    },
    review: { decisions: 0, confirmed: 0, rejected: 0, pending: 10, pendingKey: 0, pendingByType: { pass: 10 } },
    positions: null,
  };
}

const ID = 'a'.repeat(32);
const base = { title: 'Friday 5s', names: ['Tekkerz', 'Lions'], analysedDuration: 1800 };

test('possession uses complementary rounding and its coverage; unmeasured stats read —, never 0', () => {
  const text = reportSummary({ ...base, analysis: analysis() });
  const possession = text.split('\n').find((l) => l.startsWith('Possession:'));
  assert.match(possession, /^Possession: 60% – 40%/);
  assert.match(possession, /ball followed at least 72% of play/);
  const shots = text.split('\n').find((l) => l.startsWith('Shots:'));
  assert.equal(shots, 'Shots: — – — (pitch not set up)');
  const tackles = text.split('\n').find((l) => l.startsWith('Tackles:'));
  assert.equal(tackles, 'Tackles: — – — (pitch not set up)');
  // Nothing confirmed yet is "—", not a 0 that reads like a measured none.
  for (const line of text.split('\n').filter((l) => /^(Passes|Won balls|Shots|Tackles):/.test(l))) assert.doesNotMatch(line, /:\s*0\b|– 0\b/);
});

test('withheld possession says why, from the coverage', () => {
  const low = analysis();
  low.stats.coverage.possessionShown = false;
  low.stats.coverage.possessionPercent = 43.3;
  assert.match(reportSummary({ ...base, analysis: low }), /Possession: — – — \(ball followed only 43% of play\)/);
  low.stats.coverage.possessionPercent = 75;
  assert.match(reportSummary({ ...base, analysis: low }), /Possession: — – — \(no team possession established\)/);
});

test('estimates keep ≈ and the range; a partial review is a lower bound', () => {
  const text = reportSummary({ ...base, analysis: analysis({ reviewed: true }) });
  assert.match(text, /Passes: ≈105 \(66–123\) – ≈83 \(51–98\)/);
  assert.match(text, /Accurate passes: 3\+ confirmed – 1\+ confirmed/);
});

test('the score appears only when entered or confirmed in review, and says which', () => {
  const none = reportSummary({ ...base, analysis: analysis() });
  assert.match(none, /Tekkerz v Lions/);
  assert.match(none, /Score not entered/);
  assert.doesNotMatch(none, /\d+–\d+ Lions/);
  const reviewed = reportSummary({ ...base, analysis: analysis({ reviewed: true }) });
  assert.match(reviewed, /Tekkerz 2–1 Lions/);
  assert.match(reviewed, /Goals confirmed in review/);
  const entered = { ...analysis(), enteredScore: [4, 3] };
  const text = reportSummary({ ...base, analysis: entered });
  assert.match(text, /Tekkerz 4–3 Lions/);
  assert.match(text, /Score entered by the team/);
});

test('the text carries no link, job id or owner key', () => {
  const text = reportSummary({ ...base, title: 'Friday 5s', analysis: analysis({ reviewed: true }), matchDate: '12/10/2026' });
  assert.doesNotMatch(text, /http/i);
  assert.doesNotMatch(text, /[a-f0-9]{32}/);
  assert.ok(!text.includes(ID));
});

test('a player-load job prints each kit distance (— when unmeasured) and says the ball was not tracked', () => {
  const pl = analysis();
  for (const t of pl.stats.teams) Object.assign(t, { possession: null, passes: null, passesComplete: null, interceptions: null });
  pl.stats.coverage = { ...pl.stats.coverage, possessionShown: false, possessionPercent: 0, shotsBasis: 'no-ball' };
  const text = reportSummary({ ...base, analysis: pl, ballTracked: false, playerLoad: { metres: [4321.9, null], steps: 1200 } });
  assert.match(text, /Distance: 4321\.9 m – — \(per kit, camera pan removed\)/);
  assert.match(text, /Ball not tracked/);
  assert.doesNotMatch(text, /Possession:|Passes:/);
});

test('a test section is marked as not the full match', () => {
  const text = reportSummary({ ...base, analysis: analysis(), analysedDuration: 20, diagnosticSpan: [90, 110] });
  assert.match(text, /Test section 1:30–1:50, not the full match/);
  assert.doesNotMatch(reportSummary({ ...base, analysis: analysis() }), /Test section/);
});
