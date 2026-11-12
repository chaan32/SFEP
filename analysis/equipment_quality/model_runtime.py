from threadpoolctl import threadpool_limits


def single_threaded() -> threadpool_limits:
    return threadpool_limits(limits=1)
