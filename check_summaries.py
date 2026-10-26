import sqlite3
from datetime import date

conn = sqlite3.connect('users.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Check schema
cursor.execute("PRAGMA table_info(daily_summary_cards);")
columns = cursor.fetchall()
print("Columns in daily_summary_cards:")
for col in columns:
    print(f"  - {col['name']}")

print("\n" + "="*60 + "\n")

today = date.today().isoformat()
cursor.execute('SELECT user_id, subject, topic, summary_date FROM daily_summary_cards WHERE summary_date = ? ORDER BY user_id, subject LIMIT 5', (today,))
rows = cursor.fetchall()

print(f'Daily summary cards created today ({today}):')
print(f'Total: {len(rows)} cards\n')
for row in rows:
    print(f'  User {row["user_id"]}: {row["subject"]} | {row["topic"]}')

conn.close()



