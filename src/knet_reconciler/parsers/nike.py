from ._named import NamedRetailerParser


class NikeParser(NamedRetailerParser):
    name = "nike"
    retailer = "Nike"
    domains = ("nike.com", "email.nike.com", "ship-confirm.nike.com")
    subject_hints = ("shipped", "your order", "on the way", "tracking")
    priority = 50
