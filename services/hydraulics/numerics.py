"""Observe the pinned WNTR Newton method without changing its equations or steps."""
from contextlib import contextmanager
import time
import numpy as np
from wntr.sim.solvers import NewtonSolver


@contextmanager
def solver_statistics():
    calls=[]
    original=NewtonSolver.solve
    def measured(solver,model,*args,**kwargs):
        started=time.perf_counter()
        result=original(solver,model,*args,**kwargs)
        residual=model.evaluate_residuals()
        calls.append(dict(status=int(result[0]),iterations=result[2],elapsed_s=time.perf_counter()-started,
            final_residual_inf=float(np.max(np.abs(residual))) if len(residual) else 0.0))
        return result
    # A worker owns exactly one simulation; no shared service-process monkeypatch.
    NewtonSolver.solve=measured
    try:yield calls
    finally:NewtonSolver.solve=original
