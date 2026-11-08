resource "azurerm_role_definition" "example" {
  name        = "custom-subscription-role"
  scope       = "/subscriptions/00000000-0000-0000-0000-000000000000"
  description = "Custom role with full administrative access"

  permissions {
    actions = ["*"]
  }

  assignable_scopes = [
    "/subscriptions/00000000-0000-0000-0000-000000000000",
  ]
}
