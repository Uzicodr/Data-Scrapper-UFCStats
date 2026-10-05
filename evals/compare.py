"""Compare extracted stats to ground truth. Measure accuracy."""
import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class AccuracyMetrics:
    """Accuracy metrics for a single event."""
    event_name: str
    total_fields: int
    correct_fields: int
    invented_fields: int  # Values that don't exist in ground truth
    missing_fields: int   # Ground truth values not extracted
    accuracy: float
    invented_rate: float  # invented / total


def compare_stats(extracted: dict, ground_truth: dict) -> AccuracyMetrics:
    """Compare extracted stats dict to ground truth.

    Ground truth has keys like "sig_strikes_landed": 42
    Extracted may have same structure or be partial.
    """
    total_fields = 0
    correct_fields = 0
    invented_fields = 0
    missing_fields = 0

    # Check extracted fields
    for key, extracted_val in extracted.items():
        total_fields += 1
        if key not in ground_truth:
            invented_fields += 1
        elif ground_truth[key] == extracted_val:
            correct_fields += 1

    # Check missing fields
    for key in ground_truth:
        if key not in extracted:
            total_fields += 1
            missing_fields += 1

    accuracy = correct_fields / total_fields if total_fields > 0 else 0
    invented_rate = invented_fields / total_fields if total_fields > 0 else 0

    return AccuracyMetrics(
        event_name="",
        total_fields=total_fields,
        correct_fields=correct_fields,
        invented_fields=invented_fields,
        missing_fields=missing_fields,
        accuracy=accuracy,
        invented_rate=invented_rate
    )


def analyze_eval_results(results_file: Path) -> dict:
    """Analyze eval results file. Return aggregate metrics and per-event breakdown."""
    with open(results_file) as f:
        results = json.load(f)

    events = results.get("results", [])
    accuracies = []

    for event in events:
        if "error" in event:
            continue
        # TODO: compare to ground truth when we have it
        # For now just return submission counts
        accuracies.append({
            "event": event["event"],
            "stats_submitted": event.get("stats_submitted", 0),
            "issues_flagged": event.get("issues_flagged", 0),
        })

    total_accuracy = sum(m.get("stats_submitted", 0) for m in accuracies) / len(accuracies) if accuracies else 0

    return {
        "file": str(results_file),
        "timestamp": results.get("timestamp"),
        "events_evaluated": len(accuracies),
        "aggregate_accuracy": total_accuracy,
        "per_event": accuracies,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        results_file = Path(sys.argv[1])
        analysis = analyze_eval_results(results_file)
        print(json.dumps(analysis, indent=2))
