.. _ref_cli_edgedb_instance_revert:


======================
edgedb instance revert
======================

Revert a major instance upgrade.

.. cli:synopsis::

     edgedb instance revert [OPTIONS] <name>


Description
===========

When :ref:`ref_cli_edgedb_server_upgrade` performs a major version
upgrade on an instance the old instance data is kept around. The
``edgedb instance revert`` command removes the new instance version and
replaces it with the old copy. It also ensures that the previous
version of EdgeDB server is used to run it.


Options
=======

:cli:synopsis:`<name>`
    The name of the EdgeDB instance to revert.

:cli:synopsis:`--ignore-pid-check`
    Do not check if upgrade is in progress.

:cli:synopsis:`-y, --no-confirm`
    Do not ask for a confirmation.
