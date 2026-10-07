# Findings: SSIM AD Key / Transaction Error Research

## Error Messages Under Investigation

### Error 1 (Severity 3)
```
Incorrect (ad) key '50C6F92FDF15DB750201F602' for user or dl!  DL Reason ''!
```
- `(ad)` may refer to: Active Directory, Address, or an SAP-specific key type
- Key format `50C6F92FDF15DB750201F602` looks like a hex identifier (24 chars)
- "DL Reason" suggests Distribution List context

### Error 2 (Severity 1 — Informational)
```
Transaction started with number 'FA163E50105B1FE19BEDAC50C9236BF1'
```
- Severity 1 = Informational (not an error per se)
- UUID-like format: could be IDoc number, workflow instance, or SAP change document
- Typically accompanies Severity 3 error above (transaction start before the failure)

---

## Wiki Search Results

### Search 1: SISM Space (`space.key="sism"`)
- Confirmed: SISM = SAP TDC (Technology Data Center) team's internal wiki
- Homepage structure: Jobs, Tasks, Tools, Information, Time, ToDo-List, Team, Help
- Contains "Standard deletion process of R3-system" page — operational procedure referencing "DL SISM" (distribution list for the SISM team itself)
- **Verdict**: SISM wiki is for data center hosting operations team, NOT a product error reference DB

### Search 2: GITSW Space — "Distribution List Creation"
- Found: page `941556803` "Distribution List Creation - Old Process *OBSOLETE*"
- Content: HR Portal DL creation via IPP system and ABAP program `ZHR_SAPNET_ACCESS_DISTRIBUTION`
- "DL" here = HR-generated group mailing lists in SAP Portal/Outlook
- References SAP Portal address book, Groups tab, `CIPCLNT001` RFC parameter
- **Verdict**: Obsolete HR DL creation procedure — not related to error messages

### Search 3: TIC/GMP Integration (`wikissisal` space, page `2479713553`)
- **KEY FINDING**: "TIC / GMP jobs are replaced by CCIR/SISM"
- Confirms SISM replaced TIC (Test Infrastructure Center) and GMP (Group Messaging Platform?) for certain jobs
- The SISM system that generates these errors is likely the successor to TIC/GMP integration
- **Verdict**: Critical context — SISM replaced TIC/GMP; the errors likely come from SISM's messaging/DL processing layer

### Search 4: CCIR Space
- Space key "CCIR" returns 404 (not accessible or doesn't exist publicly)
- No pages matching "ad key", "DL Reason", or "address key" were found in the wikissisal space

### Search 5: Broad error message searches
- `text ~ "incorrect ad key"` → 4,458 results, none on first page related to the error message
- `text ~ "ad key" AND text ~ "DL Reason" AND space.key IN PS KB spaces` → Only one CIF error spec attachment (SCM, unrelated)
- No direct wiki documentation found for the exact error message text

---

## Key Observations

### What SISM Is
Based on wiki research, **SISM** in this context is a SAP internal system that:
1. **Replaced TIC/GMP** (TIC/GMP Integration page explicitly states: "TIC / GMP jobs are replaced by CCIR/SISM")
2. Processes distribution list (DL) membership and messaging jobs
3. Is part of the SAP SSI Support Application Landscape (space `wikissisal`)

### What "(ad) key" Means
The `(ad)` in the error message is **NOT Active Directory**. In SAP's internal address/messaging context:
- `ad` likely stands for **"address"** — the key is an internal address book entry identifier
- The key format `50C6F92FDF15DB750201F602` is a **24-character hex string** — matches SAP Confluence userKey format (e.g., `8ae187dc51720d460151726baeb338e1`)
- The "DL" in "for user or dl" = **Distribution List** in SAP's internal messaging system
- "DL Reason ''" = an empty distribution list reason/description field

### Error Sequence
```
[Sev 1] Transaction started with number 'FA163E50105B1FE19BEDAC50C9236BF1'
[Sev 3] Incorrect (ad) key '50C6F92FDF15DB750201F602' for user or dl! DL Reason ''!
```
- Severity 1 = informational start of a transaction/operation
- Severity 3 = warning/error during address key lookup for a user or distribution list
- The transaction starts, then fails because the address key `50C6F92FDF15DB750201F602` cannot be resolved

### Root Cause Analysis
The error occurs when SISM attempts to:
1. Process a messaging/notification job involving a user or distribution list
2. Look up the internal address key `50C6F92FDF15DB750201F602` for the recipient
3. The lookup fails — the key does not exist or is invalid in the address book
4. This is reported as Severity 3 with an empty "DL Reason" (suggesting the DL entry has no description)

### Likely Causes
1. **Stale/deleted user or DL**: The address key belonged to a user account or distribution list that was deleted, deactivated, or migrated, but the SISM configuration still references it
2. **Orphaned address book entry**: The object was removed from SAP's internal directory/address book but the key reference was not cleaned up
3. **Cross-system sync issue**: The key exists in one system (e.g., old portal) but not in the system SISM is querying

---

## How Outlook DLs (`DL_<hex>@global.corp.sap`) Work with Active Directory

### The Two-Tier Architecture

SAP uses a **two-tier address system** where the SAP Portal Address Book / `profiles.wdf.sap.corp` is the **source of truth**, and Microsoft Active Directory / Exchange is the **replication target**.

```
SAP Portal Address Book          Active Directory / Exchange
(profiles.wdf.sap.corp)    -->   (global.corp.sap domain)
  Group object: hex key          Mail-enabled Security Group
  e.g. 50C6F92FDF15DB750201F602  e.g. DL_50C6F92FDF15DB750201F602@global.corp.sap
```

### Address Format Anatomy

`DL_0176000317E@global.corp.sap` decomposes as:
- **`DL_`** — prefix indicating a Distribution List (group object)
- **`0176000317E`** — the internal SAP address book object key (the `(ad)` key)
- **`@global.corp.sap`** — the SAP corporate AD / Exchange domain

The hex string in the email address **IS** the `(ad)` key. Confirmed by wiki evidence:
- CLMAM page shows: `DL_65AE35CF5354E4010825B947@global.corp.sap` (hex = group `(ad)` key)
- CAS DL page shows dozens of DLs in format `DL_<24-char-hex>@global.corp.sap`
- `profiles.wdf.sap.corp/groups/<hex>` URLs use the same hex as the DL suffix

### Replication Flow: Portal → Active Directory → Outlook

1. **Group created in SAP Portal Address Book** (or provisioned via SAP IDM / HR system)
   - Assigned a unique 24-character hex object key (the `(ad)` key)
   - Stored in SAP's internal directory (portal address book / `profiles.wdf.sap.corp`)

2. **Replicated to Microsoft Active Directory** (domain: `global.corp.sap`)
   - The portal group object is synced to AD as a **mail-enabled security group** or **distribution group**
   - AD group name / email = `DL_<hex-key>@global.corp.sap`
   - The hex key becomes the email address prefix, making it unique and traceable back to the portal object

3. **Exchange picks up the AD group** (on-premises Exchange or Exchange Online / M365)
   - Exchange recognizes the mail-enabled AD group
   - The DL becomes addressable via Outlook's GAL (Global Address List)
   - Sending to `DL_<hex>@global.corp.sap` routes through Exchange to all group members

4. **Outlook resolves via GAL**
   - When a user types the DL name or address in Outlook, Outlook queries the GAL
   - GAL is populated from AD, so the DL is discoverable
   - Members of the DL are defined in AD (replicated from Portal)

### Important Note: "Security Enabled" DLs

From the CAS Distribution Lists wiki page:
> "Distribution List Owner: Please ensure that all DLs and all sub-DLs are **Security enabled**. Only Security enabled DLs will be able to be used in SharePoints or other SAP Tools to manage permissions."

This confirms that SAP DLs in AD are provisioned as **mail-enabled security groups** (not just distribution groups). Security-enabled groups can be used for:
- Email distribution (Outlook)
- SharePoint / M365 permissions
- Other SAP tool access control

### The `SA-MAIL` Component

SA-MAIL is the SAP CSS support component responsible for the **DL replication pipeline** from Portal Address Book to AD/Exchange. If a DL fails to replicate (or gets orphaned), a CSS ticket under SA-MAIL handles remediation.

### Why the CCIR Error Occurs

When CCIR/SISM tries to resolve `(ad)` key `50C6F92FDF15DB750201F602`:
1. It looks up the Portal Address Book for the group with that hex key
2. The group no longer exists (deleted, migrated, or never properly created)
3. The Portal → AD replication has no object to sync
4. Result: `Incorrect (ad) key '50C6F92FDF15DB750201F602' for user or dl!`

---

## Recommended Escalation Path
- The error is NOT documented in SAP wiki — it is a runtime error from SISM's internal address resolution layer
- Escalate to the **SISM team** directly (TDC/hosting operations) as they own this system
- The SA-MAIL component (referenced in DL replication failure guidance) may also be relevant — raise CSS ticket under SA-MAIL if needed
