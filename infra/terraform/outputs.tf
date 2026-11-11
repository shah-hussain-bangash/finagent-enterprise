output "key_vault_name" { value = azurerm_key_vault.kv.name }
output "postgres_fqdn" { value = azurerm_postgresql_flexible_server.pg.fqdn }
output "blob_account_url" { value = azurerm_storage_account.blobs.primary_blob_endpoint }
output "servicebus_namespace" { value = "${azurerm_servicebus_namespace.bus.name}.servicebus.windows.net" }
output "search_endpoint" { value = "https://${azurerm_search_service.search.name}.search.windows.net" }
output "workload_identity_client_ids" {
  value = { for k, v in azurerm_user_assigned_identity.workload : k => v.client_id }
}
