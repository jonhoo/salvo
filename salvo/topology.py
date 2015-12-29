import json


class Topology:
    clusters = []

    def __init__(self, clusters):
        self.clusters = clusters

    def load_file(handle, parameters):
        t = json.load(handle)

        for c in t['clusters']:
            assert c['name'] != 'hq'

        return Topology([
            Cluster(c['name'], c, parameters)
            for c in t['clusters']
        ])


class Cluster:
    def __init__(self, name, attrs, params):
        self.name = name
        self.attrs = {
            "public": False,
            "image": "ami-60b6c60a",  # Amazon Linux AMI 2015.09.1
            "itype": "t2.nano",
            "role": None,
            "count": 1,
        }

        for k, v in attrs.items():
            if k in self.attrs:
                if v.startswith('$'):
                    self.attrs[k] = params[attrs[k].lstrip('$')]
                else:
                    self.attrs[k] = attrs[k]
            elif k != "name":
                raise KeyError("Unknown cluster attribute '{}'".format(k))

        assert self.attrs['role'] is not None
