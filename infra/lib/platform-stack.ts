import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as aps from 'aws-cdk-lib/aws-aps';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as events from 'aws-cdk-lib/aws-events';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as route53Targets from 'aws-cdk-lib/aws-route53-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';
import { LokiDataStack } from './data-stack.js';
import { LokiNetworkStack } from './network-stack.js';

export interface LokiServiceConfig {
  readonly imageTag: string;
  readonly certificateArn: string;
  readonly allowedIngressCidr: string;
  readonly authSecretArn: string;
  readonly hostedZoneId: string;
  readonly hostedZoneName: string;
  readonly serviceDomainName: string;
  readonly entraRedirectUri: string;
  readonly allowedOrigin: string;
  readonly desiredCount: number;
}

export interface LokiPlatformStackProps extends cdk.StackProps {
  readonly network: LokiNetworkStack;
  readonly data: LokiDataStack;
  readonly deployService: boolean;
  readonly serviceConfig?: LokiServiceConfig;
}

interface QueuePair {
  readonly queue: sqs.Queue;
  readonly deadLetterQueue: sqs.Queue;
}

export class LokiPlatformStack extends cdk.Stack {
  public readonly repository: ecr.Repository;
  public readonly cluster: ecs.Cluster;
  public readonly eventBus: events.EventBus;
  public readonly workflow: sfn.StateMachine;

  public constructor(scope: Construct, id: string, props: LokiPlatformStackProps) {
    super(scope, id, props);

    this.repository = new ecr.Repository(this, 'ApplicationRepository', {
      imageScanOnPush: true,
      imageTagMutability: ecr.TagMutability.IMMUTABLE,
      encryption: ecr.RepositoryEncryption.KMS,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [{ maxImageCount: 40, description: 'Retain recent release images' }],
    });

    this.cluster = new ecs.Cluster(this, 'Cluster', {
      vpc: props.network.vpc,
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
    });

    this.eventBus = new events.EventBus(this, 'DomainEventBus');

    const approval = this.queuePair('HumanApproval', false);
    const simulation = this.queuePair('Simulation', true);
    const serviceNow = this.queuePair('ServiceNow', true);
    const systemsManager = this.queuePair('SystemsManager', true);
    const activeDirectory = this.queuePair('ActiveDirectory', true);
    const vCenter = this.queuePair('VCenter', true);
    const awsNative = this.queuePair('AwsNative', true);
    const http = this.queuePair('Http', true);

    const completed = new sfn.Succeed(this, 'StepCompleted');
    const rejected = new sfn.Fail(this, 'UnknownOwner', {
      cause: 'Step owner was neither ai nor human',
      error: 'InvalidStepOwner',
    });

    const waitForAgent = new tasks.SqsSendMessage(this, 'DispatchSimulationAction', {
      queue: simulation.queue,
      integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
      messageGroupId: 'loki-simulation',
      messageBody: sfn.TaskInput.fromObject({
        taskToken: sfn.JsonPath.taskToken,
        runId: sfn.JsonPath.stringAt('$.run_id'),
        stepId: sfn.JsonPath.stringAt('$.step_id'),
        envelope: sfn.JsonPath.objectAt('$.envelope'),
      }),
      heartbeatTimeout: sfn.Timeout.duration(cdk.Duration.minutes(5)),
      taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(1)),
    });
    waitForAgent.next(completed);

    const waitForHuman = new tasks.SqsSendMessage(this, 'RequestHumanCompletion', {
      queue: approval.queue,
      integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
      messageBody: sfn.TaskInput.fromObject({
        taskToken: sfn.JsonPath.taskToken,
        runId: sfn.JsonPath.stringAt('$.run_id'),
        stepId: sfn.JsonPath.stringAt('$.step_id'),
        approvalDigest: sfn.JsonPath.stringAt('$.approval_digest'),
      }),
      taskTimeout: sfn.Timeout.duration(cdk.Duration.days(30)),
    });
    waitForHuman.next(completed);

    const route = new sfn.Choice(this, 'RouteByStepOwner')
      .when(sfn.Condition.stringEquals('$.owner', 'ai'), waitForAgent)
      .when(sfn.Condition.stringEquals('$.owner', 'human'), waitForHuman)
      .otherwise(rejected);

    const workflowLogs = new logs.LogGroup(this, 'WorkflowLogs', {
      retention: logs.RetentionDays.THREE_MONTHS,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    this.workflow = new sfn.StateMachine(this, 'RunWorkflow', {
      definitionBody: sfn.DefinitionBody.fromChainable(route),
      stateMachineType: sfn.StateMachineType.STANDARD,
      timeout: cdk.Duration.days(30),
      tracingEnabled: true,
      logs: {
        destination: workflowLogs,
        level: sfn.LogLevel.ALL,
        includeExecutionData: false,
      },
    });

    const prometheus = new aps.CfnWorkspace(this, 'PrometheusWorkspace', {
      alias: 'loki-platform',
      loggingConfiguration: { logGroupArn: workflowLogs.logGroupArn },
    });

    const dashboard = new cloudwatch.Dashboard(this, 'FoundationDashboard', {
      dashboardName: 'LOKI-Foundation',
    });
    const queuePairs = [approval, simulation, serviceNow, systemsManager, activeDirectory, vCenter, awsNative, http];
    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Connector queue depth',
        left: queuePairs.map((pair) => pair.queue.metricApproximateNumberOfMessagesVisible()),
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Oldest connector message',
        left: queuePairs.map((pair) => pair.queue.metricApproximateAgeOfOldestMessage()),
        width: 12,
      }),
    );

    for (const pair of queuePairs) {
      pair.deadLetterQueue.metricApproximateNumberOfMessagesVisible().createAlarm(this, `${pair.queue.node.id}DlqAlarm`, {
        threshold: 1,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });
    }

    new cdk.CfnOutput(this, 'RepositoryUri', { value: this.repository.repositoryUri });
    new cdk.CfnOutput(this, 'ClusterName', { value: this.cluster.clusterName });
    new cdk.CfnOutput(this, 'EventBusName', { value: this.eventBus.eventBusName });
    new cdk.CfnOutput(this, 'WorkflowArn', { value: this.workflow.stateMachineArn });
    new cdk.CfnOutput(this, 'PrometheusWorkspaceArn', { value: prometheus.attrArn });

    if (props.deployService) {
      if (!props.serviceConfig) {
        throw new Error('deployService=true requires a complete serviceConfig');
      }
      this.createApplicationService(props, props.serviceConfig, simulation.queue);
    } else {
      new cdk.CfnOutput(this, 'ServiceDeploymentGate', {
        value: 'BLOCKED: set explicit PostgreSQL/auth/TLS contexts after migration and integration validation',
      });
    }
  }

  private createApplicationService(
    props: LokiPlatformStackProps,
    config: LokiServiceConfig,
    simulationQueue: sqs.Queue,
  ): void {
    const appLogs = new logs.LogGroup(this, 'ApplicationLogs', {
      retention: logs.RetentionDays.THREE_MONTHS,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const executionRole = new iam.Role(this, 'ApplicationTaskExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: 'Pulls the LOKI image, writes logs, and resolves startup secrets',
    });
    const taskDefinition = new ecs.FargateTaskDefinition(this, 'ApplicationTask', {
      cpu: 512,
      memoryLimitMiB: 1024,
      executionRole,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });
    const applicationDatabaseSecret = secretsmanager.Secret.fromSecretCompleteArn(
      this,
      'ApplicationDatabaseSecretReference',
      props.data.applicationSecret.secretArn,
    );
    const authSecret = secretsmanager.Secret.fromSecretCompleteArn(
      this,
      'AuthenticationSecret',
      config.authSecretArn,
    );
    this.repository.grantPull(executionRole);
    executionRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`${appLogs.logGroupArn}:*`],
    }));
    executionRole.addToPolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'],
      resources: [applicationDatabaseSecret.secretArn, authSecret.secretArn],
    }));
    executionRole.addToPolicy(new iam.PolicyStatement({
      actions: ['kms:Decrypt'],
      resources: [props.data.dataKey.keyArn],
      conditions: {
        StringEquals: { 'kms:ViaService': `secretsmanager.${this.region}.amazonaws.com` },
      },
    }));

    const container = taskDefinition.addContainer('Application', {
      image: ecs.ContainerImage.fromEcrRepository(this.repository, config.imageTag),
      readonlyRootFilesystem: true,
      logging: ecs.LogDrivers.awsLogs({
        logGroup: appLogs,
        streamPrefix: 'loki-api',
      }),
      environment: {
        LOKI_STORE_BACKEND: 'postgres',
        LOKI_POSTGRES_HOST: props.data.databaseProxy.endpoint,
        LOKI_POSTGRES_PORT: '5432',
        LOKI_POSTGRES_DB: 'loki',
        LOKI_POSTGRES_SSLMODE: 'require',
        LOKI_POSTGRES_AUTO_MIGRATE: '0',
        LOKI_EXECUTION_MODE: 'simulation',
        LOKI_ENFORCE_AUTH: '1',
        OVERLAY_ALLOW_DEV_LOGIN: '0',
        LOKI_COOKIE_SECURE: '1',
        LOKI_DEFAULT_ROLE: 'viewer',
        OVERLAY_ALLOWED_ORIGINS: config.allowedOrigin,
        LOKI_ENTRA_REDIRECT_URI: config.entraRedirectUri,
      },
      secrets: {
        LOKI_POSTGRES_USER: ecs.Secret.fromSecretsManager(applicationDatabaseSecret, 'username'),
        LOKI_POSTGRES_PASSWORD: ecs.Secret.fromSecretsManager(applicationDatabaseSecret, 'password'),
        LOKI_SECRET_KEY: ecs.Secret.fromSecretsManager(authSecret, 'session_key'),
        LOKI_ENTRA_TENANT_ID: ecs.Secret.fromSecretsManager(authSecret, 'tenant_id'),
        LOKI_ENTRA_CLIENT_ID: ecs.Secret.fromSecretsManager(authSecret, 'client_id'),
        LOKI_ENTRA_CLIENT_SECRET: ecs.Secret.fromSecretsManager(authSecret, 'client_secret'),
      },
      healthCheck: {
        command: ['CMD-SHELL', "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8800/api/health',timeout=3).read()\" || exit 1"],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(20),
      },
    });
    container.addPortMappings({ containerPort: 8800, protocol: ecs.Protocol.TCP });

    // Deny-by-default application SG: DNS, VPC HTTPS endpoints, and RDS Proxy only.
    const applicationSecurityGroup = new ec2.SecurityGroup(this, 'ApplicationSecurityGroup', {
      vpc: props.network.vpc,
      allowAllOutbound: false,
      description: 'LOKI API task network boundary',
    });
    // External HTTPS is required for Entra and ServiceNow through NAT. A
    // production network firewall/proxy supplies the hostname allowlist.
    applicationSecurityGroup.addEgressRule(
      ec2.Peer.anyIpv4(), ec2.Port.tcp(443), 'TLS egress for Entra, ServiceNow, and AWS endpoints',
    );
    applicationSecurityGroup.addEgressRule(
      ec2.Peer.ipv4(props.network.vpc.vpcCidrBlock), ec2.Port.udp(53), 'VPC DNS',
    );
    applicationSecurityGroup.addEgressRule(
      ec2.Peer.ipv4(props.network.vpc.vpcCidrBlock), ec2.Port.tcp(53), 'VPC DNS fallback',
    );
    new ec2.CfnSecurityGroupIngress(this, 'ApplicationToProxyIngress', {
      groupId: props.data.databaseProxySecurityGroup.securityGroupId,
      sourceSecurityGroupId: applicationSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 5432,
      toPort: 5432,
      description: 'Only LOKI application tasks may connect to the database proxy',
    });
    new ec2.CfnSecurityGroupEgress(this, 'ApplicationToProxyEgress', {
      groupId: applicationSecurityGroup.securityGroupId,
      destinationSecurityGroupId: props.data.databaseProxySecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 5432,
      toPort: 5432,
      description: 'Application tasks connect only to the TLS database proxy',
    });

    const service = new ecs.FargateService(this, 'ApplicationService', {
      cluster: this.cluster,
      taskDefinition,
      desiredCount: config.desiredCount,
      assignPublicIp: false,
      vpcSubnets: { subnetGroupName: 'application' },
      securityGroups: [applicationSecurityGroup],
      circuitBreaker: { rollback: true },
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
      healthCheckGracePeriod: cdk.Duration.seconds(60),
    });
    // Deliberately single-task until remaining multi-write transactions and
    // abandoned execution-claim recovery are implemented.

    const albSecurityGroup = new ec2.SecurityGroup(this, 'LoadBalancerSecurityGroup', {
      vpc: props.network.vpc,
      allowAllOutbound: false,
      description: 'Internal TLS entry point for LOKI',
    });
    albSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(config.allowedIngressCidr), ec2.Port.tcp(443), 'Approved internal clients',
    );
    new ec2.CfnSecurityGroupEgress(this, 'LoadBalancerToApplicationEgress', {
      groupId: albSecurityGroup.securityGroupId,
      destinationSecurityGroupId: applicationSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 8800,
      toPort: 8800,
      description: 'Forward only to LOKI application tasks',
    });
    new ec2.CfnSecurityGroupIngress(this, 'LoadBalancerToApplicationIngress', {
      groupId: applicationSecurityGroup.securityGroupId,
      sourceSecurityGroupId: albSecurityGroup.securityGroupId,
      ipProtocol: 'tcp',
      fromPort: 8800,
      toPort: 8800,
      description: 'Only the internal ALB may call the application',
    });

    const loadBalancer = new elbv2.ApplicationLoadBalancer(this, 'LoadBalancer', {
      vpc: props.network.vpc,
      internetFacing: false,
      vpcSubnets: { subnetGroupName: 'application' },
      securityGroup: albSecurityGroup,
      deletionProtection: true,
    });
    const certificate = acm.Certificate.fromCertificateArn(this, 'Certificate', config.certificateArn);
    const listener = loadBalancer.addListener('HttpsListener', {
      port: 443,
      protocol: elbv2.ApplicationProtocol.HTTPS,
      certificates: [certificate],
      sslPolicy: elbv2.SslPolicy.RECOMMENDED_TLS,
    });
    const targetGroup = listener.addTargets('ApplicationTargets', {
      protocol: elbv2.ApplicationProtocol.HTTP,
      port: 8800,
      targets: [service],
      deregistrationDelay: cdk.Duration.seconds(30),
      healthCheck: {
        path: '/api/ready',
        healthyHttpCodes: '200',
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
      },
    });

    const accessLogsBucket = new s3.Bucket(this, 'LoadBalancerAccessLogs', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      lifecycleRules: [{ expiration: cdk.Duration.days(90) }],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    loadBalancer.logAccessLogs(accessLogsBucket, 'alb');

    const zone = route53.HostedZone.fromHostedZoneAttributes(this, 'PrivateHostedZone', {
      hostedZoneId: config.hostedZoneId,
      zoneName: config.hostedZoneName,
    });
    new route53.ARecord(this, 'ServiceAlias', {
      zone,
      recordName: config.serviceDomainName,
      target: route53.RecordTarget.fromAlias(new route53Targets.LoadBalancerTarget(loadBalancer)),
    });

    new cloudwatch.Alarm(this, 'ApplicationUnhealthyHosts', {
      metric: targetGroup.metrics.unhealthyHostCount(),
      threshold: 1,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cdk.CfnOutput(this, 'ServiceDeploymentGate', { value: 'ENABLED: PostgreSQL + TLS + auth contexts supplied' });
    new cdk.CfnOutput(this, 'ServiceName', { value: service.serviceName });
    new cdk.CfnOutput(this, 'ServiceUrl', { value: `https://${config.serviceDomainName}` });
    new cdk.CfnOutput(this, 'TaskDefinitionArn', { value: taskDefinition.taskDefinitionArn });

    // ---- Simulation worker -------------------------------------------------
    // Always-on single Fargate task that consumes the Simulation queue, runs
    // each dispatched step through the SAME simulation gateway as the API
    // (python -m overlay.worker_main), and reports the task token back to Step
    // Functions. It shares the application image, the deny-by-default SG, the
    // database/auth secrets and the RDS Proxy path. No ALB, no inbound port.
    this.createSimulationWorkerService(
      props,
      config,
      simulationQueue,
      appLogs,
      executionRole,
      applicationDatabaseSecret,
      authSecret,
      applicationSecurityGroup,
    );
  }

  private createSimulationWorkerService(
    props: LokiPlatformStackProps,
    config: LokiServiceConfig,
    simulationQueue: sqs.Queue,
    appLogs: logs.LogGroup,
    executionRole: iam.Role,
    applicationDatabaseSecret: secretsmanager.ISecret,
    authSecret: secretsmanager.ISecret,
    applicationSecurityGroup: ec2.SecurityGroup,
  ): void {
    // The worker's runtime (task) role: consume the Simulation queue and report
    // outcomes to Step Functions. Nothing else -- it never serves traffic and
    // never writes to any target system (execution stays simulated).
    const workerTaskRole = new iam.Role(this, 'WorkerTaskRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: 'Simulation worker: consume Simulation queue, callback to Step Functions',
    });
    simulationQueue.grantConsumeMessages(workerTaskRole);
    workerTaskRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        'states:SendTaskSuccess',
        'states:SendTaskFailure',
        'states:SendTaskHeartbeat',
      ],
      // Task tokens are issued by the run workflow; scope callbacks to it.
      resources: [this.workflow.stateMachineArn],
    }));

    const workerTask = new ecs.FargateTaskDefinition(this, 'WorkerTask', {
      cpu: 256,
      memoryLimitMiB: 512,
      executionRole,
      taskRole: workerTaskRole,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    workerTask.addContainer('Worker', {
      image: ecs.ContainerImage.fromEcrRepository(this.repository, config.imageTag),
      readonlyRootFilesystem: true,
      command: ['python', '-m', 'overlay.worker_main'],
      logging: ecs.LogDrivers.awsLogs({ logGroup: appLogs, streamPrefix: 'loki-worker' }),
      environment: {
        LOKI_STORE_BACKEND: 'postgres',
        LOKI_POSTGRES_HOST: props.data.databaseProxy.endpoint,
        LOKI_POSTGRES_PORT: '5432',
        LOKI_POSTGRES_DB: 'loki',
        LOKI_POSTGRES_SSLMODE: 'require',
        LOKI_POSTGRES_AUTO_MIGRATE: '0',
        LOKI_EXECUTION_MODE: 'simulation',
        LOKI_SIMULATION_QUEUE_URL: simulationQueue.queueUrl,
      },
      secrets: {
        LOKI_POSTGRES_USER: ecs.Secret.fromSecretsManager(applicationDatabaseSecret, 'username'),
        LOKI_POSTGRES_PASSWORD: ecs.Secret.fromSecretsManager(applicationDatabaseSecret, 'password'),
      },
    });

    const workerService = new ecs.FargateService(this, 'WorkerService', {
      cluster: this.cluster,
      taskDefinition: workerTask,
      desiredCount: 1,
      assignPublicIp: false,
      vpcSubnets: { subnetGroupName: 'application' },
      securityGroups: [applicationSecurityGroup],
      circuitBreaker: { rollback: true },
      minHealthyPercent: 0,
      maxHealthyPercent: 100,
    });
    // Deliberately a single task: ordering is kept clean on the FIFO queue and a
    // database lease elects one consumer. The idempotency ledger makes execution
    // safe even if a second task ever runs.

    new cdk.CfnOutput(this, 'WorkerServiceName', { value: workerService.serviceName });
    new cdk.CfnOutput(this, 'WorkerTaskDefinitionArn', { value: workerTask.taskDefinitionArn });
  }

  private queuePair(id: string, fifo: boolean): QueuePair {
    const deadLetterQueue = new sqs.Queue(this, `${id}DeadLetterQueue`, {
      fifo,
      contentBasedDeduplication: fifo,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      retentionPeriod: cdk.Duration.days(14),
    });
    const queue = new sqs.Queue(this, `${id}Queue`, {
      fifo,
      contentBasedDeduplication: fifo,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      retentionPeriod: cdk.Duration.days(14),
      visibilityTimeout: cdk.Duration.minutes(5),
      deadLetterQueue: { queue: deadLetterQueue, maxReceiveCount: 5 },
    });
    return { queue, deadLetterQueue };
  }
}
