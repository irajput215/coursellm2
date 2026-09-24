output "vpc_id" {
  description = "ID of the VPC everything runs in."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "CIDR block of the VPC."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "Public subnet IDs, ordered by availability zone. The ALB and NAT gateways live here."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs, ordered by availability zone. ECS tasks, RDS and ElastiCache live here."
  value       = aws_subnet.private[*].id
}

output "private_route_table_ids" {
  description = "Private route table IDs. The S3 gateway endpoint is attached to these."
  value       = aws_route_table.private[*].id
}

output "internet_gateway_id" {
  description = "ID of the internet gateway attached to the VPC."
  value       = aws_internet_gateway.this.id
}

output "nat_gateway_ids" {
  description = "NAT gateway IDs. Length 1 when single_nat_gateway is true, otherwise one per AZ."
  value       = aws_nat_gateway.nat[*].id
}

output "nat_gateway_count" {
  description = "Number of NAT gateways created. The dominant fixed cost in a low-traffic account."
  value       = local.nat_count
}

output "vpc_endpoint_security_group_id" {
  description = "Security group attached to the interface VPC endpoints, or null when endpoints are disabled."
  value       = try(aws_security_group.vpc_endpoints[0].id, null)
}

output "s3_gateway_endpoint_id" {
  description = "ID of the S3 gateway endpoint, or null when endpoints are disabled."
  value       = try(aws_vpc_endpoint.s3[0].id, null)
}

output "interface_endpoint_ids" {
  description = "Map of interface endpoint short name to endpoint ID."
  value       = { for name, endpoint in aws_vpc_endpoint.interface : name => endpoint.id }
}
