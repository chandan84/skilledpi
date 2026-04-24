"""Optional Cython compilation of performance-critical agent modules.

Run:
  python setup_cython.py build_ext --inplace

This compiles the servicer and pi_agent hot paths to C extensions.
Pure-Python fallback is always available — Cython is entirely optional.
"""

from setuptools import Extension, setup

try:
    from Cython.Build import cythonize

    ext_modules = cythonize(
        [
            Extension(
                "chibu.grpc_server._servicer_cy",
                ["chibu/grpc_server/servicer.py"],
                extra_compile_args=["-O3", "-ffast-math"],
            ),
            Extension(
                "chibu.agent._pi_agent_cy",
                ["chibu/agent/pi_agent.py"],
                extra_compile_args=["-O3"],
            ),
        ],
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "nonecheck": False,
            "cdivision": True,
        },
        annotate=False,
    )
    print("Cython available — compiling hot-path extensions")
except ImportError:
    ext_modules = []
    print("Cython not available — skipping C extension build")

setup(
    name="chibu-cython-exts",
    ext_modules=ext_modules,
)
