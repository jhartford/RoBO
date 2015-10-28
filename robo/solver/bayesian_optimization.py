
import time
import logging
import numpy as np

from robo.models.gpy_model import GPyModel
from robo.recommendation.optimize_posterior import optimize_posterior_mean_and_std
from robo.solver.base_solver import BaseSolver

logger = logging.getLogger(__name__)


class BayesianOptimization(BaseSolver):
    """
    Class implementing general Bayesian optimization.
    """
    def __init__(self, acquisition_func, model,
                 maximize_func, task, save_dir=None,
                 initialization=None, recommendation_strategy=None, num_save=1, train_intervall=1, n_restarts=1):
        """
        Initializes the Bayesian optimization.
        Either acquisition function, model, maximization function, bounds, dimensions and objective function are
        specified or an existing run can be continued by specifying only save_dir.

        :param acquisition_funct: Any acquisition function
        :param model: A model
        :param maximize_func: The function for maximizing the acquisition function
        :param initialization: The initialization strategy that to find some starting points in order to train the model
        :param task: The task (derived from BaseTask) that should be optimized
        :param recommendation_strategy: A function that recommends which configuration should be return at the end
        :param save_dir: The directory to save the iterations to (or to load an existing run from)
        :param num_save: A number specifying the n-th iteration to be saved
        """

        logging.basicConfig(level=logging.INFO)

        super(BayesianOptimization, self).__init__(acquisition_func, model, maximize_func, task, save_dir)

        self.initialization = initialization

        self.X = None
        self.Y = None
        self.time_func_eval = None
        self.time_optimization_overhead = None
        self.train_intervall = train_intervall

        self.num_save = num_save

        self.model_untrained = True
        self.recommendation_strategy = recommendation_strategy
        self.incumbent = None
        self.n_restarts = n_restarts

    def initialize(self, n_init_points=3):
        """
        Draws random points to initialize the model
        """
        self.time_func_eval = np.zeros([n_init_points])
        self.time_optimization_overhead = np.zeros([n_init_points])
        self.X = np.zeros([1, self.task.n_dims])
        self.Y = np.zeros([1, 1])

        for i in range(n_init_points):
            start_time = time.time()
            x = np.array([np.random.uniform(self.task.X_lower, self.task.X_upper, self.task.n_dims)])
            self.time_optimization_overhead[i] = time.time() - start_time

            start_time = time.time()
            y = self.task.evaluate(x)
            self.time_func_eval[i] = time.time() - start_time

            if i == 0:
                self.X[i] = x[0, :]
                self.Y[i] = y[0, :]
            else:
                self.X = np.append(self.X, x, axis=0)
                self.Y = np.append(self.Y, y, axis=0)

            logger.info("Configuration achieved a performance of %f " % (self.Y[i]))
            logger.info("Evaluation of this configuration took %f seconds" % (self.time_func_eval[i]))

            # Use best point seen so far as incumbent
            best_idx = np.argmin(self.Y)
            self.incumbent = self.X[best_idx]
            self.incumbent_value = self.Y[best_idx]

            if self.save_dir is not None and (0) % self.num_save == 0:
                self.save_iteration(i, hyperparameters=None, acquisition_value=0)

    def run(self, num_iterations=10, X=None, Y=None):
        """
        The main Bayesian optimization loop

        :param num_iterations: number of iterations to perform
        :param X: (optional) Initial observations. If a run continues these observations will be overwritten by the load
        :param Y: (optional) Initial observations. If a run continues these observations will be overwritten by the load
        :param overwrite: data present in save_dir will be deleted and overwritten, otherwise the run will be continued.
        :return: the incumbent
        """
        # Save the time where we start the Bayesian optimization procedure
        self.time_start = time.time()

        if X is None and Y is None:
            self.initialize()
        else:
            self.X = X
            self.Y = Y
            self.time_func_eval = np.zeros([self.X.shape[0]])
            self.time_optimization_overhead = np.zeros([self.X.shape[0]])

        n_init_points = 3
        for it in range(n_init_points, num_iterations):
            logger.info("Start iteration %d ... ", it)

            start_time = time.time()
            # Choose next point to evaluate
            if it % self.train_intervall == 0:
                do_optimize = True
            else:
                do_optimize = False
            new_x = self.choose_next(self.X, self.Y, do_optimize)

            start_time_inc = time.time()
            if self.recommendation_strategy is None:
                logger.info("Use best point seen so far as incumbent.")
                best_idx = np.argmin(self.Y)
                self.incumbent = self.X[best_idx]
                self.incumbent_value = self.Y[best_idx]
            
            elif self.recommendation_strategy is optimize_posterior_mean_and_std:
                logger.info("Optimize the posterior mean and std to find a new incumbent")
                # Start one local search from the best observed point and N - 1 from random points
                startpoints = [np.random.uniform(self.task.X_lower, self.task.X_upper, self.task.n_dims) for i in range(self.n_restarts)]
                best_idx = np.argmin(self.Y)
                startpoints.append(self.X[best_idx])

                self.incumbent, self.incumbent_value = self.recommendation_strategy(self.model, self.task.X_lower, self.task.X_upper, startpoint=startpoints, with_gradients=True)
            else:
                startpoints = [np.random.uniform(self.task.X_lower, self.task.X_upper, self.task.n_dims) for i in range(self.n_restarts)]
                x_opt = np.zeros([len(startpoints), self.task.n_dims])
                fval = np.zeros([len(startpoints)])
                for i, startpoint in enumerate(startpoints):
                    x_opt[i], fval[i] = self.recommendation_strategy(self.model, self.task.X_lower, self.task.X_upper, startpoint=startpoint)
                 
                best = np.argmin(fval)
                self.incumbent = x_opt[best]
                self.incumbent_value = fval[best]
            logger.info("New incumbent %s found in %f seconds with estimated performance %f", str(self.incumbent), time.time() - start_time_inc, self.incumbent_value)

            time_optimization_overhead = time.time() - start_time
            self.time_optimization_overhead = np.append(self.time_optimization_overhead, np.array([time_optimization_overhead]))

            logger.info("Optimization overhead was %f seconds" % (self.time_optimization_overhead[-1]))

            logger.info("Evaluate candidate %s" % (str(new_x)))
            start_time = time.time()
            new_y = self.task.evaluate(new_x)
            time_func_eval = time.time() - start_time
            self.time_func_eval = np.append(self.time_func_eval, np.array([time_func_eval]))

            logger.info("Configuration achieved a performance of %f " % (new_y[0, 0]))

            logger.info("Evaluation of this configuration took %f seconds" % (self.time_func_eval[-1]))

            # Update the data
            self.X = np.append(self.X, new_x, axis=0)
            self.Y = np.append(self.Y, new_y, axis=0)

            if self.save_dir is not None and (it) % self.num_save == 0:
                hypers = self.model.hypers
                self.save_iteration(it, hyperparameters=hypers, acquisition_value=self.acquisition_func(new_x))

        logger.info("Return %s as incumbent with predicted performance %f" % (str(self.incumbent), self.incumbent_value))

        return self.incumbent, self.incumbent_value

    def choose_next(self, X=None, Y=None, do_optimize=True):
        """
        Chooses the next configuration by optimizing the acquisition function.

        :param X: The point that have been where the objective function has been evaluated
        :param Y: The function values of the evaluated points
        :return: The next promising configuration
        """
        if X is not None and Y is not None:
            try:
                logger.info("Train model...")
                t = time.time()
                self.model.train(X, Y, do_optimize)
                logger.info("Time to train the model: %f", (time.time() - t))
            except Exception, e:
                logger.info("Model could not be trained", X, Y)
                raise
            self.model_untrained = False
            self.acquisition_func.update(self.model)

            logger.info("Maximize acquisition function...")
            t = time.time()
            x = self.maximize_func.maximize()
            logger.info("Time to maximize the acquisition function: %f", (time.time() - t))
        else:
            self.initialize()
            x = self.X
        return x