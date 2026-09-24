# Network layer: VPC, public/private subnets across at least two AZs, NAT egress
# for the private subnets, and the VPC endpoints that keep ECR, Secrets Manager
# and CloudWatch Logs traffic off the NAT.
#
# Layout (docs/architecture/deployment.md section 6):
#   public subnets   -> internet-facing ALB and the NAT gateway(s) only
#   private subnets  -> ECS tasks, RDS, ElastiCache; no route from the internet
#
# Nothing is applied by this module on its own; `terraform plan` creates a diff
# and no resource until an operator applies it.

locals {
  name_prefix = "${var.project}-${var.environment}"
  az_count    = length(var.availability_zones)

  # Derive /20 subnet blocks from the VPC CIDR when the caller does not supply
  # them: first half of the space public, second half private.
  public_subnet_cidrs = length(var.public_subnet_cidrs) > 0 ? var.public_subnet_cidrs : [
    for i in range(local.az_count) : cidrsubnet(var.vpc_cidr, 4, i)
  ]
  private_subnet_cidrs = length(var.private_subnet_cidrs) > 0 ? var.private_subnet_cidrs : [
    for i in range(local.az_count) : cidrsubnet(var.vpc_cidr, 4, i + 8)
  ]

  # One NAT gateway in dev, one per AZ in prod (see README cost table).
  nat_count        = var.single_nat_gateway ? 1 : local.az_count
  private_rt_count = var.single_nat_gateway ? 1 : local.az_count

  create_endpoints = var.enable_vpc_endpoints && length(var.interface_endpoints) > 0
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${local.name_prefix}-igw" })
}

# --- public subnets ----------------------------------------------------------

resource "aws_subnet" "public" {
  count = local.az_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = local.public_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  # The ALB attaches an elastic IP per public subnet; tasks never run here, so
  # there is no reason to hand out public addresses on launch.
  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-public-${var.availability_zones[count.index]}"
    Tier = "public"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-public-rt"
    Tier = "public"
  })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = local.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- private subnets ---------------------------------------------------------

resource "aws_subnet" "private" {
  count = local.az_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-private-${var.availability_zones[count.index]}"
    Tier = "private"
  })
}

resource "aws_eip" "nat" {
  count = local.nat_count

  domain = "vpc"

  tags = merge(var.tags, { Name = "${local.name_prefix}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_nat_gateway" "nat" {
  count = local.nat_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(var.tags, { Name = "${local.name_prefix}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "private" {
  count = local.private_rt_count

  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-private-rt-${count.index}"
    Tier = "private"
  })
}

resource "aws_route" "private_nat" {
  count = local.private_rt_count

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.nat[count.index % local.nat_count].id
}

resource "aws_route_table_association" "private" {
  count = local.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[var.single_nat_gateway ? 0 : count.index].id
}

# --- VPC endpoints -----------------------------------------------------------

# An S3 gateway endpoint keeps document traffic and ECR image layers on the AWS
# network: cheaper than NAT data processing, and image pulls stop depending on a
# NAT gateway being up.
resource "aws_vpc_endpoint" "s3" {
  count = var.enable_vpc_endpoints ? 1 : 0

  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = merge(var.tags, { Name = "${local.name_prefix}-s3-endpoint" })
}

resource "aws_security_group" "vpc_endpoints" {
  count = local.create_endpoints ? 1 : 0

  name        = "${local.name_prefix}-vpce-sg"
  description = "HTTPS to the interface VPC endpoints from inside the VPC"
  vpc_id      = aws_vpc.this.id

  # Interface endpoints are reached over TLS on 443. No egress rules are needed:
  # security groups are stateful, so return traffic is allowed automatically.
  egress = []

  tags = merge(var.tags, { Name = "${local.name_prefix}-vpce-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "vpc_endpoints_vpc_https" {
  count = local.create_endpoints ? 1 : 0

  security_group_id = aws_security_group.vpc_endpoints[0].id
  description       = "HTTPS from the VPC CIDR to interface endpoints"
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.create_endpoints ? toset(var.interface_endpoints) : toset([])

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints[0].id]
  private_dns_enabled = true

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-${replace(each.value, ".", "-")}-endpoint"
  })
}
