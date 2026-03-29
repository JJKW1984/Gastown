# Local OpenTofu Development

This guide covers how to work with OpenTofu locally for the Gastown infrastructure.

## Installation

### Windows (using winget)

```powershell
winget install OpenTofu.OpenTofu
```

### macOS (using Homebrew)

```bash
brew install opentofu
```

### Linux

Download from [OpenTofu releases](https://github.com/opentofu/opentofu/releases) or use your package manager.

## Verification

```bash
tofu version
```

Should output: `OpenTofu v1.x.x` or later.

## Working with the Gastown infrastructure

### Prerequisites

1. Azure CLI installed and authenticated:
   ```bash
   az login
   ```

2. GitHub environment variables and secrets configured (see [azure-webapp-terraform-deploy.md](./azure-webapp-terraform-deploy.md))

3. Terraform backend storage created (one-time bootstrap from deployment docs)

### Initialize backends

Navigate to `infra/terraform` and initialize with Azure backend:

```bash
cd infra/terraform

# Set your backend configuration details
export TF_STATE_RESOURCE_GROUP="<your-tf-state-rg>"
export TF_STATE_STORAGE_ACCOUNT="<your-tfstatesa>"
export TF_STATE_CONTAINER="<tfstate-container>"
export TF_STATE_KEY="gastown-prod.tfstate"

# Initialize with remote backend
tofu init \
  -backend-config="resource_group_name=$TF_STATE_RESOURCE_GROUP" \
  -backend-config="storage_account_name=$TF_STATE_STORAGE_ACCOUNT" \
  -backend-config="container_name=$TF_STATE_CONTAINER" \
  -backend-config="key=$TF_STATE_KEY" \
  -backend-config="use_azuread_auth=true"
```

### Plan changes

```bash
# View what changes will be applied
tofu plan -var-file=terraform.tfvars
```

Create a plan file for review:

```bash
tofu plan -var-file=terraform.tfvars -out=tfplan
```

### Apply infrastructure

```bash
# Apply with saved plan
tofu apply tfplan

# Or apply interactively (will prompt for confirmation)
tofu apply -var-file=terraform.tfvars
```

### Inspect state

```bash
# List all resources
tofu state list

# Show details of a resource
tofu state show azurerm_linux_web_app.app

# Show all outputs
tofu output
```

### Destroy infrastructure

```bash
# Preview what will be destroyed
tofu plan -destroy -var-file=terraform.tfvars

# Destroy (will prompt for confirmation)
tofu destroy -var-file=terraform.tfvars
```

## Format and validate code

### Format HCL files

```bash
# Check if formatting is correct
tofu fmt -check

# Auto-format files
tofu fmt -recursive
```

### Validate syntax

```bash
tofu init -backend=false
tofu validate
```

## Environment variables configuration

Create a `.tfvars` file or use environment variables to override defaults:

```bash
# Using a .tfvars file
tofu apply -var-file=terraform.tfvars

# Using environment variables
export TF_VAR_location="eastus"
export TF_VAR_environment="prod"
export TF_VAR_gastown_model="anthropic/claude-sonnet-4-6"
tofu apply
```

## Troubleshooting

### Backend authentication fails

Ensure you're logged into Azure:

```bash
az login
```

And check that the backend storage account and container exist:

```bash
az storage account show --name $TF_STATE_STORAGE_ACCOUNT --resource-group $TF_STATE_RESOURCE_GROUP
az storage container exists --account-name $TF_STATE_STORAGE_ACCOUNT --name $TF_STATE_CONTAINER
```

### State lock issues

If state is locked (from a failed apply):

```bash
tofu force-unlock <lock-id>
```

Get the lock ID from the error message.

### Provider version conflicts

Clear the local cache and reinitialize:

```bash
rm -rf .terraform
tofu init
```

## Migrating from Terraform to OpenTofu

If you have existing Terraform state, you can use the same state files with OpenTofu (and vice versa). Both tools are compatible at the HCL and state level.

```bash
# Initialize with existing Terraform state
tofu init

# Plan and apply as normal
tofu plan
tofu apply
```

## Additional Resources

- [OpenTofu Documentation](https://opentofu.org/docs/)
- [OpenTofu GitHub](https://github.com/opentofu/opentofu)
