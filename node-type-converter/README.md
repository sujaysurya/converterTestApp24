# Appian Node-Type -> Java Converter Suite

55 node-type scripts + 1 shared engine (`appian_common.py`). Every script has the same CLI:

```
python appian_<slug>_converter.py --demo
python appian_<slug>_converter.py --inspect <path-to-one-exported-xml-file>
python appian_<slug>_converter.py --export-root <dir> --node-report <report.json> --node-id <id> --out-dir <dir>
```

**Before running against real data:** `appian_common.py`'s `DesignObjectIndex` uses permissive regex parsing because your export's exact XML schema wasn't verified against a real file. Run `--inspect` on one real exported object first and adjust the patterns in `appian_common.py` (one fix applies to all 55 scripts). Similarly, `load_node_from_report()` assumes a `{"nodes": [{"id","name","type","parameters":{...},"inputs":[],"outputs":[]}]}` JSON shape -- adjust to match your actual per-process-model report format.

| # | Appian Node Type | Script | Java Category | Confidence |
|---|---|---|---|---|
| 1 | Sub_PROC | `appian_sub_proc_converter.py` | SUBPROCESS | Standard Appian process node -- accurate. |
| 2 | XOR | `appian_xor_converter.py` | GATEWAY | Standard Appian exclusive gateway -- structurally accurate; real branch names follow your export. |
| 3 | Write To Data Store Entity | `appian_write_to_data_store_entity_converter.py` | DATA_STORE_WRITE | Standard Appian node -- accurate. |
| 4 | End Node | `appian_end_node_converter.py` | END_NODE | Standard Appian node -- accurate. |
| 5 | Start Node | `appian_start_node_converter.py` | START_NODE | Standard Appian node -- accurate. |
| 6 | User Input Task | `appian_user_input_task_converter.py` | USER_TASK | Standard Appian node -- accurate structurally; it's a human-in-the-loop boundary, see script comments. |
| 7 | Execute Stored Procedure | `appian_execute_stored_procedure_converter.py` | STORED_PROC | Standard Database smart service -- accurate at a high level; exact IN/OUT parameter shape varies, verify. |
| 8 | Call Integration | `appian_call_integration_converter.py` | INTEGRATION_CALL | Standard Appian node -- accurate. |
| 9 | Send E-Mail | `appian_send_email_converter.py` | EMAIL | Standard SMTP smart service -- accurate. |
| 10 | Intermediate Consuming Event | `appian_intermediate_consuming_event_converter.py` | EVENT_CONSUME | Standard BPMN-style event node -- accurate at concept level. |
| 11 | Start Process | `appian_start_process_converter.py` | SUBPROCESS | Standard Appian node -- accurate. Fire-and-forget, unlike Sub_PROC's call-and-wait. |
| 12 | Write Records and Related Records | `appian_write_records_and_related_records_converter.py` | DATA_STORE_WRITE | Standard Records-based write -- accurate at concept level. |
| 13 | Generate Text | `appian_generate_text_converter.py` | AI_TEXT | Best-effort -- likely an AI/document-generation smart service; verify actual parameter names. |
| 14 | AND | `appian_and_converter.py` | GATEWAY | Standard Appian parallel gateway -- accurate. |
| 15 | Start Process [Deprecated] | `appian_start_process_deprecated_converter.py` | SUBPROCESS | Standard Appian node, deprecated -- accurate; confirm still-active usage before converting. |
| 16 | Delete and Data Store Entries | `appian_delete_data_store_entries_converter.py` | DATA_STORE_DELETE | Standard Appian node -- accurate. |
| 17 | DeleteDocument | `appian_delete_document_converter.py` | SERVICE_CALL | Well-documented AppMarket Document Management plugin -- accurate at operation level. |
| 18 | OR | `appian_or_converter.py` | GATEWAY | Standard Appian inclusive gateway -- accurate. |
| 19 | Move Document | `appian_move_document_converter.py` | SERVICE_CALL | Well-documented Document Management plugin -- accurate. |
| 20 | Create Folder | `appian_create_folder_converter.py` | SERVICE_CALL | Well-documented Document Management plugin -- accurate. |
| 21 | Complex | `appian_complex_converter.py` | GATEWAY | Standard Appian complex gateway -- accurate. |
| 22 | Write to Multiple Dta Store Entities | `appian_write_to_multiple_data_store_entities_converter.py` | DATA_STORE_WRITE | Standard Appian node -- accurate. (Original label has a typo: 'Dta' -> 'Data'.) |
| 23 | Rename Folders | `appian_rename_folders_converter.py` | SERVICE_CALL | Well-documented Document Management plugin -- name is plural, may be a bulk variant; verify. |
| 24 | Add members to Group | `appian_add_members_to_group_converter.py` | SERVICE_CALL | Well-documented Users & Groups Management plugin -- accurate. |
| 25 | Export DataStore Entity to CSV | `appian_export_data_store_entity_to_csv_converter.py` | SERVICE_CALL | Well-documented Excel/CSV utility plugin -- accurate. |
| 26 | Generate Excel from CSV | `appian_generate_excel_from_csv_converter.py` | SERVICE_CALL | Well-documented Excel/CSV utility plugin -- accurate. |
| 27 | Import Excel to Database v3 | `appian_import_excel_to_database_v3_converter.py` | SERVICE_CALL | Version-suffixed (v3) -- almost certainly a custom/internal build. Schema is a placeholder, verify. |
| 28 | Insert Documents as Base64 into Database | `appian_insert_documents_as_base64_into_database_converter.py` | SERVICE_CALL | Likely custom/internal -- schema is a placeholder, verify against your export. |
| 29 | Export Data Store Entity to Excel | `appian_export_data_store_entity_to_excel_converter.py` | SERVICE_CALL | Well-documented Excel/CSV utility plugin -- accurate. |
| 30 | Remove Group Members | `appian_remove_group_members_converter.py` | SERVICE_CALL | Well-documented Users & Groups Management plugin -- accurate. |
| 31 | Update User Profile | `appian_update_user_profile_converter.py` | SERVICE_CALL | Well-documented Users & Groups Management plugin -- accurate. |
| 32 | Publish to Kafka | `appian_publish_to_kafka_converter.py` | KAFKA_PRODUCE | Custom/internal Kafka integration plugin -- reasonable generic guess, verify parameter names. |
| 33 | Delete Folder | `appian_delete_folder_converter.py` | SERVICE_CALL | Well-documented Document Management plugin -- accurate. |
| 34 | Publish to Kafka in Bulk | `appian_publish_to_kafka_in_bulk_converter.py` | KAFKA_PRODUCE | Custom/internal Kafka integration plugin -- reasonable generic guess, verify parameter names. |
| 35 | FolderSecurity | `appian_foldersecurity_converter.py` | SERVICE_CALL | Document Management plugin -- verify exact role-map shape against your export. |
| 36 | Document Zipper | `appian_document_zipper_converter.py` | SERVICE_CALL | Document Management plugin -- accurate at operation level. |
| 37 | Delete Documents Created Before Date | `appian_delete_documents_created_before_date_converter.py` | SERVICE_CALL | Document Management plugin -- verify; could also be a custom retention-policy build. |
| 38 | Update Document | `appian_update_document_converter.py` | SERVICE_CALL | Document Management plugin -- accurate at operation level. |
| 39 | Deactivate Users | `appian_deactivate_users_converter.py` | SERVICE_CALL | Well-documented Users & Groups Management plugin -- accurate. |
| 40 | Sync Records | `appian_sync_records_converter.py` | RECORD_SYNC | Best-effort -- likely a custom Records-sync utility; verify. |
| 41 | Create User | `appian_create_user_converter.py` | SERVICE_CALL | Well-documented Users & Groups Management plugin -- accurate. |
| 42 | Delete Records and Related Records | `appian_delete_records_and_related_records_converter.py` | DATA_STORE_DELETE | Standard Records concept -- accurate. |
| 43 | Set Alert Recipient for PM | `appian_set_alert_recipient_for_pm_converter.py` | SERVICE_CALL | Custom/internal -- 'PM' likely = Process Model admin alerting. Placeholder schema, verify heavily. |
| 44 | cHANGE User Password | `appian_change_user_password_converter.py` | SERVICE_CALL | Well-known plugin operation (original label has irregular casing -- treated as 'Change User Password'). |
| 45 | Kafka Server Listner Daemon v8 | `appian_kafka_server_listner_daemon_v8_converter.py` | KAFKA_CONSUME | Version-suffixed (v8), custom/internal daemon -- placeholder schema, verify heavily. |
| 46 | Get Base64 Document from Database | `appian_get_base64_document_from_database_converter.py` | SERVICE_CALL | Likely custom/internal -- verify. |
| 47 | JSON Document to CSV | `appian_json_document_to_csv_converter.py` | SERVICE_CALL | Plugin/custom -- verify; confirm whether distinct from 'Json To Csv Converter' (#54) in your instance. |
| 48 | Combine Excel Sheet | `appian_combine_excel_sheet_converter.py` | SERVICE_CALL | Excel/CSV utility plugin -- accurate at operation level. |
| 49 | Delete Excel Sheet | `appian_delete_excel_sheet_converter.py` | SERVICE_CALL | Excel/CSV utility plugin -- accurate at operation level. |
| 50 | Database Access | `appian_database_access_converter.py` | STORED_PROC | Generic DB access smart service -- reasonable generic guess, verify against export. |
| 51 | Export SQL To Excel | `appian_export_sql_to_excel_converter.py` | SERVICE_CALL | Plugin/custom -- verify. |
| 52 | Reactivate Users | `appian_reactivate_users_converter.py` | SERVICE_CALL | Well-documented Users & Groups Management plugin -- accurate. |
| 53 | Extract Zip | `appian_extract_zip_converter.py` | SERVICE_CALL | Document Management plugin -- accurate at operation level. |
| 54 | Json To Csv Converter | `appian_json_to_csv_converter_converter.py` | SERVICE_CALL | Plugin/custom -- verify; confirm whether distinct from 'JSON Document to CSV' (#13) in your instance. |
| 55 | Consume from Kafka [Deprecated] | `appian_consume_from_kafka_deprecated_converter.py` | KAFKA_CONSUME | Deprecated + custom -- placeholder schema, verify, and confirm still-in-use before converting. |

## Reading the confidence column
- **Standard Appian node** / **Well-documented plugin** -- parameter schema is a reasoned, best-effort match to real Appian/AppMarket documentation.
- **Custom/internal, placeholder, verify** -- the node name (version suffixes like v3/v8, org-specific naming) indicates a bespoke plugin I have no way to verify. The drill-down engine still works correctly regardless -- only the *parameter names* in `PARAMETER_SCHEMA` and the demo data need correcting once you inspect a real export of that node type.

## What's shared vs. what's per-script
`appian_common.py` holds: design-object indexing, SAIL reference extraction, the recursive drill-down resolver (cycle-safe, memoized, depth-limited), Java naming helpers, and the 19 Java-scaffold category templates (gateway, subprocess, data-store write/delete, start/end, user task, stored proc, integration call, email, event-consume, AI text, record sync, generic service call, Kafka produce/consume).

Each `appian_<slug>_converter.py` supplies only: the node type's label, its Java category, any category-specific config (e.g. which service class/operation for SERVICE_CALL nodes), its parameter schema, and self-contained demo data -- then delegates everything else to `appian_common.py`. This is a single generated batch (`generate_all_node_scripts.py`); if you need to tweak a template's Java shape, it's usually faster to edit the category template in `appian_common.py` and let it apply everywhere at once, than to hand-edit 55 files.