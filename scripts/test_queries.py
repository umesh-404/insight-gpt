import json
import httpx

with httpx.Client(timeout=180.0) as client:
    login = client.post(
        "http://localhost:8000/api/v1/auth/login",
        json={"email": "admin@insightgpt.dev", "password": "admin-pass"},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    questions = [
        "What was the total revenue by region?",
        "Which product category had the highest sales?",
        "Why did sales decline last quarter?",
    ]

    for q in questions:
        print("=" * 70)
        print(f"QUESTION: {q}")
        full_text = ""
        tables = []
        sql = []
        citations = []
        current_event = None

        with client.stream("POST", "http://localhost:8000/api/v1/ask", headers=headers, json={"question": q}) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                line_str = line.strip()
                if line_str.startswith("event: "):
                    current_event = line_str[7:].strip()
                elif line_str.startswith("data: "):
                    data_str = line_str[6:].strip()
                    try:
                        data = json.loads(data_str)
                    except Exception:
                        continue

                    if current_event == "token":
                        full_text += data.get("text", "")
                    elif current_event == "sql":
                        sql.append(data.get("sql", ""))
                    elif current_event == "tables":
                        tables.append(data)
                    elif current_event == "citations":
                        citations = data.get("items", [])

        print(f"ANSWER:\n{full_text.strip()}\n")
        if tables:
            print(f"DATA TABLES ({len(tables)}):")
            for t in tables:
                print(f"  - Name: {t.get('name')}")
                print(f"    Columns: {t.get('columns')}")
                print(f"    Rows: {t.get('rows')}")
        if citations:
            print(f"\nCITATIONS ({len(citations)}):")
            for c in citations[:3]:
                print(
                    f"  [{c.get('n')}] {c.get('title')} ({c.get('source_type')}) - {c.get('date')}"
                )
        if sql:
            print(f"\nSQL GENERATED ({len(sql)} statements):")
            for s in sql:
                first_line = s.strip().splitlines()[0]
                print(f"  - {first_line}...")
        print()
