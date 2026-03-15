import pytest

import agent_data
from pipeline_shared.firestore import firestore_headers


class _Resp:
    def __init__(self, status_code, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_hent_abonnenter_returns_false_on_firestore_http_error(monkeypatch):
    monkeypatch.setattr(
        agent_data.requests,
        'get',
        lambda *args, **kwargs: _Resp(403, text='permission denied'),
    )

    subscribers, new_subscribers, ok = agent_data.hent_abonnenter()

    assert ok is False
    assert subscribers == []
    assert new_subscribers == []


def test_hent_abonnenter_returns_true_on_valid_payload(monkeypatch):
    payload = {
        'documents': [
            {
                'name': 'projects/x/databases/(default)/documents/subscribers/abc',
                'fields': {
                    'name': {'stringValue': 'Tester'},
                    'email': {'stringValue': 'tester@example.com'},
                    'team_id': {'integerValue': '123'},
                    'welcome_sent': {'booleanValue': False},
                },
            }
        ]
    }
    monkeypatch.setattr(agent_data.requests, 'get', lambda *args, **kwargs: _Resp(200, payload=payload))

    subscribers, new_subscribers, ok = agent_data.hent_abonnenter()

    assert ok is True
    assert len(subscribers) == 1
    assert len(new_subscribers) == 1


def test_firestore_headers_with_token(monkeypatch):
    monkeypatch.setenv('FIREBASE_BEARER_TOKEN', 'abc123')
    monkeypatch.setenv('FIREBASE_REQUIRE_AUTH', 'false')

    headers = firestore_headers()

    assert headers == {'Authorization': 'Bearer abc123'}


def test_firestore_headers_raises_when_auth_required_but_missing(monkeypatch):
    monkeypatch.delenv('FIREBASE_BEARER_TOKEN', raising=False)
    monkeypatch.setenv('FIREBASE_REQUIRE_AUTH', 'true')

    with pytest.raises(RuntimeError):
        firestore_headers()
