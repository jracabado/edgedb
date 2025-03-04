.. _ref_eql_operators_set:

===
Set
===

:edb-alt-title: Set Aggregates and Operators
:index: set aggregate


.. list-table::
    :class: funcoptable

    * - :eql:op:`DISTINCT set <DISTINCT>`
      - :eql:op-desc:`DISTINCT`

    * - :eql:op:`anytype IN set <IN>`
      - :eql:op-desc:`IN`

    * - :eql:op:`set UNION set <UNION>`
      - :eql:op-desc:`UNION`

    * - :eql:op:`EXISTS set <EXISTS>`
      - :eql:op-desc:`EXISTS`

    * - :eql:op:`set IF bool ELSE set <IF..ELSE>`
      - :eql:op-desc:`IF..ELSE`

    * - :eql:op:`OPTIONAL anytype ?? set <COALESCE>`
      - :eql:op-desc:`COALESCE`

    * - :eql:op:`anytype [IS type] <ISINTERSECT>`
      - :eql:op-desc:`ISINTERSECT`

    * - :eql:func:`assert_single`
      - :eql:func-desc:`assert_single`

    * - :eql:func:`count`
      - :eql:func-desc:`count`

    * - :eql:func:`array_agg`
      - :eql:func-desc:`array_agg`

    * - :eql:func:`sum`
      - :eql:func-desc:`sum`

    * - :eql:func:`all`
      - :eql:func-desc:`all`

    * - :eql:func:`any`
      - :eql:func-desc:`any`

    * - :eql:func:`enumerate`
      - :eql:func-desc:`enumerate`

    * - :eql:func:`min`
      - :eql:func-desc:`min`

    * - :eql:func:`max`
      - :eql:func-desc:`max`

    * - :eql:func:`math::mean`
      - :eql:func-desc:`math::mean`

    * - :eql:func:`math::stddev`
      - :eql:func-desc:`math::stddev`

    * - :eql:func:`math::stddev_pop`
      - :eql:func-desc:`math::stddev_pop`

    * - :eql:func:`math::var`
      - :eql:func-desc:`math::var`

    * - :eql:func:`math::var_pop`
      - :eql:func-desc:`math::var_pop`


----------


.. eql:operator:: DISTINCT: DISTINCT SET OF anytype -> SET OF anytype

    Return a set without repeating any elements.

    ``DISTINCT`` is a set operator that returns a new set where
    no member is equal to any other member.

    .. code-block:: edgeql-repl

        db> SELECT DISTINCT {1, 2, 2, 3};
        {1, 2, 3}


----------


.. eql:operator:: IN: anytype IN SET OF anytype -> bool
                      anytype NOT IN SET OF anytype -> bool

    :index: intersection

    Test the membership of an element in a set.

    Set membership operators :eql:op:`IN` and :eql:op:`NOT IN<IN>`
    that test for each element of ``A`` whether it is present in ``B``.

    .. code-block:: edgeql-repl

        db> SELECT 1 IN {1, 3, 5};
        {true}

        db> SELECT 'Alice' IN User.name;
        {true}

        db> SELECT {1, 2} IN {1, 3, 5};
        {true, false}

    This operator can also be used to implement set intersection:

    .. code-block:: edgeql-repl

        db> WITH
        ...     A := {1, 2, 3, 4},
        ...     B := {2, 4, 6}
        ... SELECT A FILTER A IN B;
        {2, 4}


----------


.. eql:operator:: UNION: SET OF anytype UNION SET OF anytype -> SET OF anytype

    Merge two sets.

    Since EdgeDB sets are formally multisets, ``UNION`` is a *multiset sum*,
    so effectively it merges two multisets keeping all of their members.

    For example, applying ``UNION`` to ``{1, 2, 2}`` and
    ``{2}``, results in ``{1, 2, 2, 2}``.

    If you need a distinct union, wrap it with :eql:op:`DISTINCT`.


----------


.. eql:operator:: IF..ELSE: SET OF anytype IF bool ELSE SET OF anytype \
                                -> SET OF anytype

    :index: if else ifelse elif ternary

    Conditionally provide one or the other result.

    .. eql:synopsis::

        <left_expr> IF <condition> ELSE <right_expr>

    If :eql:synopsis:`<condition>` is ``true``, then the value of the
    ``IF..ELSE`` expression is the value of :eql:synopsis:`<left_expr>`;
    if :eql:synopsis:`<condition>` is ``false``, the result is the value of
    :eql:synopsis:`<right_expr>`.

    .. code-block:: edgeql-repl

        db> SELECT 'hello' IF 2 * 2 = 4 ELSE 'bye';
        {'hello'}

    ``IF..ELSE`` expressions can be chained when checking multiple conditions
    is necessary:

    .. code-block:: edgeql-repl

        db> WITH color := 'yellow'
        ... SELECT 'Apple' IF color = 'red' ELSE
        ...        'Banana' IF color = 'yellow' ELSE
        ...        'Orange' IF color = 'orange' ELSE
        ...        'Other';
        {'Banana'}

-----------


.. eql:operator:: COALESCE: OPTIONAL anytype ?? SET OF anytype \
                              -> SET OF anytype

    Coalesce.

    Evaluate to ``A`` for non-empty ``A``, otherwise evaluate to ``B``.

    A typical use case of the coalescing operator is to provide default
    values for optional properties.

    .. code-block:: edgeql

        # Get a set of tuples (<issue name>, <priority>)
        # for all issues.
        SELECT (Issue.name, Issue.priority.name ?? 'n/a');

    Without the coalescing operator the above query would skip any
    ``Issue`` without priority.


----------


.. eql:operator:: EXISTS: EXISTS SET OF anytype -> bool

    Test whether a set is not empty.

    ``EXISTS`` is an aggregate operator that returns a singleton set
    ``{true}`` if the input set is not empty and returns ``{false}``
    otherwise.

    .. code-block:: edgeql-repl

        db> SELECT EXISTS {1, 2};
        {true}


----------


.. eql:operator:: ISINTERSECT: anytype [IS type] -> anytype

    :index: is type intersection

    Filter the set based on type.

    The type intersection operator removes all elements from the input set
    that aren't of the specified type. Additionally, since it
    guarantees the type of the result set, all the links and properties
    associated with the specified type can now be used on the
    resulting expression. This is especially useful in combination
    with :ref:`backlinks <ref_datamodel_links>`.

    Consider the following types:

    .. code-block:: sdl

        type User {
            required property name -> str;
        }

        abstract type Owned {
            required link owner -> User;
        }

        type Issue extending Owned {
            required property title -> str;
        }

        type Comment extending Owned {
            required property body -> str;
        }

    The following expression will get all :eql:type:`Objects <Object>`
    owned by all users (if there are any):

    .. code-block:: edgeql

        SELECT User.<owner;

    By default :ref:`backlinks <ref_datamodel_links>` don't infer any
    type information beyond the fact that it's an :eql:type:`Object`.
    To ensure that this path specifically reaches ``Issue`` the type
    intersection operator must be used:

    .. code-block:: edgeql

        SELECT User.<owner[IS Issue];

        # With the use of type intersection it's possible to refer to
        # specific property of Issue now:
        SELECT User.<owner[IS Issue].title;


----------


.. eql:function:: std::assert_single(s: SET OF anytype) -> anytype

    :index: cardinality singleton

    Check that the input set contains no more than one element.

    If the input set contains more than one element, ``assert_single``
    raises a ``CardinalityViolationError``.  This function is useful
    as a runtime cardinality assertion in queries and computed
    expressions that should always return sets with at most a single
    element, but where static cardinality inference is not capable
    enough or outright impossible.

    .. code-block:: edgeql-repl

        db> SELECT assert_single((SELECT User FILTER .name = "Unique"))
        {default::User {id: ...}}

        db> SELECT assert_single((SELECT User))
        ERROR: CardinalityViolationError: assert_single violation: more than
               one element returned by an expression


----------


.. eql:function:: std::count(s: SET OF anytype) -> int64

    :index: aggregate

    Return the number of elements in a set.

    .. code-block:: edgeql-repl

        db> SELECT count({2, 3, 5});
        {3}

        db> SELECT count(User);  # number of User objects in db
        {4}


----------


.. eql:function:: std::sum(s: SET OF int32) -> int64
                  std::sum(s: SET OF int64) -> int64
                  std::sum(s: SET OF float32) -> float32
                  std::sum(s: SET OF float64) -> float64
                  std::sum(s: SET OF bigint) -> bigint
                  std::sum(s: SET OF decimal) -> decimal

    :index: aggregate

    Return the sum of the set of numbers.

    The result type depends on the input set type. The general rule is
    that the type of the input set is preserved (as if a simple
    :eql:op:`+<PLUS>` was used) while trying to reduce the chance of
    an overflow (so all integers produce :eql:type:`int64` sum).

    .. code-block:: edgeql-repl

        db> SELECT sum({2, 3, 5});
        {10}

        db> SELECT sum({0.2, 0.3, 0.5});
        {1.0}


----------


.. eql:function:: std::all(values: SET OF bool) -> bool

    :index: aggregate

    Generalized boolean :eql:op:`AND` applied to the set of *values*.

    The result is ``true`` if all of the *values* are ``true`` or the
    set of *values* is ``{}``. Return ``false`` otherwise.

    .. code-block:: edgeql-repl

        db> SELECT all(<bool>{});
        {true}

        db> SELECT all({1, 2, 3, 4} < 4);
        {false}


----------


.. eql:function:: std::any(values: SET OF bool) -> bool

    :index: aggregate

    Generalized boolean :eql:op:`OR` applied to the set of *values*.

    The result is ``true`` if any of the *values* are ``true``. Return
    ``false`` otherwise.

    .. code-block:: edgeql-repl

        db> SELECT any(<bool>{});
        {false}

        db> SELECT any({1, 2, 3, 4} < 4);
        {true}


----------


.. eql:function:: std::enumerate(values: SET OF anytype) -> \
                  SET OF tuple<int64, anytype>

    :index: enumerate

    Return a set of tuples of the form ``(index, element)``.

    The ``enumerate()`` function takes any set and produces a set of
    tuples containing the zero-based index number and the value for each
    element.

    .. note::

        The ordering of the returned set is not guaranteed, however
        the assigned indexes are guaranteed to be in order of the
        original set.

    .. code-block:: edgeql-repl

        db> SELECT enumerate({2, 3, 5});
        {(1, 3), (0, 2), (2, 5)}

    .. code-block:: edgeql-repl

        db> SELECT enumerate(User.name);
        {(0, 'Alice'), (1, 'Bob'), (2, 'Dave')}


----------


.. eql:function:: std::min(values: SET OF anytype) -> OPTIONAL anytype

    :index: aggregate

    Return the smallest value of the input set.

    .. code-block:: edgeql-repl

        db> SELECT min({-1, 100});
        {-1}


----------


.. eql:function:: std::max(values: SET OF anytype) -> OPTIONAL anytype

    :index: aggregate

    Return the greatest value of the input set.

    .. code-block:: edgeql-repl

        db> SELECT max({-1, 100});
        {100}
