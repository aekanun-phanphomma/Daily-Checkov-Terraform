resource "azurerm_network_security_rule" "rdp" {
  name                        = "allow-rdp"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "3389"
  source_address_prefix       = "10.0.0.0/16" # FIX: restrict source to internal virtual network CIDR
  destination_address_prefix  = "*"
  resource_group_name         = "example-rg"
  network_security_group_name = "example-nsg"
}
