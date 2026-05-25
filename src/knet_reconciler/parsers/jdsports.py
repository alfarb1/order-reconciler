from ._named import NamedRetailerParser


class JDSportsParser(NamedRetailerParser):
    name = "jdsports"
    retailer = "JD Sports"
    domains = ("jdsports.com", "email.jdsports.com", "jdsports.co.uk")
    subject_hints = ("shipped", "dispatched", "on its way", "your order")
    priority = 50
