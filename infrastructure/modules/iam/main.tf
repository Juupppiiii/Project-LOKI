# IAM roles for the LOKI ECS services.
#
# * execution role (shared) — pulls images, writes logs, resolves startup
#   secrets. Used by both the API and worker task definitions.
# * api task role — the running API's permissions (minimal; it talks to the DB
#   via the proxy over the network, no AWS API calls needed beyond logs).
# * worker task role — consume the simulation queue + report to Step Functions.

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# --- Execution role (shared) ---

resource "aws_iam_role" "execution" {
  name               = "${var.project_name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = {
    Project = var.project_name
  }
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "resolve-startup-secrets"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.secret_arns
    }]
  })
}

# --- API task role ---

resource "aws_iam_role" "api_task" {
  name               = "${var.project_name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = {
    Project = var.project_name
    Service = "api"
  }
}

resource "aws_iam_role_policy" "api_logs" {
  name = "write-logs"
  role = aws_iam_role.api_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${var.log_group_arn}:*"
    }]
  })
}

# --- Worker task role ---

resource "aws_iam_role" "worker_task" {
  name               = "${var.project_name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = {
    Project = var.project_name
    Service = "worker"
  }
}

resource "aws_iam_role_policy" "worker" {
  name = "consume-queue-and-report"
  role = aws_iam_role.worker_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
        ]
        Resource = var.simulation_queue_arn
      },
      {
        Effect = "Allow"
        Action = [
          "states:SendTaskSuccess",
          "states:SendTaskFailure",
          "states:SendTaskHeartbeat",
        ]
        Resource = var.state_machine_arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${var.log_group_arn}:*"
      },
    ]
  })
}

# Optional: let both task roles invoke the advisory Bedrock model.
resource "aws_iam_role_policy" "bedrock" {
  for_each = var.enable_bedrock ? toset(["api", "worker"]) : toset([])

  name = "invoke-bedrock"
  role = each.key == "api" ? aws_iam_role.api_task.id : aws_iam_role.worker_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = var.bedrock_model_arns
    }]
  })
}

# Optional: let the API task role run the READ-ONLY diagnostic backend over SSM
# Run Command. Scoped to the AWS-RunPowerShellScript document and the explicit
# target instances only; the application layer additionally restricts this to a
# fixed set of read-only PowerShell snippets. SendCommand is split from the
# read-back calls so the document/instance scoping applies to the write-capable
# verb, while the status reads are account-local and harmless.
resource "aws_iam_role_policy" "ssm_diagnostics" {
  count = var.enable_ssm_diagnostics ? 1 : 0

  name = "ssm-readonly-diagnostics"
  role = aws_iam_role.api_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SendReadOnlyPowerShell"
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = concat(
          var.ssm_diagnostic_instance_arns,
          ["arn:aws:ssm:*::document/AWS-RunPowerShellScript"],
        )
      },
      {
        Sid      = "ReadCommandResults"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
        Resource = "*"
      },
    ]
  })
}
