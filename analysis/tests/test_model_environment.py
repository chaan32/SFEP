from importlib.metadata import version
import sys


def test_model_environment_uses_python312() -> None:
    assert sys.version_info[:2] == (3, 12)


def test_model_environment_has_pinned_direct_dependencies() -> None:
    expected = {
        "numpy": "2.2.6",
        "pandas": "2.3.0",
        "jsonschema": "4.24.0",
        "scikit-learn": "1.9.1",
        "onnx": "1.23.2",
        "skl2onnx": "1.20.0",
        "onnxruntime": "1.31.1",
        "threadpoolctl": "3.8.0",
        "pytest": "8.4.1",
    }
    assert {package: version(package) for package in expected} == expected


def test_single_threaded_limits_loaded_native_pools() -> None:
    import sklearn  # Loads scikit-learn's native OpenMP library before limiting it.
    from threadpoolctl import threadpool_info, threadpool_limits

    from equipment_quality.model_runtime import single_threaded

    with threadpool_limits(limits=2), single_threaded():
        pools = threadpool_info()
        assert pools
        assert all(pool["num_threads"] == 1 for pool in pools), pools
