# Azure Resource Group Migration — Command Documentation

**Date:** 25/03/2026  
**Source RG:** `rg-Yash.Desai-2452`  
**Target RG:** `sellafield-dpmo-dev`  
**Subscription ID:** `530e4a26-4cf6-44b1-9549-84ae1b36b4d9`  
**Backup Location:** `C:\AzureBackup\`

---

## Phase 0 — Prerequisites & Setup

- Verify Azure CLI is installed
```cmd
az --version
```

- Login to Azure
```cmd
az login
```

- Verify you are on the correct subscription
```cmd
az account show
```

- Set all variables used throughout the migration
```cmd
set SOURCE_RG=rg-Yash.Desai-2452
set TARGET_RG=sellafield-dpmo-dev
set SUBSCRIPTION_ID=530e4a26-4cf6-44b1-9549-84ae1b36b4d9
set BACKUP_FOLDER=C:\AzureBackup
```

- Create local backup folder to store all exports
```cmd
mkdir %BACKUP_FOLDER%
```

---

## Phase 1 — Backup Source Resource Group

- Export full ARM template of source RG as a safety net (can be used to redeploy if anything goes wrong)
```cmd
az group export --name %SOURCE_RG% --include-parameter-default-value > %BACKUP_FOLDER%\source_rg_template.json
echo ARM Template backup saved to %BACKUP_FOLDER%\source_rg_template.json
```

- Save all role assignments from source RG (used to recreate permissions in target RG later)
```cmd
az role assignment list --resource-group %SOURCE_RG% --output json > %BACKUP_FOLDER%\role_assignments.json
echo Role assignments saved to %BACKUP_FOLDER%\role_assignments.json
```

- Save full resource list before moving (used to verify everything moved correctly later)
```cmd
az resource list --resource-group %SOURCE_RG% --output table > %BACKUP_FOLDER%\resource_list_before.txt
az resource list --resource-group %SOURCE_RG% --output json > %BACKUP_FOLDER%\resource_list_before.json
echo Resource list saved to %BACKUP_FOLDER%\resource_list_before.txt
```

- Save all CognitiveServices account details
```cmd
az cognitiveservices account list --resource-group %SOURCE_RG% --output json > %BACKUP_FOLDER%\cognitive_services_backup.json
```

- Save all Web App details
```cmd
az webapp list --resource-group %SOURCE_RG% --output json > %BACKUP_FOLDER%\webapp_backup.json
```

- Save App Insights details
```cmd
az monitor app-insights component show --resource-group %SOURCE_RG% --output json > %BACKUP_FOLDER%\appinsights_backup.json
```

- Save all ML Workspace details
```cmd
az ml workspace list --resource-group %SOURCE_RG% --output json > %BACKUP_FOLDER%\ml_workspace_backup.json
echo ML Workspaces backup saved!
```

- Get all Web App names (used to check hybrid connections)
```cmd
az webapp list --resource-group %SOURCE_RG% --query "[].name" --output tsv > %BACKUP_FOLDER%\webapp_names.txt
type %BACKUP_FOLDER%\webapp_names.txt
```

> **Note:** `webapp_names.txt` was 0 KB — no Web Apps found in source RG so hybrid connections step was skipped.

---

## Phase 2 — Pre-Move Checks

- Check for resource locks that would block the move (must be removed before moving)
```cmd
az lock list --resource-group %SOURCE_RG% --output table
```

> **Result:** No locks found — safe to proceed.

- Save all resource IDs to a text file
```cmd
az resource list --resource-group %SOURCE_RG% --query "[].id" --output tsv > %BACKUP_FOLDER%\resource_ids.txt
type %BACKUP_FOLDER%\resource_ids.txt
```

- Save all resource IDs as JSON (used for validation and filtering)
```cmd
az resource list --resource-group %SOURCE_RG% --query "[].id" --output json > %BACKUP_FOLDER%\resource_ids.json
type %BACKUP_FOLDER%\resource_ids.json
```

- Filter to top-level resources only and exclude CognitiveServices (Azure does not support moving AIServices — child resources move automatically with their parent)
```cmd
python -c "import json; ids=json.load(open(r'%BACKUP_FOLDER%\resource_ids.json')); top_level=[i for i in ids if len(i.split('/providers/')[1].split('/')) == 3 and 'cognitiveservices' not in i.lower()]; json.dump({'resources': top_level, 'targetResourceGroup': '/subscriptions/%SUBSCRIPTION_ID%/resourceGroups/%TARGET_RG%'}, open(r'%BACKUP_FOLDER%\validate_body.json', 'w')); open(r'%BACKUP_FOLDER%\resource_ids.txt', 'w').write('\n'.join(top_level)); print(f'Top-level movable resources: {len(top_level)}')"
```

> **Result:** Total 14 resources → 13 top-level → 12 movable (1 CognitiveServices excluded)

- Validate the move without actually moving anything (catches unmovable resources early)
```cmd
az resource invoke-action --action validateMoveResources --ids /subscriptions/%SUBSCRIPTION_ID%/resourceGroups/%SOURCE_RG% --request-body @%BACKUP_FOLDER%\validate_body.json
```

> **Result:** `{}` — validation passed, all 12 resources are safe to move.

---

## Phase 3 — Move Resources

- Move all 12 top-level resources to target RG one by one (do NOT close CMD window during this step)
```cmd
for /f "delims=" %i in (%BACKUP_FOLDER%\resource_ids.txt) do (echo Moving: %i && az resource move --destination-group %TARGET_RG% --ids "%i" && echo SUCCESS: %i || echo ERROR: %i)
echo All resources processed.
```

> **Result:** All 12 resources moved successfully with no errors.

---

## Phase 4 — Verify the Move

- Check what is now in the target RG
```cmd
az resource list --resource-group %TARGET_RG% --output table > %BACKUP_FOLDER%\resource_list_after.txt && type %BACKUP_FOLDER%\resource_list_after.txt
```

- Check what is left in the source RG (should only be CognitiveServices)
```cmd
az resource list --resource-group %SOURCE_RG% --output table
```

- Compare resource count before and after
```cmd
echo BEFORE: && find /c "" %BACKUP_FOLDER%\resource_list_before.txt && echo AFTER: && az resource list --resource-group %TARGET_RG% --query "length(@)"
```

---

## Phase 5 — Recreate Role Assignments

- Create Python script to recreate role assignments using the backed-up JSON file
```cmd
notepad %BACKUP_FOLDER%\assign_roles.py
```

Paste the following into Notepad and save:
```python
import json, subprocess

with open(r'C:\AzureBackup\role_assignments.json') as f:
    assignments = json.load(f)

scope = '/subscriptions/530e4a26-4cf6-44b1-9549-84ae1b36b4d9/resourceGroups/sellafield-dpmo-dev'

for a in assignments:
    principal = a['principalId']
    role = a['roleDefinitionName']
    print(f'Assigning: {role} to {principal}')
    cmd = f'az role assignment create --assignee {principal} --role "{role}" --scope {scope}'
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    if result.returncode == 0:
        print('SUCCESS: ' + role)
    else:
        print('ERROR: ' + result.stderr.strip())

print('All role assignments processed!')
```

- Run the role assignment script
```cmd
python %BACKUP_FOLDER%\assign_roles.py
```

> **Result:** All 4 role assignments recreated successfully (Azure AI User, Azure AI Developer, Owner, Azure AI Administrator).

- Confirm role assignments in target RG
```cmd
az role assignment list --resource-group %TARGET_RG% --output table
```

---

## Phase 6 — Recreate CognitiveServices Account

> **Why manual?** Azure does not support moving `AIServices/CognitiveServices` accounts between resource groups. They must be deleted and recreated.

- Delete the child project first using Azure REST API (CLI argument inconsistency workaround)
```cmd
az rest --method delete --url "https://management.azure.com/subscriptions/%SUBSCRIPTION_ID%/resourceGroups/%SOURCE_RG%/providers/Microsoft.CognitiveServices/accounts/movar-secure-azure-resource/projects/movar-secure-azure?api-version=2025-04-01-preview"
```

- Delete the parent CognitiveServices account from source RG
```cmd
az cognitiveservices account delete --name movar-secure-azure-resource --resource-group %SOURCE_RG%
```

- Purge the soft-deleted account (must purge before reusing the same name)
```cmd
az cognitiveservices account purge --name movar-secure-azure-resource --resource-group %SOURCE_RG% --location uksouth
```

- Recreate the CognitiveServices account in target RG with same name and custom domain (preserves original endpoint URL)
```cmd
az cognitiveservices account create --name movar-secure-azure-resource --resource-group %TARGET_RG% --kind AIServices --sku S0 --location uksouth --custom-domain movar-secure-azure-resource --yes
```

- Enable project management on the new account
```cmd
az cognitiveservices account update --name movar-secure-azure-resource --resource-group %TARGET_RG% --set properties.allowProjectManagement=true
```

- Recreate the project inside the new account
```cmd
az cognitiveservices account project create --account-name movar-secure-azure-resource --resource-group %TARGET_RG% --name movar-secure-azure
```

- Verify the account was created correctly in target RG
```cmd
az cognitiveservices account show --name movar-secure-azure-resource --resource-group %TARGET_RG% --output table
```

---

## Phase 7 — Final Verification

- Export role assignments from both RGs for comparison
```cmd
az role assignment list --resource-group %SOURCE_RG% --output table > %BACKUP_FOLDER%\source_roles_final.txt
az role assignment list --resource-group %TARGET_RG% --output table > %BACKUP_FOLDER%\target_roles_final.txt
```

- View both role assignment lists side by side
```cmd
echo ===== SOURCE RG ROLES ===== && type %BACKUP_FOLDER%\source_roles_final.txt
echo ===== TARGET RG ROLES ===== && type %BACKUP_FOLDER%\target_roles_final.txt
```

- Run automated comparison script to confirm permissions are identical
```cmd
notepad %BACKUP_FOLDER%\compare_roles.py
```

Paste the following into Notepad and save:
```python
import subprocess, json

def get_roles(rg):
    result = subprocess.run(
        f'az role assignment list --resource-group {rg} --output json',
        capture_output=True, text=True, shell=True
    )
    assignments = json.loads(result.stdout)
    return {(a['principalName'], a['roleDefinitionName']) for a in assignments}

source_rg = 'rg-Yash.Desai-2452'
target_rg = 'sellafield-dpmo-dev'

print('Fetching role assignments...')
source_roles = get_roles(source_rg)
target_roles = get_roles(target_rg)

print(f'\nSource RG has {len(source_roles)} role assignments')
print(f'Target RG has {len(target_roles)} role assignments')

missing = source_roles - target_roles
extra = target_roles - source_roles

if not missing and not extra:
    print('\n✅ PERFECT MATCH — All permissions are identical!')
else:
    if missing:
        print('\n❌ MISSING in Target RG (need to be added):')
        for principal, role in missing:
            print(f'   - {role} → {principal}')
    if extra:
        print('\n⚠️  EXTRA in Target RG (not in source):')
        for principal, role in extra:
            print(f'   + {role} → {principal}')
```

- Run the comparison script
```cmd
python %BACKUP_FOLDER%\compare_roles.py
```

> **Result:** `✅ PERFECT MATCH — All permissions are identical!`

---

## Phase 8 — Generate Post-Move Checklist

- Create a checklist file for remaining manual tasks
```cmd
echo POST-MOVE MANUAL CHECKLIST > %BACKUP_FOLDER%\post_move_checklist.txt && echo ================================ >> %BACKUP_FOLDER%\post_move_checklist.txt && echo [ ] Recreate CognitiveServices account movar-secure-azure-resource in TARGET RG >> %BACKUP_FOLDER%\post_move_checklist.txt && echo [ ] Reconfigure CognitiveServices RAI Safety Provider >> %BACKUP_FOLDER%\post_move_checklist.txt && echo [ ] Reassign keys/endpoints to new CognitiveServices account >> %BACKUP_FOLDER%\post_move_checklist.txt && echo [ ] Reset App Insights Pricing Plan >> %BACKUP_FOLDER%\post_move_checklist.txt && echo [ ] Restore App Insights saved analytics items >> %BACKUP_FOLDER%\post_move_checklist.txt && echo [ ] Reconfigure ML Workspace Feature Sets >> %BACKUP_FOLDER%\post_move_checklist.txt && echo [ ] Reconfigure ML Inference Pools >> %BACKUP_FOLDER%\post_move_checklist.txt && echo [ ] Re-link ML Linked Workspaces >> %BACKUP_FOLDER%\post_move_checklist.txt && echo [ ] Reconfigure ML Feature Store Entities >> %BACKUP_FOLDER%\post_move_checklist.txt && type %BACKUP_FOLDER%\post_move_checklist.txt
```

---

## Final Migration Results

| Metric | Result |
|---|---|
| Resources moved | 12/12 ✅ |
| CognitiveServices recreated | ✅ |
| Role assignments recreated | 4/4 ✅ |
| Permissions verified | ✅ Perfect Match |
| Source RG cleared | ✅ |
| Endpoint URLs preserved | ✅ |
| Data loss | None |

---

## Backup Files Reference

| File | Purpose |
|---|---|
| `source_rg_template.json` | Full ARM backup of source RG |
| `cognitive_services_backup.json` | CognitiveServices config backup |
| `appinsights_backup.json` | App Insights config backup |
| `ml_workspace_backup.json` | ML Workspace config backup |
| `webapp_backup.json` | Web App config backup |
| `role_assignments.json` | Role assignments backup |
| `resource_ids.json` | All resource IDs (including child resources) |
| `resource_ids.txt` | Top-level movable resource IDs only |
| `validate_body.json` | Validation request body |
| `resource_list_before.txt` | Resource list before move |
| `resource_list_after.txt` | Resource list after move |
| `source_roles_final.txt` | Final source RG role assignments |
| `target_roles_final.txt` | Final target RG role assignments |
| `assign_roles.py` | Python script to recreate role assignments |
| `compare_roles.py` | Python script to compare role assignments |
| `post_move_checklist.txt` | Remaining manual tasks checklist |
