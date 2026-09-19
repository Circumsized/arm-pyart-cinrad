"""
Shared input-validation layer for the :py:mod:`pyart.io` readers.

Every value below is derived from *file content*, which is attacker
controlled until proven otherwise. The d2238d lesson was that a 12 KB
Sigmet file could request a multi-exabyte allocation; the rules here make
that class of defect structurally impossible:

1. **Validate before allocating**: any allocation size derived from file
   content goes through :py:func:`validate_dims` first.
2. **Bound every cursor**: any length/offset used to slice or advance goes
   through :py:func:`validate_cursor` first.
3. **Stream and bound decompression**: compressed payloads are expanded
   through :py:func:`decompress_bzip2_bounded` with a hard output ceiling
   (:py:data:`MAX_DECOMPRESSED`), so a small file cannot expand into an
   unbounded amount of memory.

Violations raise :py:class:`pyart.exceptions.PyARTDataError`.
"""

import bz2

import numpy as np

from pyart.exceptions import PyARTDataError

# Security limits for a single file/field/volume. Values are the "legal
# maximum" for the supported formats and can be tuned per deployment.
MAX_NGATES = 100_000  # gates per ray
MAX_NRAYS = 20_000  # rays per sweep
MAX_NSWEEPS = 100  # sweeps per volume
MAX_NVOLUME_ELEMS = 2**28  # max elements in one array (64M, ~256 MB f32)
MAX_FILE_BYTES = 1 << 30  # max size of a single input file
MAX_DECOMPRESSED = 256 * 2**20  # max streamed decompression output
MAX_STREAM_DECOMPRESSED = 64 * 2**20  # max output of a single bz2 stream
MAX_DIM_PRODUCT = 2**32  # max product of dimensions (int overflow guard)
# MDV field volumes are (nx, ny, nz); the format caps the number of vertical
# levels at 122 (MDV_MAX_VLEVELS in mdv_common).
MAX_MDV_NX = 100_000  # grid points along x
MAX_MDV_NY = 100_000  # grid points along y
MAX_MDV_NZ = 122  # vertical levels


def validate_dims(*dims, limits=None, max_elements=MAX_NVOLUME_ELEMS, name="array"):
    """
    Validate dimensions derived from file content before allocating.

    Each dimension must be a positive integer, no single dimension may
    exceed its entry in ``limits`` (or :py:data:`MAX_DIM_PRODUCT` when no
    per-dimension limit is given), and the product of all dimensions must
    not exceed ``max_elements``. The product is accumulated with an early
    exit so a bogus header value can never overflow an integer or a float
    while being checked.

    Parameters
    ----------
    *dims : int
        Dimensions to validate, e.g. ``validate_dims(nsweeps, nrays, nbins)``.
    limits : tuple of int, optional
        Per-dimension caps, in axis order, e.g.
        ``(MAX_NSWEEPS, MAX_NRAYS, MAX_NGATES)``. If None, every dimension
        is only capped by :py:data:`MAX_DIM_PRODUCT`.
    max_elements : int, optional
        Maximum allowed product of the dimensions. Defaults to
        :py:data:`MAX_NVOLUME_ELEMS`.
    name : str, optional
        Name used in the error message to identify the offending array.

    Raises
    ------
    PyARTDataError
        If any dimension is not a positive integer, any dimension exceeds
        its limit, or the product exceeds ``max_elements``.
    """
    if not dims:
        raise PyARTDataError(f"{name}: at least one dimension is required")
    if limits is not None and len(limits) != len(dims):
        raise PyARTDataError(
            f"{name}: {len(limits)} limits given for {len(dims)} dimensions"
        )
    product = 1
    for i, dim in enumerate(dims):
        if isinstance(dim, bool) or not isinstance(dim, (int, np.integer)):
            raise PyARTDataError(
                f"{name}: dimension {i} must be an integer, got {type(dim).__name__}"
            )
        if dim <= 0:
            raise PyARTDataError(f"{name}: dimensions must be positive, got {dims}")
        cap = MAX_DIM_PRODUCT if limits is None else limits[i]
        if dim > cap:
            raise PyARTDataError(
                f"{name}: dimension {i}={dim} exceeds the limit of {cap}"
            )
        product *= dim
        if product > max_elements:
            raise PyARTDataError(
                f"{name}: {dims} requests {product} elements, exceeding the "
                f"limit of {max_elements}"
            )


def validate_volume_dims(nsweeps, nrays, ngates, name="volume"):
    """
    Validate the canonical (nsweeps, nrays, ngates) triple of a radar volume.

    Convenience wrapper around :py:func:`validate_dims` with the radar
    per-dimension limits, for readers whose arrays are laid out as
    ``(nsweeps, nrays, ngates)``.

    Raises
    ------
    PyARTDataError
        If any dimension violates its limit or the volume exceeds
        :py:data:`MAX_NVOLUME_ELEMS` elements.
    """
    validate_dims(
        nsweeps,
        nrays,
        ngates,
        limits=(MAX_NSWEEPS, MAX_NRAYS, MAX_NGATES),
        name=name,
    )


def validate_mdv_dims(nx, ny, nz, name="MDV field"):
    """
    Validate the (nx, ny, nz) triple of an MDV field header.

    Convenience wrapper around :py:func:`validate_dims` with the MDV
    per-dimension limits, for readers whose arrays are laid out as
    ``(nz, ny, nx)``.

    Raises
    ------
    PyARTDataError
        If any dimension violates its limit or the field exceeds
        :py:data:`MAX_NVOLUME_ELEMS` elements.
    """
    validate_dims(
        nz,
        ny,
        nx,
        limits=(MAX_MDV_NZ, MAX_MDV_NY, MAX_MDV_NX),
        name=name,
    )


def validate_cursor(value, buffer, consumed=0, name="cursor"):
    """
    Validate a length/offset against a buffer before using it.

    Parameters
    ----------
    value : int
        The length or offset to validate.
    buffer : bytes or ndarray
        The buffer the value indexes into.
    consumed : int, optional
        Number of elements already consumed from the buffer. The value must
        not point past ``len(buffer) - consumed``.
    name : str, optional
        Name used in the error message.

    Raises
    ------
    PyARTDataError
        If ``value`` is negative, or ``consumed + value`` exceeds
        ``len(buffer)``.
    """
    remaining = len(buffer) - consumed
    if value < 0:
        raise PyARTDataError(f"{name}: negative value {value}")
    if value > remaining:
        raise PyARTDataError(
            f"{name}: {value} exceeds the {remaining} bytes remaining "
            f"in a {len(buffer)} byte buffer"
        )


def check_decompressed_size(size, limit=MAX_DECOMPRESSED):
    """
    Return ``size`` if it is within the decompression limit, else raise.

    Parameters
    ----------
    size : int
        Cumulative decompressed size in bytes.
    limit : int, optional
        Maximum allowed size. Defaults to :py:data:`MAX_DECOMPRESSED`.

    Raises
    ------
    PyARTDataError
        If ``size`` exceeds ``limit``.
    """
    if size > limit:
        raise PyARTDataError(
            f"decompressed data is {size} bytes, exceeding the limit of "
            f"{limit} bytes"
        )
    return size


def decompress_bzip2_bounded(payload, limit=MAX_DECOMPRESSED, chunk_size=1 << 20):
    """
    Decompress a bz2 payload with a hard ceiling on the output size.

    A small compressed payload can expand to gigabytes (a decompression
    bomb); this function streams the expansion and aborts as soon as the
    cumulative output would exceed ``limit``.

    Parameters
    ----------
    payload : bytes
        The compressed data.
    limit : int, optional
        Maximum allowed decompressed size. Defaults to
        :py:data:`MAX_DECOMPRESSED`.
    chunk_size : int, optional
        Granularity of both input feeding and output draining.

    Returns
    -------
    bytes
        The decompressed data.

    Raises
    ------
    PyARTDataError
        If the payload is not valid bz2 data, the stream ends before the
        end-of-stream marker, or the output would exceed ``limit``.
    """
    if not payload:
        return b""
    decompressor = bz2.BZ2Decompressor()
    chunks = []
    total = 0
    pos = 0
    try:
        while not decompressor.eof:
            if decompressor.needs_input:
                if pos >= len(payload):
                    raise PyARTDataError(
                        "bz2 stream ended before the end-of-stream marker"
                    )
                data_in = payload[pos : pos + chunk_size]
                pos += len(data_in)
            else:
                data_in = b""
            block = decompressor.decompress(data_in, chunk_size)
            if block:
                total += len(block)
                check_decompressed_size(total, limit)
                chunks.append(block)
    except (OSError, EOFError, ValueError) as err:
        raise PyARTDataError(f"bz2 decompression failed: {err}") from err
    return b"".join(chunks)


def decompress_bzip2_records_bounded(
    payload,
    first_skip,
    control_word_size,
    limit=MAX_DECOMPRESSED,
    stream_limit=MAX_STREAM_DECOMPRESSED,
    chunk_size=1 << 20,
):
    """
    Decompress a run of concatenated bz2 streams with hard ceilings.

    Archive 2 files store their records as a sequence of bz2 streams, each
    preceded by a control word: the first stream starts at ``first_skip``
    and every following stream starts ``control_word_size`` bytes after the
    end of the previous one. Reassembling them with per-stream
    ``decompress()`` calls lets a small file request an unbounded amount of
    memory (FU-18), so the output of every stream is capped at
    ``stream_limit`` bytes and the cumulative output at ``limit`` bytes,
    and the streams are expanded in chunks.

    Parameters
    ----------
    payload : bytes
        The compressed file content.
    first_skip : int
        Byte offset of the first bz2 stream in ``payload``.
    control_word_size : int
        Number of bytes between the end of a stream and the start of the
        next one.
    limit : int, optional
        Maximum cumulative output. Defaults to
        :py:data:`MAX_DECOMPRESSED`.
    stream_limit : int, optional
        Maximum output of a single stream. Defaults to
        :py:data:`MAX_STREAM_DECOMPRESSED`.
    chunk_size : int, optional
        Granularity of both input feeding and output draining.

    Returns
    -------
    bytes
        The concatenation of every stream's output.

    Raises
    ------
    PyARTDataError
        If the payload is not valid bz2 data, a stream ends before the
        end-of-stream marker, or either ceiling would be exceeded.
    """
    chunks = []
    total = 0
    pos = first_skip
    try:
        while pos < len(payload):
            decompressor = bz2.BZ2Decompressor()
            stream_total = 0
            while not decompressor.eof:
                if decompressor.needs_input:
                    if pos >= len(payload):
                        raise PyARTDataError(
                            "bz2 stream ended before the end-of-stream marker"
                        )
                    data_in = payload[pos : pos + chunk_size]
                    pos += len(data_in)
                else:
                    data_in = b""
                block = decompressor.decompress(data_in, chunk_size)
                if block:
                    stream_total += len(block)
                    if stream_total > stream_limit:
                        raise PyARTDataError(
                            f"a bz2 stream expands to more than {stream_limit} "
                            f"bytes"
                        )
                    total += len(block)
                    check_decompressed_size(total, limit)
                    chunks.append(block)
            # The stream is complete; the next one begins after the control
            # word that follows the bytes consumed so far. Bytes were only
            # ever fed ahead of the cursor, so the unconsumed tail held in
            # unused_data is the difference between the cursor and the end
            # of the stream.
            pos = pos - len(decompressor.unused_data) + control_word_size
    except (OSError, EOFError, ValueError) as err:
        raise PyARTDataError(f"bz2 decompression failed: {err}") from err
    return b"".join(chunks)
