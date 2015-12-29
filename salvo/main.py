import sys
import boto3
import argparse
from time import sleep
from salvo.topology import Topology, Cluster


def main(argv=None):
    """The main entry-point to salvo."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description='Provision a new salvo.')
    parser.add_argument('config', type=argparse.FileType('r'),
                        help='salvo configuration file to run')
    parser.add_argument('--deployment', '-d', type=str, default='salvo',
                        help='deployment name for this salvo')
    parser.add_argument('--set', '-s', nargs='*', type=str,
                        help='key:value pair to set for this salvo execution')
    parser.add_argument('--dry-run', '-n', action='store_true', default=False,
                        help='only print what actions would be taken')
    args = parser.parse_args(argv)

    args.dry_run = True  # TODO: remove before release

    args.set = dict(item.split(":", maxsplit=1) for item in args.set)
    topology = Topology.load_file(args.config, args.set)

    hq = Cluster('hq', {
        'public': True,
        'role': 'salvo-hq',
    }, {})
    topology.clusters = [hq] + topology.clusters

    client = boto3.client('ec2')
    ec2 = boto3.resource('ec2')

    # Set up VPC
    vpc = client.create_vpc(DryRun=args.dry_run, CidrBlock='10.0.0.0/16')
    vpc = ec2.Vpc(vpc['Vpc']['VpcId'])

    gateway = client.create_internet_gateway(DryRun=args.dry_run)
    gateway = ec2.InternetGateway(
            gateway['InternetGateway']['InternetGatewayId']
    )
    gateway.attach_to_vpc(DryRun=args.dry_run, VpcId=vpc.id)

    iroutable = vpc.create_route_table(DryRun=args.dry_run)
    iroutable.create_route(DryRun=args.dry_run,
                           DestinationCidrBlock='0.0.0.0/0',
                           GatewayId=gateway.id)

    subnets = []
    for i, c in enumerate(topology.clusters):
        subnet = vpc.create_subnet(DryRun=args.dry_run,
                                   CidrBlock='10.0.{}.0/24'.format(i))

        if topology.public:
            iroutable.associate_with_subnet(DryRun=args.dry_run,
                                            SubnetId=subnet.id)

        subnets.append(subnet)

    # Tag all our VPC resources
    ec2.create_tags(DryRun=args.dry_run,
                    Resources=[
                        vpc.id,
                        gateway.id,
                        iroutable.id,
                    ] + [sn.id for sn in subnets],
                    Tags=[{
                        'Key': 'salvo',
                        'Value': args.deployment,
                    }])

    # Create access keys
    keys = client.create_key_pair(DryRun=args.dry_run,
                                  KeyName=args.deployment)
    keys = ec2.KeyPair(keys['KeyName'])

    # Launch instances
    instances = [
        subnets[i].create_instances(
                DryRun=args.dry_run,
                KeyName=keys.name,
                # SecurityGroupIds = ,
                ImageId=c.attrs['image'],
                MinCount=c.attrs['count'],
                MaxCount=c.attrs['count'],
                InstanceType=c.attrs['itype'],
                InstanceInitiatedShutdownBehavior='terminate')
        for i, c in enumerate(topology.clusters)]

    try:
        hq = instances[0][0]
        while hq.state == 'pending':
            sleep(0.5)
            hq.load()
        if hq.state != 'running':
            raise ChildProcessError(hq.state_reason['Message'])

        # XXX: start setting up hq
        # XXX: set up other machines from hq
        # XXX: detect when all workers finish...
    except Exception as e:
        print("An error occurred: {}".format(e))
    finally:
        # Terminate instances and delete VPC resources
        vpc.instances.terminate(DryRun=args.dry_run)
        for sn in subnets:
            sn.delete(DryRun=args.dry_run)
        gateway.detach_from_vpc(DryRun=args.dry_run, VpcId=vpc.id)
        gateway.delete(DryRun=args.dry_run)
        vpc.delete(DryRun=args.dry_run)
        keys.delete(DryRun=args.dry_run)

    return 0
