.. _ref_cli_edgedb_instance_logs:


====================
edgedb instance logs
====================

Show instance logs.

.. cli:synopsis::

     edgedb instance logs [OPTIONS] <name>


Description
===========

``edgedb instance logs`` is a terminal command for displaying the logs
for a given EdgeDB instance.


Options
=======

:cli:synopsis:`<name>`
    The name of the EdgeDB instance.

:cli:synopsis:`-n, --tail=<tail>`
    Number of the most recent lines to show.

:cli:synopsis:`-f, --follow`
    Show log's tail and the continue watching for the new entries.
