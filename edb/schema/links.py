#
# This source file is part of the EdgeDB open source project.
#
# Copyright 2008-present MagicStack Inc. and the EdgeDB authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


from __future__ import annotations

from typing import *

from edb.edgeql import ast as qlast
from edb.edgeql import qltypes

from edb import errors

from . import abc as s_abc
from . import constraints
from . import delta as sd
from . import indexes
from . import inheriting
from . import properties
from . import name as sn
from . import objects as so
from . import pointers
from . import referencing
from . import sources
from . import utils

if TYPE_CHECKING:
    from . import objtypes as s_objtypes
    from . import types as s_types
    from . import schema as s_schema


LinkTargetDeleteAction = qltypes.LinkTargetDeleteAction


def merge_actions(
    target: so.InheritingObject,
    sources: List[so.Object],
    field_name: str,
    *,
    ignore_local: bool = False,
    schema: s_schema.Schema,
) -> Any:
    if not ignore_local:
        ours = target.get_explicit_local_field_value(schema, field_name, None)
    else:
        ours = None
    if ours is None:
        current = None
        current_from = None

        for source in sources:
            theirs = source.get_explicit_field_value(schema, field_name, None)
            if theirs is not None:
                if current is None:
                    current = theirs
                    current_from = source
                elif current != theirs:
                    target_source = target.get_source(schema)
                    current_from_source = current_from.get_source(schema)
                    source_source = source.get_source(schema)

                    tgt_repr = (
                        f'{target_source.get_displayname(schema)}.'
                        f'{target.get_displayname(schema)}'
                    )
                    cf_repr = (
                        f'{current_from_source.get_displayname(schema)}.'
                        f'{current_from.get_displayname(schema)}'
                    )
                    other_repr = (
                        f'{source_source.get_displayname(schema)}.'
                        f'{source.get_displayname(schema)}'
                    )

                    raise errors.SchemaError(
                        f'cannot implicitly resolve the '
                        f'`on target delete` action for '
                        f'{tgt_repr!r}: it is defined as {current} in '
                        f'{cf_repr!r} and as {theirs} in {other_repr!r}; '
                        f'to resolve, declare `on target delete` '
                        f'explicitly on {tgt_repr!r}'
                    )
        return current
    else:
        return ours


class Link(
    sources.Source,
    pointers.Pointer,
    s_abc.Link,
    qlkind=qltypes.SchemaObjectClass.LINK,
    data_safe=False,
):

    on_target_delete = so.SchemaField(
        LinkTargetDeleteAction,
        default=LinkTargetDeleteAction.Restrict,
        coerce=True,
        compcoef=0.9,
        merge_fn=merge_actions)

    def get_target(self, schema: s_schema.Schema) -> s_objtypes.ObjectType:
        return self.get_field_value(  # type: ignore[no-any-return]
            schema, 'target')

    def is_link_property(self, schema: s_schema.Schema) -> bool:
        return False

    def is_property(self, schema: s_schema.Schema) -> bool:
        return False

    def scalar(self) -> bool:
        return False

    def has_user_defined_properties(self, schema: s_schema.Schema) -> bool:
        return bool([p for p in self.get_pointers(schema).objects(schema)
                     if not p.is_special_pointer(schema)])

    def get_source_type(
        self,
        schema: s_schema.Schema
    ) -> s_types.Type:
        from . import types as s_types
        source = self.get_source(schema)
        assert isinstance(source, s_types.Type)
        return source

    def compare(
        self,
        other: so.Object,
        *,
        our_schema: s_schema.Schema,
        their_schema: s_schema.Schema,
        context: so.ComparisonContext,
    ) -> float:
        if not isinstance(other, Link):
            if isinstance(other, pointers.Pointer):
                return 0.0
            else:
                raise NotImplementedError()

        return super().compare(
            other, our_schema=our_schema,
            their_schema=their_schema, context=context)

    def set_target(
        self,
        schema: s_schema.Schema,
        target: s_types.Type,
    ) -> s_schema.Schema:
        schema = super().set_target(schema, target)
        tgt_prop = self.getptr(schema, sn.UnqualName('target'))
        schema = tgt_prop.set_target(schema, target)
        return schema

    @classmethod
    def get_root_classes(cls) -> Tuple[sn.QualName, ...]:
        return (
            sn.QualName(module='std', name='link'),
            sn.QualName(module='schema', name='__type__'),
        )

    @classmethod
    def get_default_base_name(self) -> sn.QualName:
        return sn.QualName('std', 'link')


class LinkSourceCommandContext(sources.SourceCommandContext):
    pass


class LinkSourceCommand(inheriting.InheritingObjectCommand[sources.Source_T]):
    pass


class LinkCommandContext(pointers.PointerCommandContext[Link],
                         constraints.ConsistencySubjectCommandContext,
                         properties.PropertySourceContext,
                         indexes.IndexSourceCommandContext):
    pass


class LinkCommand(
    properties.PropertySourceCommand[Link],
    pointers.PointerCommand[Link],
    context_class=LinkCommandContext,
    referrer_context_class=LinkSourceCommandContext,
):

    def _append_subcmd_ast(
        self,
        schema: s_schema.Schema,
        node: qlast.DDLOperation,
        subcmd: sd.Command,
        context: sd.CommandContext,
    ) -> None:
        if (
            isinstance(subcmd, pointers.PointerCommand)
            and subcmd.classname != self.classname

        ):
            pname = sn.shortname_from_fullname(subcmd.classname)
            if pname.name in {'source', 'target'}:
                return

        super()._append_subcmd_ast(schema, node, subcmd, context)

    def validate_object(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
    ) -> None:
        """Check that link definition is sound."""
        super().validate_object(schema, context)

        scls = self.scls
        assert isinstance(scls, Link)

        if not scls.get_owned(schema):
            return

        target = scls.get_target(schema)
        assert target is not None

        if not target.is_object_type():
            srcctx = self.get_attribute_source_context('target')
            raise errors.InvalidLinkTargetError(
                f'invalid link target, expected object type, got '
                f'{target.get_schema_class_displayname()}',
                context=srcctx,
            )

        if (
            not scls.is_pure_computable(schema)
            and not scls.get_from_alias(schema)
            and target.is_view(schema)
        ):
            srcctx = self.get_attribute_source_context('target')
            raise errors.InvalidLinkTargetError(
                f'invalid link type: {target.get_displayname(schema)!r}'
                f' is an expression alias, not a proper object type',
                context=srcctx,
            )

    def _get_ast(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
        *,
        parent_node: Optional[qlast.DDLOperation] = None,
    ) -> Optional[qlast.DDLOperation]:
        node = super()._get_ast(schema, context, parent_node=parent_node)
        # __type__ link is special, and while it exists on every object
        # it does not have a defined default in the schema (and therefore
        # it isn't marked as required.)  We intervene here to mark all
        # __type__ links required when rendering for SDL/TEXT.
        if context.declarative and node is not None:
            assert isinstance(node, (qlast.CreateConcreteLink,
                                     qlast.CreateLink))
            if node.name.name == '__type__':
                assert isinstance(node, qlast.CreateConcretePointer)
                node.is_required = True
        return node

    def _reinherit_classref_dict(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
        refdict: so.RefDict,
    ) -> Tuple[s_schema.Schema,
               Dict[sn.Name, Type[sd.ObjectCommand[so.Object]]]]:
        if self.scls.get_computable(schema) and refdict.attr != 'pointers':
            # If the link is a computable, the inheritance would only
            # happen in the case of aliasing, and in that case we only
            # need to inherit the link properties and nothing else.
            return schema, {}

        return super()._reinherit_classref_dict(schema, context, refdict)


class CreateLink(
    pointers.CreatePointer[Link],
    LinkCommand,
):
    astnode = [qlast.CreateConcreteLink, qlast.CreateLink]
    referenced_astnode = qlast.CreateConcreteLink

    @classmethod
    def _cmd_tree_from_ast(
        cls,
        schema: s_schema.Schema,
        astnode: qlast.DDLOperation,
        context: sd.CommandContext,
    ) -> sd.Command:
        cmd = super()._cmd_tree_from_ast(schema, astnode, context)
        if isinstance(astnode, qlast.CreateConcreteLink):
            assert isinstance(cmd, pointers.PointerCommand)
            cmd._process_create_or_alter_ast(schema, astnode, context)
        else:
            # this is an abstract property then
            if cmd.get_attribute_value('default') is not None:
                raise errors.SchemaDefinitionError(
                    f"'default' is not a valid field for an abstract link",
                    context=astnode.context)
        assert isinstance(cmd, sd.Command)
        return cmd

    def get_ast_attr_for_field(
        self,
        field: str,
        astnode: Type[qlast.DDLOperation],
    ) -> Optional[str]:
        if (
            field == 'required'
            and issubclass(astnode, qlast.CreateConcreteLink)
        ):
            return 'is_required'
        elif (
            field == 'cardinality'
            and issubclass(astnode, qlast.CreateConcreteLink)
        ):
            return 'cardinality'
        else:
            return super().get_ast_attr_for_field(field, astnode)

    def _apply_field_ast(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
        node: qlast.DDLOperation,
        op: sd.AlterObjectProperty,
    ) -> None:
        objtype = self.get_referrer_context(context)

        if op.property == 'target' and objtype:
            # Due to how SDL is processed the underlying AST may be an
            # AlterConcreteLink, which requires different handling.
            if isinstance(node, qlast.CreateConcreteLink):
                if not node.target:
                    expr = self.get_attribute_value('expr')
                    if expr is not None:
                        node.target = expr.qlast
                    else:
                        t = op.new_value
                        assert isinstance(t, (so.Object, so.ObjectShell))
                        node.target = utils.typeref_to_ast(schema, t)
            else:
                assert isinstance(op.new_value, (so.Object, so.ObjectShell))
                node.commands.append(
                    qlast.SetPointerType(
                        value=utils.typeref_to_ast(schema, op.new_value),
                    )
                )
        elif op.property == 'on_target_delete':
            node.commands.append(qlast.OnTargetDelete(cascade=op.new_value))
        else:
            super()._apply_field_ast(schema, context, node, op)

    def inherit_classref_dict(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
        refdict: so.RefDict,
    ) -> sd.CommandGroup:
        if self.scls.get_computable(schema) and refdict.attr != 'pointers':
            # If the link is a computable, the inheritance would only
            # happen in the case of aliasing, and in that case we only
            # need to inherit the link properties and nothing else.
            return sd.CommandGroup()

        cmd = super().inherit_classref_dict(schema, context, refdict)

        if refdict.attr != 'pointers':
            return cmd

        parent_ctx = self.get_referrer_context(context)
        if parent_ctx is None:
            return cmd

        base_prop_name = sn.QualName('std', 'source')
        s_name = sn.get_specialized_name(
            sn.QualName('__', 'source'), str(self.classname))
        src_prop_name = sn.QualName(
            name=s_name, module=self.classname.module)

        src_prop = properties.CreateProperty(
            classname=src_prop_name,
            is_strong_ref=True,
        )
        src_prop.set_attribute_value('name', src_prop_name)
        src_prop.set_attribute_value(
            'bases',
            so.ObjectList.create(schema, [schema.get(base_prop_name)]),
        )
        src_prop.set_attribute_value(
            'source',
            self.scls,
        )
        src_prop.set_attribute_value(
            'target',
            parent_ctx.op.scls,
        )
        src_prop.set_attribute_value('required', True)
        src_prop.set_attribute_value('readonly', True)
        src_prop.set_attribute_value('final', True)
        src_prop.set_attribute_value('owned', True)
        src_prop.set_attribute_value('from_alias',
                                     self.scls.get_from_alias(schema))
        src_prop.set_attribute_value('cardinality',
                                     qltypes.SchemaCardinality.One)

        cmd.prepend(src_prop)

        base_prop_name = sn.QualName('std', 'target')
        s_name = sn.get_specialized_name(
            sn.QualName('__', 'target'), str(self.classname))
        tgt_prop_name = sn.QualName(
            name=s_name, module=self.classname.module)

        tgt_prop = properties.CreateProperty(
            classname=tgt_prop_name,
            is_strong_ref=True,
        )

        tgt_prop.set_attribute_value('name', tgt_prop_name)
        tgt_prop.set_attribute_value(
            'bases',
            so.ObjectList.create(schema, [schema.get(base_prop_name)]),
        )
        tgt_prop.set_attribute_value(
            'source',
            self.scls,
        )
        tgt_prop.set_attribute_value(
            'target',
            self.get_attribute_value('target'),
        )
        tgt_prop.set_attribute_value('required', False)
        tgt_prop.set_attribute_value('readonly', True)
        tgt_prop.set_attribute_value('final', True)
        tgt_prop.set_attribute_value('owned', True)
        tgt_prop.set_attribute_value('from_alias',
                                     self.scls.get_from_alias(schema))
        tgt_prop.set_attribute_value('cardinality',
                                     qltypes.SchemaCardinality.One)

        cmd.prepend(tgt_prop)

        return cmd


class RenameLink(
    LinkCommand,
    referencing.RenameReferencedInheritingObject[Link],
):
    pass


class RebaseLink(
    LinkCommand,
    referencing.RebaseReferencedInheritingObject[Link],
):
    pass


class SetLinkType(
    pointers.SetPointerType[Link],
    referrer_context_class=LinkSourceCommandContext,
    field='target',
):

    def _alter_begin(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
    ) -> s_schema.Schema:
        schema = super()._alter_begin(schema, context)
        scls = self.scls

        new_target = scls.get_target(schema)

        if not context.canonical:
            # We need to update the target link prop as well
            tgt_prop = scls.getptr(schema, sn.UnqualName('target'))
            tgt_prop_alter = tgt_prop.init_delta_command(
                schema, sd.AlterObject)
            tgt_prop_alter.set_attribute_value('target', new_target)
            self.add(tgt_prop_alter)

        return schema


class AlterLinkUpperCardinality(
    pointers.AlterPointerUpperCardinality[Link],
    referrer_context_class=LinkSourceCommandContext,
    field='cardinality',
):
    pass


class AlterLinkLowerCardinality(
    pointers.AlterPointerLowerCardinality[Link],
    referrer_context_class=LinkSourceCommandContext,
    field='required',
):
    pass


class AlterLinkOwned(
    referencing.AlterOwned[Link],
    pointers.PointerCommandOrFragment[Link],
    referrer_context_class=LinkSourceCommandContext,
    field='owned',
):
    pass


class SetTargetDeletePolicy(sd.Command):
    astnode = qlast.OnTargetDelete

    @classmethod
    def _cmd_from_ast(
        cls,
        schema: s_schema.Schema,
        astnode: qlast.DDLOperation,
        context: sd.CommandContext,
    ) -> sd.AlterObjectProperty:
        return sd.AlterObjectProperty(
            property='on_target_delete'
        )

    @classmethod
    def _cmd_tree_from_ast(
        cls,
        schema: s_schema.Schema,
        astnode: qlast.DDLOperation,
        context: sd.CommandContext,
    ) -> sd.Command:
        assert isinstance(astnode, qlast.OnTargetDelete)
        cmd = super()._cmd_tree_from_ast(schema, astnode, context)
        assert isinstance(cmd, sd.AlterObjectProperty)
        cmd.new_value = astnode.cascade
        return cmd


class AlterLink(
    LinkCommand,
    pointers.AlterPointer[Link],
):
    astnode = [qlast.AlterConcreteLink, qlast.AlterLink]
    referenced_astnode = qlast.AlterConcreteLink

    @classmethod
    def _cmd_tree_from_ast(
        cls,
        schema: s_schema.Schema,
        astnode: qlast.DDLOperation,
        context: sd.CommandContext,
    ) -> AlterLink:
        cmd = super()._cmd_tree_from_ast(schema, astnode, context)
        assert isinstance(cmd, AlterLink)
        if isinstance(astnode, qlast.CreateConcreteLink):
            cmd._process_create_or_alter_ast(schema, astnode, context)
        else:
            cmd._process_alter_ast(schema, astnode, context)
        return cmd

    def _apply_field_ast(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
        node: qlast.DDLOperation,
        op: sd.AlterObjectProperty,
    ) -> None:
        if op.property == 'target':
            if op.new_value:
                assert isinstance(op.new_value, so.ObjectShell)
                node.commands.append(
                    qlast.SetPointerType(
                        value=utils.typeref_to_ast(schema, op.new_value),
                    ),
                )
        elif op.property == 'computable':
            if not op.new_value:
                node.commands.append(
                    qlast.SetField(
                        name='expr',
                        value=None,
                        special_syntax=True,
                    ),
                )
        elif op.property == 'on_target_delete':
            node.commands.append(qlast.OnTargetDelete(cascade=op.new_value))
        else:
            super()._apply_field_ast(schema, context, node, op)


class DeleteLink(
    LinkCommand,
    pointers.DeletePointer[Link],
):
    astnode = [qlast.DropConcreteLink, qlast.DropLink]
    referenced_astnode = qlast.DropConcreteLink

    # NB: target type cleanup (e.g. target compound type) is done by
    #     the DeleteProperty handler for the @target property.

    def _get_ast(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
        *,
        parent_node: Optional[qlast.DDLOperation] = None,
    ) -> Optional[qlast.DDLOperation]:
        if self.get_orig_attribute_value('from_alias'):
            # This is an alias type, appropriate DDL would be generated
            # from the corresponding Alter/DeleteAlias node.
            return None
        else:
            return super()._get_ast(schema, context, parent_node=parent_node)
