Salvo is a toolkit for provisioning large, single-shot, multi-worker
computations. It creates an [Amazon Virtual Private
Cloud](https://aws.amazon.com/vpc/), and launches a configurable number
of machines in separate subnets. These instances are then configured and
started according to [Ansible
playbook](https://docs.ansible.com/ansible/playbooks_intro.html)
[roles](https://docs.ansible.com/ansible/playbooks_roles.html#roles).
Once all the launched processes have finished, Salvo terminates the EC2
machines and cleans up the VPC.

## Quick Start

Install Salvo:

 1. `git clone https://github.com/jonhoo/salvo.git && cd salvo`
 2. `virtualenv env`
 3. `env/bin/pip install -e git+https://github.com/ansible/ansible.git@devel#egg=ansible-2.1.0`
 4. `env/bin/pip install -e .`

Write configuration files:

 1. Create an `ec2.json` file (see *Machine provisioning* below)
 2. Create an Ansible playbook file (see *Machine configuration* below)
 3. Store your
    [Amazon credentials](https://docs.aws.amazon.com/aws-sdk-php/v2/guide/credentials.html#credential-profiles)
    in `~/.aws/credentials`. Instructions on how to get your credentials
    can be found
    [here](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-getting-set-up.html#d0e1950).

Run Salvo:

```
env/bin/salvo --playbook /path/to/playbook.yml /path/to/ec2.json
```

## Machine provisioning

Salvo requires you to specify the machine topology you want through a
single json file that is passed in as a command-line argument. A file
provisioning a single machine might look like this:

```json
{
	"clusters": [
		{ "name": "master" }
	]
}
```

A more involved example might look as follows:

```json
{
	"clusters": [
		{
			"expose": [22, 80],
			"name": "master",
		},
		{
			"count": 10,
			"name": "mappers",
		},
		{
			"count": 10,
			"internet": false,
			"name": "reducers",
		}
	]
}
```

By default, all machines will be given a public IP address, but no
inbound connection attempts are allowed. To expose port to the outside
world, set the `expose` attribute. Machines in clusters marked with
`internet: false` will not be given a public IP, and cannot have exposed
ports.

Salvo always provisions one extra host called `hq`, in its own cluster,
which has a public IP address, and which exposes SSH over the internet.
This is a bastion host used to access the other machines which all have
only private IP addresses.

## Machine configuration

Machine configuration and application deployment is done with Ansible.
The integration is simple: Salvo will spin up the appropriate number of
machines for each cluster, put them in separate private IP subnets, and
put the list of IPs in a file called `inventory` in the same directory
as the Ansible playbook (give with `--playbook`). Each cluster's list of
IPs will be preceeded by `[$cluster_name]`, allowing the cluster names
to be used directly in Ansible `hosts:` patterns.

As an example of how one might specify the configuration for the hosts
in the first ec2 provisioning example above, consider the following
`playbook.yml` file:

```yaml
- hosts: master
  become: true
  name: Map/Reduce master
  gather_facts: true
  roles:
        - mr-master
```

The role `mr-master` can then be constructed the same way as regular
Ansible roles, such as by creating `roles/mr-master/tasks/main.yml`.

Salvo will also add an `ssh.cfg` file in the same directory, which
allows direct, password-less SSH access to all the provisioned machines
using:

```
ssh -F ssh.cfg 10.0.XXX.YYY
```
