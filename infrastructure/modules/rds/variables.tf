variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
}

variable "vpc_id" {
  description = "VPC to place the database in"
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the DB subnet group and proxy"
  type        = list(string)
}

variable "database_name" {
  description = "Initial database name"
  type        = string
  default     = "loki"
}

variable "master_username" {
  description = "Master DB username"
  type        = string
  default     = "loki_app"
}

variable "engine_version" {
  description = "Aurora PostgreSQL engine version"
  type        = string
  default     = "16.4"
}

variable "min_acu" {
  description = "Serverless v2 minimum Aurora Capacity Units"
  type        = number
  default     = 0.5
}

variable "max_acu" {
  description = "Serverless v2 maximum Aurora Capacity Units"
  type        = number
  default     = 4
}
