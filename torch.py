import torch
import torch.nn as nn
import torch.nn.functional as F

#def bp(*args):
#    print("AssertionError:",*args)
#    breakpoint()
### VERY USEFUL: assert False, bp("message")


class Trial:
    def __init__(self, net, shape, silent=False, print_params=False):
        self.BATCH = 23 # ideally a weird number we won't come across
        shape = list(shape) # for mutability
        if -1 in shape:
            shape[shape.index(-1)] = self.BATCH
        self.t = torch.zeros(shape)

        self.net = net
        self.shapes = {}
        self.silent = silent
        self.print_params = print_params
        self.original_shape = self.t.shape
        # the shape of the first instance of Trial called on a net determines its input shape
        if not hasattr(net,'_inputshape'):
            net._inputshape = self.original_shape
            assert not hasattr(net,'_shapes')
            net._shapes = {}

    def build_deconv(self, encoder, encoder_input_shape=None):
        assert encoder_input_shape or (encoder in self.net._shapes.keys()), "Must run trial.apply(encoder) before building its deconv network so that the input shape is known, or specify shape with build_deconv(encoder_input_shape=...)"
        if not encoder_input_shape:
            encoder_input_shape = self.net._shapes[encoder][0]

        assert isinstance(encoder,nn.Sequential), "todo implement nn.Conv*d support outside of Sequentials"

        res = []
        trial = Trial(self, encoder_input_shape, silent=True)
        forward_shapes = [trial.shape()]
        for layer in encoder:
            trial.apply(layer)
            forward_shapes.append(trial.shape())
        backward_shapes = forward_shapes[::-1]
        # now backward_shapes is [final_encoder_output,...,final_encoder_input] == [final_decoder_input,...,final_decoder_output]
        backward_shapes = backward_shapes[1:]
        # now backward_shapes is [first_decoder_output,...,final_decoder_output]

        for i,layer in enumerate(encoder[::-1]):
            if not isinstance(layer,nn.Conv1d):
                if isinstance(layer,nn.Conv2d):
                    raise NotImplementedError
                inverse_layer = layer
            else: # nn.Conv1d case


                # initial guess is to just swap in and out channels
                kwargs = {
                    'in_channels':layer.out_channels,
                    'out_channels':layer.in_channels,
                    'kernel_size':layer.kernel_size,
                    'stride':layer.stride,
                    'padding':layer.padding,
                    'dilation':layer.dilation,
                    'groups':layer.groups,
                    'output_padding':0
                }
                ## careful idk how to feed in bias and padding mode
                #bias=layer.bias
                #padding_mode=layer.padding_mode

                # build initial guess
                inverse_layer = nn.ConvTranspose1d(**kwargs)
#                inverse_layer = nn.ConvTranspose1d(
#                    in_channels=in_channels,
#                    out_channels=out_channels,
#                    kernel_size=kernel_size,
#                    stride=stride,
#                    padding=padding,
#                    dilation=dilation,
#                    groups=groups,
#                    output_padding=0,
#                    #bias=bias,
#                    #padding_mode=padding_mode
#                    )

                test = trial.clone()
                test.apply(inverse_layer)
                out_shape = test.shape()
                if out_shape != backward_shapes[i]: # if our first guess failed
                    diff = backward_shapes[i][-1] - out_shape[-1]
                    assert diff != 0
                    assert out_shape[:-1] == backward_shapes[i][:-1]
                    if diff > 0: # we are `diff` short of ideal
                        kwargs['output_padding'] = diff
                    else: # we are `-diff` over ideal (note diff==0 case never happens)
                        # we are trying for: diff = output_padding - 2*padding
                        # every bit of `padding` we add decreases us by 2, so lets do that and overshoot then make up the remaining bit with output padding
                        ## important question: if we add padding to the deconv like this, should we also be adding that padding to the original conv?
                        padding_to_add = -diff//2 + 1 # this may always be 1, not sure
                        output_padding = diff + 2*padding_to_add
                        assert padding_to_add >= 0
                        assert output_padding_to_add >= 0
                        assert len(kwargs['padding']) == 1, breakpoint() # not implemented yet
                        kwargs['padding'] = tuple([kwargs['padding'][0] + padding_to_add])
                        kwargs['output_padding'] = output_padding
                    inverse_layer = nn.ConvTranspose1d(**kwargs)

                    test = trial.clone()
                    test.apply(inverse_layer)
                    assert test.shape() == backward_shapes[i], breakpoint()

            trial.apply(inverse_layer)
            res.append(inverse_layer)
            assert trial.shape() == backward_shapes[i]
        return nn.Sequential(*res)

    def reset(self):
        self.t = torch.zeros(self.original_shape)

    #tr_idx is None if model.forward() returns a tensor, 0 if it returns a tuple with the first element being the desired tensor, etc.
    def forward(self, reset=True, assert_same=False, tr_idx=None):
        self.print("Running full forward pass...")

        # reset to original shape
        shape_before_reset = self.t.shape
        if reset:
            self.reset()
        old_shape = self.t.shape

        with torch.no_grad():
            res = self.net.forward(self.t)
            if tr_idx is None:
                self.t = res
            else:
                self.t = res[tr_idx]
        self.log("forward()", old_shape)
        if assert_same:
            assert shape_before_reset == self.t.shape

    def new_shape(self, shape, reason=None, batch_included=False):
        if isinstance(shape,int):
            shape = tuple([shape])
        if batch_included:
            assert -1 in shape
            shape = list(shape)
            shape[shape.index(-1)] = self.BATCH
            shape = tuple(shape)
        else:
            shape = (self.BATCH,*shape)
        old_shape = self.t.shape
        self.t = torch.zeros(shape)
        if reason is None:
            reason = 'manual trial.new_shape'
        self.log(reason,old_shape)

    def get_inshape(self, layer):
        return self.net._shapes[layer][0]
    def get_outshape(self, layer):
        return self.net._shapes[layer][1]


    def print(self,msg):
        if not self.silent:
            print(msg)

    def log(self, reason, oldshape, newshape=None, depth=0):
        if not newshape:
            newshape = self.t.shape
        batchidx_old = tuple(newshape).index(self.BATCH)
        batchidx_new = tuple(oldshape).index(self.BATCH)
        #assert oldshape[0] == newshape[0] and newshape[0] == self.BATCH
        oldshape = tuple([*oldshape[:batchidx_old],-1,*oldshape[batchidx_old+1:]])
        newshape = tuple([*newshape[:batchidx_new],-1,*newshape[batchidx_new+1:]])
        if oldshape == newshape:
            body = "(no effect)"
        else:
            body = "{} -> {}".format(oldshape,newshape)

        self.print("{}{}: {}".format('\t'*depth,reason,body))



# convenience function
    def view(self, shape):
        old_shape = self.t.shape
        self.t = self.t.view(shape)
        self.log("view",old_shape)

    def clone(self):
        return Trial(self.net, self.t.shape, silent=self.silent)

    # usage:
    # shape = trial.shape()
    # last_dim = trial.shape(-1)
    # batches = trial.shape(0)
    # returns shape with batch size self.BATCH
    def shape(self,dim=None, warn=True):
        if dim == None:
            return self.t.shape
        if dim == 0 and warn:
            print("[warn] Accessing batch count, this is an arbitrary number. Disable warning with .shape(0,warn=False)")
        return self.t.shape[dim]

    # usage:
    # self.flatten, self.unflatten = trial.flat_shape()
    # trial.view(self.flatten)
    # converts NABCDE to NF where F=A*B*C*D*E
    def flat_shapes(self):
        flat = 1
        for dimsize in self.t.shape[1:]:
            flat = flat*dimsize
        return (-1,flat), (-1,*self.t.shape[1:])

    # note if you wanted to do a method like x.view that didn't have a trial.____ equivalent you could do either:
    #   trial.t = trial.t.view(...)
    # or
    #   trial.apply(lambda t: t.view(...))
    # or
    #   trial.apply(torch.Tensor.view,...)
    def apply(self, callable_obj, *args, name=None, depth=0, tr_idx=None, **kwargs): # note this works with functions not just layers
        if isinstance(callable_obj,list):
            for item in callable_obj:
                self.apply(item,depth=depth,name=name,tr_idx=tr_idx)
        assert callable(callable_obj)

        saved = self.clone() # for use in nn.Sequential case
        old_shape = self.t.shape

        # apply the function
        with torch.no_grad():
            res =  callable_obj(self.t,*args,**kwargs)
            if tr_idx is not None:
                res = res[tr_idx] # do a deref to get the tensor
            assert isinstance(res,torch.Tensor), "you should be using apply_noassign. The function ({}) you used in apply() returns a nontensor".format(callable_obj)
            self.t = res

        if isinstance(callable_obj,nn.ReLU):
            return self # abort early. you dont want this printed at all, really.

        if name is not None:
            self.net._shapes[name] = self.t.shape
        self.net._shapes[callable_obj] = (old_shape, self.t.shape)

        # make printed name pretty
        if name is None:
            if '__name__' in dir(callable_obj):
                name = callable_obj.__name__
            elif isinstance(callable_obj,nn.Module):
                name = str(callable_obj)
                name = name[:name.find('(')]
            else:
                name = str(callable_obj)

        self.log(name, old_shape, depth=depth)

        # print out params if desired
        if self.print_params and isinstance(callable_obj,nn.Module):
            print("params: {}".format(sum([x.view(-1,1).shape[0] for x in callable_obj.parameters()])))


        # deal with sequentials
        if isinstance(callable_obj,nn.Sequential):
            for layer in callable_obj:
                saved.apply(layer, depth=depth+1)

        return self

    def apply_noassign(self, callable_obj):
        return callable_obj(self.t)





