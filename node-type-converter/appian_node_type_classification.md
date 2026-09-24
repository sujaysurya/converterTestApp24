# Appian Node Type Classification — Standard vs. Needs Developer Input

Companion reference to the `appian_node_type_converters.zip` script suite.
Use this to prioritize which node types are safe to convert as-is (verifying
only field names) vs. which need a conversation with your Appian developer
before conversion work starts, so effort isn't wasted converting the wrong
functionality.

---

## Group A — Standard Appian process nodes / well-documented AppMarket plugins (40)

Functionality is known; only exact parameter *field names* need confirming
against your export via `--inspect`.

### Process control (native Appian)
| Node type |
|---|
| Sub_PROC |
| Start Node |
| End Node |
| XOR |
| AND |
| OR |
| Complex |
| User Input Task |
| Start Process |
| Start Process [Deprecated] |
| Intermediate Consuming Event |
| Execute Stored Procedure |
| Call Integration |
| Send E-Mail |

### Data Store / Records (native Appian)
| Node type |
|---|
| Write To Data Store Entity |
| Write to Multiple Dta Store Entities |
| Write Records and Related Records |
| Delete and Data Store Entries |
| Delete Records and Related Records |

### Document Management plugin suite
| Node type |
|---|
| DeleteDocument |
| Move Document |
| Create Folder |
| Rename Folders *(plural name — confirm not a bulk variant)* |
| Delete Folder |
| FolderSecurity |
| Document Zipper |
| Update Document |
| Extract Zip |

### Users & Groups Management plugin suite
| Node type |
|---|
| Add members to Group |
| Remove Group Members |
| Update User Profile |
| Deactivate Users |
| Reactivate Users |
| Create User |
| cHANGE User Password |

### Excel/CSV utility plugin suite
| Node type |
|---|
| Export DataStore Entity to CSV |
| Generate Excel from CSV |
| Export Data Store Entity to Excel |
| Combine Excel Sheet |
| Delete Excel Sheet |

---

## Group B — Needs Appian developer input (15)

Version-suffixed, org-specific naming, or genuine ambiguity about actual
functionality. Not verifiable from general Appian/AppMarket knowledge.

| Node type | Why it needs a developer |
|---|---|
| Generate Text | Likely an AI/document-generation smart service, but which one (and its prompt/model config) is implementation-specific |
| Import Excel to Database v3 | Version suffix strongly suggests a custom/internal build, not a stock plugin |
| Insert Documents as Base64 into Database | No standard Appian equivalent confirmable — likely custom |
| Publish to Kafka | Kafka integration isn't a stock Appian capability — custom/internal connector |
| Publish to Kafka in Bulk | Same as above |
| Delete Documents Created Before Date | Could be the Document Management plugin's retention feature, or a custom-built job — functionality itself is ambiguous |
| Sync Records | No standard Appian node by this name — likely a custom Records-sync utility |
| Set Alert Recipient for PM | "PM" likely = Process Model admin alerting, but this isn't a stock capability |
| Kafka Server Listner Daemon v8 | Version-suffixed custom daemon — need to understand what it actually listens for and dispatches |
| Get Base64 Document from Database | No standard equivalent — likely pairs with "Insert Documents as Base64" above as a custom read/write pair |
| JSON Document to CSV | Custom/plugin — also worth asking whether this is the same as "Json To Csv Converter" below |
| Database Access | Generic name — need to know if this wraps raw SQL, a query builder, or something else entirely |
| Export SQL To Excel | Custom/plugin — need actual parameter shape |
| Json To Csv Converter | Custom/plugin — worth confirming whether distinct from "JSON Document to CSV" above |
| Consume from Kafka [Deprecated] | Deprecated custom consumer — worth confirming it's even still in use before spending conversion effort on it |

---

## Suggested first questions for your Appian developer

1. Are **"JSON Document to CSV"** and **"Json To Csv Converter"** the same plugin under two names?
2. Are **"Get Base64 Document from Database"** and **"Insert Documents as Base64 into Database"** a matched read/write pair built together?
3. Is **"Consume from Kafka [Deprecated]"** still actually invoked anywhere in the 550 process models, or fully replaced by the Kafka daemon/publish nodes?
4. What does **"Set Alert Recipient for PM"** actually configure — process-model-level admin alerts, or something else?
5. Is **"Delete Documents Created Before Date"** the Document Management plugin's built-in retention feature, or a custom scheduled job?
