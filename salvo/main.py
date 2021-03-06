# Python 3.x compat
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import (  # NOQA
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
import agenda

# boto3.set_stream_logger(name='boto3')


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
    parser.add_argument('--wait', '-w', default=False, action='store_true',
                        help='wait for [Enter] before cleaning up')
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
        'expose': [22],
    }, {})
    topology.clusters = [hq] + topology.clusters

    agenda.section("Set up network")

    client = boto3.client('ec2')
    ec2 = boto3.resource('ec2')

    # Set up VPC
    agenda.task("Create VPC")
    vpc = client.create_vpc(DryRun=args.dry_run, CidrBlock='10.0.0.0/16')
    vpc = ec2.Vpc(vpc['Vpc']['VpcId'])

    agenda.task("Attach VPC internet gateway")
    gateway = client.create_internet_gateway(DryRun=args.dry_run)
    gateway = ec2.InternetGateway(
            gateway['InternetGateway']['InternetGatewayId']
    )
    gateway.attach_to_vpc(DryRun=args.dry_run, VpcId=vpc.id)

    agenda.task("Create internet-enabled route table")
    iroutable = vpc.create_route_table(DryRun=args.dry_run)
    iroutable.create_route(DryRun=args.dry_run,
                           DestinationCidrBlock='0.0.0.0/0',
                           GatewayId=gateway.id)

    subnets = []
    secs = []
    for i, c in enumerate(topology.clusters):
        agenda.task("Allocate subnet #{}".format(i+1))
        subnet = vpc.create_subnet(DryRun=args.dry_run,
                                   CidrBlock='10.0.{}.0/24'.format(i))

        if c.internet:
            agenda.subtask("Hook in internet-enable route table")
            iroutable.associate_with_subnet(DryRun=args.dry_run,
                                            SubnetId=subnet.id)

        # set up security croups
        agenda.subtask("Create network security group")
        sec = vpc.create_security_group(
            DryRun=args.dry_run,
            GroupName='{}-cluster-{}'.format(args.deployment, i+1),
            Description='Ingress rules for cluster {}-{}'
                        .format(args.deployment, c.name)
        )
        # allow all internal traffic
        sec.authorize_ingress(DryRun=args.dry_run,
                              IpProtocol='tcp',
                              FromPort=1,
                              ToPort=65535,
                              CidrIp='10.0.0.0/16'
                              )

        if c.expose is not False:
            for p in c.expose:
                agenda.subtask("Allow ingress traffic on port {}".format(p))
                sec.authorize_ingress(DryRun=args.dry_run,
                                      IpProtocol='tcp',
                                      FromPort=p,
                                      ToPort=p,
                                      CidrIp='0.0.0.0/0'
                                      )

        secs.append(sec)
        subnets.append(subnet)

    # Tag all our VPC resources
    agenda.task("Tag all VPC resources")
    ec2.create_tags(DryRun=args.dry_run,
                    Resources=[
                        vpc.id,
                        gateway.id,
                        iroutable.id,
                    ] +
                    [sn.id for sn in subnets] + [sg.id for sg in secs],
                    Tags=[{
                        'Key': 'salvo',
                        'Value': args.deployment,
                    }])

    # Create access keys
    agenda.task("Generate VPC key pair")
    try:
        keys = client.create_key_pair(DryRun=args.dry_run,
                                      KeyName=args.deployment)
    except botocore.exceptions.ClientError:
        # Key probably already exists. Delete and re-create.
        agenda.subfailure("Could not create key pair")
        agenda.subtask("Attempting to delete old key pair")
        client.delete_key_pair(DryRun=args.dry_run, KeyName=args.deployment)
        agenda.subtask("Attempting to generate new key pair")
        keys = client.create_key_pair(DryRun=args.dry_run,
                                      KeyName=args.deployment)

    keymat = keys['KeyMaterial']
    keys = ec2.KeyPair(keys['KeyName'])

    agenda.section("Launch instances")

    # Launch instances
    clusters = []
    for i, c in enumerate(topology.clusters):
        nics = [
            {
                "DeviceIndex": 0,
                "Groups": [secs[i].id],
                "SubnetId": subnets[i].id,
                "DeleteOnTermination": True,
                "AssociatePublicIpAddress": c.internet,
            }
        ]

        agenda.task(
            "Launching {} instances in cluster {}"
            .format(c.attrs['count'], c.name)
        )
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

    exit = 1
    try:
        agenda.task("Wait for HQ to start running")

        hq = clusters[0][0]
        while hq.state['Name'] == 'pending':
            agenda.subtask("Still in 'pending' state")
            sleep(3)
            hq.load()

        if hq.state['Name'] != 'running':
            agenda.failure(hq.state_reason['Message'])
            raise ChildProcessError(hq.state_reason['Message'])

        def prepare(ci, instance):
            global hq
            print("instance {} in {} now available through {}",
                  instance.private_ip_address,
                  topology.clusters[ci].name,
                  hq.public_ip_address)

        agenda.task("Wait for workers to reach 'running' state")

        done = []
        p = Pool(5)
        pending = True
        while pending:
            pending = False
            for i, cluster in enumerate(clusters):
                for ii, instance in enumerate(cluster):
                    if instance.state['Name'] == 'pending':
                        agenda.subtask(
                            "Instance {}.{} is still pending".format(i+1, ii+1)
                        )

                        pending = True
                        instance.load()
                        break
                    elif instance.state['Name'] != 'running':
                        agenda.subfailure(
                            "Instance {}.{} failed: {}".format(
                                i+1, ii+1,
                                instance.state_reason['Message']
                            )
                        )
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

        agenda.task("Wait for HQ to become pingable")

        # Wait for hq to be pingable
        deployment = Deployer(args.playbook.name, topology, keymat, clusters)
        while not deployment.test(hq.public_ip_address):
            sleep(1)

        agenda.task("Wait for workers to become pingable")

        # Wait for workers to be pingable
        for i, cluster in enumerate(clusters):
            for ii, instance in enumerate(cluster):
                while not deployment.test(instance.private_ip_address):
                    sleep(1)

        # Deploy!
        agenda.section("Deploy application")
        exit = deployment.deploy()
    except:
        import traceback
        traceback.print_exc()
    finally:
        agenda.section("Clean up VPC")

        if args.wait:
            agenda.prompt("Press [Enter] when you are ready to clean")
            input()

        # Terminate instances and delete VPC resources
        agenda.task("Terminate all instances")
        instances = list(vpc.instances.all())
        vpc.instances.terminate(DryRun=args.dry_run)
        still_running = True
        while still_running:
            still_running = False
            for i in instances:
                i.load()
                if i.state['Name'] != 'terminated':
                    agenda.subtask("At least one instance still shutting down")
                    still_running = True
                    sleep(3)
                    break

        agenda.task("Delete network resources")
        agenda.subtask("key pair")
        keys.delete(DryRun=args.dry_run)
        agenda.subtask("internet-enabled route associations")
        for r in iroutable.associations.all():
            r.delete(DryRun=args.dry_run)
        agenda.subtask("internet-enabled route table")
        iroutable.delete(DryRun=args.dry_run)
        agenda.subtask("internet gateway")
        gateway.detach_from_vpc(DryRun=args.dry_run, VpcId=vpc.id)
        gateway.delete(DryRun=args.dry_run)
        agenda.subtask("subnets")
        try:
            for sn in subnets:
                sn.delete(DryRun=args.dry_run)
        except:
            agenda.subfailure("failed to delete subnet:")
            import traceback
            traceback.print_exc()
        agenda.subtask("security groups")
        for sg in secs:
            sg.delete()
        agenda.subtask("network interfaces")
        for i in vpc.network_interfaces.all():
            i.delete(DryRun=args.dry_run)

        agenda.task("Delete the VPC")
        vpc.delete(DryRun=args.dry_run)

    return exit
