# Step Functions state machine: routes a dispatched step by owner. An ai-owned
# step is sent to the simulation queue with a task token and waits for the
# worker to call SendTaskSuccess/Failure; a human-owned step waits on the
# approval queue. This mirrors the application's execution contract.

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.project_name}-run-workflow"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json

  tags = {
    Project = var.project_name
  }
}

resource "aws_iam_role_policy" "sfn" {
  name = "dispatch-and-log"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = var.simulation_queue_arn
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/states/${var.project_name}-run-workflow"
  retention_in_days = var.log_retention_days

  tags = {
    Project = var.project_name
  }
}

resource "aws_sfn_state_machine" "run_workflow" {
  name     = "${var.project_name}-run-workflow"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "LOKI run workflow: route a dispatched step by owner"
    StartAt = "RouteByStepOwner"
    States = {
      RouteByStepOwner = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.owner"
            StringEquals = "ai"
            Next         = "DispatchSimulationAction"
          },
        ]
        Default = "UnknownOwner"
      }
      DispatchSimulationAction = {
        Type     = "Task"
        Resource = "arn:aws:states:::sqs:sendMessage.waitForTaskToken"
        Parameters = {
          QueueUrl       = var.simulation_queue_url
          MessageGroupId = "loki-simulation"
          MessageBody = {
            "taskToken.$" = "$$.Task.Token"
            "runId.$"     = "$.run_id"
            "stepId.$"    = "$.step_id"
            "envelope.$"  = "$.envelope"
          }
        }
        HeartbeatSeconds = 300
        TimeoutSeconds   = 3600
        Next             = "StepCompleted"
      }
      StepCompleted = { Type = "Succeed" }
      UnknownOwner = {
        Type  = "Fail"
        Error = "InvalidStepOwner"
        Cause = "Step owner was neither ai nor human"
      }
    }
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = false
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  tags = {
    Project = var.project_name
  }
}
