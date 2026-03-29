variable "prefix" {
  description = "Prefix used for default resource naming."
  type        = string
  default     = "gastown"
}

variable "environment" {
  description = "Environment name used for naming and state keying."
  type        = string
  default     = "prod"
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
  default     = "eastus"
}

variable "resource_group_name" {
  description = "Optional explicit resource group name."
  type        = string
  default     = null
}

variable "app_service_plan_name" {
  description = "Optional explicit App Service plan name."
  type        = string
  default     = null
}

variable "web_app_name" {
  description = "Optional explicit Linux Web App name."
  type        = string
  default     = null
}

variable "acr_name" {
  description = "Optional explicit Azure Container Registry name (alphanumeric only)."
  type        = string
  default     = null
}

variable "storage_account_name" {
  description = "Optional explicit storage account name for persistent sqlite mount."
  type        = string
  default     = null
}

variable "service_plan_sku" {
  description = "App Service plan SKU (for example B1, P1v3)."
  type        = string
  default     = "B1"
}

variable "image_name" {
  description = "Container repository name inside ACR."
  type        = string
  default     = "gastown"
}

variable "image_tag" {
  description = "Container tag used by Terraform-managed baseline deployment."
  type        = string
  default     = "latest"
}

variable "enable_persistent_storage" {
  description = "Whether to mount Azure Files for sqlite persistence."
  type        = bool
  default     = true
}

variable "db_mount_path" {
  description = "Linux mount path inside the web app container."
  type        = string
  default     = "/mnt/data"
}

variable "db_file_name" {
  description = "sqlite database filename."
  type        = string
  default     = "gastown.db"
}

variable "max_concurrent_polecats" {
  description = "Default max concurrent polecats."
  type        = number
  default     = 4
}

variable "stuck_timeout_seconds" {
  description = "Default witness stuck timeout seconds."
  type        = number
  default     = 120
}

variable "gastown_model" {
  description = "Default LLM model identifier."
  type        = string
  default     = "anthropic/claude-sonnet-4-6"
}

variable "app_settings" {
  description = "Additional web app settings merged with Gastown defaults."
  type        = map(string)
  default     = {}
}
