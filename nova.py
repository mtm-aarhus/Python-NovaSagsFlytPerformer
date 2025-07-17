import requests
from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
import uuid

def get_access_token(orchestrator_connection: OrchestratorConnection):
    NovaToken = orchestrator_connection.get_credential("KMDAccessToken")
    Secret = orchestrator_connection.get_credential("KMDClientSecret")
    

    NovaTokenAPI = NovaToken.username
    secret = Secret.password
    id = Secret.username
    
    # Authenticate
    auth_payload = {
        "client_secret": secret,
        "grant_type": "client_credentials",
        "client_id": id,
        "scope": "client"
    }
    
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(NovaTokenAPI, data=auth_payload, headers=headers)

    response.raise_for_status()
    access_token = response.json().get("access_token")
    return access_token

def fetch_case(Sagsnummer, transaction, access_token, KMDNovaURL, orchestrator_connection: OrchestratorConnection):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    
    Caseurl = f"{KMDNovaURL}/Case/GetList?api-version=2.0-Case"

    data = {
            "common": {
                "transactionId": transaction
            },
            "paging": {
                "startRow": 1,
                "numberOfRows": 500
            },
            "caseAttributes": {
                "userFriendlyCaseNumber": Sagsnummer
            },
           "caseGetOutput": {
                "caseAttributes": {
                    "userFriendlyCaseNumber": True
                },
                "caseworker": {
                    "kspIdentity": {
                        "novaUserId": True,
                        "racfId": True,
                        "fullName": True
                    },
                    "fkOrgIdentity": {
                        "fkUuid": True,
                        "type": True,
                        "fullName": True
                    },
                    "losIdentity": {
                        "novaUnitId": True,
                        "administrativeUnitId": True,
                        "fullName": True,
                        "userKey": True
                    },
                    "caseworkerCtrlBy": True
                }
            }
        }

    response = requests.put(Caseurl, headers=headers, json=data)

    response.raise_for_status()

    response_json = response.json()
    return response_json

def get_task_list(transaction, case_uuid, access_token, KMDNovaURL):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    url = f"{KMDNovaURL}/Task/GetList?api-version=2.0-Case"
    start_row = 1
    page_size = 500
    all_tasks = []

    while True:
        data = {
            "common": {
                "transactionId": transaction
            },
            "paging": {
                "startRow": start_row,
                "numberOfRows": page_size
            },
            "caseUuid": case_uuid
        }

        response = requests.put(url, headers=headers, json=data)
        response.raise_for_status()
        response_json = response.json()

        task_page = response_json.get("taskList", [])
        all_tasks.extend(task_page)

        paging_info = response_json.get("pagingInformation", {})
        if not paging_info.get("hasMoreRows", False):
            break

        start_row += page_size

    return all_tasks


def lookup_caseworker_by_racfId(racfId, transaction, access_token, KMDNovaURL):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    # First: Try searching cases
    case_url = f"{KMDNovaURL}/Case/GetList?api-version=2.0-Case"
    case_data = {
        "common": {
            "transactionId": transaction
        },
        "paging": {
            "startRow": 1,
            "numberOfRows": 500
        },
        "caseWorker": {
            "kspIdentity": {
                "racfId": racfId
            }
        },
        "caseGetOutput": {
            "caseAttributes": {
                "userFriendlyCaseNumber": True
            },
            "caseworker": {
                "kspIdentity": {
                    "novaUserId": True,
                    "racfId": True,
                    "fullName": True
                },
                "fkOrgIdentity": {
                    "fkUuid": True,
                    "type": True,
                    "fullName": True
                },
                "losIdentity": {
                    "novaUnitId": True,
                    "administrativeUnitId": True,
                    "fullName": True,
                    "userKey": True
                },
                "caseworkerCtrlBy": True
            }
        }
    }

    case_response = requests.put(case_url, headers=headers, json=case_data)
    case_response.raise_for_status()
    case_json = case_response.json()

    # Check if any case matches the RACF ID
    for case in case_json.get("cases", []):
        caseworker = case.get("caseworker", {})
        ksp = caseworker.get("kspIdentity", {})
        if ksp.get("racfId", "").lower() == racfId.lower():
            return caseworker

    # Second: Try searching tasks
    task_url = f"{KMDNovaURL}/Task/GetList?api-version=2.0-Case"
    task_data = {
        "common": {
            "transactionId": str(uuid.uuid4())
        },
        "caseworker": {
            "kspIdentity": {
                "racfId": racfId
            }
        },
        "paging": {
            "startRow": 1,
            "numberOfRows": 500
        }
    }

    task_response = requests.put(task_url, headers=headers, json=task_data)
    task_response.raise_for_status()
    task_json = task_response.json()

    for task in task_json.get("taskList", []):
        caseworker = task.get("caseworker", {})
        ksp = caseworker.get("kspIdentity", {})
        if ksp.get("racfId", "").lower() == racfId.lower():
            return caseworker

    return None  # No match found


def update_caseworker_task(task, access_token, KMDNovaURL, new_caseworker):
    """
    Updates a single task's caseworker via the KMD Nova Task/Update API.
    - Renames task-prefixed fields to match schema.
    - Filters and includes only schema-allowed fields.
    - Replaces the caseworker field with the new kspIdentity.
    """

    # Ensure kspIdentity is provided
    new_ksp_identity = new_caseworker.get("kspIdentity")
    if not new_ksp_identity:
        raise ValueError("new_caseworker must contain 'kspIdentity'")

    # Mapping of Task/GetList fields → Task/Update schema fields
    field_mapping = {
        "taskUuid": "uuid",
        "caseUuid": "caseUuid",
        "taskTitle": "title",
        "taskDescription": "description",
        "taskStatusCode": "statusCode",
        "taskType": "taskType",
        "deadline": "deadline",
        "startDate": "startDate",
        "closeDate": "closeDate",
        "kle": "kle",
        "taskRepeat": "taskRepeat",
    }

    # Extract and rename allowed fields
    transformed_task = {
        new_key: task.get(old_key)
        for old_key, new_key in field_mapping.items()
        if task.get(old_key) is not None
    }

    # Replace caseworker with only kspIdentity
    transformed_task["caseworker"] = {"kspIdentity": new_ksp_identity}

    # Build final payload with flattened structure
    payload = {
        "common": {
            "transactionId": str(uuid.uuid4())
        },
        **transformed_task
    }

    # Perform the update call
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    url = f"{KMDNovaURL}/Task/Update?api-version=2.0-Case"
    response = requests.put(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.status_code, response.json()


def patch_caseworker_racfId(task_uuid, novaUserid, access_token, KMDNovaURL):
    """
    Patch the RACF ID of a task's caseworker using the Task/Patch API.
    Only the RACF ID (CaseworkerPersonIdent) is updated.
    The UUID and transactionId must be sent as query parameters.
    """
    transaction_id = str(uuid.uuid4())
    url = f"{KMDNovaURL}/Task/PatchTask?id={task_uuid}&transactionId={transaction_id}&api-version=2.0-Case"

    payload = {
        "patchEntity": [
            {
                "path": "CaseworkerPersonIdent",
                "op": "Replace",
                "value": novaUserid
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.patch(url, headers=headers, json=payload)
    response.raise_for_status()

    return response.status_code, response.json()

def test_patch_caseworker_racfId(task_uuid, new_caseworker, access_token, KMDNovaURL):
    """
    Tries all possible identifiers for the 'CaseworkerPersonIdent' patch operation.
    Attempts patching using: novaUserId, racfId, fkUuid.
    """
    ksp_identity = new_caseworker.get("kspIdentity", {})
    fk_org_identity = new_caseworker.get("fkOrgIdentity", {})
    
    test_values = {
        "novaUserId": ksp_identity.get("novaUserId"),
        "racfId": ksp_identity.get("racfId"),
        "fkUuid": fk_org_identity.get("fkUuid")
    }

    transaction_id = str(uuid.uuid4())
    
    for label, value in test_values.items():
        if not value:
            continue

        url = f"{KMDNovaURL}/Task/PatchTask?id={task_uuid}&transactionId={transaction_id}&api-version=2.0-Case"
        payload = {
            "patchEntity": [
                {
                    "path": "CaseworkerPersonIdent",
                    "op": "Replace",
                    "value": value
                }
            ]
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        }

        print(f"\n🔄 Trying patch with {label} = {value}")
        try:
            response = requests.patch(url, headers=headers, json=payload)
            response.raise_for_status()
            print(f"✅ Success with {label}: {response.status_code}")
            print(response.json())
            print("success?")
        except requests.HTTPError as e:
            print(f"❌ Failed with {label}: {e.response.status_code}")
            print(e.response.json())


def update_caseworker_case(case_uuid, new_caseworker, access_token, KMDNovaURL):
    """
    Updates the caseworker on a case using the full kspIdentity element
    from the new_caseworker lookup result.
    """
    ksp_identity = new_caseworker.get("kspIdentity")
    if not ksp_identity:
        raise ValueError("new_caseworker must contain 'kspIdentity'")

    payload = {
        "common": {
            "transactionId": str(uuid.uuid4())
        },
        "case": {
            "caseUuid": case_uuid,
            "caseworker": {
                "kspIdentity": ksp_identity
            }
        }
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    url = f"{KMDNovaURL}/Case/Update?api-version=2.0-Case"
    
    print(f"Case: {payload}")

    # response = requests.patch(url, headers=headers, json=payload)
    # response.raise_for_status()

    # return response.status_code, response.json()


