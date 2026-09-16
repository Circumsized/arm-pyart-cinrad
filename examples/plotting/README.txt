.. _plotting_examples:

Plotting examples
-----------------

Plotting real world radar data with Py-ART.

Contributing a gallery example
==============================

Every ``plot_*.py`` in ``examples/`` is executed at documentation build time
by Sphinx-Gallery, so examples must satisfy a strict contract. Follow these
rules or the docs build will fail:

1. **Run with no arguments.** ``python plot_my_example.py`` must succeed on
   its own. Keep a ``sys.argv`` fallback only for optional real files.
2. **Do not block on the network.** When data is required, synthesise it with
   helpers from :mod:`pyart.testing` (e.g.
   :func:`pyart.testing.make_empty_ppi_radar`) instead of downloading it.
   The CI docs build runs with ``PYART_DOCS_OFFLINE=1``.
3. **Finish quickly.** Target under 30 seconds and emit at most one figure.
4. **Use the gallery header.** Start the module docstring with a title
   underlined by ``===``, as in the existing examples.

A lightweight CI step (``Gallery example contract`` in
``.github/workflows/ci.yml``) runs each example with no arguments and no
network, so a broken contract is reported in seconds rather than after the
full docs job.
