output "resource_group_name" {
  description = "Resource group name hosting Gastown resources."
  value       = azurerm_resource_group.main.name
}

output "acr_name" {
  description = "Azure Container Registry name."
  value       = azurerm_container_registry.acr.name
}

output "acr_login_server" {
  description = "ACR login server used for image pushes and pulls."
  value       = azurerm_container_registry.acr.login_server
}

output "web_app_name" {
  description = "Linux Web App name."
  value       = azurerm_linux_web_app.app.name
}

output "web_app_default_hostname" {
  description = "Default public hostname for the Linux Web App."
  value       = azurerm_linux_web_app.app.default_hostname
}

output "web_app_url" {
  description = "Fully-qualified URL for the deployed web app."
  value       = "https://${azurerm_linux_web_app.app.default_hostname}"
}

output "storage_account_name" {
  description = "Persistent storage account when enabled."
  value       = var.enable_persistent_storage ? azurerm_storage_account.app[0].name : null
}
