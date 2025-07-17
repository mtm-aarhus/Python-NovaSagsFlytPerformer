from nova import get_access_token, fetch_case, get_task_list, lookup_caseworker_by_racfId, update_caseworker_case, patch_caseworker_racfId, test_patch_caseworker_racfId, update_caseworker_task
from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
import os
import uuid

orchestrator_connection = OrchestratorConnection("NovaSagsFlyt", os.getenv('OpenOrchestratorSQL'), os.getenv('OpenOrchestratorKey'), None)

access_token = get_access_token(orchestrator_connection)

transaction = str(uuid.uuid4())

Sagsnummer = "S2021-292593"
oldazident = "AZ60026"
newazident = "AZMTM01"
Nova_URL = orchestrator_connection.get_constant("KMDNovaURL").value
response_json = fetch_case(Sagsnummer, transaction, access_token, Nova_URL, orchestrator_connection)
cases = response_json.get("cases", [])
case_uuid = None
caseworker = None

for case in cases:
    ksp = case.get("caseworker", {}).get("kspIdentity", {})
    if ksp.get("racfId", "").lower() == oldazident.lower():
        case_uuid = case.get("common", {}).get("uuid")
        caseworker = case.get("caseworker", {})
        break  # stop after first matching case
    
new_caseworker = lookup_caseworker_by_racfId(newazident, str(uuid.uuid4()), access_token, Nova_URL)
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
    status_code, updated_task = update_caseworker_task(task, access_token, Nova_URL, new_caseworker)
    print(f"Updated task {task['taskUuid']} - Status: {status_code}")

# Update the case with the new caseworker
update_caseworker_case(case_uuid, new_caseworker, access_token, Nova_URL)
# status_code, response = update_caseworker_case(case_uuid, new_caseworker, access_token, Nova_URL)
# print(f"Case {case_uuid} updated with new caseworker. Status: {status_code}")


