from .base import Strategy, Transform
from summit.domain import Domain, ContinuousVariable
from summit.utils.dataset import DataSet
from summit import get_summit_config_path
import chemopt
from chemopt.logger import get_handlers

import numpy as np
import pandas as pd
import tensorflow as tf

import logging
import json
import os.path as osp
import os
import pathlib
import uuid
from collections import namedtuple
from copy import deepcopy

class DRO(Strategy):
    ''' Deep Reaction Optimizer from the paper:
    "Optimizing Chemical Reactions with Deep Reinforcement Learning"
    published by Zhenpeng Zhou, Xiaocheng Li, Richard N. Zare.

    Copyright (c) 2017 Zhenpeng Zhou

    Code is adapted from: https://github.com/lightingghost/chemopt

    Please cite their work, if you use this strategy.

    Parameters
    ----------
    domain: `summit.domain.Domain`
        A summit domain object
    transform: `summit.strategies.base.Transform`, optional
        A transform class (i.e, not the object itself). By default
        no transformation will be done the input variables or
        objectives.
    save_dir: string, optional
        Name of subfolder where temporary files during Gryffin execution are stored, i.e., summit/strategies/tmp_files/dro/<save_dir>.
        By default: None (i.e. no subfolder created, files stored in summit/strategies/tmp_files/dro)
    pretrained_model_config_path: string, optional
        Path to the config file of a pretrained DRO model (note that the number of inputs parameters should match the domain inputs)
        By default: a pretrained model (from chemopt/chemopt/config_<#inputs>_inputs_standard.json) will be used
    model_size: string, optional
        Whether the model has the same size as originally published by the developers of DRO ("standard"),
        or whether the model is bigger w.r.t. number of pretraining epochs, LSTM hidden size, unroll_length ("bigger").
        Note that the pretraining can increase exponentially when changing these hyperparameters and the number of input variables,
        the number of epochs the each bigger model was trained can be found in the "checkpoint" file in the respective save directory
        (chemopt/chemopt/save/<num_inputs>_inputs_<standard/bigger>/checkpoint).
        By default: "standard" (these models were all pretrained for 50.000 epochs)

    Notes
    -------
        For applying the DRO it is necessary to define reasonable bounds of the objective variable, e.g., yield in [0, 1],
        since the DRO normalizes the objective function values to be between 0 and 1.


    Examples
    -------
    >>> from summit.domain import Domain, ContinuousVariable
    >>> from summit.strategies import DRO
    >>> domain = Domain()
    >>> domain += ContinuousVariable(name='temperature', description='reaction temperature in celsius', bounds=[10, 100])
    >>> domain += ContinuousVariable(name='flowrate_a', description='flow of reactant a in mL/min', bounds=[0, 1])
    >>> domain += ContinuousVariable(name='yield', description='relative conversion to xyz', bounds=[0,100], is_objective=True, maximize=True)
    >>> strategy = DRO(domain)
    >>> next_experiments = strategy.suggest_experiments()

    '''

    def __init__(self, domain: Domain, transform: Transform=None, save_dir=None, pretrained_model_config_path=None, model_size="standard", **kwargs):
        Strategy.__init__(self, domain, transform)

        # Create directories to store temporary files
        summit_config_path = get_summit_config_path()
        self.uuid_val = uuid.uuid4()  # Unique identifier for this run
        tmp_dir = summit_config_path / "dro" / str(self.uuid_val)
        if not os.path.isdir(tmp_dir):
            os.makedirs(tmp_dir)

        self._pretrained_model_config_path = pretrained_model_config_path
        self._infer_model_path = tmp_dir
        self._model_size = model_size
        self.prev_param = None

    def suggest_experiments(self, prev_res: DataSet=None, **kwargs):
        """ Suggest experiments using the Deep Reaction Optimizer

        Parameters
        ----------
        num_experiments: int, optional
            The number of experiments (i.e., samples) to generate. Default is 1.
        prev_res: summit.utils.data.DataSet, optional
            Dataset with data from previous experiments.
            If no data is passed, the DRO optimization algorithm
            will be initialized and suggest initial experiments.

        Returns
        -------
        next_experiments: DataSet
            A `Dataset` object with the suggested experiments by DRO algorithm

        Notes
        -------
        xbest, internal state
            Best point from all iterations.
        fbest, internal state
            Objective value at best point from all iterations.
        param, internal state
            A dict containing: state of LSTM of DRO, last requested point, xbest, fbest,
            number of iterations (corresponding to the unroll length of the LSTM)
        """

        # Extract dimension of input domain
        self.dim = self.domain.num_continuous_dimensions()

        # Get bounds of input variables
        bounds = []
        obj_maximize = False
        obj_bounds = None
        for v in self.domain.variables:
            if not isinstance(v, ContinuousVariable):
                raise TypeError("DRO can only handle continuous variables.")
            if not v.is_objective:
                bounds.append(v.bounds)
            if v.is_objective:
                if obj_bounds is not None:
                    raise ValueError("DRO can not handle multiple objectives. Please use transform.")
                obj_bounds = v.bounds
                if v.maximize:
                    obj_maximize = True
        self.bounds = np.asarray(bounds, dtype=float)

        # Initialization
        self.x0 = None
        self.y0 = None
        # Get previous results
        if prev_res is not None:
            inputs, outputs = self.transform.transform_inputs_outputs(prev_res)
            # Set up maximization and minimization and normalize inputs (x) and outputs (y)
            for v in self.domain.variables:
                if v.is_objective:
                    a, b = np.asarray(v.bounds, dtype=float)
                    y = outputs[v.name]
                    y = (y - a) / (b - a)
                    if v.maximize:
                        y = 1 - y
                    outputs[v.name] = y
                else:
                    a, b = np.asarray(v.bounds, dtype=float)
                    x = inputs[v.name]
                    x = (x - a) / (b - a)
                    inputs[v.name] = x
            self.x0 = inputs.data_to_numpy()
            self.y0 = outputs.data_to_numpy()
        # If no prev_res are given but prev_param -> raise error
        elif self.prev_param is not None:
            raise ValueError('Parameter from previous optimization iteration are given but previous results are '
                             'missing!')

        # TODO: how does DRO handle constraints?
        if self.domain.constraints != []:
            raise NotImplementedError("DRO can not handle constraints yet.")

        next_experiments, param = self.main(num_input=self.dim, prev_res=[self.x0, self.y0], prev_param=self.prev_param)

        objective_dir = -1.0 if obj_maximize else 1.0
        self.fbest = objective_dir * param["fbest"] * (obj_bounds[1] - obj_bounds[0]) + obj_bounds[0]
        self.prev_param = param

        # Do any necessary transformations back
        next_experiments = self.transform.un_transform(next_experiments)

        return next_experiments

    def reset(self):
        """Reset internal parameters"""
        self.prev_param = None

    def to_dict(self):
        """Convert hyperparameters and internal state to a dictionary"""
        if self.prev_param is not None:
            params = deepcopy(self.prev_param)
            tup_to_json = [list(e) for e in [list(t) for t in list([params["state"]])][0]]
            params["state"] = [[tup_to_json[0][0].tolist(), tup_to_json[0][1].tolist()], [tup_to_json[1][0].tolist(), tup_to_json[1][1].tolist()]]
            params["xbest"] = params["xbest"].tolist()
            params["fbest"] =  params["fbest"].tolist()
            params["last_requested_point"] = params["last_requested_point"].tolist()
        else:
            params = None
        strategy_params = dict(
            prev_param=params
        )
        return super().to_dict(**strategy_params)

    @classmethod
    def from_dict(cls, d):
        dro = super().from_dict(d)
        params = d["strategy_params"]["prev_param"]
        if params is not None:
            params["state"] = tuple([tuple([np.array(s, dtype=np.float32) for s in e]) for e in params["state"]])
            params["xbest"] = np.array(params["xbest"])
            params["fbest"] =  np.array(params["fbest"])
            params["last_requested_point"] = np.array(params["last_requested_point"])
        dro.prev_param = params
        return dro

    def x_convert(self, x):
        real_x = np.zeros([self.dim])
        for i in range(self.dim):
            a, b = self.bounds[i]
            real_x[i] = x[0, i] * (b - a) + a
        return real_x

    def main(self, num_input=3, prev_res=None, prev_param=None):
        x0, y0 = prev_res[0], prev_res[1]
        module_path = os.path.dirname(chemopt.__file__)
        if self._pretrained_model_config_path:
            path = self._pretrained_model_config_path
        else:
            path = osp.join(module_path, 'config_' + str(num_input) + '_inputs_' + str(self._model_size) + '.json')
        config_file = open(path)
        config = json.load(config_file,
                           object_hook=lambda d: namedtuple('x', d.keys())(*d.values()))
        saved_model_path = osp.join(os.path.dirname(os.path.realpath(path)), str(config.save_path))
        if prev_param:
            if prev_param["iteration"] > config.unroll_length:
                raise ValueError("Number of iterations exceeds unroll length of the pretrained model!")

        logging.basicConfig(level=logging.WARNING, handlers=get_handlers())
        logger = logging.getLogger()

        cell = chemopt.rnn.StochasticRNNCell(cell=chemopt.rnn.LSTM,
                                             kwargs={'hidden_size': config.hidden_size},
                                             nlayers=config.num_layers,
                                             reuse=config.reuse)
        optimizer = self.StepOptimizer(cell=cell, ndim=config.num_params,
                                  nsteps=config.num_steps,
                                  ckpt_path=saved_model_path, infer_model_path=self._infer_model_path, logger=logger,
                                  constraints=True, x=x0, y=y0)
        x, state = optimizer.run(prev_res=y0, prev_param=prev_param)

        real_x = self.x_convert(x)
        next_experiments = {}
        i_inp = 0
        for v in self.domain.variables:
            if not v.is_objective:
                next_experiments[v.name] = [real_x[i_inp]]
                i_inp += 1
        next_experiments = DataSet.from_df(pd.DataFrame(data=next_experiments))
        next_experiments[('strategy', 'METADATA')] = ['DRO']

        param = {}
        if not y0:
            y0 = np.array([[float("inf")]])
            param["iteration"] = 0
        else:
            param["iteration"] = prev_param["iteration"] + 1
        if not prev_param:
            self.fbest = y0[0]
            self.xbest = real_x
        elif y0 < prev_param["fbest"]:
            self.fbest = y0[0]
            self.xbest = real_x
        else:
            self.fbest = prev_param["fbest"]
            self.xbest = prev_param["xbest"]

        param.update({"state": state, "last_requested_point": x, "xbest": self.xbest, "fbest": self.fbest})

        tf.reset_default_graph()

        return next_experiments, param

    class StepOptimizer:
        def __init__(self, cell, ndim, nsteps, ckpt_path, infer_model_path, logger, constraints, x, y):
            self.logger = logger
            self.cell = cell
            self.ndim = ndim
            self.nsteps = nsteps
            self.ckpt_path = ckpt_path
            self._infer_model_path = infer_model_path
            self.constraints = constraints
            self.init_state = self.cell.get_initial_state(1, tf.float32)
            self.results = self.build_graph()
            self.x, self.y = x, y

            self.saver = tf.train.Saver(tf.global_variables())

        def get_state_shapes(self):
            return [(s[0].get_shape().as_list(), s[1].get_shape().as_list())
                    for s in self.init_state]

        def step(self, sess, x, y, state):
            feed_dict = {'input_x:0':x, 'input_y:0':y}
            for i in range(len(self.init_state)):
                feed_dict['state_l{0}_c:0'.format(i)] = state[i][0]
                feed_dict['state_l{0}_h:0'.format(i)] = state[i][1]
            new_x, new_state = sess.run(self.results, feed_dict=feed_dict)
            return new_x, new_state

        def build_graph(self):
            x = tf.placeholder(tf.float32, shape=[1, self.ndim], name='input_x')
            y = tf.placeholder(tf.float32, shape=[1, 1], name='input_y')
            state = []
            for i in range(len(self.init_state)):
                state.append((tf.placeholder(
                                  tf.float32, shape=self.init_state[i][0].get_shape(),
                                  name='state_l{0}_c'.format(i)),
                              tf.placeholder(
                                  tf.float32, shape=self.init_state[i][1].get_shape(),
                                  name='state_l{0}_h'.format(i))))

            with tf.name_scope('opt_cell'):
                new_x, new_state = self.cell(x, y, state)
                if self.constraints:
                    new_x = tf.clip_by_value(new_x, 0.001, 0.999)
            return new_x, new_state

        def load(self, sess, model_path):
            try:
                self.saver.restore(sess, model_path)
            except:
                raise FileNotFoundError('No checkpoint available')

        def get_init(self):
            x = np.random.normal(loc=0.5, scale=0.2, size=(1, self.ndim))
            x = np.maximum(np.minimum(x, 0.9), 0.1)
            init_state = [(np.zeros(s[0]), np.zeros(s[1]))
                          for s in self.get_state_shapes()]
            return x, init_state

        def run(self, prev_res=None, prev_param=None):
            with tf.Session() as sess:
                if prev_res is None:
                    x, state = self.get_init()
                    ckpt = tf.train.get_checkpoint_state(self.ckpt_path)
                    model_id = ckpt.model_checkpoint_path.split("/")[-1]
                    init_model = os.path.join(os.path.dirname(self.ckpt_path), model_id)
                    self.load(sess, init_model)
                    self._infer_model_path = os.path.join(self._infer_model_path, model_id)
                    self.saver.save(sess, self._infer_model_path)
                else:
                    ckpt = tf.train.get_checkpoint_state(self.ckpt_path)
                    model_id = ckpt.model_checkpoint_path.split("/")[-1]
                    self._infer_model_path = os.path.join(self._infer_model_path, model_id)
                    self.load(sess, self._infer_model_path)
                    state = prev_param["state"]
                    if not np.allclose(self.x, prev_param["last_requested_point"]):
                        raise ValueError("Values for input variables do not match requested points: {} != {}".format(str(self.x), str(prev_param["last_requested_point"])))
                    x, state = self.step(sess, self.x, self.y, state)
                    self.saver.save(sess, self._infer_model_path)
            return x, state

