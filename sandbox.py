from nova import get_access_token, fetch_case, get_task_list, lookup_caseworker_by_racfId, update_caseworker_case, update_caseworker_task, create_task
from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
import os
import uuid
from datetime import datetime


orchestrator_connection = OrchestratorConnection("NovaSagsFlyt", os.getenv('OpenOrchestratorSQL'), os.getenv('OpenOrchestratorKey'), None)

access_token = get_access_token(orchestrator_connection)
queue_file = "input_cases.txt"

# Read all lines
with open(queue_file, "r", encoding="utf-8") as f:
    lines = [line.strip() for line in f if line.strip()]
    
# Process one by one
for line in lines:
    Sagsnummer, oldazident, newazident = map(str.strip, line.split(","))

    transaction = str(uuid.uuid4())
    
    print(f"Processing {Sagsnummer}: {oldazident} ➝ {newazident}")

    Nova_URL = orchestrator_connection.get_constant("KMDNovaURL").value
    response_json = fetch_case(Sagsnummer, transaction, access_token, Nova_URL, orchestrator_connection)
    cases = response_json.get("cases", [])
    case_uuid = None
    caseworker = None
    caseworker_fullname = None
    for case in cases:
        ksp = case.get("caseworker", {}).get("kspIdentity", {})
        if ksp.get("racfId", "").lower() == oldazident.lower():
            case_uuid = case.get("common", {}).get("uuid")
            caseworker = case.get("caseworker", {})
            caseworker_fullname = caseworker.get("kspIdentity", {}).get("fullName")
            break  # stop after first matching case

    if case_uuid is None:
        raise Exception("No caseuuid")
    new_caseworker = lookup_caseworker_by_racfId(newazident, str(uuid.uuid4()), access_token, Nova_URL)
    new_caseworker_fullname = new_caseworker.get("kspIdentity", {}).get("fullName")
    task_list = get_task_list(str(uuid.uuid4()),case_uuid, access_token, Nova_URL)

    # Extract only the tasks where RACF ID matches and statusCode is not "F"
    tasks_to_update = [
        task for task in task_list
        if task.get("caseworker", {}).get("kspIdentity", {}).get("racfId") == oldazident
        and task.get("taskStatusCode") != "F"
    ]

    # Perform task updates with full new caseworker object
    for task in tasks_to_update:
        print(task.get("taskTitle"))
        print(task)

        update_caseworker_task(task, access_token, Nova_URL, new_caseworker)
        status_code = update_caseworker_task(task, access_token, Nova_URL, new_caseworker)
        print(f"Updated task {task['taskUuid']} - Status: {status_code}")

    # Update the case with the new caseworker
    status_code = update_caseworker_case(case_uuid, new_caseworker, access_token, Nova_URL)

    create_task(case_uuid, new_caseworker, f"Robotten har overført sagen fra {caseworker_fullname} til {new_caseworker_fullname}. Husk at ændre assistent på opgaverne hvis det er relevant.", access_token, Nova_URL)



