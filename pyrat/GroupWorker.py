from __future__ import print_function
import pyrat
import logging
from PyQt4 import QtGui, QtCore


class GroupWorker(pyrat.Worker):
    def __init__(self, *args, **kwargs):
        super(GroupWorker, self).__init__(*args, **kwargs)

    def run(self, *args, **kwargs):
        para = [foo['var'] for foo in self.para]
        self.checkpara(kwargs, para)
        logging.info('Starting processing group : ' + self.name + '  ' + str(dict((k, v)
                                                  for k, v in self.__dict__.items() if k in para or k in kwargs)))

        if self.checkinput():
            self.group(*args, **kwargs)
            newlayer = pyrat.data.active
            if len(newlayer) == 1:
                newlayer = newlayer[0]
            return newlayer
        else:
            return False

    def group(self, *args, **kwargs):
        print("ERROR: group() method not overloaded")
        return False, False
