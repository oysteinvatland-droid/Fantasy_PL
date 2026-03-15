import json
from pathlib import Path

import pytest

from agent_levering import send_rapporter
from pipeline_shared.firestore import split_subscribers


REQUIRED_REPORT_KEYS = {"name", "email", "team_id", "report_file"}


def _validate_generated_reports_structure(reports):
    assert isinstance(reports, list)
    for item in reports:
        assert REQUIRED_REPORT_KEYS.issubset(item.keys())
        assert isinstance(item["name"], str) and item["name"]
        assert isinstance(item["email"], str) and "@" in item["email"]
        assert isinstance(item["team_id"], int) and item["team_id"] > 0
        assert isinstance(item["report_file"], str) and item["report_file"]


def test_generated_reports_json_structure(tmp_path: Path):
    reports_payload = [
        {
            "name": "Ola Nordmann",
            "email": "ola@example.com",
            "team_id": 123456,
            "report_file": "reports/Ola_Nordmann_123456.html",
        }
    ]
    manifest = tmp_path / "generated_reports.json"
    manifest.write_text(json.dumps(reports_payload), encoding="utf-8")

    loaded = json.loads(manifest.read_text(encoding="utf-8"))
    _validate_generated_reports_structure(loaded)


class _FakeSMTP:
    def __init__(self):
        self.sendmail_calls = []
        self.closed = False

    def sendmail(self, sender, recipient, message):
        self.sendmail_calls.append((sender, recipient, message))

    def quit(self):
        self.closed = True


def test_send_reports_sends_one_email_per_report(monkeypatch, tmp_path: Path):
    smtp = _FakeSMTP()

    def _fake_connect():
        return smtp, "test@example.com"

    monkeypatch.setattr("agent_levering.koble_til_smtp", _fake_connect)

    report_a = tmp_path / "a.html"
    report_b = tmp_path / "b.html"
    report_a.write_text("<html>A</html>", encoding="utf-8")
    report_b.write_text("<html>B</html>", encoding="utf-8")

    reports = [
        {"name": "A", "email": "a@example.com", "report_file": str(report_a)},
        {"name": "B", "email": "b@example.com", "report_file": str(report_b)},
    ]

    sent_count, fail_count = send_rapporter(reports, "Testemne")

    assert sent_count == 2
    assert fail_count == 0
    assert len(smtp.sendmail_calls) == 2
    assert smtp.closed is True


@pytest.mark.parametrize(
    "team_field, expected_team_id",
    [
        ({"integerValue": "42"}, 42),
        ({"stringValue": "99"}, 99),
        ({"stringValue": "ikke-heltall"}, 0),
    ],
)
def test_firestore_subscriber_parsing_contract(team_field, expected_team_id):
    docs = [
        {
            "name": "projects/p/databases/(default)/documents/subscribers/doc1",
            "fields": {
                "name": {"stringValue": "Test"},
                "email": {"stringValue": "test@example.com"},
                "team_id": team_field,
                "welcome_sent": {"booleanValue": False},
            },
        }
    ]

    subscribers, new_subscribers = split_subscribers(docs)

    if expected_team_id > 0:
        assert subscribers == [
            {
                "name": "Test",
                "email": "test@example.com",
                "team_id": expected_team_id,
                "doc_id": "doc1",
            }
        ]
        assert new_subscribers == subscribers
    else:
        assert subscribers == []
        assert new_subscribers == []
