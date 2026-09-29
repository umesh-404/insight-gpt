import json
import sys
import httpx

API_BASE = "http://localhost:8000/api/v1"

print("1. Logging in...")
login_resp = httpx.post(
    f"{API_BASE}/auth/login",
    json={"email": "admin@insightgpt.dev", "password": "admin-pass"},
    timeout=10.0,
)
if login_resp.status_code != 200:
    print("Login failed:", login_resp.text)
    sys.exit(1)

token = login_resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

question = "Why did sales decline last quarter?"
print(f"\n2. Asking Question: '{question}'")
print("-" * 60)

full_answer = []
tables = []
sql_queries = []
citations = []
chart = None
current_event = None

with httpx.Client(timeout=300.0) as client:
    with client.stream("POST", f"{API_BASE}/ask", headers=headers, json={"question": question}) as response:
        for line in response.iter_lines():
            if not line:
                continue
            line = line.strip()
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: "):
                raw_data = line[6:].strip()
                try:
                    data = json.loads(raw_data)
                except Exception:
                    continue

                if current_event == "token":
                    chunk = data.get("text", "")
                    full_answer.append(chunk)
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                elif current_event == "sql":
                    sql_queries.append(data.get("sql", ""))
                elif current_event == "tables":
                    tables.append(data)
                elif current_event == "citations":
                    citations.extend(data.get("items", []))
                elif current_event == "chart":
                    chart = data.get("chart_spec")

print("\n" + "=" * 60)
print("\n--- RESULTS OVERVIEW ---")
print(f"Total SQL Queries Executed: {len(sql_queries)}")
for i, s in enumerate(sql_queries, 1):
    first_line = s.strip().splitlines()[0]
    print(f"  SQL #{i}: {first_line}")

print(f"\nTables Produced: {len(tables)}")
for t in tables:
    print(f"\n[Table: {t.get('name')}]")
    cols = t.get("columns", [])
    rows = t.get("rows", [])
    print(" | ".join(cols))
    print("-" * 40)
    for r in rows:
        print(" | ".join(str(val) for val in r))

print(f"\nEvidence Citations: {len(citations)}")
for c in citations:
    print(f"  [{c.get('n')}] {c.get('title')} ({c.get('source_type')}) - Date: {c.get('date')}")

if chart:
    print(f"\nChart Generated: Type={chart.get('type')}, X={chart.get('x')}")
