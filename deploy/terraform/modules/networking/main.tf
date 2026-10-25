# Shared VPC: public subnets (load balancers, NAT) + private subnets
# (Lambda functions, EKS nodes, RDS). Both platform stacks use this module
# so the app image and env vars are the only per-platform differences.

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}

locals {
  # slice() fails the plan if az_count exceeds the AZs available in the
  # region — that is the fail-fast point for the AZ-count check, since
  # variable validation blocks cannot reference data sources.
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # One NAT per AZ when nat_per_az is true, else a single NAT in AZ 0.
  # The single-NAT default is a deliberate dev-cost tradeoff: an AZ-0
  # failure kills egress from every private subnet (see nat_per_az).
  nat_count      = var.enable_nat ? (var.nat_per_az ? var.az_count : 1) : 0
  private_rt_ids = aws_route_table.private[*].id
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(var.tags, { Name = "${var.name}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-igw" })
}

resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.cidr_block, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.tags, { Name = "${var.name}-public-${local.azs[count.index]}" })
}

resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.cidr_block, 8, count.index + var.az_count)
  availability_zone = local.azs[count.index]

  tags = merge(var.tags, { Name = "${var.name}-private-${local.azs[count.index]}" })
}

resource "aws_eip" "nat" {
  count  = local.nat_count
  domain = "vpc"

  tags = merge(var.tags, { Name = "${var.name}-nat-eip-${count.index}" })
}

resource "aws_nat_gateway" "this" {
  count         = local.nat_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[var.nat_per_az ? count.index : 0].id

  tags = merge(var.tags, { Name = "${var.name}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(var.tags, { Name = "${var.name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  # One route table per AZ when nat_per_az is true (each AZ's table
  # points at its own NAT), else a single shared table.
  count  = var.enable_nat ? (var.nat_per_az ? var.az_count : 1) : 0
  vpc_id = aws_vpc.this.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[var.nat_per_az ? count.index : 0].id
  }

  tags = merge(var.tags, { Name = "${var.name}-private-rt-${count.index}" })
}

resource "aws_route_table_association" "private" {
  count          = var.enable_nat ? var.az_count : 0
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[var.nat_per_az ? count.index : 0].id
}

# VPC endpoints (optional, production): keep ECR/STS/S3 traffic off the
# NAT. The S3 gateway endpoint is free; interface endpoints cost per AZ.
resource "aws_vpc_endpoint" "s3" {
  count             = var.enable_vpc_endpoints ? 1 : 0
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = local.private_rt_ids

  tags = merge(var.tags, { Name = "${var.name}-vpce-s3" })
}

resource "aws_security_group" "vpce" {
  count       = var.enable_vpc_endpoints ? 1 : 0
  name        = "${var.name}-vpce-sg"
  description = "VPC interface endpoints (ECR, STS)"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  tags = merge(var.tags, { Name = "${var.name}-vpce-sg" })
}

resource "aws_vpc_endpoint" "interface" {
  for_each = var.enable_vpc_endpoints ? toset(["ecr.api", "ecr.dkr", "sts"]) : toset([])

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.${each.value}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpce[0].id]

  tags = merge(var.tags, { Name = "${var.name}-vpce-${each.value}" })
}
