"""
migrate_to_postgres.py
----------------------
One-time script: copies all rows from the local SQLite neurostamp.db
into the Neon PostgreSQL database defined by DATABASE_URL in .env.

Run once:
    python migrate_to_postgres.py
"""

from dotenv import load_dotenv
load_dotenv()

import sqlite3, os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "neurostamp.db")

# ── Import models & pg engine ────────────────────────────────────────────────
from src.database import engine as pg_engine, Base, User, ImageRegistry, init_db

# ── Ensure tables exist on Postgres ─────────────────────────────────────────
print("🔧  Creating tables on PostgreSQL (if not present)…")
init_db()
print("    ✅  Tables ready.\n")

# ── Open SQLite ───────────────────────────────────────────────────────────────
if not os.path.exists(SQLITE_PATH):
    print(f"❌  SQLite file not found at: {SQLITE_PATH}")
    exit(1)

sqlite_conn = sqlite3.connect(SQLITE_PATH)
sqlite_conn.row_factory = sqlite3.Row
cur = sqlite_conn.cursor()

PgSession = sessionmaker(bind=pg_engine)
pg = PgSession()

# ── Migrate users ─────────────────────────────────────────────────────────────
print("👤  Migrating users…")
cur.execute("SELECT * FROM users")
users = cur.fetchall()
migrated_users = 0
skipped_users  = 0

for row in users:
    exists = pg.query(User).filter(User.user_uid == row["user_uid"]).first()
    if exists:
        skipped_users += 1
        continue
    pg.add(User(
        id=row["id"],
        username=row["username"],
        hashed_password=row["hashed_password"],
        user_uid=row["user_uid"],
        encrypted_key_data=row["encrypted_key_data"],
    ))
    migrated_users += 1

pg.commit()
print(f"    ✅  {migrated_users} migrated, {skipped_users} already existed.\n")

# ── Migrate image_registry ────────────────────────────────────────────────────
print("🖼️   Migrating image registry…")
cur.execute("SELECT * FROM image_registry")
rows = cur.fetchall()
migrated_imgs = 0
skipped_imgs  = 0

for row in rows:
    exists = pg.query(ImageRegistry).filter(ImageRegistry.image_hash == row["image_hash"]).first()
    if exists:
        skipped_imgs += 1
        continue
    pg.add(ImageRegistry(
        id=row["id"],
        image_hash=row["image_hash"],
        owner_uid=row["owner_uid"],
        original_width=row["original_width"],
        original_height=row["original_height"],
    ))
    migrated_imgs += 1

pg.commit()
print(f"    ✅  {migrated_imgs} migrated, {skipped_imgs} already existed.\n")

# Sync PostgreSQL sequences so auto-increment IDs don't collide with migrated IDs
with pg_engine.connect() as conn:
    conn.execute(text("SELECT setval('users_id_seq', (SELECT MAX(id) FROM users))"))
    conn.execute(text("SELECT setval('image_registry_id_seq', (SELECT MAX(id) FROM image_registry))"))
    conn.commit()

sqlite_conn.close()
pg.close()
print("🎉  Migration complete!")
