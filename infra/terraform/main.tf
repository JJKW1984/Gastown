# Generate a random suffix for globally-unique resource naming.
# Compatible with both Terraform and OpenTofu.
resource "random_string" "suffix" {
  length  = 5
  special = false
  upper   = false
  numeric = true
}

locals {
  # Sanitize prefix and environment for Azure naming constraints.
  prefix_sanitized = regexreplace(lower(var.prefix), "[^a-z0-9-]", "")
  env_sanitized    = regexreplace(lower(var.environment), "[^a-z0-9-]", "")

  # Construct resource names with optional overrides.
  resource_group_name = coalesce(var.resource_group_name, "${local.prefix_sanitized}-${local.env_sanitized}-rg")
  app_service_plan    = coalesce(var.app_service_plan_name, "${local.prefix_sanitized}-${local.env_sanitized}-plan")
  web_app_name        = coalesce(var.web_app_name, "${local.prefix_sanitized}-${local.env_sanitized}-web-${random_string.suffix.result}")

  acr_name_generated = substr(
    regexreplace("${local.prefix_sanitized}${local.env_sanitized}acr${random_string.suffix.result}", "[^a-z0-9]", ""),
    0,
    50
  )
  acr_name = coalesce(var.acr_name, local.acr_name_generated)

  storage_account_generated = substr(
    regexreplace("${local.prefix_sanitized}${local.env_sanitized}st${random_string.suffix.result}", "[^a-z0-9]", ""),
    0,
    24
  )
  storage_account_name = coalesce(var.storage_account_name, local.storage_account_generated)

  image_reference = "${azurerm_container_registry.acr.login_server}/${var.image_name}:${var.image_tag}"
}

resource "azurerm_resource_group" "main" {
  name     = local.resource_group_name
  location = var.location
}

resource "azurerm_container_registry" "acr" {
  # Disable admin credentials and use managed identity pull from Web App.
  name                = local.acr_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false
}

resource "azurerm_service_plan" "plan" {
  name                = local.app_service_plan
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = var.service_plan_sku
}

resource "azurerm_storage_account" "app" {
  count = var.enable_persistent_storage ? 1 : 0

  name                     = local.storage_account_name
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"

  allow_nested_items_to_be_public = false
}

resource "azurerm_storage_share" "app" {
  count = var.enable_persistent_storage ? 1 : 0

  name               = "gastown-data"
  storage_account_id = azurerm_storage_account.app[0].id
  quota              = 20
}

resource "azurerm_linux_web_app" "app" {
  name                = local.web_app_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  service_plan_id     = azurerm_service_plan.plan.id

  https_only              = true
  client_affinity_enabled = false

  identity {
    type = "SystemAssigned"
  }

  site_config {
    always_on                           = true
    ftps_state                          = "Disabled"
    health_check_path                   = "/"
    acr_use_managed_identity_credentials = true

    application_stack {
      docker_image_name   = local.image_reference
      docker_registry_url = "https://${azurerm_container_registry.acr.login_server}"
    }
  }

  app_settings = merge(
    {
      WEBSITES_PORT                    = "8000"
      GASTOWN_HOST                     = "0.0.0.0"
      GASTOWN_PORT                     = "8000"
      GASTOWN_DB_PATH                  = var.enable_persistent_storage ? "${var.db_mount_path}/${var.db_file_name}" : var.db_file_name
      GASTOWN_MAX_CONCURRENT_POLECATS  = tostring(var.max_concurrent_polecats)
      GASTOWN_STUCK_TIMEOUT_SECONDS    = tostring(var.stuck_timeout_seconds)
      GASTOWN_MODEL                    = var.gastown_model
    },
    var.app_settings
  )

  dynamic "storage_account" {
    # SQLite persistence uses Azure Files mount to survive container restarts.
    for_each = var.enable_persistent_storage ? [1] : []
    content {
      name         = "gastown-data"
      type         = "AzureFiles"
      account_name = azurerm_storage_account.app[0].name
      share_name   = azurerm_storage_share.app[0].name
      access_key   = azurerm_storage_account.app[0].primary_access_key
      mount_path   = var.db_mount_path
    }
  }
}

resource "azurerm_role_assignment" "acr_pull" {
  # Grant least-privilege pull access from Web App identity to ACR.
  scope                            = azurerm_container_registry.acr.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_linux_web_app.app.identity[0].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}
