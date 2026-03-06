"""Firestore-relaterte hjelpefunksjoner for abonnentflyten."""

from __future__ import annotations

from typing import Any

import requests

PROJECT_ID = "fpl-ai-analyzer"


def subscribers_collection_url(project_id: str = PROJECT_ID) -> str:
    return (
        f"https://firestore.googleapis.com/v1/projects/{project_id}"
        f"/databases/(default)/documents/subscribers"
    )


def parse_team_id(team_id_field: dict[str, Any]) -> int:
    """Firestore team_id kan komme som integerValue eller stringValue."""
    if 'integerValue' in team_id_field:
        return int(team_id_field['integerValue'])
    if 'stringValue' in team_id_field:
        try:
            return int(team_id_field['stringValue'])
        except ValueError:
            return 0
    return 0


def parse_subscriber_document(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Parse ett Firestore-dokument til intern subscriber-struktur."""
    fields = doc.get('fields', {})
    doc_id = doc.get('name', '').split('/')[-1]

    name = fields.get('name', {}).get('stringValue', '')
    email = fields.get('email', {}).get('stringValue', '')
    welcome_sent = fields.get('welcome_sent', {}).get('booleanValue', False)
    team_id = parse_team_id(fields.get('team_id', {}))

    if not (name and email and team_id > 0):
        return None

    return {
        'name': name,
        'email': email,
        'team_id': team_id,
        'doc_id': doc_id,
        'welcome_sent': welcome_sent,
    }


def split_subscribers(documents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Del parsed abonnenter i alle og nye."""
    subscribers: list[dict[str, Any]] = []
    new_subscribers: list[dict[str, Any]] = []

    for doc in documents:
        parsed = parse_subscriber_document(doc)
        if not parsed:
            continue

        public_subscriber = {
            'name': parsed['name'],
            'email': parsed['email'],
            'team_id': parsed['team_id'],
            'doc_id': parsed['doc_id'],
        }
        subscribers.append(public_subscriber)

        if not parsed['welcome_sent']:
            new_subscribers.append(public_subscriber)

    return subscribers, new_subscribers


def mark_welcome_sent(doc_id: str, project_id: str = PROJECT_ID, timeout: int = 30) -> bool:
    """Oppdater ett Firestore-dokument med welcome_sent=true."""
    url = (
        f"{subscribers_collection_url(project_id)}/{doc_id}"
        f"?updateMask.fieldPaths=welcome_sent"
    )
    payload = {"fields": {"welcome_sent": {"booleanValue": True}}}

    response = requests.patch(url, json=payload, timeout=timeout)
    return response.status_code == 200
