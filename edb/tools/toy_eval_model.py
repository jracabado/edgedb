# mypy: no-ignore-errors, strict-optional, disallow-any-generics

"""Toy evaluator model for an edgeql subset.

The idea here is to be able to test queries against a simple semantics
driven evaluator model. The core of this is basically a transcription
of the "EdgeQL - Overview" section of the docs.

This version does not have any understanding of schemas and has the
function signatures and behaviors of a bunch of basic functions
hardcoded in.

The data model is a super simple in-memory one hardcoded into this
file, though it shouldn't be too hard to populate it from a real DB or
vice versa to do testing.

It is a goal that this can usefully be pointed at different corners of
the language for testing. It is a non-goal that it can be scaled up to
be a full evaluator model; if it can serve as the basis of one, great,
but I'm not worrying about that yet. If we have to start from scratch,
also fine, because this one is pretty simple so not much will be lost
in throwing it away.

Also a non-goal: performance.

Right now we support a lot of the core SELECT fragment of the language,
but are missing:
 * Any DML at all
 * Most casts (we support int and str casts)
 * Most of the standard library, except for some basic functions
 * Any understanding of modules
 * Any understanding of the schema or the type hierarchy
 * Cardinality inference for shape elements; everything is reported as as list

Cardinality inference is one of the big open design questions that I have:
is there a reasonable way that we could implement it as a mostly-dynamic
analysis, without needing a full separate cardinality checker?

There is no type or error checking.

Run this as a script for a bad REPL that can be noodled around
in. I've tested out a bunch of queries playing around and have a small
test suite but this hasn't gotten any particular rigorous testing
against the real DB.

"""

from __future__ import annotations

from pathlib import Path
import sys
EDB_DIR = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(EDB_DIR))

from typing import *


from edb.common import debug
from edb.common.compiler import SimpleCounter
from edb import edgeql

from edb.common.ast import NodeVisitor
from edb.edgeql import ast as qlast
from edb.edgeql import qltypes as ft

from dataclasses import dataclass, replace
from collections import defaultdict

import argparse
import contextlib
import functools
import itertools
import json
import operator
import pprint
import random
import traceback
import uuid


T = TypeVar('T')


def bsid(n: int) -> uuid.UUID:
    return uuid.UUID(f'ffffffff-ffff-ffff-ffff-{n:012x}')


# ############# Data model

Data = Any
Result = List[Data]
Row = Tuple[Data, ...]
DB = Dict[uuid.UUID, Dict[str, Data]]


class Obj:
    def __init__(
        self,
        id: uuid.UUID,
        shape: Optional[Dict[str, Data]]=None,
        data: Optional[Dict[str, Data]]=None,
    ) -> None:
        self.id = id
        if shape is None:
            shape = {"id": id}
        self.shape = shape
        self.data = data or {}

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Obj) and other.id == self.id

    def __repr__(self) -> str:
        return f'Obj{self.shape!r}'


def mk_db(data: Iterable[Dict[str, Data]]) -> DB:
    return {x["id"]: x for x in data}


def bslink(n: int, **kwargs: Data) -> Data:
    lprops = {'@' + k: v for k, v in kwargs.items()}
    return Obj(bsid(n), data=lprops)


# # Toy basis stuff

SET_OF, OPTIONAL, SINGLETON = (
    ft.TypeModifier.SetOfType, ft.TypeModifier.OptionalType,
    ft.TypeModifier.SingletonType)

# We just list things with weird behavior
BASIS = {
    'count': [SET_OF],
    'sum': [SET_OF],
    'min': [SET_OF],
    'max': [SET_OF],
    'all': [SET_OF],
    'any': [SET_OF],
    'array_agg': [SET_OF],
    'enumerate': [SET_OF],
    'IN': [SINGLETON, SET_OF],
    'NOT IN': [SINGLETON, SET_OF],
    '??': [OPTIONAL, SET_OF],
    'EXISTS': [SET_OF],
    'DISTINCT': [SET_OF],
    'IF': [SET_OF, SINGLETON, SET_OF],
    'UNION': [SET_OF, SET_OF],
    '?=': [OPTIONAL, OPTIONAL],
    '?!=': [OPTIONAL, OPTIONAL],
}


# #############
class IPartial(NamedTuple):
    pass


class IExpr(NamedTuple):
    expr: qlast.Expr


class IORef(NamedTuple):
    name: str


class ITypeIntersection(NamedTuple):
    typ: str


class IPtr(NamedTuple):
    ptr: str
    direction: Optional[str]
    is_link_property: bool


IPathElement = Union[IPartial, IExpr, IORef, ITypeIntersection, IPtr]
IPath = Tuple[IPathElement, ...]

# Implementation of built in functions and operators


class LiftedFunc(Protocol):
    def __call__(self, *args: Result) -> Result:
        pass


def lift(f: Callable[..., Data]) -> LiftedFunc:
    """Lifts a function operating on base data to operator on sets.

    The result is the usual cartesian product."""
    def inner(*args: Result) -> Result:
        out = []
        for args1 in itertools.product(*args):
            val = f(*args1)
            out.append(val)
        return out
    return inner


def lift_set_of(f: Callable[..., Union[Data]]) -> LiftedFunc:
    def inner(*args: Result) -> Result:
        return [f(*args)]
    return inner


def opt_eq(x: Result, y: Result) -> Result:
    if not x or not y:
        return [len(x) == len(y)]
    return lift(operator.eq)(x, y)


def opt_ne(x: Result, y: Result) -> Result:
    if not x or not y:
        return [len(x) != len(y)]
    return lift(operator.ne)(x, y)


def contains(es: Result, s: Result) -> Result:
    return [e in s for e in es]


def not_contains(es: Result, s: Result) -> Result:
    return [e not in s for e in es]


def coalesce(x: Result, y: Result) -> Result:
    return strip_shapes(x or y)


def distinct(x: Result) -> Result:
    return dedup(x)


def union(x: Result, y: Result) -> Result:
    return strip_shapes(x + y)


def enumerate_(x: Result) -> Result:
    return list(enumerate(x))


def array_agg(x: Result) -> Result:
    return [x]


def array_unpack(x: Result) -> Result:
    return [y for array in x for y in array]


def if_(x: Result, bs: Result, y: Result) -> Result:
    out = []
    for b in bs:
        if b:
            out.extend(x)
        else:
            out.extend(y)
    return out


def bad_array_cast(x: Data) -> Result:
    if x != []:
        raise ValueError("We only know how to cast empty arrays")
    return x


# For implementing a next() testing function
NextCounter = SimpleCounter()


_BASIS_BINOP_IMPLS: Any = {
    '+': lift(operator.add),
    '-': lift(operator.sub),
    '*': lift(operator.mul),
    '/': lift(operator.truediv),
    '//': lift(operator.floordiv),
    '%': lift(operator.mod),
    '++': lift(operator.add),
    '=': lift(operator.eq),
    '!=': lift(operator.ne),
    '<': lift(operator.lt),
    '<=': lift(operator.le),
    '>': lift(operator.gt),
    '>=': lift(operator.ge),
    '^': lift(operator.pow),
    'OR': lift(operator.or_),
    'AND': lift(operator.and_),
    '?=': opt_eq,
    '?!=': opt_ne,
    'IN': contains,
    'NOT IN': not_contains,
    '??': coalesce,
    'UNION': union,
    # ... not really a binop
    'IF': if_,
}
_BASIS_UNOP_IMPLS: Any = {
    '-': lift(operator.neg),
    '+': lift(operator.pos),
    'NOT': lift(operator.not_),
    'EXISTS': lift_set_of(bool),
    'DISTINCT': distinct,
}
_BASIS_CAST_IMPLS: Any = {
    'str': lift(str),
    'int32': lift(int),
    'int64': lift(int),
    'uuid': lift(uuid.UUID),
    'array': lift(bad_array_cast),
}
_BASIS_FUNC_IMPLS: Any = {
    'enumerate': enumerate_,
    'count': lift_set_of(len),
    'sum': lift_set_of(sum),
    'min': lift_set_of(min),
    'max': lift_set_of(max),
    'all': lift_set_of(all),
    'any': lift_set_of(any),
    'len': lift(len),
    'array_agg': array_agg,
    'array_unpack': array_unpack,
    'random': lift(random.random),
    'contains': lift(operator.contains),
    'round': lift(round),
    'next': lift(NextCounter.nextval),  # testing func, not really in std
    'uuid_generate_v1mc': lift(uuid.uuid4),
}

BASIS_IMPLS: Dict[Tuple[str, str], LiftedFunc] = {
    (typ, key): impl
    for typ, impls in [
        ('binop', _BASIS_BINOP_IMPLS),
        ('unop', _BASIS_UNOP_IMPLS),
        ('cast', _BASIS_CAST_IMPLS),
        ('func', _BASIS_FUNC_IMPLS),
    ]
    for key, impl in impls.items()
}


# ############### The actual evaluator

@dataclass
class EvalContext:
    query_input_list: List[IPath]
    input_tuple: Tuple[Data, ...]
    aliases: Dict[str, Result]
    cur_path: Optional[qlast.Path]
    db: DB


@functools.singledispatch
def _eval(
    node: qlast.Base,
    ctx: EvalContext,
) -> Result:
    raise NotImplementedError(
        f'no EdgeQL eval handler for {node.__class__}')


def graft(
    prefix: Optional[qlast.Path], new: qlast.Path, always_partial: bool=False
) -> qlast.Path:
    if new.partial or always_partial:
        assert prefix is not None
        return qlast.Path(
            steps=prefix.steps + new.steps, partial=prefix.partial)
    else:
        return new


def update_path(
    prefix: Optional[qlast.Path], query: Optional[qlast.Expr]
) -> Optional[qlast.Path]:
    if query is None:
        return None
    elif isinstance(query, qlast.ReturningMixin):
        if query.result_alias is not None:
            return qlast.Path(steps=[
                qlast.ObjectRef(name=query.result_alias)
            ])
        else:
            query = query.result

    while isinstance(query, qlast.Shape):
        query = query.expr

    if isinstance(query, qlast.Path):
        return graft(prefix, query)
    else:
        # XXX: synthesize a real prefix name?
        return qlast.Path(partial=True, steps=[])


def ptr_name(ptr: qlast.Ptr) -> str:
    name = ptr.ptr.name
    if ptr.type == 'property':
        name = '@' + name
    return name


def eval_filter(
    where: Optional[qlast.Expr],
    qil: List[IPath],
    out: List[Row],
    ctx: EvalContext
) -> List[Row]:
    if not where:
        return out

    new = []
    for row in out:
        subctx = replace(ctx, query_input_list=qil, input_tuple=row)

        if any(subquery(where, ctx=subctx)):
            new.append(row)
    return new


def eval_orderby(
    orderby: List[qlast.SortExpr],
    qil: List[IPath],
    out: List[Row],
    ctx: EvalContext
) -> List[Row]:
    # Go through the sort specifiers in reverse order, which takes
    # advantage of sort being stable to do the right thing. (We can't
    # just build one composite key because they might have different
    # sort orders, so we'd have to use cmp_to_key if we wanted to do
    # it in one go...)
    for sort in reversed(orderby):
        nones_bigger = (
            (sort.direction == 'ASC' and sort.nones_order == 'last')
            or (sort.direction == 'DESC' and sort.nones_order == 'first')
        )

        # Decorate
        new = []
        for row in out:
            subctx = replace(ctx, query_input_list=qil, input_tuple=row)
            vals = subquery(sort.path, ctx=subctx)
            assert len(vals) <= 1
            # We wrap the result in a tuple with an emptiness tag at the start
            # to handle sorting empty values
            val = (not nones_bigger, vals[0]) if vals else (nones_bigger,)
            new.append((row, (val,)))

        # Sort
        new.sort(key=lambda x: x[1], reverse=sort.direction == 'DESC')

        # Undecorate
        out = [row for row, _ in new]

    return out


def eval_offset(offset: Optional[qlast.Expr], out: List[Row],
                ctx: EvalContext) -> List[Row]:
    if offset:
        res = subquery(offset, ctx=ctx)
        assert len(res) == 1
        out = out[int(res[0]):]
    return out


def eval_limit(limit: Optional[qlast.Expr], out: List[Row],
               ctx: EvalContext) -> List[Row]:
    if limit:
        res = subquery(limit, ctx=ctx)
        assert len(res) == 1
        out = out[:int(res[0])]
    return out


def eval_aliases(node: qlast.Statement, ctx: EvalContext) -> EvalContext:
    if node.aliases:
        ctx = replace(ctx, aliases=ctx.aliases.copy())
        for alias in node.aliases:
            assert isinstance(alias, qlast.AliasedExpr)
            ctx.aliases[alias.alias] = (
                strip_shapes(subquery(alias.expr, ctx=ctx)))

    return ctx


@_eval.register
def eval_Select(node: qlast.SelectQuery, ctx: EvalContext) -> Result:
    ctx = eval_aliases(node, ctx)

    # XXX: I believe this is right, but:
    # WHERE and ORDER BY are treated as subqueries of the result query,
    # and LIMIT and OFFSET are not.
    subq_path = update_path(ctx.cur_path, node)

    orderby = node.orderby or []
    # XXX: wait, why do we need extra_subqs? All the queries I've
    # thought of where it has an effect
    # (SELECT (User.name, User.deck.cost) FILTER User.deck.name != "Dragon")
    # get rejected by the real compiler for "changing the interpretation".
    subqs = [node.where] + [x.path for x in orderby]
    extra_subqs = [(subq_path, subq) for subq in subqs]
    new_qil, out = subquery_full(node.result, extra_subqs=extra_subqs, ctx=ctx)
    new_qil += [simplify_path(subq_path) if subq_path else (IPartial(),)]
    if node.result_alias:
        out = [row + (row[-1],) for row in out]
        new_qil += [(IORef(node.result_alias),)]

    subq_ctx = replace(ctx, cur_path=subq_path)
    out = eval_filter(node.where, new_qil, out, ctx=subq_ctx)
    out = eval_orderby(orderby, new_qil, out, ctx=subq_ctx)

    limoff_ctx = replace(ctx, cur_path=None)
    out = eval_offset(node.offset, out, ctx=limoff_ctx)
    out = eval_limit(node.limit, out, ctx=limoff_ctx)

    return [row[-1] for row in out]


@_eval.register
def eval_ShapeElement(el: qlast.ShapeElement, ctx: EvalContext) -> Result:
    if el.compexpr:
        result = el.compexpr
    else:
        result = qlast.Path(partial=True, steps=el.expr.steps)

    if el.elements:
        result = qlast.Shape(expr=result, elements=el.elements)

    fake_select = qlast.SelectQuery(
        result=result,
        orderby=el.orderby,
        where=el.where,
        limit=el.limit,
        offset=el.offset,
    )

    return eval(fake_select, ctx=ctx)


ANONYMOUS_SHAPE_EXPR = qlast.DetachedExpr(
    expr=qlast.Path(
        steps=[qlast.ObjectRef(name='VirtualObject')],
    ),
)


@_eval.register
def eval_Shape(node: qlast.Shape, ctx: EvalContext) -> Result:

    subq_path = update_path(ctx.cur_path, node.expr)
    subq_ipath = simplify_path(subq_path) if subq_path else (IPartial(),)
    qil = ctx.query_input_list + [subq_ipath]

    # XXX: do we need to do extra_subqs??
    expr = node.expr or ANONYMOUS_SHAPE_EXPR
    shape_vals = eval(expr, ctx=ctx)

    out = []
    for val in shape_vals:
        subctx = replace(ctx, query_input_list=qil,
                         cur_path=subq_path,
                         input_tuple=ctx.input_tuple + (val,))

        vals = {}
        for el in node.elements:
            ptr = el.expr.steps[0]
            assert isinstance(ptr, qlast.Ptr)
            name = ptr_name(ptr)

            el_val = eval(el, ctx=subctx)
            vals[name] = el_val

        # Merge any data already on the object with any new shape info
        data = {**val.data, **vals}

        val = Obj(val.id, shape=vals, data=data)
        out.append(val)

    return out


@_eval.register
def eval_For(node: qlast.ForQuery, ctx: EvalContext) -> Result:
    ctx = eval_aliases(node, ctx)
    iter_vals = strip_shapes(subquery(node.iterator, ctx=ctx))
    qil = ctx.query_input_list + [(IORef(node.iterator_alias),)]
    out = []
    for val in iter_vals:
        subctx = replace(ctx, query_input_list=qil,
                         input_tuple=ctx.input_tuple + (val,))
        out.extend(subquery(node.result, ctx=subctx))

    return out


@_eval.register
def eval_DetachedExpr(
        node: qlast.DetachedExpr, ctx: EvalContext) -> Result:
    return toplevel_query(node.expr, db=ctx.db)


def eval_func_or_op(op: str, args: List[qlast.Expr], typ: str,
                    ctx: EvalContext) -> Result:
    arg_specs = BASIS.get(op)

    results = []
    for i, arg in enumerate(args):
        if arg_specs and arg_specs[i] in (SET_OF, OPTIONAL):
            # SET OF is a subquery
            results.append(subquery(arg, ctx=ctx))
        else:
            results.append(eval(arg, ctx=ctx))

    f = BASIS_IMPLS[typ, op]
    return f(*results)


@_eval.register
def eval_BinOp(node: qlast.BinOp, ctx: EvalContext) -> Result:
    return eval_func_or_op(
        node.op.upper(), [node.left, node.right], 'binop', ctx)


@_eval.register
def eval_UnaryOp(node: qlast.UnaryOp, ctx: EvalContext) -> Result:
    return eval_func_or_op(
        node.op.upper(), [node.operand], 'unop', ctx)


@_eval.register
def eval_Call(node: qlast.FunctionCall, ctx: EvalContext) -> Result:
    assert isinstance(node.func, str)
    return eval_func_or_op(node.func, node.args or [], 'func', ctx)


@_eval.register
def visit_IfElse(query: qlast.IfElse, ctx: EvalContext) -> Result:
    return eval_func_or_op(
        'IF', [query.if_expr, query.condition, query.else_expr], 'binop', ctx)


@_eval.register
def eval_Indirection(
        node: qlast.Indirection, ctx: EvalContext) -> Result:
    base = eval(node.arg, ctx)
    for index in node.indirection:
        index_out = (
            lift(slice)(eval(index.start, ctx) if index.start else [None],
                        eval(index.stop, ctx) if index.stop else [None])
            if isinstance(index, qlast.Slice)
            else eval(index.index, ctx)
        )
        base = lift(operator.getitem)(base, index_out)
    return base


@_eval.register
def eval_StringConstant(
        node: qlast.StringConstant, ctx: EvalContext) -> Result:
    return [node.value]


@_eval.register
def eval_IntegerConstant(
        node: qlast.IntegerConstant, ctx: EvalContext) -> Result:
    return [int(node.value) * (-1 if node.is_negative else 1)]


@_eval.register
def eval_BooleanConstant(
        node: qlast.BooleanConstant, ctx: EvalContext) -> Result:
    return [node.value == 'true']


@_eval.register
def eval_FloatConstant(
        node: qlast.FloatConstant, ctx: EvalContext) -> Result:
    return [float(node.value) * (-1 if node.is_negative else 1)]


@_eval.register
def eval_Set(
        node: qlast.Set, ctx: EvalContext) -> Result:
    out = []
    for elem in node.elements:
        out.extend(eval(elem, ctx))
    return out


@_eval.register
def eval_Tuple(
        node: qlast.Tuple, ctx: EvalContext) -> Result:
    args = [eval(arg, ctx) for arg in node.elements]
    return lift(lambda *va: va)(*args)


@_eval.register
def eval_Array(
        node: qlast.Array, ctx: EvalContext) -> Result:
    args = [eval(arg, ctx) for arg in node.elements]
    return lift(lambda *va: list(va))(*args)


@_eval.register
def eval_NamedTuple(
        node: qlast.NamedTuple, ctx: EvalContext) -> Result:
    names = [elem.name.name for elem in node.elements]
    args = [eval(arg.val, ctx) for arg in node.elements]
    return lift(lambda *va: dict(zip(names, va)))(*args)


@_eval.register
def eval_TypeCast(node: qlast.TypeCast, ctx: EvalContext) -> Result:
    typ = node.type.maintype.name  # type: ignore  # our types are hinky.
    f = BASIS_IMPLS['cast', typ]
    return f(eval(node.expr, ctx))


@_eval.register
def eval_Path(node: qlast.Path, ctx: EvalContext) -> Result:
    return eval_path(simplify_path(graft(ctx.cur_path, node)), ctx)


def eval(node: qlast.Base, ctx: EvalContext) -> Result:
    return _eval(node, ctx)

# Query setup


def get_links(obj: Data, key: str) -> Result:
    out = obj.get(key)
    if out is None:
        out = []
    if not isinstance(out, list):
        out = [out]
    return out


def eval_fwd_ptr(base: Data, ptr: IPtr, ctx: EvalContext) -> Result:
    if isinstance(base, tuple):
        return [base[int(ptr.ptr)]]
    elif isinstance(base, Obj):
        name = '@' + ptr.ptr if ptr.is_link_property else ptr.ptr
        if name in base.data:
            obj = base.data
        else:
            obj = ctx.db[base.id]
        return get_links(obj, name)
    else:
        return [base[ptr.ptr]]


def eval_bwd_ptr(base: Data, ptr: IPtr, ctx: EvalContext) -> Result:
    # XXX: This is slow even by the standards of this terribly slow model
    res = []
    for obj in ctx.db.values():
        for tgt in get_links(obj, ptr.ptr):
            if base == tgt:
                # Extract any lprops and put them on the backlink
                data = {k: v for k, v in tgt.data.items() if k[0] == '@'}
                res.append(Obj(obj['id'], data=data))
                break
    return res


def eval_ptr(base: Data, ptr: IPtr, ctx: EvalContext) -> Result:
    return (
        eval_bwd_ptr(base, ptr, ctx) if ptr.direction == '<'
        else eval_fwd_ptr(base, ptr, ctx))


def eval_intersect(
        base: Data, ptr: ITypeIntersection, ctx: EvalContext) -> Result:
    # TODO: we want actual types but for now we just match directly
    typ = ctx.db[base.id]["__type__"]
    return [base] if typ == ptr.typ else []


# This should only get called during input tuple building
def eval_objref(name: str, ctx: EvalContext) -> Result:
    if name in ctx.aliases:
        return ctx.aliases[name]

    return [
        Obj(obj["id"]) for obj in ctx.db.values()
        if obj["__type__"] == name
    ]


def last_index(vs: Sequence[T], x: T) -> int:
    for i in range(len(vs) - 1, -1, -1):
        if vs[i] == x:
            return i
    raise ValueError(f"{x} not in list")


def eval_path(path: IPath, ctx: EvalContext) -> Result:
    # Base case for stuff in the input list
    if path in ctx.query_input_list:
        # need last index, since there could be multiple IPrefixes or the like
        i = last_index(ctx.query_input_list, path)
        obj = ctx.input_tuple[i]
        return [obj] if obj is not None else []

    if len(path) == 1:
        if isinstance(path[0], IORef):
            return eval_objref(path[0].name, ctx)
        elif isinstance(path[0], IExpr):
            return eval(path[0].expr, ctx)
        else:
            raise AssertionError(f"Bogus path base: {path[0]}")

    base = eval_path(path[:-1], ctx)
    out = []
    ptr = path[-1]
    assert isinstance(ptr, (IPtr, ITypeIntersection))
    for obj in base:
        if isinstance(ptr, IPtr):
            out.extend(eval_ptr(obj, ptr, ctx))
        else:
            out.extend(eval_intersect(obj, ptr, ctx))
    # We need to deduplicate links.
    if base and isinstance(base[0], Obj) and out and isinstance(out[0], Obj):
        out = dedup(out)

    return out


def build_input_tuples(
        qil: List[IPath], always_optional: Dict[IPath, bool],
        ctx: EvalContext) -> List[Tuple[Data, ...]]:
    data: List[Tuple[Data, ...]] = [ctx.input_tuple]
    for i, in_path in enumerate(qil):
        new_data: List[Tuple[Data, ...]] = []
        new_qil = ctx.query_input_list + qil[:i]
        for row in data:
            subctx = replace(ctx, query_input_list=new_qil, input_tuple=row)
            out = eval_path(in_path, subctx)
            for val in out:
                new_data.append(row + (val,))
            if not out and always_optional[in_path]:
                new_data.append(row + (None,))
        data = new_data

    return data

# ############### Preparation


class PathFinder(NodeVisitor):
    def __init__(self, cur_path: Optional[qlast.Path]) -> None:
        super().__init__()
        self.in_optional = False
        self.optional_counter = 0
        self.in_subquery = False
        self.paths: List[Tuple[qlast.Path, Optional[int], bool]] = []
        self.current_path = cur_path

    def _update(self, **kwargs: Any) -> Iterator[None]:
        old = {k: getattr(self, k) for k in kwargs}
        for k, v in kwargs.items():
            setattr(self, k, v)
        try:
            yield
        finally:
            for k, v in old.items():
                setattr(self, k, v)

    @contextlib.contextmanager
    def subquery(self) -> Iterator[None]:
        yield from self._update(in_subquery=True)

    @contextlib.contextmanager
    def update_path(
            self, query: Optional[qlast.Expr]) -> Iterator[None]:
        yield from self._update(
            current_path=update_path(self.current_path, query))

    def visit_Path(self, path: qlast.Path, always_partial: bool=False) -> None:
        self.paths.append((
            graft(self.current_path, path, always_partial=always_partial),
            self.optional_counter if self.in_optional else None,
            self.in_subquery,
        ))
        self.generic_visit(path)

    def visit_SelectQuery(self, query: qlast.SelectQuery) -> None:
        with self.subquery():
            self.visit(query.result)

            with self.update_path(query):
                self.visit(query.orderby)
                self.visit(query.where)

            with self.update_path(None):
                self.visit(query.limit)
                self.visit(query.offset)

    def visit_Shape(self, shape: qlast.Shape) -> None:
        self.visit(shape.expr)
        with self.subquery(), self.update_path(shape.expr):
            self.visit(shape.elements)

    def visit_ShapeElement(self, el: qlast.ShapeElement) -> None:
        if not el.compexpr:
            self.visit_Path(el.expr, always_partial=True)
        self.visit(el.compexpr)
        with self.subquery(), self.update_path(el.expr):
            self.visit(el.elements)

    def visit_ForQuery(self, query: qlast.ForQuery) -> None:
        with self.subquery():
            self.generic_visit(query)

    def visit_func_or_op(self, op: str, args: List[qlast.Expr]) -> None:
        # Totally ignoring that polymorphic whatever is needed
        arg_specs = BASIS.get(op)
        old = self.in_optional, self.in_subquery
        for i, arg in enumerate(args):
            if arg_specs:
                # SET OF is a subquery so we skip it
                if arg_specs[i] == SET_OF:
                    self.in_subquery = True
                elif arg_specs[i] == OPTIONAL:
                    if not self.in_subquery and not self.in_optional:
                        self.optional_counter += 1
                    self.in_optional = True

            self.visit(arg)
            self.in_optional, self.in_subquery = old

    def visit_BinOp(self, query: qlast.BinOp) -> None:
        self.visit_func_or_op(query.op.upper(), [query.left, query.right])

    def visit_UnaryOp(self, query: qlast.UnaryOp) -> None:
        self.visit_func_or_op(query.op.upper(), [query.operand])

    def visit_FunctionCall(self, query: qlast.FunctionCall) -> None:
        assert not query.kwargs
        assert isinstance(query.func, str)
        self.visit_func_or_op(query.func, query.args)
        assert not query.window  # done last or we get dced.

    def visit_IfElse(self, query: qlast.IfElse) -> None:
        self.visit_func_or_op(
            'IF', [query.if_expr, query.condition, query.else_expr])

    def visit_DetachedExpr(self, query: qlast.DetachedExpr) -> None:
        pass


def find_paths(
    e: qlast.Expr,
    cur_path: Optional[qlast.Path],
    extra_subqs: Iterable[
        Tuple[Optional[qlast.Path], Optional[qlast.Base]]] = (),
) -> List[Tuple[qlast.Path, Optional[int], bool]]:
    pf = PathFinder(cur_path)
    pf.visit(e)
    pf.in_subquery = True
    for path, subq in extra_subqs:
        pf.current_path = path
        pf.visit(subq)
    return pf.paths


def longest_common_prefix(p1: IPath, p2: IPath) -> IPath:
    common = []
    for a, b in zip(p1, p2):
        if a == b:
            common.append(a)
        else:
            break
    return tuple(common)


def dedup(old: List[T]) -> List[T]:
    new: List[T] = []
    for x in old:
        if x not in new:
            new.append(x)
    return new


def find_common_prefixes(
    direct_refs: List[Tuple[IPath, Optional[int]]],
    subquery_refs: List[Tuple[IPath, Optional[int]]],
) -> Set[IPath]:
    prefixes = set()
    for i, (x, ox) in enumerate(direct_refs):
        # We start from only the refs directly in the query, but we
        # look for common prefixes with anything in subqueries also.
        # XXX: The docs are wrong and don't suggest this.
        for (y, oy) in direct_refs[i:] + subquery_refs:
            # XXX: Also optional stuff that the docs don't suggest
            if ox is not None and ox == oy:
                continue
            pfx = longest_common_prefix(x, y)
            if pfx:
                prefixes.add(pfx)
    return prefixes


def make_query_input_list(
    direct_refs: List[Tuple[IPath, Optional[int]]],
    subquery_refs: List[Tuple[IPath, Optional[int]]],
    old: List[IPath],
) -> List[IPath]:
    direct_refs = [x for x in direct_refs if isinstance(x[0][0], IORef)]
    qil = find_common_prefixes(direct_refs, subquery_refs)

    return sorted(x for x in qil if x not in old)


def simplify_path(path: qlast.Path) -> IPath:
    spath: List[IPathElement] = []
    # XXX: Could there still be some scoping issues with partial?
    # Probably yes? Maybe we need to make up fake path ids...
    if path.partial:
        spath.append(IPartial())
    for step in path.steps:
        if isinstance(step, qlast.ObjectRef):
            assert not spath
            spath.append(IORef(step.name))
        elif isinstance(step, qlast.Ptr):
            is_property = step.type == 'property'
            spath.append(IPtr(step.ptr.name, step.direction, is_property))
        elif isinstance(step, qlast.TypeIntersection):
            spath.append(ITypeIntersection(
                step.type.maintype.name))  # type: ignore
        else:
            assert not spath
            spath.append(IExpr(step))

    return tuple(spath)


def parse(querystr: str) -> qlast.Expr:
    source = edgeql.Source.from_string(querystr)
    statements = edgeql.parse_block(source)
    assert len(statements) == 1
    assert isinstance(statements[0], qlast.Expr)
    return statements[0]


def analyze_paths(
    q: qlast.Expr,
    extra_subqs: Iterable[
        Tuple[Optional[qlast.Path], Optional[qlast.Base]]],
    cur_path: Optional[qlast.Path],
) -> Tuple[
    List[Tuple[IPath, Optional[int]]],
    List[Tuple[IPath, Optional[int]]],
    Dict[IPath, bool],
]:
    paths_opt = [(simplify_path(p), optional, subq)
                 for p, optional, subq in find_paths(q, cur_path, extra_subqs)]

    # For any link property reference we see, strip off the
    # link and the link prop, and add that. So for Person.notes@name,
    # add Person to our input set too. This prevents us from
    # deduplicating the link before we access the prop.
    for path, optional, subquery in list(paths_opt):
        if isinstance(path[-1], IPtr) and path[-1].is_link_property:
            i = -2
            while isinstance(path[i], ITypeIntersection):
                i -= 1
            paths_opt.append((path[:i], optional, subquery))

    always_optional = defaultdict(lambda: True)

    direct_paths = []
    subquery_paths = []
    for path, optional, subquery in paths_opt:
        if subquery:
            subquery_paths.append((path, optional))
        else:
            direct_paths.append((path, optional))
            if not optional:
                # Mark all path prefixes as not being optional
                for i in range(1, len(path) + 1):
                    always_optional[path[:i]] = False

    return direct_paths, subquery_paths, always_optional


def subquery_full(
    q: qlast.Expr,
    *,
    extra_subqs: Iterable[
        Tuple[Optional[qlast.Path], Optional[qlast.Base]]] = (),
    ctx: EvalContext
) -> Tuple[List[IPath], List[Row]]:
    direct_paths, subquery_paths, always_optional = analyze_paths(
        q, extra_subqs, ctx.cur_path)

    qil = make_query_input_list(
        direct_paths, subquery_paths, ctx.query_input_list)

    in_tuples = build_input_tuples(qil, always_optional, ctx)

    # Actually eval it
    out = []
    new_qil = ctx.query_input_list + qil
    for row in in_tuples:
        subctx = replace(ctx, query_input_list=new_qil, input_tuple=row)
        for val in eval(q, subctx):
            out.append(row + (val,))

    return new_qil, out


def subquery(q: qlast.Expr, *, ctx: EvalContext) -> Result:
    return [row[-1] for row in subquery_full(q, ctx=ctx)[1]]


def toplevel_query(q: qlast.Expr, db: DB) -> Data:
    ctx = EvalContext(
        query_input_list=[],
        input_tuple=(),
        aliases={},
        cur_path=None,
        db=db,
    )
    return subquery(q, ctx=ctx)


def strip_shapes(x: Data) -> Data:
    if isinstance(x, Obj):
        return Obj(x.id, data=x.data, shape=None)
    elif isinstance(x, dict):
        return {k: strip_shapes(v) for k, v in x.items()}
    elif isinstance(x, tuple):
        return tuple(strip_shapes(v) for v in x)
    elif isinstance(x, list):
        return [strip_shapes(v) for v in x]
    else:
        return x


def clean_data(x: Data) -> Data:
    if isinstance(x, Obj):
        return clean_data(x.shape)
    elif isinstance(x, dict):
        return {k: clean_data(v) for k, v in x.items()}
    elif isinstance(x, tuple):
        return tuple(clean_data(v) for v in x)
    elif isinstance(x, list):
        return [clean_data(v) for v in x]
    else:
        return x


def go(q: qlast.Expr, db: DB) -> Data:
    return clean_data(toplevel_query(q, db))


class EdbJSONEncoder(json.JSONEncoder):
    def default(self, x: Any) -> Any:
        if isinstance(x, uuid.UUID):
            return str(x)
        return super().default(x)


def run(db: DB, s: str, print_asts: bool, output_mode: str) -> None:
    q = parse(s)
    if print_asts:
        debug.dump(q)
    res = go(q, db)
    if output_mode == 'pprint':
        pprint.pprint(res)
    elif output_mode == 'json':
        print(EdbJSONEncoder().encode(res))
    else:
        debug.dump(res)


def repl(db: DB, print_asts: bool=False, output_mode: str='debug') -> None:
    # for now users should just invoke this script with rlwrap since I
    # don't want to fiddle with history or anything
    while True:
        print("> ", end="", flush=True)
        s = ""
        while ';' not in s:
            s += sys.stdin.readline()
            if not s:
                return
        try:
            run(db, s, print_asts, output_mode)
        except Exception:
            traceback.print_exception(*sys.exc_info())


# Our toy DB
# Make this UUIDs?
# This is just documentation I guess.
SCHEMA = '''
type Note {
    required single property name -> str;
    optional single property note -> str;
}
type Person {
    required single property name -> str;
    optional multi property multi_prop -> str;
    multi link notes -> Note {
        property metanote -> str;
    }
    optional single property tag -> str;
}
type Foo {
    required single property val -> str;
    optional single property opt -> int64;
}
'''


def load_json_obj(obj: Any) -> Any:
    new_obj = {}
    for k, v in obj.items():
        if k == 'id':
            v = uuid.UUID(v)
        elif k == 'typ':
            k = '__type__'
            v = v.replace('test::', '')

        vs = v if isinstance(v, list) else [v]
        nvs = []
        for v1 in vs:
            if isinstance(v1, dict):
                lprops = {lk: lv for lk, lv in v1.items() if lk[0] == '@'}
                v1 = Obj(uuid.UUID(v1['id']), data=lprops)
            nvs.append(v1)
        nv = nvs if isinstance(v, list) else nvs[0]

        new_obj[k] = nv
    return new_obj


def load_json_db(data: Any) -> Any:
    return [load_json_obj(obj) for obj in data]


null: None = None
CARDS_DB = [
    {
        "avatar": {
            "@text": "Best",
            "id": "81537667-c308-11eb-98b8-e7ee6a203949"
        },
        "awards": [
            {"id": "81537661-c308-11eb-98b8-d7ab026ed715"},
            {"id": "81537663-c308-11eb-98b8-47f340f064e1"}
        ],
        "deck": [
            {"@count": 2, "id": "81537666-c308-11eb-98b8-67d1235c4527"},
            {"@count": 2, "id": "81537667-c308-11eb-98b8-e7ee6a203949"},
            {"@count": 3, "id": "81537668-c308-11eb-98b8-2b363efb8a80"},
            {"@count": 3, "id": "81537669-c308-11eb-98b8-c37076e778d2"}
        ],
        "friends": [
            {
                "@nickname": "Swampy",
                "id": "81537670-c308-11eb-98b8-d3d2e939fbfc"
            },
            {
                "@nickname": "Firefighter",
                "id": "81537671-c308-11eb-98b8-6b6a92e0be3e"
            },
            {
                "@nickname": "Grumpy",
                "id": "81537672-c308-11eb-98b8-53b70c263a56"
            }
        ],
        "id": "8153766f-c308-11eb-98b8-af7e8ffd99f3",
        "name": "Alice",
        "typ": "test::User"
    },
    {
        "avatar": null,
        "awards": [{"id": "81537665-c308-11eb-98b8-a7fee63c63ca"}],
        "deck": [
            {"@count": 3, "id": "81537668-c308-11eb-98b8-2b363efb8a80"},
            {"@count": 3, "id": "81537669-c308-11eb-98b8-c37076e778d2"},
            {"@count": 3, "id": "8153766a-c308-11eb-98b8-330dce42eb46"},
            {"@count": 3, "id": "8153766b-c308-11eb-98b8-430d489d7125"}
        ],
        "friends": [],
        "id": "81537670-c308-11eb-98b8-d3d2e939fbfc",
        "name": "Bob",
        "typ": "test::User"
    },
    {
        "avatar": null,
        "awards": [],
        "deck": [
            {"@count": 3, "id": "81537668-c308-11eb-98b8-2b363efb8a80"},
            {"@count": 2, "id": "81537669-c308-11eb-98b8-c37076e778d2"},
            {"@count": 4, "id": "8153766a-c308-11eb-98b8-330dce42eb46"},
            {"@count": 2, "id": "8153766b-c308-11eb-98b8-430d489d7125"},
            {"@count": 4, "id": "8153766c-c308-11eb-98b8-5bd98eec95bd"},
            {"@count": 3, "id": "8153766d-c308-11eb-98b8-8b072b1a5f69"},
            {"@count": 1, "id": "8153766e-c308-11eb-98b8-1b59432eef87"}
        ],
        "friends": [],
        "id": "81537671-c308-11eb-98b8-6b6a92e0be3e",
        "name": "Carol",
        "typ": "test::User"
    },
    {
        "avatar": null,
        "awards": [],
        "deck": [
            {"@count": 1, "id": "81537667-c308-11eb-98b8-e7ee6a203949"},
            {"@count": 1, "id": "81537668-c308-11eb-98b8-2b363efb8a80"},
            {"@count": 1, "id": "81537669-c308-11eb-98b8-c37076e778d2"},
            {"@count": 1, "id": "8153766b-c308-11eb-98b8-430d489d7125"},
            {"@count": 4, "id": "8153766c-c308-11eb-98b8-5bd98eec95bd"},
            {"@count": 1, "id": "8153766d-c308-11eb-98b8-8b072b1a5f69"},
            {"@count": 1, "id": "8153766e-c308-11eb-98b8-1b59432eef87"}
        ],
        "friends": [
            {"@nickname": null, "id": "81537670-c308-11eb-98b8-d3d2e939fbfc"}
        ],
        "id": "81537672-c308-11eb-98b8-53b70c263a56",
        "name": "Dave",
        "typ": "test::User"
    },
    {
        "awards": [{"id": "81537663-c308-11eb-98b8-47f340f064e1"}],
        "cost": 1,
        "element": "Fire",
        "id": "81537666-c308-11eb-98b8-67d1235c4527",
        "name": "Imp",
        "typ": "test::Card"
    },
    {
        "awards": [{"id": "81537661-c308-11eb-98b8-d7ab026ed715"}],
        "cost": 5,
        "element": "Fire",
        "id": "81537667-c308-11eb-98b8-e7ee6a203949",
        "name": "Dragon",
        "typ": "test::Card"
    },
    {
        "awards": [],
        "cost": 2,
        "element": "Water",
        "id": "81537668-c308-11eb-98b8-2b363efb8a80",
        "name": "Bog monster",
        "typ": "test::Card"
    },
    {
        "awards": [],
        "cost": 3,
        "element": "Water",
        "id": "81537669-c308-11eb-98b8-c37076e778d2",
        "name": "Giant turtle",
        "typ": "test::Card"
    },
    {
        "awards": [],
        "cost": 1,
        "element": "Earth",
        "id": "8153766a-c308-11eb-98b8-330dce42eb46",
        "name": "Dwarf",
        "typ": "test::Card"
    },
    {
        "awards": [],
        "cost": 3,
        "element": "Earth",
        "id": "8153766b-c308-11eb-98b8-430d489d7125",
        "name": "Golem",
        "typ": "test::Card"
    },
    {
        "awards": [],
        "cost": 1,
        "element": "Air",
        "id": "8153766c-c308-11eb-98b8-5bd98eec95bd",
        "name": "Sprite",
        "typ": "test::Card"
    },
    {
        "awards": [],
        "cost": 2,
        "element": "Air",
        "id": "8153766d-c308-11eb-98b8-8b072b1a5f69",
        "name": "Giant eagle",
        "typ": "test::Card"
    },
    {
        "awards": [{"id": "81537665-c308-11eb-98b8-a7fee63c63ca"}],
        "cost": 4,
        "element": "Air",
        "id": "8153766e-c308-11eb-98b8-1b59432eef87",
        "name": "Djinn",
        "typ": "test::Card"
        # "typ": "test::SpecialCard"
    },
    {
        "id": "81537661-c308-11eb-98b8-d7ab026ed715",
        "name": "1st",
        "typ": "test::Award"
    },
    {
        "id": "81537663-c308-11eb-98b8-47f340f064e1",
        "name": "2nd",
        "typ": "test::Award"
    },
    {
        "id": "81537665-c308-11eb-98b8-a7fee63c63ca",
        "name": "3rd",
        "typ": "test::Award"
    }
]


PersonT = "Person"
NoteT = "Note"
FooT = "Foo"
DB1 = mk_db([
    # VirtualObject
    {"id": bsid(0x01), "__type__": "VirtualObject"},

    # Person
    {"id": bsid(0x10), "__type__": PersonT,
     "name": "Phil Emarg",
     "notes": [bslink(0x20), bslink(0x21, metanote="arg!")]},
    {"id": bsid(0x11), "__type__": PersonT,
     "name": "Madeline Hatch", "notes": [bslink(0x21, metanote="sigh")]},
    {"id": bsid(0x12), "__type__": PersonT,
     "name": "Emmanuel Villip"},
    # Note
    {"id": bsid(0x20), "__type__": NoteT, "name": "boxing"},
    {"id": bsid(0x21), "__type__": NoteT, "name": "unboxing", "note": "lolol"},
    {"id": bsid(0x22), "__type__": NoteT, "name": "dynamic", "note": "blarg"},

    # Foo
    {"id": bsid(0x30), "__type__": FooT, "val": "a"},
    {"id": bsid(0x31), "__type__": FooT, "val": "b", "opt": 111},

    # volatility
    {"id": bsid(0x81), "__type__": "Tgt", "n": 1},
    {"id": bsid(0x82), "__type__": "Tgt", "n": 2},
    {"id": bsid(0x83), "__type__": "Tgt", "n": 3},
    {"id": bsid(0x84), "__type__": "Tgt", "n": 4},

    {"id": bsid(0x91), "__type__": "Obj", "n": 1,
     "tgt": [bslink(0x81), bslink(0x82)]},
    {"id": bsid(0x92), "__type__": "Obj", "n": 2,
     "tgt": [bslink(0x82), bslink(0x83)]},
    {"id": bsid(0x93), "__type__": "Obj", "n": 3,
     "tgt": [bslink(0x83), bslink(0x84)]},
] + load_json_db(CARDS_DB))

parser = argparse.ArgumentParser(description='Toy EdgeQL eval model')
parser.add_argument('--debug', '-d', action='store_true',
                    help='Dump ASTs after parsing')
parser.add_argument('--pprint', '-p', action='store_true',
                    help='Use pprint instead of debug.dump')
parser.add_argument('--json', '-j', action='store_true',
                    help='Use json.dump instead of debug.dump')

parser.add_argument('commands', metavar='cmd', type=str, nargs='*',
                    help='commands to run')


def main() -> None:
    db = DB1

    args = parser.parse_args()

    output_mode = 'json' if args.json else 'pprint' if args.pprint else 'debug'

    if args.commands:
        for arg in args.commands:
            run(db, arg, args.debug, output_mode)
    else:
        return repl(db, args.debug, output_mode)


if __name__ == '__main__':
    main()
