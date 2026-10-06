"""Private browser workspace: bearer cookie is hashed before database storage."""
import hashlib
import secrets
from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from database.postgres_sql import get_engine

router=APIRouter(tags=['notebook'])

class Note(BaseModel):
    company:str=Field(min_length=1,max_length=100)
    note:str=Field(default='',max_length=5000)
    pinned:bool=False

def owner(request,response):
    token=request.cookies.get('research_workspace')
    if not token or len(token)!=64:
        token=secrets.token_hex(32)
        response.set_cookie('research_workspace',token,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=31536000)
    return hashlib.sha256(token.encode()).hexdigest()

@router.get('/notes')
def read_notes(request:Request,response:Response):
    identity=owner(request,response)
    try:
        with get_engine().begin() as conn:
            conn.execute(text('''CREATE TABLE IF NOT EXISTS private_notes (
                owner text NOT NULL,company text NOT NULL,note text NOT NULL DEFAULT '',
                pinned boolean NOT NULL DEFAULT false,PRIMARY KEY(owner,company))'''))
            conn.execute(text('ALTER TABLE private_notes ENABLE ROW LEVEL SECURITY'))
            rows=conn.execute(text('SELECT company,note,pinned FROM private_notes WHERE owner=:owner'),{'owner':identity}).mappings().all()
            return {'data':[dict(r) for r in rows]}
    except Exception:raise HTTPException(503,'Private notebook database unavailable.') from None

@router.post('/notes')
def save_note(note:Note,request:Request,response:Response):
    if not request.cookies.get('research_workspace'):
        raise HTTPException(401,'Open the notebook before saving a note.')
    identity=owner(request,response)
    try:
        with get_engine().begin() as conn:
            conn.execute(text('''INSERT INTO private_notes (owner,company,note,pinned) VALUES (:owner,:company,:note,:pinned)
              ON CONFLICT(owner,company) DO UPDATE SET note=excluded.note,pinned=excluded.pinned'''),{'owner':identity,**note.model_dump()})
        return {'success':True}
    except Exception:raise HTTPException(503,'Could not save the private note.') from None
