# Infrastructure as Code for Gastown Azure Web App for Containers.
# Compatible with both Terraform >= 1.7.0 and OpenTofu >= 1.7.0.
# OpenTofu uses identical HCL syntax and provider APIs as Terraform.

terraform {
  required_version = ">= 1.7.0"

  # Both Terraform and OpenTofu use the same provider registry format.
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
  }

  # Azure Blob Storage backend for remote state.
  # Backend config passed at init time via CLI flags or environment variables.
  backend "azurerm" {}
}

provider "azurerm" {
  features {}
}
