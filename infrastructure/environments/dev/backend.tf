terraform {
  required_version = ">= 1.5.0"

  # GitLab-managed Terraform state (HTTP backend), same as the pace/manila repos.
  # Configured at init time via `terraform init -backend-config=...`.
  backend "http" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}
