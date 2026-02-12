import os
import time
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
from jose import jwt

from config import get as cfg_get

SECRET = cfg_get("admin.jwt_secret", "change_me")
ADMIN_PASS = cfg_get("admin.admin_pass", "change_me")
JWT_EXPIRE_SECONDS = int(cfg_get("admin.jwt_expire_seconds", 7 * 24 * 3600))

security = HTTPBearer()

def login(password: str):
    if password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="invalid password")
    return jwt.encode({"user": "admin", "exp": int(time.time()) + JWT_EXPIRE_SECONDS}, SECRET)

def auth_required(token=Depends(security)):
    try:
        jwt.decode(token.credentials, SECRET)
    except Exception:
        raise HTTPException(status_code=401, detail="unauthorized")
