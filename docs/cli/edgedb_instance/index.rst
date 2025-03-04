.. _ref_cli_edgedb_instance:

===============
edgedb instance
===============

The ``edgedb instance`` group of commands contains all sorts of tools
for managing EdgeDB instances.

.. toctree::
    :maxdepth: 3
    :hidden:

    edgedb_instance_create
    edgedb_instance_status
    edgedb_instance_start
    edgedb_instance_stop
    edgedb_instance_restart
    edgedb_instance_destroy
    edgedb_instance_logs
    edgedb_instance_revert
    edgedb_instance_reset_password

.. list-table::
    :class: funcoptable

    * - :ref:`ref_cli_edgedb_instance_create`
      - Initialize a new server instance
    * - :ref:`ref_cli_edgedb_instance_status`
      - Show statuses of all or of a matching instance
    * - :ref:`ref_cli_edgedb_instance_start`
      - Start an instance
    * - :ref:`ref_cli_edgedb_instance_stop`
      - Stop an instance
    * - :ref:`ref_cli_edgedb_instance_restart`
      - Restart an instance
    * - :ref:`ref_cli_edgedb_instance_destroy`
      - Destroy a server instance and remove the data stored
    * - :ref:`ref_cli_edgedb_instance_logs`
      - Show logs of an instance
    * - :ref:`ref_cli_edgedb_instance_revert`
      - Revert a major instance upgrade
    * - :ref:`ref_cli_edgedb_instance_reset_auth`
      - Reset password for a user in the instance
