from __future__ import print_function
from PyQt4 import QtGui, QtCore
import pyrat
import copy
import logging
from pyrat.tools import ProgressBar, flattenlist, unflattenlist


def exec_out(args):
    """
    Helper function to make multiprocessing on class methods possible
    """
    func = getattr(args[0], args[1])
    if 'args' in args[2]:
        arg = args[2]['args']
        del args[2]['args']
        return func(arg, **args[2]), args[2]['meta']
    else:
        return func(**args[2]), args[2]['meta']


class Worker(object):

    para = {}
    blocksize = 128
    blockprocess = True
    delete = False

    def __init__(self, *args, **kwargs):
        super(Worker, self).__init__()

        self.nthreads = pyrat.pool._processes                                  # number of threads for processing
        # self.blockprocess = True                                               # blockprocessing on/off
        # self.blocksize = 128                                                   # size of single block

        for para in self.para:                                                 # copy defaults to self
            setattr(self, para['var'], para['value'])
        for (k, v) in kwargs.items():                                          # copy keywords to self
            setattr(self, k, v)                                                # eventually overwriting defaults
        if not hasattr(self, 'layer'):                                         # if no keyword was used
            self.layer = pyrat.data.active                                     # use active layer
# --------------------------------------------------

        self.name = self.__class__.__name__                                    # name of worker class (string)
        self.input = ''                                                        # input layer(s)
        self.output = ''                                                       # output layer(s)
        self.blockoverlap = 0                                                  # block overlap
        self.vblock = False                                                    # vertical blocks on/off
        self.blocks = []                                                       # list of block boundaries
        self.valid = []                                                        # valid part of each block
        # self.block = False                                                     # actual block range / validity
        self.allowed_ndim = False
        self.require_para = False
        self.allowed_dtype = False

        if pyrat._debug is True:
            self.nthreads = 1

    def layer_process(self, func, silent=True, **kwargs):
        """
        Generates a new layer from the return of its method 'func', called with **kwargs (and possible args stored
        in in the keyword 'args' as tuple). The size of the produced layer must be passed in the 'size'
        keyword. Returns the name of the new layer(s)
        """
        if 'layer' in kwargs:
            self.input = kwargs['layer']
        else:
            self.input = pyrat.data.active

        if any([isinstance(foo, list) for foo in self.input]):
            layshp = self.input
            self.input = flattenlist(self.input)
            nested = True
        else:
            nested = False

        query = pyrat.data.queryLayer(self.input)
        if isinstance(query, list):
            dshape = query[0]['shape']
        else:
            dshape = query['shape']

        if self.vblock:                                                        # init block processing
            self.initBP(dshape[-1])
        else:
            self.initBP(dshape[-2])

        if len(self.blocks) > 1 and self.nthreads > 1:                         # group chunks of blocks
            idx = [self.blocks[i:i + self.nthreads] for i in range(0, len(self.blocks), self.nthreads)]
        else:
            idx = [[block] for block in self.blocks]

        metain = pyrat.data.getAnnotation(layer=self.input)

        nb1 = 0                                                                # input block number
        nb2 = 0                                                                # output block number
        if silent is False:
            P = ProgressBar('  ' + self.name, len(self.blocks))
            P.update(0)
        for bidx in idx:                                                       # loop over chunks of blocks
            meta = copy.deepcopy(metain)
            inputs = []
            for ix in bidx:                                                    # loop over blocks in chunk
                data = self.read_block(nb1)
                if nested is True:
                    data = unflattenlist(data, layshp)
                kwargs_copy = copy.deepcopy(kwargs)
                kwargs_copy["args"] = data
                kwargs_copy["meta"] = meta
                if self.vblock:
                    kwargs_copy['block'] = (0, dshape[-2]) + tuple(self.blocks[nb1])
                else:
                    kwargs_copy['block'] = tuple(self.blocks[nb1])+(0, dshape[-1])
                kwargs_copy['valid'] = tuple(self.valid[nb1])
                inputs.append((self, func.__name__, kwargs_copy))              # accumulate inputs
                nb1 += 1

            if self.nthreads > 1:
                result = pyrat.pool.imap(exec_out, inputs)                     # do the multiprocessing
            else:
                result = map(exec_out, inputs)                                 # or avoid it...
            for res in result:                                                 # loop over output blocks (in chunk)
                metaout = res[1]                                               # meta data (possibly modified)
                if nb2 == 0:                                                   # first block -> generate new layer(s)
                    if isinstance(res[0], list) or isinstance(res[0], tuple):
                        self.output = []
                        for n, re in enumerate(res[0]):
                            lshape = re.shape[0:-2]                            # layer geometry
                            if self.vblock:
                                dshape = (re.shape[-2], dshape[-1])
                            else:
                                dshape = (dshape[-2], re.shape[-1])
                            if self.blockprocess is False:                     # no blockprocessing
                                lshape = ()                                    # -> entire image
                                dshape = re.shape
                            self.output.append(pyrat.data.addLayer(dtype=re.dtype, shape=lshape+dshape))
                    else:
                        lshape = res[0].shape[0:-2]                            # layer geometry
                        if self.vblock:
                            dshape = (res[0].shape[-2], dshape[-1])
                        else:
                            dshape = (dshape[-2], res[0].shape[-1])
                        if self.blockprocess is False:                         # no blockprocessing
                            lshape = ()                                        # -> entire image
                            dshape = res[0].shape
                        self.output = pyrat.data.addLayer(dtype=res[0].dtype, shape=lshape+dshape)
                self.save_block(res[0], nb2)
                nb2 += 1
                if silent is False:
                    P.update(nb2)
        if silent is False:
            del P
        pyrat.data.setAnnotation(metaout, layer=self.output)                   # add meta data to output layer
        return self.output                                                     # return output layer

    def layer_fromfunc(self, func, size=(1, 1), silent=True, **kwargs):
        """
        Generates a new layer from the return of its method 'func', called with **kwargs (and possible args stored
        in in the keyword 'args' as tuple). The size of the produced layer must be passed in the 'size'
        keyword. Returns the name of the new layer(s)
        """

        if self.vblock:
            self.initBP(size[-1])
            kwargs["size"] = (size[-2], self.blocksize)
        else:
            self.initBP(size[-2])
            kwargs["size"] = (self.blocksize, size[-1])

        if len(self.blocks) > 1 and self.nthreads > 1:
            idx = [self.blocks[i:i + self.nthreads] for i in range(0, len(self.blocks), self.nthreads)]
        else:
            idx = [[block] for block in self.blocks]

        kwargs["meta"] = {}
        nb1 = 0                                                                # input block number
        nb2 = 0                                                                # output block number
        if silent is False:
            P = ProgressBar('  ' + self.name, len(self.blocks))
            P.update(0)
        for bidx in idx:
            inputs = []
            for ix in bidx:
                kwargs_copy = copy.deepcopy(kwargs)
                if self.vblock:
                    kwargs_copy['block'] = (0, size[-2]) + tuple(self.blocks[nb1])
                else:
                    kwargs_copy['block'] = tuple(self.blocks[nb1])+(0, size[-1])
                kwargs_copy['valid'] = tuple(self.valid[nb1])
                inputs.append((self, func.__name__, kwargs_copy))
                nb1 += 1
            if self.nthreads > 1:
                result = pyrat.pool.imap(exec_out, inputs)
            else:
                result = map(exec_out, inputs)
            for res in result:
                if nb2 == 0:
                    if isinstance(res[0], list) or isinstance(res[0], tuple):
                        self.output = []
                        for n, re in enumerate(res[0]):
                            self.output.append(pyrat.data.addLayer(dtype=re.dtype, shape=size))
                    else:
                        self.output = pyrat.data.addLayer(dtype=res[0].dtype, shape=size)
                self.save_block(res[0], nb2)
                nb2 += 1
                if silent is False:
                    P.update(nb2)
        if silent is False:
            del P
        return self.output

    def layer_accumulate(self, func, silent=True, **kwargs):
        if 'layer' in kwargs:
            self.input = kwargs['layer']
        else:
            self.input = pyrat.data.active

        query = pyrat.data.queryLayer(self.input)
        if isinstance(query, list):
            dshape = query[0]['shape']
        else:
            dshape = query['shape']

        if self.vblock:
            self.initBP(dshape[-1])
        else:
            self.initBP(dshape[-2])

        if len(self.blocks) > 1 and self.nthreads > 1:
            idx = [self.blocks[i:i + self.nthreads] for i in range(0, len(self.blocks), self.nthreads)]
        else:
            idx = [[block] for block in self.blocks]

        if 'combine' in kwargs:                   # if a combine function is provided, extract it
            combine_func = kwargs['combine']
            del kwargs['combine']
        else:
            combine_func = lambda x: x

        out = []
        nb = 0
        metain = pyrat.data.getAnnotation(layer=self.input)
        if silent is False:
            P = ProgressBar('  ' + self.name, len(self.blocks))
            P.update(0)
        for bidx in idx:
            meta = copy.deepcopy(metain)
            inputs = []
            for ix in bidx:
                data = self.read_block(nb)
                kwargs_copy = copy.deepcopy(kwargs)
                kwargs_copy["args"] = data
                kwargs_copy["meta"] = meta
                if self.vblock:
                    kwargs_copy['block'] = (0, dshape[-2]) + tuple(self.blocks[nb])
                else:
                    kwargs_copy['block'] = tuple(self.blocks[nb])+(0, dshape[-1])
                kwargs_copy['valid'] = tuple(self.valid[nb])
                inputs.append((self, func.__name__, kwargs_copy))
                nb += 1
                if silent is False:
                    P.update(nb)
            if self.nthreads > 1:
                result = pyrat.pool.imap(exec_out, inputs)
            else:
                result = map(exec_out, inputs)
            for res in result:
                out.append(res[0])

        if silent is False:
            del P
        out = combine_func(out)                 # call the combine function
        return out

    def layer_extract(self, func, silent=True, **kwargs):
        if 'layer' in kwargs:
            self.input = kwargs['layer']
        else:
            self.input = pyrat.data.active

        query = pyrat.data.queryLayer(self.input)
        if isinstance(query, list):
            dshape = query[0]['shape']
        else:
            dshape = query['shape']

        if self.vblock:
            self.initBP(dshape[-1])
        else:
            self.initBP(dshape[-2])

        if len(self.blocks) > 1 and self.nthreads > 1:
            idx = [self.blocks[i:i + self.nthreads] for i in range(0, len(self.blocks), self.nthreads)]
        else:
            idx = [[block] for block in self.blocks]

        out = []
        nb = 0
        metain = pyrat.data.getAnnotation(layer=self.input)
        if silent is False:
            P = ProgressBar('  ' + self.name, len(self.blocks))
            P.update(0)
        for bidx in idx:
            meta = copy.deepcopy(metain)
            inputs = []
            for ix in bidx:
                data = self.read_block(nb)
                kwargs_copy = copy.deepcopy(kwargs)
                kwargs_copy["args"] = data
                kwargs_copy["meta"] = meta
                if self.vblock:
                    kwargs_copy['block'] = (0, dshape[-2]) + tuple(self.blocks[nb])
                else:
                    kwargs_copy['block'] = tuple(self.blocks[nb])+(0, dshape[-1])
                kwargs_copy['valid'] = tuple(self.valid[nb])
                inputs.append((self, func.__name__, kwargs_copy))
                nb += 1
                if silent is False:
                    P.update(nb)
            if self.nthreads > 1:
                result = pyrat.pool.imap(exec_out, inputs)
            else:
                result = map(exec_out, inputs)
            for res in result:
                out.append(res[0])

        if silent is False:
            del P
        return self.input

    def initBP(self, size):
        """
        Calculates all block positions for a given array length, plus their valid parts, and saves them to the
        member variables self.blocks and self.valid.
        """
        if self.blockprocess is False:                                         # no blockprocessing
            self.blocksize = size
            self.blockoverlap = 0
            self.nthreads = 1
            self.blocks = [[0, size]]
            self.valid = [[0,size]]

        while 4 * self.blockoverlap > self.blocksize:                          # ensure efficient blocksize
            self.blocksize *= 2
        if self.blocksize > size:                                              # but maximum equal image size
            self.blocksize = size

        self.blocks = [[0, self.blocksize]]                      # calculate all block boundaries (considering overlap)
        while self.blocks[-1][1] < size:
            self.blocks.append([self.blocks[-1][1] - 2 * self.blockoverlap, self.blocks[-1][1]
                                - 2 * self.blockoverlap + self.blocksize])
        offset = self.blocks[-1][1] - size                                     # last block starts earlier
        self.blocks[-1][0] -= offset                                           # with increased overlap
        self.blocks[-1][1] -= offset

        self.valid = [0] * len(self.blocks)     # calculate the valid part of each block (start, end)
        for k, block in enumerate(self.blocks):
            if k == 0:                          # first block
                self.valid[k] = [0, block[1] - block[0] - self.blockoverlap]
            elif k == len(self.blocks)-1:       # last block
                self.valid[k] = [self.blocks[-2][1]-self.blockoverlap-block[0], block[1] - block[0]]
            else:                               # middle block
                self.valid[k] = [self.blockoverlap, block[1] - block[0] - self.blockoverlap]

    def save_block(self, data, k):
        """
        Save data to block number k of output layer(s).
        """
        if isinstance(self.output, list) or isinstance(self.output, tuple):
            output = self.output
        else:
            output = (self.output,)

        if isinstance(data, tuple) or isinstance(data, list):
            pass
        else:
            data = (data,)

        for n, dat in enumerate(data):
            if self.blockprocess is True:
                if self.vblock:           # vertical blocks
                    pyrat.data.setData(dat[..., self.valid[k][0]:self.valid[k][1]],
                                       block=(0, 0, self.blocks[k][0]+self.valid[k][0], self.blocks[k][0]+self.valid[k][1]),
                                       layer=output[n])
                else:                     # horizontal blocks
                    pyrat.data.setData(dat[..., self.valid[k][0]:self.valid[k][1], :],
                                       block=(self.blocks[k][0]+self.valid[k][0], self.blocks[k][0]+self.valid[k][1], 0, 0),
                                       layer=output[n])
            else:
                pyrat.data.setData(dat, layer=output[n])

    def read_block(self, k):
        """
        Read block number k from input layer(s).
        """
        if isinstance(self.input, tuple) or isinstance(self.input, list):
            input = self.input
        else:
            input = (self.input,)
        out = []
        for layer in input:
            if self.vblock:
                out.append(pyrat.data.getData(block=(0, 0, self.blocks[k][0], self.blocks[k][1]), layer=layer))
            else:
                out.append(pyrat.data.getData(block=(self.blocks[k][0], self.blocks[k][1], 0, 0), layer=layer))
        if len(input) == 1:
            return out[0]
        else:
            return out

    def checkpara(self, kwargs, para):
        wrong_keys = []
        allowed = ['layer', 'nthreads', 'blockprocess', 'blocksize', 'delete']
        for (key, val) in kwargs.items():
            if key not in para and key not in allowed:          # some keywords are always allowed!
                logging.warning("WARNING: Parameter '"+key+"' not valid. Removing it!")
                wrong_keys.append(key)
        for key in wrong_keys:
            del kwargs[key]

    def checkinput(self):
        if isinstance(self.layer, list):
            return True

        query = pyrat.data.queryLayer(self.layer)

        if self.allowed_ndim is not False:
            if query['ndim'] not in self.allowed_ndim:
                logging.error('ERROR: input layer dimensionality mismatch')
                return False

        if self.allowed_dtype is not False:
            if len(set(self.allowed_dtype).intersection(query['dtype'])) == 0:
                logging.error('ERROR: data type mismatch')
                return False

        if self.require_para is not False:
            annotation = pyrat.data.getAnnotation(layer=self.layer)
            if not set(annotation.keys()).issuperset(self.require_para):
                keys = list(set(self.require_para).difference(annotation.keys()))
                logging.error('ERROR: meta data parameter missing '+str(keys))
                return False
        return True


    @classmethod
    def registerGUI(cls, viewer):
        shortcut = cls.gui['shortcut'] if 'shortcut' in cls.gui else ''

        action = QtGui.QAction(cls.gui['entry'], viewer, shortcut=shortcut)                # generate new menu action
        viewer.connect(action, QtCore.SIGNAL('triggered()'), lambda: cls.guirun(viewer))   # and connect to class method guirun

        if cls.gui['menu'] not in viewer.menue:
            logging.warning("\nWARNING: The gui annotation '" +
                            cls.gui['menu'] +
                            "' of the plugin '" +
                            cls.__name__+
                            "' is not present in the PyRat menue!\n")
            return

        before = viewer.exitAct
        if 'before' in cls.gui:                                           # if there is a "before" specified,
            entries = viewer.menue[cls.gui['menu']].actions()             # put new menu entry there
            for entry in entries:
                if str(entry.text()) == cls.gui['before'] or str(entry.whatsThis()) == cls.gui['before']:
                    before = entry
                    break
        viewer.menue[cls.gui['menu']].insertAction(before, action)        # finally insert entry...

    @classmethod
    def guirun(cls, viewer):
        para_backup = copy.deepcopy(cls.para)                # keep a deep copy of the default parameters
        res = 1
        if len(cls.para) > 0:
            wid = pyrat.viewer.Dialogs.FlexInputDialog(cls.para, parent=viewer, doc=cls.__doc__)
            res = wid.exec_()
        if res == 1:
            plugin = cls()                                   # instance with new parameters
            setattr(cls, 'para', para_backup)                # copy back the defaults
            viewer.statusBar.setMessage(message=' '+plugin.name+' ', colour = 'R')
            if pyrat._debug is False:
                try:
                    layers = plugin.run()
                    del plugin
                    viewer.updateViewer(layer=layers)
                except Exception as ex:
                    import traceback, os.path, sys, textwrap
                    tb = sys.exc_info()[2]
                    tbinfo = traceback.extract_tb(tb)[-1]
                    print()
                    if hasattr(pyrat, "app"):
                        pyrat.app.statusBar.progressbar.setValue(0)
                        message = """
                        Ooops, this was not planned!
                        You either found a bug, or are using a module in the wrong way!
                        PyRAT will try to continue ignoring this error...

                        Error : %s
                        Module: %s
                        Line  : %s
                        """ % (str(type(ex).__name__), os.path.basename(tbinfo[0]), str(tbinfo[1]))
                        foo = QtGui.QMessageBox(parent=pyrat.app)
                        foo.setIcon(1)
                        foo.setText(textwrap.dedent(message))
                        foo.exec_()
                    logging.error(str(type(ex).__name__)+" in "+os.path.basename(tbinfo[0])+" (line "+str(tbinfo[1])+")")
            else:
                layers = plugin.run()
                del plugin
                viewer.updateViewer(layer=layers)
            viewer.statusBar.setMessage(message=' Ready ', colour='G')
