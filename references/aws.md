# AWS EC2 workflow

Use this reference only after the user selects AWS. Commands are templates: resolve placeholders into explicit values, show the final resource summary, and obtain confirmation before any create/import/authorize/run command.

## Official sources

Fetched from official AWS documentation on 2026-08-15:

- [AWS CLI configuration and credential files](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html)
- [`sts get-caller-identity`](https://docs.aws.amazon.com/cli/latest/reference/sts/get-caller-identity.html)
- [`ec2 import-key-pair`](https://docs.aws.amazon.com/cli/latest/reference/ec2/import-key-pair.html)
- [`ec2 run-instances`](https://docs.aws.amazon.com/cli/latest/reference/ec2/run-instances.html)
- [`ec2 describe-instance-status`](https://docs.aws.amazon.com/cli/latest/reference/ec2/describe-instance-status.html)

Re-fetch these pages if current syntax, regional availability, pricing, or quotas matter. CLI references establish command behavior, not price.

## Verify context

```bash
aws --version
aws configure list
aws sts get-caller-identity --output json
aws ec2 describe-regions --region <region> --query 'Regions[?RegionName==`<region>`].OptInStatus' --output text
aws ec2 describe-vpcs --region <region> --vpc-ids <vpc-id> --output json
aws ec2 describe-subnets --region <region> --subnet-ids <subnet-id> --output json
```

Use `--profile <profile>` consistently when the user selected a non-default profile. Confirm the returned AWS account and ARN with the user. Confirm the subnet maps public IPs or explicitly request a public IP at launch. Verification errors do not authorize credential reconfiguration.

## Resolve an Ubuntu image

Use Canonical owner ID `099720109477`, require HVM/EBS/amd64, and select the newest matching Ubuntu 24.04 LTS image in the chosen region:

```bash
aws ec2 describe-images \
  --region <region> --owners 099720109477 \
  --filters 'Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*' 'Name=state,Values=available' \
  --query 'sort_by(Images,&CreationDate)[-1].[ImageId,Name,CreationDate]' --output json
```

Show the resolved AMI ID and name. Do not use an AMI copied from another region.

## Create narrowly scoped access

Derive unique names such as `remote-computer-<vm-name>`. Detect collisions first:

```bash
aws ec2 describe-key-pairs --region <region> --key-names <key-name> --output json
aws ec2 describe-security-groups --region <region> --filters Name=group-name,Values=<security-group-name> Name=vpc-id,Values=<vpc-id> --output json
```

A “not found” response is expected for a new key. Do not overwrite or reuse an unrelated cloud key.

Import only the public key:

```bash
aws ec2 import-key-pair --region <region> --key-name <key-name> --public-key-material fileb://<absolute-public-key-path> --output json
```

Create a security group in the selected VPC and restrict SSH to the confirmed client CIDR:

```bash
aws ec2 create-security-group --region <region> --vpc-id <vpc-id> --group-name <security-group-name> --description 'SSH for remote-computer <vm-name>' --output json
aws ec2 authorize-security-group-ingress --region <region> --group-id <security-group-id> --protocol tcp --port 22 --cidr <confirmed-ip/32>
```

Record the key name and security-group ID immediately. If a later step fails, report these residual resources; do not delete them without approval.

## Launch and verify

Use a client token to make retries idempotent. Require IMDSv2, an encrypted gp3 boot volume, an explicit subnet/security group, and tags:

```bash
aws ec2 run-instances \
  --region <region> --client-token <stable-unique-token> \
  --image-id <ami-id> --instance-type <machine-type> \
  --key-name <key-name> --subnet-id <subnet-id> \
  --security-group-ids <security-group-id> --associate-public-ip-address \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=<vm-name>},{Key=ManagedBy,Value=remote-computer}]' \
  --count 1 --output json
```

Capture `Instances[0].InstanceId`; never infer it by listing the newest instance. Then:

```bash
aws ec2 wait instance-running --region <region> --instance-ids <instance-id>
aws ec2 describe-instances --region <region> --instance-ids <instance-id> --query 'Reservations[0].Instances[0].{State:State.Name,IP:PublicIpAddress,Type:InstanceType,Image:ImageId}' --output json
aws ec2 describe-instance-status --region <region> --include-all-instances --instance-ids <instance-id> --output json
```

Wait for a public IP and running state. System/instance status checks may remain initializing briefly; report that honestly. The direct login for the Ubuntu image is usually `ubuntu`.
