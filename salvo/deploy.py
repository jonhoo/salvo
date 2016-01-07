# Python 3.x compat
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import (
         bytes, dict, int, list, object, range, str,
         ascii, chr, hex, input, next, oct, open,
         pow, round, super,
         filter, map, zip)

import os.path
import sys
import os


class Deployer:
    def __init__(self, config, topology, keymat, clusters):
        self.pwd = os.path.abspath(os.getcwd())
        self.playbook = os.path.abspath(config)
        self.wd = os.path.dirname(self.playbook)
        hq = clusters[0][0]

        os.chdir(self.wd)

        # Write out inventory
        with open("inventory", "w") as hosts:
            for ci, cluster in enumerate(clusters):
                print("[{}]".format(topology.clusters[ci].name), file=hosts)
                for instance in cluster:
                    print(
                        "{}".format(
                            instance.private_ip_address if ci != 0 else
                            instance.public_ip_address
                        ),
                        file=hosts
                    )
                print("", file=hosts)

        # Write out SSH key
        with open("key.pem", "w") as keyfile:
            import stat
            print(keymat.decode('UTF-8'), file=keyfile)
            os.chmod(keyfile.name, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        # Set up all SSH connections to proxy through hq
###############################################################################
        # https://medium.com/@paulskarseth/ansible-bastion-host-proxycommand-e6946c945d30
        with open("ssh.cfg", "w") as sshcfg:
            from itertools import chain
            ips = list(chain.from_iterable(
                map(lambda c:
                    map(lambda i: i.private_ip_address, c),
                    clusters)
            ))

            # TODO: User here depends on AMI
            print("""
Host {}
  ProxyCommand           ssh -F ssh.cfg -W %h:%p {}
  # This is safe since it's all internal traffic
  StrictHostKeyChecking  no
  UserKnownHostsFile     /dev/null

Host *
  User            ubuntu
  IdentityFile    key.pem
  ControlMaster   auto
  ControlPath     ~/.ssh/mux-%r@%h:%p
  ControlPersist  15m
  # This is *not* safe -- should verify traffic to the bastion!
  StrictHostKeyChecking  no
  UserKnownHostsFile     /dev/null
""".format(" ".join(ips), hq.public_ip_address), file=sshcfg)

        with open("ansible.cfg", "w") as anscfg:
            print("""
[ssh_connection]
ssh_args = -F "{}"
control_path = ~/.ssh/mux-%r@%h:%p
""".format("ssh.cfg"), file=anscfg)
###############################################################################

        os.chdir(self.pwd)

    def test(self, target):
        os.chdir(self.wd)
        # need to chdir before importing!
        from ansible.cli.adhoc import AdHocCLI
        cli = AdHocCLI(
            [
                sys.argv[0],
                '-i', 'inventory',
                target,
                '-m', 'ping',
                '-vvvv',
                '-o'
            ]
        )
        cli.parse()
        exit = cli.run()
        os.chdir(self.pwd)
        return exit == 0

    def deploy(self):
        os.chdir(self.wd)
        # need to chdir before importing!
        from ansible.cli.playbook import PlaybookCLI
        cli = PlaybookCLI(
            [
                sys.argv[0],
                '-i', 'inventory',
                '-vvvv',
                self.playbook
            ]
        )
        cli.parse()
        exit = cli.run()
        os.chdir(self.pwd)
        return exit
