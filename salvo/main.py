import sys
import boto3
import os.path
import argparse
from time import sleep
from multiprocessing import Pool
from salvo.topology import Topology, Cluster


def main(argv=None):
    """The main entry-point to salvo."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description='Provision a new salvo.')
    parser.add_argument('config', type=argparse.FileType('r'),
                        help='salvo configuration file to run')
    parser.add_argument('--playbook', '-p', type=argparse.FileType('r'),
                        default='./deploy/playbook.yml',
                        help='directory where playbooks reside')
    parser.add_argument('--deployment', '-d', type=str, default='salvo',
                        help='deployment name for this salvo')
    parser.add_argument('--set', '-s', nargs='*', type=str,
                        help='key:value pair to set for this salvo execution')
    parser.add_argument('--dry-run', '-n', action='store_true', default=False,
                        help='only print what actions would be taken')
    args = parser.parse_args(argv)

    args.dry_run = True  # TODO: remove before release

    args.set = dict(item.split(":", maxsplit=1) for item in args.set)
    playdir = os.path.dirname(os.path.abspath(args.playbook))
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
    clusters = [
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
        hq = clusters[0][0]
        while hq.state['Name'] == 'pending':
            sleep(0.5)
            hq.load()
        if hq.state['Name'] != 'running':
            raise ChildProcessError(hq.state_reason['Message'])

        # XXX: start deployment master on hq

        def prepare(ci, instance):
            global hq
            print("setup {} as {} through {}",
                  instance.private_ip_address,
                  topology.clusters[ci].role,
                  hq.public_ip_address)
            # XXX: set hq.private_ip_address as master in /etc/hosts
            # XXX: start deployment slave on instance

        done = []
        p = Pool(5)
        pending = True
        while pending:
            pending = False
            for i, cluster in enumerate(clusters):
                for ii, instance in enumerate(cluster):
                    if i.state['Name'] == 'pending':
                        pending = True
                        i.load()
                        break
                    elif i.state['Name'] != 'running':
                        raise ChildProcessError(i.state_reason['Message'])
                    else:
                        # State is now 'running'
                        tag = (i, ii)
                        if tag not in done:
                            # State hasn't been 'running' before
                            done.append(tag)
                            p.apply_async(prepare, [i, instance])
                if pending:
                    break
        p.close()
        p.join()

        with open(os.path.join(playdir, "hosts"), "w") as hosts:
            print("- hosts:", file=hosts)
            for ci, cluster in enumerate(clusters):
                print("  - {}:".format(topology.clusters[ci].role), file=hosts)
                for instance in cluster:
                    print(
                        "    - {}".format(instance.private_ip_address),
                        file=hosts
                    )

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
