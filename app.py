import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

CFG = json.loads(Path(__file__).with_name("config.json").read_text())
THRESHOLD = CFG["threshold"]

class RecordIn(BaseModel):
    location: str = Field(min_length=2,max_length=120)
    value: float = Field(ge=0,le=1000000,allow_inf_nan=False)
    note: str = Field(default="",max_length=1500)

class ReviewIn(BaseModel):
    status: str
    comment: str = Field(min_length=3,max_length=1000)

def create_app(path=None):
    app=FastAPI(title=CFG["project"]+" API")
    database=str(path or os.getenv("DATA_FILE","app.sqlite3"))
    lock=Lock()
    @contextmanager
    def db():
        con=sqlite3.connect(database,timeout=10)
        con.row_factory=sqlite3.Row
        try: yield con
        finally: con.close()
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, location TEXT NOT NULL, value REAL NOT NULL, note TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, record_id TEXT NOT NULL, status TEXT NOT NULL, comment TEXT NOT NULL, created_at TEXT NOT NULL)")
        con.commit()
    def detail(identifier):
        with db() as con:
            row=con.execute("SELECT * FROM records WHERE id=?",(identifier,)).fetchone()
            if not row: raise HTTPException(404,"Record not found")
            events=con.execute("SELECT status,comment,created_at FROM events WHERE record_id=? ORDER BY rowid",(identifier,)).fetchall()
            return {**dict(row),"alert":row["value"]>THRESHOLD,"history":[dict(e) for e in events]}
    @app.get("/")
    def home(): return FileResponse(Path(__file__).with_name("index.html"))
    @app.get("/api/health")
    def health(): return {"status":"ok"}
    @app.get("/api/config")
    def config(): return CFG
    @app.get("/api/records")
    def records(status: str|None=None):
        if status and status not in ("new","investigating","resolved"): raise HTTPException(422,"Bad status")
        with db() as con:
            q="SELECT * FROM records"+(" WHERE status=?" if status else "")+" ORDER BY rowid DESC"
            rows=con.execute(q,(status,) if status else ()).fetchall()
            return [{**dict(r),"alert":r["value"]>THRESHOLD} for r in rows]
    @app.get("/api/records/{identifier}")
    def get(identifier:str): return detail(identifier)
    @app.get("/api/metrics")
    def metrics():
        with db() as con: rows=con.execute("SELECT value,status FROM records").fetchall()
        return {"total":len(rows),"alerts":sum(r["value"]>THRESHOLD for r in rows),"open":sum(r["status"]!="resolved" for r in rows),"average":round(sum(r["value"] for r in rows)/len(rows),2) if rows else 0}
    @app.post("/api/records",status_code=201)
    def create(payload:RecordIn):
        identifier=str(uuid4())
        when=datetime.now(timezone.utc).isoformat()
        with lock,db() as con:
            con.execute("INSERT INTO records VALUES (?,?,?,?,?,?)",(identifier,payload.location.strip(),payload.value,payload.note,"new",when))
            con.execute("INSERT INTO events VALUES (?,?,?,?,?)",(str(uuid4()),identifier,"new","Recorded",when))
            con.commit()
        return detail(identifier)
    @app.post("/api/records/{identifier}/review")
    def review(identifier:str,payload:ReviewIn):
        if payload.status not in ("investigating","resolved"): raise HTTPException(422,"Invalid state")
        with lock,db() as con:
            row=con.execute("SELECT status FROM records WHERE id=?",(identifier,)).fetchone()
            if not row: raise HTTPException(404,"Record not found")
            if row["status"]=="resolved": raise HTTPException(409,"Closed record")
            when=datetime.now(timezone.utc).isoformat()
            con.execute("UPDATE records SET status=? WHERE id=?",(payload.status,identifier))
            con.execute("INSERT INTO events VALUES (?,?,?,?,?)",(str(uuid4()),identifier,payload.status,payload.comment,when))
            con.commit()
        return detail(identifier)
    return app

app=create_app()
