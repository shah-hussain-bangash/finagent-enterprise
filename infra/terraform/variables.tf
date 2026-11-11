variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be dev, staging or production (local/test never use Azure)."
  }
}
variable "location" {
  type        = string
  description = "Azure region - OPEN decision SEC-D06 (data residency)."
}
variable "tenant_id" { type = string }
variable "postgres_sku" {
  type    = string
  default = "GP_Standard_D2ds_v5"   # PROPOSED
}
variable "postgres_backup_days" {
  type    = number
  default = 35                      # PROPOSED; RPO/RTO are OPEN (SEC-D09)
}
variable "geo_redundant_backup" {
  type    = bool
  default = true
}
variable "postgres_subnet_id" { type = string }
variable "postgres_private_dns_zone_id" { type = string }
variable "log_retention_days" {
  type    = number
  default = 90                      # PROPOSED; retention schedule is OPEN (SEC-D05)
}
