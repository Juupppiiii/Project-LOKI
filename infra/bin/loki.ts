#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { LokiDataStack } from '../lib/data-stack.js';
import { LokiNetworkStack } from '../lib/network-stack.js';
import { LokiPlatformStack, LokiServiceConfig } from '../lib/platform-stack.js';

const app = new cdk.App();
const environmentName = app.node.tryGetContext('loki:environment') ?? 'dev';
const deployService = String(app.node.tryGetContext('loki:deployService') ?? 'false').toLowerCase() === 'true';
const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

function requiredContext(name: string): string {
  const value = String(app.node.tryGetContext(name) ?? '').trim();
  if (!value) {
    throw new Error(`deployService=true requires CDK context ${name}`);
  }
  return value;
}

let serviceConfig: LokiServiceConfig | undefined;
if (deployService) {
  const storeBackend = requiredContext('loki:storeBackend').toLowerCase();
  if (storeBackend !== 'postgres') {
    throw new Error('deployService=true requires loki:storeBackend=postgres');
  }
  const imageTag = requiredContext('loki:imageTag');
  if (imageTag === 'latest') {
    throw new Error('LOKI service images must use an immutable release tag, never latest');
  }
  const serviceDomainName = requiredContext('loki:serviceDomainName');
  const hostedZoneName = requiredContext('loki:hostedZoneName');
  if (!serviceDomainName.endsWith(hostedZoneName)) {
    throw new Error('loki:serviceDomainName must belong to loki:hostedZoneName');
  }
  const origin = `https://${serviceDomainName}`;
  serviceConfig = {
    imageTag,
    certificateArn: requiredContext('loki:certificateArn'),
    allowedIngressCidr: requiredContext('loki:allowedIngressCidr'),
    authSecretArn: requiredContext('loki:authSecretArn'),
    hostedZoneId: requiredContext('loki:hostedZoneId'),
    hostedZoneName,
    serviceDomainName,
    entraRedirectUri: `${origin}/api/auth/callback`,
    allowedOrigin: origin,
    // Multi-task deployment remains blocked until remaining multi-write service
    // transactions and abandoned execution-claim recovery are implemented.
    desiredCount: 1,
  };
}

const network = new LokiNetworkStack(app, `Loki-${environmentName}-Network`, {
  env,
  environmentName,
  description: 'LOKI private network and AWS service endpoints',
});
const data = new LokiDataStack(app, `Loki-${environmentName}-Data`, {
  env,
  network,
  description: 'LOKI encrypted Aurora PostgreSQL and S3 data foundations',
  terminationProtection: true,
});
const platform = new LokiPlatformStack(app, `Loki-${environmentName}-Platform`, {
  env,
  network,
  data,
  deployService,
  serviceConfig,
  description: 'LOKI ECS, event, queue, workflow, registry, and observability foundations',
});

data.addStackDependency(network);
platform.addStackDependency(network);
if (deployService) {
  platform.addStackDependency(data);
}

for (const stack of [network, data, platform]) {
  cdk.Tags.of(stack).add('Application', 'LOKI');
  cdk.Tags.of(stack).add('Environment', environmentName);
  cdk.Tags.of(stack).add('ManagedBy', 'AWS-CDK');
}

new AwsSolutionsChecks(app, { verbose: true });
app.synth();
