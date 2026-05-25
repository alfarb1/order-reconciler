from ._named import NamedRetailerParser


class FootLockerParser(NamedRetailerParser):
    name = "footlocker"
    retailer = "Foot Locker"
    domains = ("footlocker.com", "email.footlocker.com", "footlocker.eu")
    subject_hints = ("shipped", "dispatched", "your order", "on the way")
    priority = 50
