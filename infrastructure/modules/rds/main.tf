# Aurora PostgreSQL Serverless v2 + RDS Proxy for LOKI's governed store.
# The app connects through the proxy (connection pooling, IAM-less rotation via
# Secrets Manager). The DB is private; only the application security group may
# reach the proxy.

# --- Credentials in Secrets Manager ---

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "db" {
  name        = "${var.project_name}/db/app"
  description = "LOKI application database credentials"

  tags = {
    Project = var.project_name
  }
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = var.master_username
    password = random_password.db.result
  })
}

# --- Security groups ---

resource "aws_security_group" "db" {
  name        = "${var.project_name}-db"
  description = "LOKI Aurora cluster; reachable only from the RDS Proxy"
  vpc_id      = var.vpc_id

  tags = {
    Project = var.project_name
  }
}

resource "aws_security_group" "proxy" {
  name        = "${var.project_name}-db-proxy"
  description = "LOKI RDS Proxy; reachable only from the application"
  vpc_id      = var.vpc_id

  tags = {
    Project = var.project_name
  }
}

# Proxy -> cluster on 5432
resource "aws_security_group_rule" "cluster_from_proxy" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.db.id
  source_security_group_id = aws_security_group.proxy.id
  description              = "PostgreSQL from the RDS Proxy only"
}

resource "aws_security_group_rule" "proxy_to_cluster" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.proxy.id
  source_security_group_id = aws_security_group.db.id
  description              = "Proxy egress to the Aurora cluster"
}

# --- Subnet group ---

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db"
  subnet_ids = var.subnet_ids

  tags = {
    Project = var.project_name
  }
}

# --- Aurora PostgreSQL Serverless v2 ---

resource "aws_rds_cluster" "main" {
  cluster_identifier        = "${var.project_name}-aurora"
  engine                    = "aurora-postgresql"
  engine_mode               = "provisioned"
  engine_version            = var.engine_version
  database_name             = var.database_name
  master_username           = var.master_username
  master_password           = random_password.db.result
  db_subnet_group_name      = aws_db_subnet_group.main.name
  vpc_security_group_ids    = [aws_security_group.db.id]
  storage_encrypted         = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project_name}-aurora-final"
  deletion_protection       = true

  serverlessv2_scaling_configuration {
    min_capacity = var.min_acu
    max_capacity = var.max_acu
  }

  tags = {
    Project = var.project_name
  }
}

resource "aws_rds_cluster_instance" "main" {
  identifier         = "${var.project_name}-aurora-1"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version
}

# --- RDS Proxy ---

data "aws_iam_policy_document" "proxy_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "proxy" {
  name               = "${var.project_name}-rds-proxy"
  assume_role_policy = data.aws_iam_policy_document.proxy_assume.json

  tags = {
    Project = var.project_name
  }
}

resource "aws_iam_role_policy" "proxy_secrets" {
  name = "read-db-secret"
  role = aws_iam_role.proxy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.db.arn
    }]
  })
}

resource "aws_db_proxy" "main" {
  name                   = "${var.project_name}-proxy"
  engine_family          = "POSTGRESQL"
  role_arn               = aws_iam_role.proxy.arn
  vpc_subnet_ids         = var.subnet_ids
  vpc_security_group_ids = [aws_security_group.proxy.id]
  require_tls            = true

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "DISABLED"
    secret_arn  = aws_secretsmanager_secret.db.arn
  }

  tags = {
    Project = var.project_name
  }
}

resource "aws_db_proxy_default_target_group" "main" {
  db_proxy_name = aws_db_proxy.main.name

  connection_pool_config {
    max_connections_percent      = 90
    max_idle_connections_percent = 50
  }
}

resource "aws_db_proxy_target" "main" {
  db_proxy_name         = aws_db_proxy.main.name
  target_group_name     = aws_db_proxy_default_target_group.main.name
  db_cluster_identifier = aws_rds_cluster.main.id
}
