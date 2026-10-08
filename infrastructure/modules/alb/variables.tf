variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
}

variable "vpc_id" {
  description = "VPC to place the ALB in"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR, used for the ALB egress rule"
  type        = string
}

variable "subnet_ids" {
  description = "Subnets for the internal ALB"
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate ARN for the HTTPS listener"
  type        = string
}

variable "allowed_ingress_cidrs" {
  description = "Internal CIDRs allowed to reach the ALB on 443"
  type        = list(string)
}

variable "access_logs_bucket" {
  description = "S3 bucket name for ALB access logs"
  type        = string
}

variable "target_port" {
  description = "Container port the API listens on"
  type        = number
  default     = 8800
}
