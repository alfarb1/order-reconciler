from ._named import NamedRetailerParser


class AdidasParser(NamedRetailerParser):
    name = "adidas"
    retailer = "Adidas"
    domains = ("adidas.com", "email.adidas.com")
    subject_hints = ("shipped", "your order", "on its way")
    priority = 50
