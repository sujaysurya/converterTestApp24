#!/usr/bin/env python3
"""
generate_all_node_scripts.py
=============================
Generates the 55 per-node-type converter scripts (appian_<slug>_converter.py)
from a single NODE_REGISTRY spec table, each importing appian_common.py.

Run once:  python generate_all_node_scripts.py --out-dir ./generated
"""

import argparse
from pathlib import Path
from string import Template

# (slug, appian_name, category, extra, parameter_schema, demo_parameters, confidence_note)
NODE_REGISTRY = [
    ("sub_proc", "Sub_PROC", "SUBPROCESS", {"mode": "call_wait"},
     {"subprocessModelName": "Name/UUID of the target sub-process model (verify against export)",
      "inputMapping": "SAIL expression(s) mapping parent process variables to sub-process inputs"},
     {"subprocessModelName": '"Loan Approval Subprocess"', "accountId": "pv!accountId",
      "approvalThreshold": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Standard Appian process node -- accurate."),

    ("xor", "XOR", "GATEWAY", {"mode": "xor"},
     {"<branchName>": "one parameter per outgoing flow, each a boolean SAIL condition expression"},
     {"approve": "pv!currentBalance >= rule!demoBusinessRule(balance: pv!currentBalance)", "reject": "true"},
     "Standard Appian exclusive gateway -- structurally accurate; real branch names follow your export."),

    ("write_to_data_store_entity", "Write To Data Store Entity", "DATA_STORE_WRITE", {"multiplicity": "single"},
     {"dataStoreEntity": "target Data Store Entity (verify)",
      "valuesToStore": "SAIL expression for the record/CDT value(s) to write"},
     {"dataStoreEntity": '"Account"',
      "valuesToStore": "a!map(id: pv!accountId, balance: rule!demoBusinessRule(balance: pv!currentBalance))"},
     "Standard Appian node -- accurate."),

    ("end_node", "End Node", "END_NODE", {},
     {"<outputName>": "one parameter per process output variable"},
     {"summary": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Standard Appian node -- accurate."),

    ("start_node", "Start Node", "START_NODE", {},
     {"<inputName>": "one parameter per process start input"},
     {"accountId": "ri!accountId"},
     "Standard Appian node -- accurate."),

    ("user_input_task", "User Input Task", "USER_TASK", {},
     {"form": "interface/form design object shown to the user",
      "assignedTo": "SAIL expression for the assignee(s)", "dueDate": "SAIL expression for the due date"},
     {"assignedTo": "pv!approverUser", "dueDate": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Standard Appian node -- accurate structurally; it's a human-in-the-loop boundary, see script comments."),

    ("execute_stored_procedure", "Execute Stored Procedure", "STORED_PROC", {},
     {"procedureName": "stored procedure name", "dataSource": "JDBC connected system",
      "parameters": "SAIL expression mapping IN/OUT parameters"},
     {"procedureName": '"SP_CALC_INTEREST"',
      "parameters": "a!map(balance: pv!currentBalance, rate: rule!demoBusinessRule(balance: pv!currentBalance))"},
     "Standard Database smart service -- accurate at a high level; exact IN/OUT parameter shape varies, verify."),

    ("call_integration", "Call Integration", "INTEGRATION_CALL", {},
     {"integrationObject": "referenced Integration design object",
      "inputMapping": "SAIL expression for the request body/params"},
     {"integrationObject": '"CreditBureauLookup"',
      "inputMapping": "a!map(ssn: pv!ssn, threshold: rule!demoBusinessRule(balance: pv!currentBalance))"},
     "Standard Appian node -- accurate."),

    ("send_email", "Send E-Mail", "EMAIL", {},
     {"to": "recipient(s)", "cc": "cc recipient(s)", "subject": "subject line", "body": "message body"},
     {"to": "pv!customerEmail", "subject": '"Account Update"',
      "body": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Standard SMTP smart service -- accurate."),

    ("intermediate_consuming_event", "Intermediate Consuming Event", "EVENT_CONSUME", {},
     {"eventKey": "the signal/event this node waits for", "timeout": "SAIL expression for the wait timeout"},
     {"eventKey": '"loan.approved"', "timeout": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Standard BPMN-style event node -- accurate at concept level."),

    ("start_process", "Start Process", "SUBPROCESS", {"mode": "start_async"},
     {"processModel": "target process model", "parameters": "SAIL expression(s) for process start parameters"},
     {"processModel": '"Notification Process"', "accountId": "pv!accountId",
      "threshold": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Standard Appian node -- accurate. Fire-and-forget, unlike Sub_PROC's call-and-wait."),

    ("write_records_and_related_records", "Write Records and Related Records", "DATA_STORE_WRITE",
     {"multiplicity": "records"},
     {"recordType": "target Record Type", "recordData": "SAIL expr for the record to write",
      "relatedRecords": "SAIL expr for related records to write in the same transaction"},
     {"recordType": '"Account"', "recordData": "a!map(id: pv!accountId, balance: rule!demoBusinessRule(balance: pv!currentBalance))"},
     "Standard Records-based write -- accurate at concept level."),

    ("generate_text", "Generate Text", "AI_TEXT", {},
     {"promptTemplate": "SAIL expression building the prompt", "inputData": "supporting input data"},
     {"promptTemplate": '"Summarize: " & pv!notes', "inputData": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Best-effort -- likely an AI/document-generation smart service; verify actual parameter names."),

    ("and", "AND", "GATEWAY", {"mode": "and"},
     {"<branchName>": "one parameter per parallel outgoing flow"},
     {"notifyCustomer": "true", "updateLedger": "true"},
     "Standard Appian parallel gateway -- accurate."),

    ("start_process_deprecated", "Start Process [Deprecated]", "SUBPROCESS", {"mode": "start_async_deprecated"},
     {"processModel": "target process model (deprecated node -- confirm still in use)",
      "parameters": "SAIL expression(s) for process start parameters"},
     {"processModel": '"Legacy Audit Process"', "accountId": "pv!accountId"},
     "Standard Appian node, deprecated -- accurate; confirm still-active usage before converting."),

    ("delete_data_store_entries", "Delete and Data Store Entries", "DATA_STORE_DELETE", {},
     {"dataStoreEntity": "target Data Store Entity",
      "filterCriteria": "SAIL expression identifying which records to delete"},
     {"dataStoreEntity": '"StaleSession"', "filterCriteria": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Standard Appian node -- accurate."),

    ("delete_document", "DeleteDocument", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "deleteDocument", "return_type": "boolean"},
     {"document": "document to delete (id/UUID)"},
     {"document": "pv!documentId"},
     "Well-documented AppMarket Document Management plugin -- accurate at operation level."),

    ("or", "OR", "GATEWAY", {"mode": "or"},
     {"<branchName>": "one parameter per inclusive outgoing flow, each a boolean condition"},
     {"emailAlert": "pv!currentBalance < rule!demoBusinessRule(balance: pv!currentBalance)", "smsAlert": "false"},
     "Standard Appian inclusive gateway -- accurate."),

    ("move_document", "Move Document", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "moveDocument", "return_type": "boolean"},
     {"document": "document to move", "targetFolder": "destination folder"},
     {"document": "pv!documentId", "targetFolder": '"/Archive"'},
     "Well-documented Document Management plugin -- accurate."),

    ("create_folder", "Create Folder", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "createFolder", "return_type": "Long"},
     {"folderName": "new folder name", "parentFolder": "parent folder", "description": "optional description"},
     {"folderName": "pv!customerId", "parentFolder": '"/Customers"'},
     "Well-documented Document Management plugin -- accurate."),

    ("complex", "Complex", "GATEWAY", {"mode": "complex"},
     {"<branchName>": "one parameter per outgoing flow (boolean condition)",
      "joinCondition": "SAIL expression evaluated to decide whether the gateway proceeds"},
     {"pathA": "true", "pathB": "false", "joinCondition": "true"},
     "Standard Appian complex gateway -- accurate."),

    ("write_to_multiple_data_store_entities", "Write to Multiple Dta Store Entities", "DATA_STORE_WRITE",
     {"multiplicity": "multiple"},
     {"<entityAlias>": "one parameter per Data Store Entity being written, each a SAIL expression for its value(s)"},
     {"account": "a!map(id: pv!accountId, balance: rule!demoBusinessRule(balance: pv!currentBalance))",
      "auditLog": 'a!map(event: "balance_update", accountId: pv!accountId)'},
     "Standard Appian node -- accurate. (Original label has a typo: 'Dta' -> 'Data'.)"),

    ("rename_folders", "Rename Folders", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "renameFolder", "return_type": "boolean"},
     {"folder": "folder to rename", "newName": "new folder name"},
     {"folder": '"/Customers/OldName"', "newName": "pv!newFolderName"},
     "Well-documented Document Management plugin -- name is plural, may be a bulk variant; verify."),

    ("add_members_to_group", "Add members to Group", "SERVICE_CALL",
     {"service_field": "identityManagementService", "operation": "addGroupMembers", "return_type": "boolean"},
     {"group": "target group", "users": "users to add"},
     {"group": '"Loan Approvers"', "users": "pv!newApproverList"},
     "Well-documented Users & Groups Management plugin -- accurate."),

    ("export_data_store_entity_to_csv", "Export DataStore Entity to CSV", "SERVICE_CALL",
     {"service_field": "fileProcessingService", "operation": "exportDataStoreEntityToCsv", "return_type": "Document"},
     {"dataStoreEntity": "source entity", "filterCriteria": "SAIL filter expression", "columns": "columns to export"},
     {"dataStoreEntity": '"Account"', "filterCriteria": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Well-documented Excel/CSV utility plugin -- accurate."),

    ("generate_excel_from_csv", "Generate Excel from CSV", "SERVICE_CALL",
     {"service_field": "fileProcessingService", "operation": "generateExcelFromCsv", "return_type": "Document"},
     {"csvDocument": "source CSV document", "sheetName": "target sheet name"},
     {"csvDocument": "pv!csvDoc", "sheetName": '"Accounts"'},
     "Well-documented Excel/CSV utility plugin -- accurate."),

    ("import_excel_to_database_v3", "Import Excel to Database v3", "SERVICE_CALL",
     {"service_field": "fileProcessingService", "operation": "importExcelToDatabase", "return_type": "int"},
     {"excelDocument": "TODO: confirm actual parameter name from your export",
      "targetTable": "TODO: confirm actual parameter name from your export"},
     {"excelDocument": "pv!uploadedExcel", "targetTable": '"ACCOUNT_STAGING"'},
     "Version-suffixed (v3) -- almost certainly a custom/internal build. Schema is a placeholder, verify."),

    ("insert_documents_as_base64_into_database", "Insert Documents as Base64 into Database", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "insertDocumentsAsBase64", "return_type": "boolean"},
     {"documents": "TODO: confirm actual parameter name", "targetTable": "TODO: confirm actual parameter name"},
     {"documents": "pv!documentList", "targetTable": '"DOCUMENT_BLOB"'},
     "Likely custom/internal -- schema is a placeholder, verify against your export."),

    ("export_data_store_entity_to_excel", "Export Data Store Entity to Excel", "SERVICE_CALL",
     {"service_field": "fileProcessingService", "operation": "exportDataStoreEntityToExcel", "return_type": "Document"},
     {"dataStoreEntity": "source entity", "filterCriteria": "SAIL filter expression"},
     {"dataStoreEntity": '"Account"', "filterCriteria": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Well-documented Excel/CSV utility plugin -- accurate."),

    ("remove_group_members", "Remove Group Members", "SERVICE_CALL",
     {"service_field": "identityManagementService", "operation": "removeGroupMembers", "return_type": "boolean"},
     {"group": "target group", "users": "users to remove"},
     {"group": '"Loan Approvers"', "users": "pv!offboardedUsers"},
     "Well-documented Users & Groups Management plugin -- accurate."),

    ("update_user_profile", "Update User Profile", "SERVICE_CALL",
     {"service_field": "identityManagementService", "operation": "updateUserProfile", "return_type": "boolean"},
     {"user": "target user", "profileFields": "SAIL expr for fields to update"},
     {"user": "pv!username", "profileFields": 'a!map(email: pv!newEmail)'},
     "Well-documented Users & Groups Management plugin -- accurate."),

    ("publish_to_kafka", "Publish to Kafka", "KAFKA_PRODUCE", {"bulk": False},
     {"topic": "target Kafka topic", "key": "message key", "payload": "message payload"},
     {"topic": '"account-events"', "key": "pv!accountId",
      "payload": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Custom/internal Kafka integration plugin -- reasonable generic guess, verify parameter names."),

    ("delete_folder", "Delete Folder", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "deleteFolder", "return_type": "boolean"},
     {"folder": "folder to delete"},
     {"folder": '"/Customers/Temp"'},
     "Well-documented Document Management plugin -- accurate."),

    ("publish_to_kafka_in_bulk", "Publish to Kafka in Bulk", "KAFKA_PRODUCE", {"bulk": True},
     {"topic": "target Kafka topic", "key": "message key (optional)", "payload": "list of message payloads"},
     {"topic": '"account-events"', "payload": "pv!eventBatch"},
     "Custom/internal Kafka integration plugin -- reasonable generic guess, verify parameter names."),

    ("foldersecurity", "FolderSecurity", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "setFolderSecurity", "return_type": "boolean"},
     {"folder": "target folder", "securityRoleMap": "SAIL expr mapping roles/users to permissions"},
     {"folder": '"/Customers"', "securityRoleMap": "pv!roleMap"},
     "Document Management plugin -- verify exact role-map shape against your export."),

    ("document_zipper", "Document Zipper", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "zipDocuments", "return_type": "Document"},
     {"documents": "documents to zip", "zipFileName": "output zip file name"},
     {"documents": "pv!documentList", "zipFileName": '"statements.zip"'},
     "Document Management plugin -- accurate at operation level."),

    ("delete_documents_created_before_date", "Delete Documents Created Before Date", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "deleteDocumentsCreatedBeforeDate", "return_type": "int"},
     {"folder": "folder to scan", "cutoffDate": "SAIL expression for the cutoff date"},
     {"folder": '"/Temp"', "cutoffDate": 'today() - 90'},
     "Document Management plugin -- verify; could also be a custom retention-policy build."),

    ("update_document", "Update Document", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "updateDocument", "return_type": "boolean"},
     {"document": "document to update", "newContent": "new content/version", "description": "optional description"},
     {"document": "pv!documentId", "newContent": "pv!newFileContent"},
     "Document Management plugin -- accurate at operation level."),

    ("deactivate_users", "Deactivate Users", "SERVICE_CALL",
     {"service_field": "identityManagementService", "operation": "deactivateUsers", "return_type": "boolean"},
     {"users": "users to deactivate"},
     {"users": "pv!offboardedUsers"},
     "Well-documented Users & Groups Management plugin -- accurate."),

    ("sync_records", "Sync Records", "RECORD_SYNC", {},
     {"recordType": "record type to sync"},
     {"recordType": '"Account"'},
     "Best-effort -- likely a custom Records-sync utility; verify."),

    ("create_user", "Create User", "SERVICE_CALL",
     {"service_field": "identityManagementService", "operation": "createUser", "return_type": "String"},
     {"username": "new username", "email": "new user's email", "group": "initial group membership"},
     {"username": "pv!newUsername", "email": "pv!newUserEmail", "group": '"Employees"'},
     "Well-documented Users & Groups Management plugin -- accurate."),

    ("delete_records_and_related_records", "Delete Records and Related Records", "DATA_STORE_DELETE", {},
     {"recordType": "target Record Type", "filterCriteria": "SAIL expr identifying records (and related) to delete"},
     {"recordType": '"Account"', "filterCriteria": "rule!demoBusinessRule(balance: pv!currentBalance)"},
     "Standard Records concept -- accurate."),

    ("set_alert_recipient_for_pm", "Set Alert Recipient for PM", "SERVICE_CALL",
     {"service_field": "processAdminService", "operation": "setAlertRecipient", "return_type": "boolean"},
     {"processModel": "TODO: confirm actual parameter name", "recipient": "TODO: confirm actual parameter name"},
     {"processModel": '"Loan Approval Process"', "recipient": "pv!adminUser"},
     "Custom/internal -- 'PM' likely = Process Model admin alerting. Placeholder schema, verify heavily."),

    ("change_user_password", "cHANGE User Password", "SERVICE_CALL",
     {"service_field": "identityManagementService", "operation": "changeUserPassword", "return_type": "boolean"},
     {"user": "target user", "newPassword": "new password (SAIL expr -- handle as a secret, do not log)"},
     {"user": "pv!username", "newPassword": "pv!tempPassword"},
     "Well-known plugin operation (original label has irregular casing -- treated as 'Change User Password')."),

    ("kafka_server_listner_daemon_v8", "Kafka Server Listner Daemon v8", "KAFKA_CONSUME", {"deprecated": False},
     {"topic": "TODO: confirm actual parameter name", "consumerGroup": "TODO: confirm actual parameter name"},
     {"topic": '"account-events"', "consumerGroup": '"pm-daemon-v8"'},
     "Version-suffixed (v8), custom/internal daemon -- placeholder schema, verify heavily."),

    ("get_base64_document_from_database", "Get Base64 Document from Database", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "getBase64Document", "return_type": "String"},
     {"document": "document identifier to fetch"},
     {"document": "pv!documentId"},
     "Likely custom/internal -- verify."),

    ("json_document_to_csv", "JSON Document to CSV", "SERVICE_CALL",
     {"service_field": "fileProcessingService", "operation": "jsonDocumentToCsv", "return_type": "Document"},
     {"jsonDocument": "source JSON document"},
     {"jsonDocument": "pv!jsonDoc"},
     "Plugin/custom -- verify; confirm whether distinct from 'Json To Csv Converter' (#54) in your instance."),

    ("combine_excel_sheet", "Combine Excel Sheet", "SERVICE_CALL",
     {"service_field": "fileProcessingService", "operation": "combineExcelSheets", "return_type": "Document"},
     {"sourceDocuments": "excel documents/sheets to combine"},
     {"sourceDocuments": "pv!excelDocs"},
     "Excel/CSV utility plugin -- accurate at operation level."),

    ("delete_excel_sheet", "Delete Excel Sheet", "SERVICE_CALL",
     {"service_field": "fileProcessingService", "operation": "deleteExcelSheet", "return_type": "boolean"},
     {"document": "source excel document", "sheetName": "sheet to delete"},
     {"document": "pv!excelDoc", "sheetName": '"Sheet2"'},
     "Excel/CSV utility plugin -- accurate at operation level."),

    ("database_access", "Database Access", "STORED_PROC", {},
     {"query": "SQL/SAIL query expression", "dataSource": "JDBC connected system"},
     {"query": '"SELECT * FROM ACCOUNT WHERE ID = ?"', "dataSource": '"CoreBankingDS"'},
     "Generic DB access smart service -- reasonable generic guess, verify against export."),

    ("export_sql_to_excel", "Export SQL To Excel", "SERVICE_CALL",
     {"service_field": "fileProcessingService", "operation": "exportSqlToExcel", "return_type": "Document"},
     {"sqlQuery": "query to export", "dataSource": "JDBC connected system"},
     {"sqlQuery": '"SELECT * FROM ACCOUNT"', "dataSource": '"CoreBankingDS"'},
     "Plugin/custom -- verify."),

    ("reactivate_users", "Reactivate Users", "SERVICE_CALL",
     {"service_field": "identityManagementService", "operation": "reactivateUsers", "return_type": "boolean"},
     {"users": "users to reactivate"},
     {"users": "pv!returningUsers"},
     "Well-documented Users & Groups Management plugin -- accurate."),

    ("extract_zip", "Extract Zip", "SERVICE_CALL",
     {"service_field": "documentManagementService", "operation": "extractZip", "return_type": "java.util.List"},
     {"zipDocument": "zip document to extract", "targetFolder": "destination folder"},
     {"zipDocument": "pv!uploadedZip", "targetFolder": '"/Extracted"'},
     "Document Management plugin -- accurate at operation level."),

    ("json_to_csv_converter", "Json To Csv Converter", "SERVICE_CALL",
     {"service_field": "fileProcessingService", "operation": "jsonToCsv", "return_type": "Document"},
     {"jsonData": "source JSON data/document"},
     {"jsonData": "pv!jsonPayload"},
     "Plugin/custom -- verify; confirm whether distinct from 'JSON Document to CSV' (#13) in your instance."),

    ("consume_from_kafka_deprecated", "Consume from Kafka [Deprecated]", "KAFKA_CONSUME", {"deprecated": True},
     {"topic": "TODO: confirm actual parameter name", "consumerGroup": "TODO: confirm actual parameter name"},
     {"topic": '"legacy-events"', "consumerGroup": '"legacy-group"'},
     "Deprecated + custom -- placeholder schema, verify, and confirm still-in-use before converting."),
]

assert len(NODE_REGISTRY) == 55, f"expected 55 node types, got {len(NODE_REGISTRY)}"


SCRIPT_TEMPLATE = Template('''#!/usr/bin/env python3
"""
appian_${slug}_converter.py
${underline}
Converts Appian '${appian_name}' nodes into Java method scaffolds, with full
rule!/cons! drill-down resolution via appian_common.py.

CONFIDENCE NOTE: ${confidence}

PARAMETER_SCHEMA below documents what this script expects to find in each
node's `parameters` dict (see appian_common.load_node_from_report). Adjust it
-- and the demo data further down -- once you've confirmed your export's real
field names via `--inspect`.
"""

from pathlib import Path

from appian_common import (
    DesignObjectIndex, DrillDownResolver, NodeRecord,
    convert_node, load_node_from_report, inspect_file, build_arg_parser,
)

NODE_TYPE_LABEL = "${appian_name}"
NODE_CATEGORY = "${category}"
EXTRA_CONFIG = ${extra}

PARAMETER_SCHEMA = ${parameter_schema}


def _demo_export(demo_root: Path) -> None:
    """Shared demo design objects so every generated script proves drill-down
    (rule! -> rule! -> cons!) works end-to-end without needing your real export."""
    demo_root.mkdir(parents=True, exist_ok=True)
    (demo_root / "DemoThreshold.xml").write_text("""
    <constant uuid="demo-c-0001">
      <name>DemoThreshold</name>
      <value>500</value>
    </constant>
    """, encoding="utf-8")
    (demo_root / "demoBusinessRule.xml").write_text("""
    <expressionRule uuid="demo-r-0002">
      <name>demoBusinessRule</name>
      <definition>
        a!ifThenElse(
          condition: ri!balance &gt;= cons!DemoThreshold,
          thenValue: ri!balance,
          elseValue: cons!DemoThreshold
        )
      </definition>
    </expressionRule>
    """, encoding="utf-8")


def _demo_node() -> NodeRecord:
    return NodeRecord(
        node_id="demo-${slug}",
        node_name="Demo ${appian_name}",
        node_type=NODE_TYPE_LABEL,
        parameters=${demo_parameters},
        inputs=["accountId", "currentBalance"],
        outputs=[],
    )


def run_demo(out_dir: Path) -> None:
    demo_root = out_dir / "_demo_export"
    _demo_export(demo_root)
    index = DesignObjectIndex(demo_root)
    index.build()
    node = _demo_node()
    resolved = convert_node(index, node, out_dir / "converted", NODE_CATEGORY, EXTRA_CONFIG)
    print("\\n--- Demo resolution: parameters resolved ---")
    for pname, p in resolved.parameters.items():
        print(f"  [{pname}] -> {p['resolved_expression'][:160]}")
    java_files = sorted((out_dir / "converted").glob("*.java.txt"))
    if java_files:
        print("\\n--- Demo Java scaffold ---")
        print(java_files[-1].read_text())


def main() -> None:
    parser = build_arg_parser(NODE_TYPE_LABEL)
    args = parser.parse_args()

    if args.demo:
        run_demo(args.out_dir)
        return
    if args.inspect:
        inspect_file(args.inspect)
        return
    if not (args.export_root and args.node_report and args.node_id):
        parser.error("--export-root, --node-report and --node-id are all required (or use --demo / --inspect)")

    index = DesignObjectIndex(args.export_root)
    index.build()
    node = load_node_from_report(args.node_report, args.node_id)
    resolver = DrillDownResolver(index)
    convert_node(index, node, args.out_dir, NODE_CATEGORY, EXTRA_CONFIG, resolver=resolver)


if __name__ == "__main__":
    main()
''')


def generate(out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for slug, appian_name, category, extra, schema, demo_params, confidence in NODE_REGISTRY:
        content = SCRIPT_TEMPLATE.substitute(
            slug=slug,
            underline="=" * (len(f"appian_{slug}_converter.py")),
            appian_name=appian_name,
            confidence=confidence,
            category=category,
            extra=repr(extra),
            parameter_schema=repr(schema),
            demo_parameters=repr(demo_params),
        )
        fname = f"appian_{slug}_converter.py"
        (out_dir / fname).write_text(content, encoding="utf-8")
        written.append(fname)
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=Path("./generated"))
    args = ap.parse_args()
    files = generate(args.out_dir)
    print(f"Generated {len(files)} node-type scripts into {args.out_dir}")
