import json
from pathlib import Path

DATASET_PATH = Path(__file__).parent / "data" / "evaluation_dataset.json"


def test_evaluation_dataset_structure():
    """Validates the count, language distribution, and schema of the evaluation dataset."""
    assert DATASET_PATH.exists(), f"Evaluation dataset not found at {DATASET_PATH}"

    with open(DATASET_PATH, encoding="utf-8") as f:
        data = json.load(f)

    # 1. Total count: exactly 70 questions
    assert len(data) == 70, f"Expected 70 benchmark questions, found {len(data)}"

    # 2. Count by language group
    french_q = [q for q in data if q["language_group"] == "french"]
    fusha_q = [q for q in data if q["language_group"] == "fusha"]
    derja_arabic_q = [q for q in data if q["language_group"] == "derja_arabic"]
    arabizi_q = [q for q in data if q["language_group"] == "arabizi"]
    trap_q = [q for q in data if q["language_group"].startswith("trap_")]

    assert len(french_q) == 15, f"Expected 15 French questions, got {len(french_q)}"
    assert len(fusha_q) == 15, f"Expected 15 Fusha questions, got {len(fusha_q)}"
    assert (
        len(derja_arabic_q) == 15
    ), f"Expected 15 Derja (Arabic script) questions, got {len(derja_arabic_q)}"
    assert len(arabizi_q) == 15, f"Expected 15 Arabizi questions, got {len(arabizi_q)}"
    assert len(trap_q) == 10, f"Expected 10 Trap/Out-of-corpus questions, got {len(trap_q)}"

    # 3. Check required fields for all items
    required_keys = {
        "id",
        "language_group",
        "query",
        "intent",
        "target_school",
        "expected_language",
        "ground_truth",
        "evaluation_criteria",
    }
    for item in data:
        assert required_keys.issubset(
            item.keys()
        ), f"Item {item.get('id')} missing keys: {required_keys - set(item.keys())}"
        assert item["query"].strip() != "", f"Empty query in {item.get('id')}"
        assert (
            item["ground_truth"].strip() != ""
        ), f"Empty ground truth in {item.get('id')}"
