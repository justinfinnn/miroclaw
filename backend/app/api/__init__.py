"""
API route module
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
simulation_bp = Blueprint('simulation', __name__)
report_bp = Blueprint('report', __name__)
openai_compat_bp = Blueprint('openai_compat', __name__)

from . import graph  # noqa: E402, F401
from . import simulation  # noqa: E402, F401
from . import report  # noqa: E402, F401
from . import openai_compat  # noqa: E402, F401
from .auth import auth_bp  # noqa: E402, F401
