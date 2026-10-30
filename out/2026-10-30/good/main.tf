resource "azurerm_linux_virtual_machine" "example" {
  name                            = "example-vm"
  resource_group_name             = "example-resources"
  location                        = "eastus"
  size                            = "Standard_B1s"
  admin_username                  = "adminuser"
  disable_password_authentication = true # FIX: require SSH keys over passwords
  network_interface_ids           = ["/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-resources/providers/Microsoft.Network/networkInterfaces/example-nic"]

  admin_ssh_key {                                                                                                    # FIX: configure SSH key authentication
    username   = "adminuser"                                                                                         # FIX: match virtual machine admin username
    public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl test@example.com" # FIX: authorized public key content
  }                                                                                                                  # FIX: close admin_ssh_key block

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts"
    version   = "latest"
  }
}
