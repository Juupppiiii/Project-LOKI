variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
}

variable "service_names" {
  description = "Set of service names, each gets its own ECR repository"
  type        = set(string)
}

variable "image_retention_count" {
  description = "Number of recent images to retain per repository"
  type        = number
  default     = 20
}
