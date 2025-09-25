import os
import json
import uuid
import sqlite3
from datetime import datetime

import pandas as pd

from nova import (
    get_access_token,
    fetch_case,
    get_task_list,
    lookup_caseworker_by_racfId,
    update_caseworker_case,
    update_caseworker_task,
    create_task,
)
from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection


# ---------- CONFIG ----------
XLSX_PATH = "input_cases.xlsx"
SQLITE_PATH = "sagsflyt.sqlite3"
TABLE_NAME = "sagsflyt"
# Exact Excel column names (as provided)
EXCEL_COLS = ["Oprindelig sagsbehandler", "Ny sagsbehandler", "Sagsnummer"]
# ----------------------------


def connect_db():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute(f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        sagsnummer TEXT PRIMARY KEY,
        oprindelig_sagsbehandler TEXT,
        ny_sagsbehandler TEXT,

        -- Step responses / statuses
        fetch_case_status INTEGER,
        fetch_case_response TEXT,

        lookup_new_caseworker_status INTEGER,   -- 200 if found, 404 if not found/error
        lookup_new_caseworker_response TEXT,    -- short JSON (kspIdentity etc.)

        update_tasks_status TEXT,               -- e.g. summary "updated:3;skipped:2"
        update_tasks_response TEXT,             -- JSON array with taskUuid + status

        update_case_status INTEGER,
        update_case_response TEXT,

        create_task_status INTEGER,
        create_task_response TEXT,

        processed_at TEXT
    );
    """)
    return conn


def load_xlsx_into_db(conn):
    # Read Excel (only needed columns)
    df = pd.read_excel(XLSX_PATH, dtype=str, engine="openpyxl")[EXCEL_COLS].fillna("")

    # Standardize whitespace
    for c in EXCEL_COLS:
        df[c] = df[c].map(lambda v: v.strip() if isinstance(v, str) else v)

    # Upsert rows based on Sagsnummer
    # Use a temp table approach to avoid pandas dtype surprises
    with conn:
        conn.execute("BEGIN")
        for _, row in df.iterrows():
            original = row["Oprindelig sagsbehandler"]
            new = row["Ny sagsbehandler"]
            sagsnr = row["Sagsnummer"]

            # Insert if not exists; otherwise update caseworkers (but do NOT touch response columns)
            conn.execute(f"""
            INSERT INTO {TABLE_NAME} (sagsnummer, oprindelig_sagsbehandler, ny_sagsbehandler)
            VALUES (?, ?, ?)
            ON CONFLICT(sagsnummer) DO UPDATE SET
                oprindelig_sagsbehandler=excluded.oprindelig_sagsbehandler,
                ny_sagsbehandler=excluded.ny_sagsbehandler
            """, (sagsnr, original, new))
        conn.execute("COMMIT")


def dict_preview(d, max_len=2000):
    """Safely JSON-dumps a dict and truncates (SQLite TEXT is fine, but keep it readable)."""
    try:
        s = json.dumps(d, ensure_ascii=False)
    except Exception:
        s = str(d)
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


def run_pipeline_for_unprocessed_rows():
    conn = connect_db()
    # load_xlsx_into_db(conn)

    # Prepare Orchestrator/Nova access
    orchestrator_connection = OrchestratorConnection(
        "NovaSagsFlyt",
        os.getenv("OpenOrchestratorSQL"),
        os.getenv("OpenOrchestratorKey"),
        None
    )
    access_token = get_access_token(orchestrator_connection)
    Nova_URL = orchestrator_connection.get_constant("KMDNovaURL").value

    # Only pick rows where ALL response columns are NULL (=> never processed)
    query = f"""
    SELECT sagsnummer, oprindelig_sagsbehandler, ny_sagsbehandler
    FROM {TABLE_NAME}
    WHERE fetch_case_status IS NULL
      AND lookup_new_caseworker_status IS NULL
      AND update_tasks_status IS NULL
      AND update_case_status IS NULL
      AND create_task_status IS NULL
    """
    to_process = conn.execute(query).fetchall()

    print(f"Found {len(to_process)} row(s) to process.")
    
    caseworker_cache: dict[str, dict | None] = {}


    for sagsnr, oldazident, newazident in to_process:
        print(f"\nProcessing {sagsnr}: {oldazident} ➝ {newazident}")

        # Default row fields to update
        updates = {
            "fetch_case_status": None,
            "fetch_case_response": None,
            "lookup_new_caseworker_status": None,
            "lookup_new_caseworker_response": None,
            "update_tasks_status": None,
            "update_tasks_response": None,
            "update_case_status": None,
            "update_case_response": None,
            "create_task_status": None,
            "create_task_response": None,
            "processed_at": None,
        }

        try:
            # --- 1) Fetch case list and locate the specific case by old caseworker ---
            txn1 = str(uuid.uuid4())
            response_json = fetch_case(sagsnr, txn1, access_token, Nova_URL, orchestrator_connection)
            updates["fetch_case_status"] = 200
            updates["fetch_case_response"] = dict_preview(response_json)

            cases = response_json.get("cases", []) or []
            case_uuid = None
            caseworker_fullname = None
            userFriendlyCaseNumber = case.get("caseAttributes").get("userFriendlyCaseNumber")

            for case in cases:
                ksp = case.get("caseworker").get("kspIdentity")
                if ksp.get("racfId").lower() == (oldazident).lower() and userFriendlyCaseNumber.lower() == sagsnr.strip().lower():
                    case_uuid = case.get("common").get("uuid")
                    caseworker_fullname = ksp.get("fullName")
                    break

            if not case_uuid:
                raise RuntimeError("No caseuuid matched the original caseworker")

            # --- 2) Lookup new caseworker by racfId, with per-run cache ---
            cache_key = (newazident).strip().lower()
            if cache_key in caseworker_cache:
                new_caseworker = caseworker_cache[cache_key]
            else:
                new_caseworker = lookup_caseworker_by_racfId(newazident, str(uuid.uuid4()), access_token, Nova_URL)
                caseworker_cache[cache_key] = new_caseworker

            if new_caseworker:
                updates["lookup_new_caseworker_status"] = 200
                updates["lookup_new_caseworker_response"] = dict_preview(new_caseworker)
            else:
                updates["lookup_new_caseworker_status"] = 404
                updates["lookup_new_caseworker_response"] = "Not found"
                raise RuntimeError("New caseworker not found")

            new_caseworker_fullname = new_caseworker.get("kspIdentity").get("fullName")

            # --- 3) Get task list and update tasks that belong to old caseworker and are not 'F' ---
            task_list = get_task_list(str(uuid.uuid4()), case_uuid, access_token, Nova_URL)
            tasks_to_update = [
                t for t in (task_list or [])
                if t.get("caseworker", {}).get("kspIdentity", {}).get("racfId", "") == oldazident
                and t.get("taskStatusCode") != "F"
            ]

            per_task_results = []
            updated_count = 0
            skipped_count = 0
            for t in tasks_to_update:
                try:
                    status_code = update_caseworker_task(t, access_token, Nova_URL, new_caseworker)
                    per_task_results.append({
                        "taskUuid": t.get("taskUuid"),
                        "title": t.get("taskTitle"),
                        "status": status_code
                    })
                    if 200 <= int(status_code) < 300:
                        updated_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    per_task_results.append({
                        "taskUuid": t.get("taskUuid"),
                        "title": t.get("taskTitle"),
                        "status": "ERROR",
                        "error": str(e)
                    })
                    skipped_count += 1

            updates["update_tasks_status"] = f"updated:{updated_count};failed:{skipped_count}"
            updates["update_tasks_response"] = dict_preview(per_task_results)

            # --- 4) Update the case caseworker ---
            status_code_case = update_caseworker_case(case_uuid, new_caseworker, access_token, Nova_URL)
            updates["update_case_status"] = status_code_case
            updates["update_case_response"] = f"case_uuid:{case_uuid}"

            # --- 5) Create a confirmation task on the case ---
            desc = (
                f"Robotten har overført sagen fra {caseworker_fullname} til {new_caseworker_fullname}. "
                f"Husk at ændre assistent på opgaverne hvis det er relevant."
            )
            status_code_task = create_task(case_uuid, new_caseworker, desc, access_token, Nova_URL)
            updates["create_task_status"] = status_code_task
            updates["create_task_response"] = "Task created"

            updates["processed_at"] = datetime.now().isoformat(timespec="seconds")

        except Exception as e:
            # Even on failure, we persist what we have so far (some columns may be NULL)
            # No processed_at to keep it eligible for another run (or you can choose to stamp it)
            print(f"Error on {sagsnr}: {e}")

        # Persist updates to SQLite
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        params = [updates[k] for k in updates.keys()] + [sagsnr]
        with conn:
            conn.execute(f"UPDATE {TABLE_NAME} SET {set_clause} WHERE sagsnummer = ?", params)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    run_pipeline_for_unprocessed_rows()
