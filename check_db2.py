import sqlite3
conn = sqlite3.connect('data/ftb_store.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('Tables:', [r[0] for r in c.fetchall()])
for table in ['seen_startups', 'startups', 'funding_rounds', 'founders', 'contacts']:
    try:
        c.execute(f'SELECT COUNT(*) FROM {table}')
        print(f'{table} count:', c.fetchone()[0])
    except Exception as e:
        print(f'{table}: {e}')
conn.close()
