# Python 3.x compat
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import (
         bytes, dict, int, list, object, range, str,
         ascii, chr, hex, input, next, oct, open,
         pow, round, super,
         filter, map, zip)

import sys
import boto3
import argparse
from time import sleep
from multiprocessing import Pool
from salvo.topology import Topology, Cluster
from salvo.deploy import Deployer
import botocore


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

    args.set = dict(
            item.split(":", maxsplit=1) for item in args.set
            ) if args.set is not None else {}
    topology = Topology.load_file(args.config, args.set)

    hq = Cluster('hq', {
        'public': True,
        'role': 'bastion',
    }, {})
    topology.clusters = [hq] + topology.clusters

    client = boto3.client('ec2')
    ec2 = boto3.resource('ec2')

    # Set up VPC
    vpc = client.create_vpc(DryRun=args.dry_run, CidrBlock='10.0.0.0/16')
    vpc = ec2.Vpc(vpc['Vpc']['VpcId'])

    # allow ssh to all hosts
    sec = vpc.create_security_group(
        DryRun=args.dry_run,
        GroupName=args.deployment,
        Description='Worker SSH and hq ingress in {}'.format(args.deployment)
    )
    sec.authorize_ingress(DryRun=args.dry_run,
                          IpProtocol='tcp',
                          FromPort=22,
                          ToPort=22,
                          CidrIp='0.0.0.0/0'
                          )
    # allow all internal traffic
    sec.authorize_ingress(DryRun=args.dry_run,
                          IpProtocol='tcp',
                          FromPort=1,
                          ToPort=65535,
                          CidrIp='10.0.0.0/16'
                          )

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

        if c.public:
            iroutable.associate_with_subnet(DryRun=args.dry_run,
                                            SubnetId=subnet.id)

        subnets.append(subnet)

    # Tag all our VPC resources
    ec2.create_tags(DryRun=args.dry_run,
                    Resources=[
                        vpc.id,
                        sec.id,
                        gateway.id,
                        iroutable.id,
                    ] + [sn.id for sn in subnets],
                    Tags=[{
                        'Key': 'salvo',
                        'Value': args.deployment,
                    }])

    # Create access keys
    try:
        keys = client.create_key_pair(DryRun=args.dry_run,
                                      KeyName=args.deployment)
    except botocore.exceptions.ClientError:
        # Key probably already exists. Delete and re-create.
        client.delete_key_pair(DryRun=args.dry_run, KeyName=args.deployment)
        keys = client.create_key_pair(DryRun=args.dry_run,
                                      KeyName=args.deployment)

    keymat = keys['KeyMaterial']
    keys = ec2.KeyPair(keys['KeyName'])

    # Launch instances
    clusters = []
    for i, c in enumerate(topology.clusters):
        nics = [
            {
                "DeviceIndex": 0,
                "Groups": [sec.id],
                "SubnetId": subnets[i].id,
                "DeleteOnTermination": True,
                "AssociatePublicIpAddress": c.public,
            }
        ]

        clusters.append(list(map(lambda x: ec2.Instance(x), [
            instance['InstanceId']
            for instance in client.run_instances(
               DryRun=args.dry_run,
               KeyName=keys.name,
               NetworkInterfaces=nics,
               ImageId=c.attrs['image'],
               MinCount=c.attrs['count'],
               MaxCount=c.attrs['count'],
               InstanceType=c.attrs['itype'],
               InstanceInitiatedShutdownBehavior='terminate'
               )['Instances']
        ])))

    try:
        hq = clusters[0][0]
        while hq.state['Name'] == 'pending':
            sleep(3)
            hq.load()
        if hq.state['Name'] != 'running':
            raise ChildProcessError(hq.state_reason['Message'])

        def prepare(ci, instance):
            global hq
            print("{} on {} now available through {}",
                  topology.clusters[ci].role,
                  instance.private_ip_address,
                  hq.public_ip_address)

        done = []
        p = Pool(5)
        pending = True
        while pending:
            pending = False
            for i, cluster in enumerate(clusters):
                for ii, instance in enumerate(cluster):
                    if instance.state['Name'] == 'pending':
                        pending = True
                        instance.load()
                        break
                    elif instance.state['Name'] != 'running':
                        raise ChildProcessError(
                            instance.state_reason['Message']
                        )
                    else:
                        # State is now 'running'
                        tag = (i, ii)
                        if tag not in done:
                            # State hasn't been 'running' before
                            done.append(tag)
                            p.apply_async(prepare, [i, instance])
                if pending:
                    break
            sleep(3)
        p.close()
        p.join()

        # Wait for hq to be pingable
        deployment = Deployer(args.playbook.name, topology, keymat, clusters)
        while not deployment.test(hq.public_ip_address):
            sleep(1)

        # Wait for workers to be pingable
        for i, cluster in enumerate(clusters):
            for ii, instance in enumerate(cluster):
                while not deployment.test(instance.private_ip_address):
                    sleep(1)

        # Deploy!
        exit = deployment.deploy()
    except:
        import traceback
        traceback.print_exc()
    finally:
        # Terminate instances and delete VPC resources
        vpc.instances.terminate(DryRun=args.dry_run)
        keys.delete(DryRun=args.dry_run)
        for r in iroutable.associations.all():
            r.delete(DryRun=args.dry_run)
        iroutable.delete(DryRun=args.dry_run)
        gateway.detach_from_vpc(DryRun=args.dry_run, VpcId=vpc.id)
        gateway.delete(DryRun=args.dry_run)
        try:
            for sn in subnets:
                sn.delete(DryRun=args.dry_run)
        except:
            import traceback
            traceback.print_exc()
        sec.delete()
        for i in vpc.network_interfaces.all():
            i.delete(DryRun=args.dry_run)
        vpc.delete(DryRun=args.dry_run)

    return exit
