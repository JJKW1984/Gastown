# Azure Web App for Containers Deployment (OpenTofu / Terraform)

This repository uses a separate GitHub Actions workflow for Azure provisioning and deployment using **OpenTofu** (an open-source Terraform fork).

- Workflow: `.github/workflows/azure-webapp-deploy.yml`
- Infrastructure as Code: `infra/terraform` (compatible with both OpenTofu and Terraform)

## Compatibility

The HCL code in `infra/terraform` is **100% compatible** with both:
- **OpenTofu >= 1.7.0** (recommended)
- **Terraform >= 1.7.0** (also supported)

Both tools use identical syntax and provider APIs. The workflow uses `tofu` commands; if you prefer Terraform, simply replace `tofu` with `terraform` in the workflow steps.

## What the workflow does

1. Pull request checks
- Runs `tofu fmt` and `tofu validate` using a local backend mode.

2. Manual deployment (`workflow_dispatch`)
- Runs `tofu plan`.
- Optionally runs `tofu apply` (when `apply=true`) behind a GitHub Environment approval gate.
- Builds the application image from `Dockerfile`.
- Pushes image to Azure Container Registry.
- Updates Azure Web App for Containers to the new image tag.

## Required GitHub Secrets

Set these in repository or environment secrets:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

## Required GitHub Variables

Set these in repository or environment variables:

- `AZURE_LOCATION` (for example `eastus`)
- `AZURE_ENV_NAME` (for example `prod`)
- `RESOURCE_GROUP_NAME`
- `APP_SERVICE_PLAN_NAME`
- `WEBAPP_NAME`
- `ACR_NAME`
- `TF_STATE_RESOURCE_GROUP`
- `TF_STATE_STORAGE_ACCOUNT`
- `TF_STATE_CONTAINER`

## One-time OpenTofu/Terraform backend bootstrap

Create a storage account/container for OpenTofu remote state before first workflow run:

```bash
az group create --name <tf-state-rg> --location <location>
az storage account create --name <tfstatesa> --resource-group <tf-state-rg> --location <location> --sku Standard_LRS
az storage container create --name <tfstate-container> --account-name <tfstatesa> --auth-mode login
```

Then set:

- `TF_STATE_RESOURCE_GROUP=<tf-state-rg>`
- `TF_STATE_STORAGE_ACCOUNT=<tfstatesa>`
- `TF_STATE_CONTAINER=<tfstate-container>`

## Runtime app settings

Terraform sets defaults for these settings:

- `WEBSITES_PORT=8000`
- `GASTOWN_HOST=0.0.0.0`
- `GASTOWN_PORT=8000`
- `GASTOWN_DB_PATH=/mnt/data/gastown.db` (when persistent storage is enabled)
- `GASTOWN_MAX_CONCURRENT_POLECATS`
- `GASTOWN_STUCK_TIMEOUT_SECONDS`
- `GASTOWN_MODEL`

Add provider secrets as Web App app settings or Key Vault references:

- `ANTHROPIC_API_KEY` or
- `OPENAI_API_KEY` or
- `AZURE_API_KEY` + `AZURE_API_BASE` + `AZURE_API_VERSION`

## Running a deployment

1. Open GitHub Actions and run `Azure Web App Deploy (OpenTofu / Terraform)`.
2. Choose `environment` (for example `production`).
3. Set `apply=true` to provision/apply and deploy.
4. Optionally set `image_tag`; if empty, commit SHA is used.
5. Approve the deployment in the selected GitHub Environment.

**Note**: The workflow uses OpenTofu CLI (`tofu` commands). If you prefer Terraform, modify the workflow to use `terraform` commands instead. Both are fully compatible with the HCL code.

## Local development

See [opentofu-local-dev.md](./opentofu-local-dev.md) for instructions on running OpenTofu locally for development, testing, and manual changes.

## Rollback

Run the same workflow with:

- `apply=true`
- `image_tag=<previous-working-tag>`

This redeploys a previous image tag without changing application code.
