#cython: cdivision=True
#cython: boundscheck=False
#cython: nonecheck=False
#cython: wraparound=False

from libc.math cimport M_PI


def unwrap_1d(double[::1] image, double[::1] unwrapped_image):
    """ Phase unwrapping using the naive approach. """
    cdef:
        Py_ssize_t i
        double difference
        long periods = 0
    # FU-07 (c5ca90, CWE-787): the memoryviews are typed with
    # boundscheck/wraparound disabled, but the accesses below are
    # unconditional. An empty image (reachable from a zero-gate radar via
    # dealias_unwrap_phase) would read before the allocation, and an
    # unwrapped buffer shorter than the image would be overflowed. Enforce
    # the contract explicitly at the boundary.
    if image.shape[0] == 0:
        raise ValueError("unwrap_1d requires a non-empty image")
    if unwrapped_image.shape[0] < image.shape[0]:
        raise ValueError(
            "unwrap_1d requires unwrapped_image at least as long as image "
            "(%d < %d)" % (unwrapped_image.shape[0], image.shape[0])
        )
    unwrapped_image[0] = image[0]
    for i in range(1, image.shape[0]):
        difference = image[i] - image[i - 1]
        if difference > M_PI:
            periods -= 1
        elif difference < -M_PI:
            periods += 1
        unwrapped_image[i] = image[i] + 2 * M_PI * periods
