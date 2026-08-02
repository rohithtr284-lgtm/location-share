import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATABASE_PATH = BASE_DIR / "locations.db"

ADMIN_KEY = os.getenv("ADMIN_KEY", "")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")

if not ADMIN_KEY:
    raise RuntimeError("ADMIN_KEY is missing from the .env file")


app = FastAPI(
    title="Consent Location Sharing",
    version="1.0.0",
)


class LocationSubmission(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: Optional[float] = Field(default=None, ge=0)
    shared_at: Optional[str] = None


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def create_tables() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS share_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shared_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                accuracy REAL,
                shared_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                FOREIGN KEY (token) REFERENCES share_links(token)
            )
            """
        )

        connection.commit()


def verify_admin_key(x_admin_key: Optional[str]) -> None:
    if not x_admin_key or not secrets.compare_digest(x_admin_key, ADMIN_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin key",
        )


@app.on_event("startup")
def startup_event() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    create_tables()


@app.get("/")
def home():
    return {
        "message": "Consent location-sharing service is running",
        "admin_page": f"{BASE_URL}/admin",
    }


@app.get("/share/{token}")
def share_page(token: str):
    with get_connection() as connection:
        link = connection.execute(
            """
            SELECT token, is_active
            FROM share_links
            WHERE token = ?
            """,
            (token,),
        ).fetchone()

    if not link or not link["is_active"]:
        raise HTTPException(
            status_code=404,
            detail="This location-sharing link is invalid or inactive",
        )

    return FileResponse(STATIC_DIR / "share.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.post("/api/links")
def create_share_link(
    x_admin_key: Optional[str] = Header(default=None),
):
    verify_admin_key(x_admin_key)

    token = secrets.token_urlsafe(32)
    created_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO share_links (token, created_at, is_active)
            VALUES (?, ?, 1)
            """,
            (token, created_at),
        )
        connection.commit()

    return {
        "token": token,
        "share_url": f"{BASE_URL}/share/{token}",
        "created_at": created_at,
    }


@app.post("/api/location")
def save_location(data: LocationSubmission):
    with get_connection() as connection:
        link = connection.execute(
            """
            SELECT token, is_active
            FROM share_links
            WHERE token = ?
            """,
            (data.token,),
        ).fetchone()

        if not link or not link["is_active"]:
            raise HTTPException(
                status_code=404,
                detail="This sharing link is invalid or inactive",
            )

        received_at = datetime.now(timezone.utc).isoformat()
        shared_at = data.shared_at or received_at

        connection.execute(
            """
            INSERT INTO shared_locations (
                token,
                latitude,
                longitude,
                accuracy,
                shared_at,
                received_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                data.token,
                data.latitude,
                data.longitude,
                data.accuracy,
                shared_at,
                received_at,
            ),
        )

        connection.commit()

    return {
        "success": True,
        "message": "Location shared successfully",
    }


@app.get("/api/locations")
def get_locations(
    x_admin_key: Optional[str] = Header(default=None),
):
    verify_admin_key(x_admin_key)

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                token,
                latitude,
                longitude,
                accuracy,
                shared_at,
                received_at
            FROM shared_locations
            ORDER BY id DESC
            """
        ).fetchall()

    return {
        "locations": [
            {
                "id": row["id"],
                "token": row["token"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "accuracy": row["accuracy"],
                "shared_at": row["shared_at"],
                "received_at": row["received_at"],
                "google_maps_url": (
                    f"https://www.google.com/maps?q="
                    f"{row['latitude']},{row['longitude']}"
                ),
            }
            for row in rows
        ]
    }


@app.patch("/api/links/{token}/disable")
def disable_link(
    token: str,
    x_admin_key: Optional[str] = Header(default=None),
):
    verify_admin_key(x_admin_key)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE share_links
            SET is_active = 0
            WHERE token = ?
            """,
            (token,),
        )
        connection.commit()

    if cursor.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="Sharing link not found",
        )

    return {
        "success": True,
        "message": "Sharing link disabled",
    }
