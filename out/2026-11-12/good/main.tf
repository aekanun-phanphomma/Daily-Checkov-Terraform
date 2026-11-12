resource "azurerm_network_security_rule" "example" {
  name                        = "allow-http"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "80"
  source_address_prefix       = "10.0.0.0/16" # FIX: restrict HTTP access to internal network
  destination_address_prefix  = "*"
  resource_group_name         = "example-rg"
  network_security_group_name = "example-nsg"
}
