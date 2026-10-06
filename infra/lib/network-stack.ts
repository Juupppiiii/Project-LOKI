import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface LokiNetworkStackProps extends cdk.StackProps {
  readonly environmentName: string;
}

export class LokiNetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly connectorSecurityGroup: ec2.SecurityGroup;

  public constructor(scope: Construct, id: string, props: LokiNetworkStackProps) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: props.environmentName === 'prod' ? 2 : 1,
      restrictDefaultSecurityGroup: true,
      subnetConfiguration: [
        { name: 'ingress', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'application', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 },
        { name: 'execution', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
        { name: 'data', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });

    this.connectorSecurityGroup = new ec2.SecurityGroup(this, 'ConnectorSecurityGroup', {
      vpc: this.vpc,
      allowAllOutbound: false,
      description: 'LOKI connector workers; explicit egress is added per connector',
    });

    const flowLogGroup = new logs.LogGroup(this, 'VpcFlowLogs', {
      retention: logs.RetentionDays.THREE_MONTHS,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    this.vpc.addFlowLog('FlowLog', {
      destination: ec2.FlowLogDestination.toCloudWatchLogs(flowLogGroup),
      trafficType: ec2.FlowLogTrafficType.ALL,
    });

    this.vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    const endpointServices: Array<[string, ec2.InterfaceVpcEndpointAwsService]> = [
      ['CloudWatchLogsEndpoint', ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS],
      ['EcrApiEndpoint', ec2.InterfaceVpcEndpointAwsService.ECR],
      ['EcrDockerEndpoint', ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER],
      ['KmsEndpoint', ec2.InterfaceVpcEndpointAwsService.KMS],
      ['SecretsManagerEndpoint', ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER],
      ['SsmEndpoint', ec2.InterfaceVpcEndpointAwsService.SSM],
      ['SsmMessagesEndpoint', ec2.InterfaceVpcEndpointAwsService.SSM_MESSAGES],
      ['Ec2MessagesEndpoint', ec2.InterfaceVpcEndpointAwsService.EC2_MESSAGES],
      ['StsEndpoint', ec2.InterfaceVpcEndpointAwsService.STS],
    ];
    for (const [endpointId, service] of endpointServices) {
      this.vpc.addInterfaceEndpoint(endpointId, {
        service,
        privateDnsEnabled: true,
        subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      });
    }

    new cdk.CfnOutput(this, 'VpcId', { value: this.vpc.vpcId });
  }
}
