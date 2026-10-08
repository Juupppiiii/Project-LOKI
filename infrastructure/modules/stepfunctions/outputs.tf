output "state_machine_arn" {
  description = "ARN of the run-workflow state machine"
  value       = aws_sfn_state_machine.run_workflow.arn
}

output "state_machine_name" {
  description = "Name of the run-workflow state machine"
  value       = aws_sfn_state_machine.run_workflow.name
}
