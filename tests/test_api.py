import json

import pytest
from fastapi.testclient import TestClient

from lit_agent import skills, store
from lit_agent.api import ReadingRequest, submit
from lit_agent.chat_history import create_session
from lit_agent.jobs import Worker
from lit_agent.webui import app
from conftest import FakeModel


@pytest.fixture
def client():
    # No lifespan worker: deterministic task execution is triggered explicitly in tests.
    return TestClient(app, raise_server_exceptions=False)


def test_health_does_not_leak_credentials(client):
    health = client.get('/api/health').json()
    assert health['ok'] and not health['api_configured']
    assert 'api_key' not in health


def test_profile_round_trip_and_reset(client):
    body = {"background": "CS student", "depth": "advanced", "focus": "implementation",
            "research_domain": "MARL", "compute_budget": "CPU", "framework": "PyTorch", "known_topics": "PPO"}
    assert client.put('/api/profile', json=body).status_code == 200
    assert client.get('/api/profile').json()['focus'] == 'implementation'
    assert client.get('/api/profile').json()['compute_budget'] == 'CPU'
    reset = client.post('/api/profile/reset').json()
    assert reset['focus'] == reset['compute_budget'] == reset['research_domain'] == ''
    assert len(client.get('/api/profile/presets').json()) == 3


def test_reject_cross_origin_write(client):
    assert client.put('/api/profile', json={}, headers={'Origin':'https://evil.example'}).status_code == 403


def test_no_client_controlled_keys_or_context(client):
    assert client.post('/api/chat/runs', json={'message':'hello','action':'chat','api_key':'client-value'}).status_code == 422
    assert client.post('/api/chat/runs', json={'message':'hello','action':'chat','agent_msgs':[]}).status_code == 422


def test_reading_requires_index_and_paper_count(client):
    assert client.post('/api/chat/runs', json={'message':'summary','paper_ids':[999]}).status_code == 409
    assert client.post('/api/chat/runs', json={'message':'compare','skill_id':'compare','paper_ids':[1]}).status_code == 422


def test_skill_catalog_exposes_research_workflows(client):
    skills_response = client.get('/api/skills')
    assert skills_response.status_code == 200
    catalog = {item['id']: item for item in skills_response.json()}
    assert {'overview', 'deep_read', 'compare', 'research_question', 'reproduce',
            'experiment_audit', 'critical_review', 'presentation'} <= catalog.keys()
    assert catalog['reproduce']['min_papers'] == 1
    assert catalog['compare']['min_papers'] == catalog['compare']['max_papers'] == 2


def test_citation_and_pdf_endpoint(client, indexed):
    chunk = store.one('SELECT * FROM chunks LIMIT 1')
    response = client.get('/api/chunks/' + chunk['id'])
    assert response.status_code == 200 and '#page=' in response.json()['pdf_url']
    assert client.get(response.json()['pdf_url'].split('#')[0]).content.startswith(b'%PDF-')
    assert client.get('/api/chunks/not-a-uuid').status_code == 422


def test_upload_returns_durable_task(client, pdf_bytes):
    response = client.post('/api/papers/upload', files={'file':('test.pdf',pdf_bytes,'application/pdf')})
    assert response.status_code == 200
    assert store.get_task(response.json()['task']['id'])['status'] == 'queued'
    assert client.post('/api/papers/upload', files={'file':('test.pdf',b'invalid','application/pdf')}).status_code == 422


def test_reviewed_exports_only_latest_approved_revision(client, indexed, monkeypatch):
    import lit_agent.jobs as jobs
    monkeypatch.setattr(jobs, 'Model', FakeModel)
    task = submit(ReadingRequest(message='Explain', paper_ids=[indexed[0]]))
    Worker().run_once()
    assert store.get_task(task['id'])['status'] == 'succeeded'
    assert client.get('/api/exports/sft').text == ''
    result = store.get_task(task['id'])['result']
    response = client.put(f"/api/tasks/{task['id']}/notes", json={
        'body': result['answer'], 'feedback':'Synthetic fixture reviewed for software testing only.', 'approved':True,
    })
    assert response.status_code == 200
    exported = [json.loads(line) for line in client.get('/api/exports/sft').text.splitlines()]
    assert len(exported) == 1 and exported[0]['reviewed']
    assert exported[0]['model'] == 'synthetic-test-model'
    assert exported[0]['messages'][-1]['role'] == 'assistant'
    assert exported[0]['paper_hashes']
    client.put(f"/api/tasks/{task['id']}/notes", json={'body':result['answer'], 'approved':False})
    assert client.get('/api/exports/sft').text == ''


def test_retry_never_replays_management_write(client):
    task = store.enqueue('chat', {'message':'write'}, create_session())
    with store.connect() as db:
        db.execute("UPDATE tasks SET status='failed' WHERE id=?", (task['id'],))
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409


def test_legacy_session_is_marked_without_fake_tool_history(client):
    session_id = create_session()
    response = client.get(f'/api/sessions/{session_id}').json()
    assert response['legacy_context'] and 'agent_msgs' not in response


def test_sse_replays_persisted_events(client):
    task = store.enqueue('cleanup', {'document_ids':[]})
    with store.connect() as db:
        db.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (task['id'],))
    store.emit(task['id'], 'done', {'answer':'fixture'})
    response = client.get(f"/api/tasks/{task['id']}/events")
    assert response.status_code == 200
    assert 'event: done' in response.text and 'event: terminal' in response.text
    last = store.one("SELECT max(id) AS id FROM task_events WHERE task_id=?", (task['id'],))['id']
    reconnect = client.get(f"/api/tasks/{task['id']}/events", headers={'Last-Event-ID': str(last)})
    assert 'event: done' not in reconnect.text and 'event: terminal' in reconnect.text
