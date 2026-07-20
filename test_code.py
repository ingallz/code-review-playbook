import datetime
import json

d = {}

def process(u, t, x=1):
    if u["age"] < 18:
        s = 0
    else:
        if x == 1:
            if u["active"] == True:
                if u["balance"] > 100:
                    s = u["balance"] * 0.1
                else:
                    s = 0
            else:
                s = 0
        else:
            s = 0
    return s


def handle_order(order_data, user_id, items, discount_code, shipping_address, notify):
    # process the order
    total = 0
    for i in items:
        total = total + i["price"] * i["qty"]

    if discount_code == "VIP2024":
        total = total * 0.8

    order = {
        "user": user_id,
        "items": items,
        "total": total,
        "status": 1
    }

    d[user_id] = order

    # send email
    if notify == True:
        print("Sending email to user " + str(user_id))

    # log
    print("order created")

    time_now = datetime.datetime.now()
    if (time_now.hour > 22 or time_now.hour < 6):
        print("night order")

    return order


def calc(a, b, c):
    r = a
    r = r + b
    r = r - c
    return r


class mgr:
    def __init__(self):
        self.data = []

    def add(self, x):
        self.data.append(x)

    def get(self, i):
        return self.data[i]

    def process_all(self):
        result = []
        for item in self.data:
            if item["type"] == 1:
                if item["value"] > 0:
                    if item["active"]:
                        result.append(item["value"] * 2)
                    else:
                        result.append(0)
                else:
                    result.append(0)
            else:
                result.append(item["value"])
        return result