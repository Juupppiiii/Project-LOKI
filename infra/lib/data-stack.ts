import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import { LokiNetworkStack } from './network-stack.js';

export interface LokiDataStackProps extends cdk.StackProps {
  readonly network: LokiNetworkStack;
}

export class LokiDataStack extends cdk.Stack {
  public readonly dataKey: kms.Key;
  public readonly database: rds.DatabaseCluster;
  public readonly databaseProxy: rds.DatabaseProxy;
  public readonly databaseProxySecurityGroup: ec2.ISecurityGroup;
  public readonly applicationSecret: secretsmanager.Secret;
  public readonly evidenceBucket: s3.Bucket;
  public readonly auditBucket: s3.Bucket;

  public constructor(scope: Construct, id: string, props: LokiDataStackProps) {
    super(scope, id, props);

    this.dataKey = new kms.Key(this, 'DataKey', {
      enableKeyRotation: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      description: 'Encrypts LOKI state, evidence, and audit data',
    });

    const commonBucketProps: Pick<s3.BucketProps,
      'blockPublicAccess' | 'encryption' | 'encryptionKey' | 'enforceSSL' | 'versioned' | 'removalPolicy'> = {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.dataKey,
      enforceSSL: true,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    };

    this.evidenceBucket = new s3.Bucket(this, 'EvidenceBucket', {
      ...commonBucketProps,
      lifecycleRules: [{
        id: 'ArchiveOldEvidence',
        enabled: true,
        transitions: [{ storageClass: s3.StorageClass.INFREQUENT_ACCESS, transitionAfter: cdk.Duration.days(90) }],
      }],
    });

    this.auditBucket = new s3.Bucket(this, 'AuditBucket', {
      ...commonBucketProps,
      objectLockEnabled: true,
    });

    this.database = new rds.DatabaseCluster(this, 'Database', {
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.VER_16_6,
      }),
      writer: rds.ClusterInstance.serverlessV2('Writer'),
      readers: [rds.ClusterInstance.serverlessV2('Reader', { scaleWithWriter: true })],
      serverlessV2MinCapacity: 0.5,
      serverlessV2MaxCapacity: 4,
      credentials: rds.Credentials.fromGeneratedSecret('loki_cluster_admin'),
      defaultDatabaseName: 'loki',
      storageEncrypted: true,
      storageEncryptionKey: this.dataKey,
      deletionProtection: true,
      backup: { retention: cdk.Duration.days(14) },
      cloudwatchLogsExports: ['postgresql'],
      cloudwatchLogsRetention: cdk.aws_logs.RetentionDays.THREE_MONTHS,
      vpc: props.network.vpc,
      vpcSubnets: { subnetGroupName: 'data' },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    this.applicationSecret = new secretsmanager.Secret(this, 'ApplicationDatabaseSecret', {
      encryptionKey: this.dataKey,
      description: 'Credentials for the least-privilege LOKI application database role',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'loki_app' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 32,
      },
    });

    this.databaseProxy = this.database.addProxy('Proxy', {
      secrets: [this.database.secret!, this.applicationSecret],
      vpc: props.network.vpc,
      vpcSubnets: { subnetGroupName: 'data' },
      requireTLS: true,
      debugLogging: false,
    });
    const proxySecurityGroup = this.databaseProxy.connections.securityGroups[0];
    if (!proxySecurityGroup) {
      throw new Error('RDS Proxy did not create a security group');
    }
    this.databaseProxySecurityGroup = proxySecurityGroup;

    new cdk.CfnOutput(this, 'DatabaseEndpoint', { value: this.database.clusterEndpoint.hostname });
    new cdk.CfnOutput(this, 'DatabaseProxyEndpoint', { value: this.databaseProxy.endpoint });
    new cdk.CfnOutput(this, 'DatabasePort', { value: this.database.clusterEndpoint.port.toString() });
    new cdk.CfnOutput(this, 'DatabaseName', { value: 'loki' });
    new cdk.CfnOutput(this, 'DatabaseSecretArn', { value: this.database.secret!.secretArn });
    new cdk.CfnOutput(this, 'ApplicationDatabaseSecretArn', { value: this.applicationSecret.secretArn });
    new cdk.CfnOutput(this, 'EvidenceBucketName', { value: this.evidenceBucket.bucketName });
    new cdk.CfnOutput(this, 'AuditBucketName', { value: this.auditBucket.bucketName });
  }
}
