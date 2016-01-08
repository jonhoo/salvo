# Python 3.x compat
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import (
         bytes, dict, int, list, object, range, str,
         ascii, chr, hex, input, next, oct, open,
         pow, round, super,
         filter, map, zip)

import json


class Topology:
    clusters = []

    def __init__(self, clusters):
        self.clusters = clusters

    @staticmethod
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
            "expose": False,
            "internet": True,
            "image": "ami-d05e75b8",  # Ubuntu Server 14.04 LTS
            "itype": "t2.nano",
            "count": 1,
        }

        for k, v in attrs.items():
            if k in self.attrs:
                if isinstance(v, str) and v.startswith('$'):
                    self.attrs[k] = params[attrs[k].lstrip('$')]
                else:
                    self.attrs[k] = attrs[k]
            elif k != "name":
                raise KeyError("Unknown cluster attribute '{}'".format(k))

        assert not self.attrs['expose'] or self.attrs['internet']

    def __getattr__(self, name):
        if name in self.attrs:
            return self.attrs[name]
        raise AttributeError(name)
