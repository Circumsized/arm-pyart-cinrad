cdef extern int unwrap3D(double* wrapped_volume,
                     double* unwrapped_volume,
                     unsigned char* input_mask,
                     int image_width, int image_height, int volume_depth,
                     int wrap_around_x, int wrap_around_y, int wrap_around_z)

def unwrap_3d(double[:, :, ::1] image,
              unsigned char[:, :, ::1] mask,
              double[:, :, ::1] unwrapped_image,
              wrap_around):
    # 3D phase unwrapping.
    # FU-08 (1a7f21, CWE-476/787): reject empty or mismatched inputs
    # before taking the address of the first element (an empty axis makes
    # &image[0, 0, 0] an out-of-bounds access) and before entering the C
    # unwrapper, whose buffers must match the volume exactly. The C
    # routine validates its own dimensions and allocation results and
    # reports them through its return code.
    # NOTE: a memoryview's .shape is a fixed-size C array, so the axes
    # must be compared one by one (tuple comparison compares addresses).
    if image.shape[0] < 1 or image.shape[1] < 1 or image.shape[2] < 1:
        raise ValueError(
            "unwrap_3d requires a non-empty image, got shape %s"
            % ((image.shape[0], image.shape[1], image.shape[2]),)
        )
    if (mask.shape[0] != image.shape[0] or
            mask.shape[1] != image.shape[1] or
            mask.shape[2] != image.shape[2]):
        raise ValueError(
            "mask shape %s does not match image shape %s"
            % ((mask.shape[0], mask.shape[1], mask.shape[2]),
               (image.shape[0], image.shape[1], image.shape[2]))
        )
    if (unwrapped_image.shape[0] != image.shape[0] or
            unwrapped_image.shape[1] != image.shape[1] or
            unwrapped_image.shape[2] != image.shape[2]):
        raise ValueError(
            "unwrapped_image shape %s does not match image shape %s"
            % ((unwrapped_image.shape[0], unwrapped_image.shape[1],
                unwrapped_image.shape[2]),
               (image.shape[0], image.shape[1], image.shape[2]))
        )
    status = unwrap3D(&image[0, 0, 0],
             &unwrapped_image[0, 0, 0],
             &mask[0, 0, 0],
             image.shape[2], image.shape[1], image.shape[0], #TODO: check!!!
             wrap_around[2], wrap_around[1], wrap_around[0],
             )
    if status == -1:
        raise ValueError(
            "unwrap_3d volume shape %s is too large for the unwrapper"
            % ((image.shape[0], image.shape[1], image.shape[2]),)
        )
    if status == -2:
        raise MemoryError(
            "unwrap_3d could not allocate the working buffers for a volume "
            "of shape %s" % ((image.shape[0], image.shape[1], image.shape[2]),)
        )
