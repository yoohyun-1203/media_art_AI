import unittest
from pathlib import Path

import e2e_performance


class DatasetParsingTests(unittest.TestCase):
    def test_crema_filename_label_parser(self):
        item = e2e_performance.dataset_item_from_path(Path("1001_DFA_HAP_XX.wav"))

        self.assertEqual(item.label, "happy")
        self.assertEqual(item.dataset, "crema-d")

    def test_ravdess_filename_label_parser(self):
        item = e2e_performance.dataset_item_from_path(Path("03-01-05-02-02-01-12.wav"))

        self.assertEqual(item.label, "angry")
        self.assertEqual(item.dataset, "ravdess")

    def test_manifest_row_preserves_expected_label(self):
        item = e2e_performance.dataset_item_from_manifest_row(
            {
                "path": "samples/example.wav",
                "label": "sad",
                "dataset": "custom",
            },
            base_dir=Path("bench"),
        )

        self.assertEqual(item.path, Path("bench") / "samples" / "example.wav")
        self.assertEqual(item.label, "sad")
        self.assertEqual(item.dataset, "custom")


class MetricTests(unittest.TestCase):
    def test_serial_payload_uses_existing_protocol(self):
        self.assertEqual(e2e_performance.serial_payload(0.25, -0.5), "v,0.250,-0.500")

    def test_summarize_results_reports_latency_and_accuracy(self):
        results = [
            {
                "label": "happy",
                "predicted_label": "happy",
                "first_response_ms": 80.0,
                "duration_ms": 1000.0,
                "payload_count": 20,
            },
            {
                "label": "sad",
                "predicted_label": "happy",
                "first_response_ms": 120.0,
                "duration_ms": 1000.0,
                "payload_count": 10,
            },
        ]

        summary = e2e_performance.summarize_results(results)

        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["emotion_accuracy"], 0.5)
        self.assertEqual(summary["first_response_ms_avg"], 100.0)
        self.assertEqual(summary["payload_rate_hz_avg"], 15.0)

    def test_summarize_results_marks_accuracy_unavailable_without_predictions(self):
        summary = e2e_performance.summarize_results([
            {
                "label": "happy",
                "predicted_label": "unknown",
                "first_response_ms": None,
                "duration_ms": 1000.0,
                "payload_count": 0,
            }
        ])

        self.assertIsNone(summary["emotion_accuracy"])
        self.assertEqual(summary["accuracy_note"], "unavailable")


if __name__ == "__main__":
    unittest.main()
