provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  # Assume the platform automation role to get deploy permissions
  # (iam:CreateRole etc.) that the interactive DevOps SSO role lacks. Same
  # mechanism the pace/manila ECS repo uses.
  assume_role {
    role_arn     = var.automation_role_arn
    session_name = "loki-ecs-terraform"
  }

  default_tags {
    tags = {
      Project     = var.project_name
      ManagedBy   = "Terraform"
      Environment = "dev"
      Repository  = "https://scm.tpicapcloud.com/Dann.JupiterDimalibot/project-steph"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
