/**
 * CDK template assertions using the AWS CDK assertions library.
 *
 * Verifies the deployment gate: with the service disabled (default) no ECS
 * service, load balancer, or DNS record is produced; with the gate explicitly
 * enabled the service, internal TLS listener, PostgreSQL/simulation settings,
 * and /api/ready health check appear. No AWS calls are made.
 *
 *   node --test infra/test
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { LokiNetworkStack } from '../lib/network-stack.js';
import { LokiDataStack } from '../lib/data-stack.js';
import { LokiPlatformStack } from '../lib/platform-stack.js';

const ENV = { account: '111111111111', region: 'eu-west-1' };

function build(deployService, serviceConfig) {
  const app = new cdk.App();
  const network = new LokiNetworkStack(app, 'Net', { env: ENV, environmentName: 'test' });
  const data = new LokiDataStack(app, 'Data', { env: ENV, network });
  const platform = new LokiPlatformStack(app, 'Platform', {
    env: ENV, network, data, deployService, serviceConfig,
  });
  return Template.fromStack(platform);
}

const GATED_CONFIG = {
  imageTag: 'release-0.1.0',
  certificateArn: `arn:aws:acm:${ENV.region}:${ENV.account}:certificate/00000000-0000-0000-0000-000000000000`,
  allowedIngressCidr: '10.0.0.0/8',
  authSecretArn: `arn:aws:secretsmanager:${ENV.region}:${ENV.account}:secret:loki/auth-ABCDEF`,
  hostedZoneId: 'Z0000000000000',
  hostedZoneName: 'internal.example.com',
  serviceDomainName: 'loki.internal.example.com',
  entraRedirectUri: 'https://loki.internal.example.com/api/auth/callback',
  allowedOrigin: 'https://loki.internal.example.com',
  desiredCount: 1,
};

test('service disabled: foundation only, no runtime service', () => {
  const template = build(false, undefined);
  template.resourceCountIs('AWS::ECS::Service', 0);
  template.resourceCountIs('AWS::ElasticLoadBalancingV2::LoadBalancer', 0);
  template.resourceCountIs('AWS::Route53::RecordSet', 0);
  // Foundations still exist.
  template.resourceCountIs('AWS::ECS::Cluster', 1);
  template.resourceCountIs('AWS::StepFunctions::StateMachine', 1);
});

test('service enabled: gated runtime services are created (api + worker)', () => {
  const template = build(true, GATED_CONFIG);
  // Two ECS services now: the API and the simulation worker.
  template.resourceCountIs('AWS::ECS::Service', 2);
  // Exactly one is internet-reachable via the ALB; the worker has none.
  template.resourceCountIs('AWS::ElasticLoadBalancingV2::LoadBalancer', 1);
  template.resourceCountIs('AWS::Route53::RecordSet', 1);
});

test('worker enabled: runs the worker entrypoint, simulation-only', () => {
  const template = build(true, GATED_CONFIG);
  // arrayWith matches a contiguous ordered subsequence, so assert each env var
  // independently rather than depending on the order CDK renders them.
  template.hasResourceProperties('AWS::ECS::TaskDefinition', {
    ContainerDefinitions: Match.arrayWith([
      Match.objectLike({
        Command: ['python', '-m', 'overlay.worker_main'],
        Environment: Match.arrayWith([{ Name: 'LOKI_EXECUTION_MODE', Value: 'simulation' }]),
      }),
    ]),
  });
  template.hasResourceProperties('AWS::ECS::TaskDefinition', {
    ContainerDefinitions: Match.arrayWith([
      Match.objectLike({
        Command: ['python', '-m', 'overlay.worker_main'],
        Environment: Match.arrayWith([{ Name: 'LOKI_STORE_BACKEND', Value: 'postgres' }]),
      }),
    ]),
  });
});

test('worker enabled: task role may call Step Functions task callbacks', () => {
  const template = build(true, GATED_CONFIG);
  template.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: Match.arrayWith([
            'states:SendTaskSuccess',
            'states:SendTaskFailure',
            'states:SendTaskHeartbeat',
          ]),
        }),
      ]),
    },
  });
});

test('service enabled: ALB health check targets /api/ready', () => {
  const template = build(true, GATED_CONFIG);
  template.hasResourceProperties('AWS::ElasticLoadBalancingV2::TargetGroup', {
    HealthCheckPath: '/api/ready',
  });
});

test('service enabled: container enforces postgres + simulation + auth', () => {
  const template = build(true, GATED_CONFIG);
  template.hasResourceProperties('AWS::ECS::TaskDefinition', {
    ContainerDefinitions: Match.arrayWith([
      Match.objectLike({
        Environment: Match.arrayWith([
          { Name: 'LOKI_STORE_BACKEND', Value: 'postgres' },
          { Name: 'LOKI_EXECUTION_MODE', Value: 'simulation' },
          { Name: 'LOKI_ENFORCE_AUTH', Value: '1' },
          { Name: 'OVERLAY_ALLOW_DEV_LOGIN', Value: '0' },
          { Name: 'LOKI_COOKIE_SECURE', Value: '1' },
        ]),
      }),
    ]),
  });
});

test('service enabled: HTTPS listener with TLS security policy', () => {
  const template = build(true, GATED_CONFIG);
  template.hasResourceProperties('AWS::ElasticLoadBalancingV2::Listener', {
    Protocol: 'HTTPS',
    Port: 443,
  });
});
