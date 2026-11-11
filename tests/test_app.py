import pytest
from fastapi.testclient import TestClient
from app import CFG, create_app

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/"test.db")) as c: yield c

def record(c,value=1):
    return c.post("/api/records",json={"location":"Sample Site","value":value,"note":"sample"})

def test_health_and_ui(client):
    assert client.get("/api/health").json()=={"status":"ok"}
    assert CFG["project"] in client.get("/").text

def test_alerts_metrics(client):
    r=record(client,CFG["threshold"]+1)
    assert r.status_code==201 and r.json()["alert"]
    assert client.get("/api/metrics").json()["alerts"]==1

def test_review_and_history(client):
    rid=record(client).json()["id"]
    url="/api/records/"+rid+"/review"
    assert client.post(url,json={"status":"resolved","comment":"Issue reviewed"}).status_code==200
    assert len(client.get("/api/records/"+rid).json()["history"])==2
    assert client.post(url,json={"status":"investigating","comment":"New review"}).status_code==409

def test_invalid(client):
    assert record(client,-1).status_code==422
    assert client.get("/api/records?status=bad").status_code==422
    assert client.get("/api/records/missing").status_code==404
    assert client.post("/api/records/missing/review",json={"status":"resolved","comment":"not found"}).status_code==404

def test_filter(client):
    record(client)
    assert len(client.get("/api/records?status=new").json())==1
    assert client.get("/api/records?status=resolved").json()==[]
