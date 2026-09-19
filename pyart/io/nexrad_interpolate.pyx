"""
Interpolation of NEXRAD moments from 1000 meter to 250 meter gate spacing.

"""

def _fast_interpolate_scan_4(
        float[:, :] data, float[:] scratch_ray, float fill_value,
        int start, int end, int moment_ngates, int linear_interp):
    """ Interpolate a single NEXRAD moment scan from 1000 m to 250 m. """
    # This interpolation scheme is only valid for NEXRAD data where a 4:1
    # (1000 m : 250 m) interpolation is needed.
    #
    # The scheme here performs a linear interpolation between pairs of gates
    # in a ray when the both of the gates are not masked (below threshold).
    # When one of the gates is masked the interpolation changes to a nearest
    # neighbor interpolation. Nearest neighbor is also performed at the end
    # points until the new range bin would be centered beyond half of the range
    # spacing of the original range.
    #
    # Nearest neighbor interpolation is performed when linear_interp is False,
    # this is equivalent to repeating each gate four times in each ray.
    #
    # No transformation of the raw data is performed prior to interpolation, so
    # reflectivity will be interpolated in dB units, velocity in m/s, etc,
    # this may not be the best method for interpolation.
    #
    # This method was adapted from Radx
    cdef int ray_num, i, interp_ngates
    cdef float gate_val, next_val, delta

    # FU-10 (3b55ab+89582b, CWE-787/125): moment_ngates and the ray range
    # come from the file's scan info, while the width of data/scratch_ray
    # comes from a range derived from possibly another moment's gate
    # count. Assert the contract explicitly so a mismatch is rejected here
    # instead of relying on the global boundscheck setting to turn it into
    # an IndexError (or into memory corruption where it is compiled out).
    if moment_ngates < 1 or moment_ngates > data.shape[1]:
        raise ValueError(
            "moment_ngates must be in 1..%d, got %d"
            % (data.shape[1], moment_ngates)
        )
    interp_ngates = 4 * moment_ngates  # number of gates interpolated
    if interp_ngates > scratch_ray.shape[0] or interp_ngates > data.shape[1]:
        raise ValueError(
            "interpolated scan needs %d gates but scratch_ray provides %d "
            "and data provides %d"
            % (interp_ngates, scratch_ray.shape[0], data.shape[1])
        )
    if start < 0 or end >= data.shape[0] or start > end + 1:
        raise ValueError(
            "invalid ray range [%d, %d] for %d rays"
            % (start, end, data.shape[0])
        )

    for ray_num in range(start, end+1):

        # repeat each gate value 4 times
        for i in range(moment_ngates):
            gate_val = data[ray_num, i]
            scratch_ray[i*4 + 0] = gate_val
            scratch_ray[i*4 + 1] = gate_val
            scratch_ray[i*4 + 2] = gate_val
            scratch_ray[i*4 + 3] = gate_val

        if linear_interp:
            # linear interpolate
            for i in range(2, interp_ngates - 4, 4):
                gate_val = scratch_ray[i]
                next_val = scratch_ray[i+4]
                if gate_val == fill_value or next_val == fill_value:
                    continue
                delta = (next_val - gate_val) / 4.
                scratch_ray[i+0] = gate_val + delta * 0.5
                scratch_ray[i+1] = gate_val + delta * 1.5
                scratch_ray[i+2] = gate_val + delta * 2.5
                scratch_ray[i+3] = gate_val + delta * 3.5

        for i in range(interp_ngates):
            data[ray_num, i] = scratch_ray[i]


def _fast_interpolate_scan_2(
        float[:, :] data, float[:] scratch_ray, float fill_value,
        int start, int end, int moment_ngates, int linear_interp):
    """ Interpolate a single NEXRAD moment scan from 300 m to 150 m. """
    # This interpolation scheme is only valid for NEXRAD TWDR data where a 2:1
    # (300 m : 150 m) interpolation is needed.
    #
    # The scheme here performs a linear interpolation between pairs of gates
    # in a ray when the both of the gates are not masked (below threshold).
    # When one of the gates is masked the interpolation changes to a nearest
    # neighbor interpolation. Nearest neighbor is also performed at the end
    # points until the new range bin would be centered beyond half of the range
    # spacing of the original range.
    #
    # Nearest neighbor interpolation is performed when linear_interp is False,
    # this is equivalent to repeating each gate four times in each ray.
    #
    # No transformation of the raw data is performed prior to interpolation, so
    # reflectivity will be interpolated in dB units, velocity in m/s, etc,
    # this may not be the best method for interpolation.
    #
    # This method was adapted from Radx
    cdef int ray_num, i, interp_ngates
    cdef float gate_val, next_val, delta

    # FU-10 (3b55ab+89582b, CWE-787/125): same contract as
    # _fast_interpolate_scan_4; see the comment there.
    if moment_ngates < 1 or moment_ngates > data.shape[1]:
        raise ValueError(
            "moment_ngates must be in 1..%d, got %d"
            % (data.shape[1], moment_ngates)
        )
    interp_ngates = 2 * moment_ngates - 1 # number of gates interpolated
    if interp_ngates > scratch_ray.shape[0] or interp_ngates > data.shape[1]:
        raise ValueError(
            "interpolated scan needs %d gates but scratch_ray provides %d "
            "and data provides %d"
            % (interp_ngates, scratch_ray.shape[0], data.shape[1])
        )
    if start < 0 or end >= data.shape[0] or start > end + 1:
        raise ValueError(
            "invalid ray range [%d, %d] for %d rays"
            % (start, end, data.shape[0])
        )

    for ray_num in range(start, end+1):

        # repeat each gate value 4 times
        for i in range(moment_ngates):
            gate_val = data[ray_num, i]
            if i == moment_ngates - 1:
                scratch_ray[i*2 + 0] = gate_val
            else:
                scratch_ray[i*2 + 0] = gate_val
                scratch_ray[i*2 + 1] = gate_val

        if linear_interp:
            # linear interpolate
            for i in range(1, interp_ngates - 2, 2):
                gate_val = scratch_ray[i]
                next_val = scratch_ray[i+2]
                if gate_val == fill_value or next_val == fill_value:
                    continue
                delta = (next_val - gate_val) / 2.
                scratch_ray[i+0] = gate_val + delta * 0.5
                scratch_ray[i+1] = gate_val + delta * 1.5

        for i in range(interp_ngates):
            data[ray_num, i] = scratch_ray[i]
