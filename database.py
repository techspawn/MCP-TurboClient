import sqlite3

# Connect to SQLite
conn = sqlite3.connect("mcp_config.db")
cursor = conn.cursor()

# Create table if not exists
cursor.execute("""
CREATE TABLE IF NOT EXISTS config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    license_key TEXT NOT NULL
);
""")

# Commit & close
conn.commit()
conn.close()
