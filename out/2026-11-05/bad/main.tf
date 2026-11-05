resource "azurerm_container_registry" "example" {
  name                          = "exampleacr12345"
  resource_group_name           = "example-rg"
  location                      = "eastus"
  sku                           = "Standard"
  admin_enabled                 = false
  public_network_access_enabled = false
  anonymous_pull_enabled        = true
}
