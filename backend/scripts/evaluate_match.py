"""Evaluate one analysed match against human labels.

    python scripts/evaluate_match.py JOB_DIR labels.json [--markdown report.md]

JOB_DIR is a worker job folder (result.json, optional calibration.json and
review.json). The analysis is recomputed from those inputs so the report
always matches the current code. Prints JSON; --markdown also writes a
human-readable summary listing every missed and false event with its time.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.metrics import evaluate  # noqa: E402
from app.vision.analytics import analyse  # noqa: E402


def load(path):
    return json.loads(path.read_text()) if path.is_file() else None


def markdown(report):
    lines = [f"# Evaluation: {report.get('match') or 'match'} ({report.get('split') or 'unspecified split'})", ""]
    if report.get("note"):
        lines += [report["note"], ""]
    ball = report.get("ball")
    if ball:
        r, p = ball["recall"], ball["precision"]
        lines += ["## Ball", "", f"- Visible-ball recall: {r['value']} (n={r['n']}, 95% {r['interval95']})",
                  f"- Precision of emitted positions: {p['value']} (n={p['n']}, 95% {p['interval95']})",
                  f"- Wrong place: {ball['wrongPlace']} · false observations: {ball['falseObservations']}", ""]
    for kind, e in (report.get("events") or {}).items():
        lines += [f"## Events: {kind}", "",
                  f"- Precision {e['precision']['value']} (n={e['precision']['n']}) · recall {e['recall']['value']} (n={e['recall']['n']}) · F1 {e['f1']}",
                  f"- Team correct on matched: {e['teamAccuracy']['value']} · mean timing error {e['timingErrorSeconds']} s"]
        if e["missed"]:
            lines.append("- Missed at: " + ", ".join(f"{m['t']:.1f}s" for m in e["missed"]))
        if e["false"]:
            lines.append("- False at: " + ", ".join(f"{m['t']:.1f}s" for m in e["false"]))
        lines.append("")
    pos = report.get("possession")
    if pos:
        lines += ["## Possession", "", f"- Agreement {pos['agreement']['value']} on decided time; coverage {pos['coverage']['value']}",
                  f"- Share: labelled {pos['shareTruth']}% vs predicted {pos['sharePredicted']}% (error {pos['shareErrorPoints']} points)", ""]
    cal = report.get("calibration")
    if cal:
        lines += ["## Calibration", "", f"- Held-out landmarks: median {cal.get('medianMetres')} m, p95 {cal.get('p95Metres')} m (n={cal['n']})", ""]
    rev = report.get("review")
    if rev:
        lines += ["## From the reviewer's decisions", ""]
        for kind, r in rev.items():
            lines.append(f"- {kind}: {r['reviewed']} of {r['automatic']} reviewed, precision {r['precision']['value']}, added by reviewer {r['addedByReviewer']}")
        lines.append("")
    lines.append("Small samples are flagged in the JSON (smallSample). A single match is not a release gate.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    result = load(args.job / "result.json")
    if result is None:
        raise SystemExit("result.json not found")
    calibration = load(args.job / "calibration.json")
    calibration = calibration if calibration and calibration.get("state") == "ready" else None
    review = load(args.job / "review.json")
    analysis = analyse(result, calibration, review)
    report = evaluate(result, analysis, json.loads(args.labels.read_text()), calibration, review)
    print(json.dumps(report, indent=2))
    if args.markdown:
        args.markdown.write_text(markdown(report))


if __name__ == "__main__":
    main()
