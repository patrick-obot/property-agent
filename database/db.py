import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
from config import DATABASE_PATH

# Avoid circular import — Property is imported lazily inside upsert_property


def get_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                max_price REAL,
                min_price REAL,
                location_keywords TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS seen_listings (
                listing_hash TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS properties (
                property_hash TEXT PRIMARY KEY,
                sale_date TEXT NOT NULL,
                property_number INTEGER,
                raw_text TEXT,
                size_m2 REAL,
                reserve_price REAL,
                reserve_type TEXT,
                pdf_url TEXT,
                first_seen_at TEXT NOT NULL
            )
        """)


# --- User CRUD ---

def upsert_user(telegram_id: int, chat_id: int, username: str | None):
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (telegram_id, chat_id, username, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                username = excluded.username
            """,
            (telegram_id, chat_id, username, datetime.utcnow().isoformat()),
        )


def get_all_users() -> list[sqlite3.Row]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM users")
        return cur.fetchall()


# --- Preferences CRUD ---

def upsert_preference(telegram_id: int, min_price: float | None = None,
                      max_price: float | None = None,
                      location_keywords: list[str] | None = None):
    keywords_json = json.dumps(location_keywords) if location_keywords is not None else None
    with db_cursor() as cur:
        existing = cur.execute(
            "SELECT id FROM preferences WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if existing:
            cur.execute(
                """
                UPDATE preferences
                SET min_price = COALESCE(?, min_price),
                    max_price = COALESCE(?, max_price),
                    location_keywords = COALESCE(?, location_keywords),
                    active = 1
                WHERE telegram_id = ?
                """,
                (min_price, max_price, keywords_json, telegram_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO preferences (telegram_id, min_price, max_price, location_keywords, active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (telegram_id, min_price, max_price, keywords_json),
            )


def get_preference(telegram_id: int) -> dict | None:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT * FROM preferences WHERE telegram_id = ? AND active = 1",
            (telegram_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result.get("location_keywords"):
            result["location_keywords"] = json.loads(result["location_keywords"])
        return result


def get_all_active_preferences() -> list[dict]:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM preferences WHERE active = 1"
        ).fetchall()
        prefs = []
        for row in rows:
            pref = dict(row)
            if pref.get("location_keywords"):
                pref["location_keywords"] = json.loads(pref["location_keywords"])
            prefs.append(pref)
        return prefs


def clear_preference(telegram_id: int):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE preferences SET active = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )


# --- Properties CRUD ---

def upsert_property(prop) -> bool:
    """
    Insert a property record if it does not already exist.
    Returns True if newly inserted, False if already present.
    """
    h = prop.property_hash()
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT OR IGNORE INTO properties
                (property_hash, sale_date, property_number, raw_text,
                 size_m2, reserve_price, reserve_type, pdf_url, first_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                h,
                prop.sale_date,
                prop.number,
                prop.raw_text,
                prop.size_m2,
                prop.reserve_price,
                prop.reserve_type,
                prop.pdf_url,
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.rowcount > 0


def get_upcoming_properties(today_iso: str) -> list[dict]:
    """Return all properties from DB with sale_date >= today, ordered by date and number."""
    with db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT * FROM properties
            WHERE sale_date >= ?
            ORDER BY sale_date, property_number
            """,
            (today_iso,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_sale_dates_in_db(today_iso: str) -> set[str]:
    """Return the set of sale_dates already stored in the properties table."""
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT DISTINCT sale_date FROM properties WHERE sale_date >= ?",
            (today_iso,),
        ).fetchall()
        return {r[0] for r in rows}


# --- Seen Listings CRUD ---

def is_listing_seen(listing_hash: str) -> bool:
    with db_cursor() as cur:
        row = cur.execute(
            "SELECT 1 FROM seen_listings WHERE listing_hash = ?", (listing_hash,)
        ).fetchone()
        return row is not None


def mark_listing_seen(listing_hash: str):
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT OR IGNORE INTO seen_listings (listing_hash, first_seen_at)
            VALUES (?, ?)
            """,
            (listing_hash, datetime.utcnow().isoformat()),
        )
