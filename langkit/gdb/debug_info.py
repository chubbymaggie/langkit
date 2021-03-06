"""
Data structures for mapping from generated library source lines to the
properties DSL level.
"""

from __future__ import absolute_import, division, print_function

import inspect
import shlex

import gdb

from langkit.gdb.state import Binding, ExpressionEvaluation


class ParseError(Exception):
    def __init__(self, line_no, message):
        super(ParseError, self).__init__(
            'line {}: {}'.format(line_no, message)
        )


class DebugInfo(object):
    """
    Holder for all info that maps generated code to the properties DSL level.
    """

    def __init__(self, context):
        self.context = context

        self.filename = None
        """
        :type: str|None

        Absolute path for the "$-analysis.adb" file, or None if we haven't
        found it.
        """

        self.properties = []
        """
        :type: list[Property]
        """

        self.properties_dict = {}
        """
        Name-based lookup dictionnary for properties.

        :type: dict[str, Property]
        """

    @classmethod
    def parse(cls, context):
        """
        Try to parse the $-analysis.adb source file to extract mapping
        information from its GDB helpers directives. Print error messages on
        standard output if anything goes wrong, but always return a DebugInfo
        instance anyway.

        :rtype: DebugInfo
        """

        result = cls(context)

        # Look for the "$-analysis.adb" file using some symbol that is supposed
        # to be defined there.
        has_unit_sym = gdb.lookup_global_symbol(
            '{}__analysis__implementation__is_null'.format(context.lib_name)
        )
        if not has_unit_sym:
            return result

        result.filename = has_unit_sym.symtab.fullname()
        with open(result.filename, 'r') as f:
            try:
                cls._parse_file(result, f)
            except ParseError as exc:
                print('Error while parsing directives in {}:'.format(
                    result.filename
                ))
                print(str(exc))

        return result

    def _parse_file(self, f):
        """
        Internal method. Read GDB helpers directives from the "f" source file
        and fill self according to it. Raise a ParseError if anything goes
        wrong.

        :param file f: Readable file for the $-analysis.adb source file.
        :rtype: None
        """
        self.properties = []
        self.properties_dict = {}
        scope_stack = []
        expr_stack = []

        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line.startswith('--#'):
                continue
            line = line[3:].strip()
            args = shlex.split(line)

            try:
                name = args.pop(0)
            except IndexError:
                raise ParseError(line_no, 'directive name is missing')

            d = Directive.parse(line_no, name, args)

            if d.is_a(PropertyStart):
                if scope_stack:
                    raise ParseError(line_no, 'property-start directive not'
                                     ' allowed inside another property')
                p = Property(LineRange(d.line_no, None), d.name, d.dsl_sloc,
                             d.is_dispatcher)
                self.properties.append(p)
                self.properties_dict[p.name] = p
                scope_stack.append(p)

            elif d.is_a(ScopeStart, PropertyCallStart,
                        MemoizationLookupDirective):
                if not scope_stack or not isinstance(scope_stack[-1], Scope):
                    raise ParseError(
                        line_no,
                        '{} directive must occur inside a property or a'
                        ' property scope'.format(d.directive_name)
                    )

                line_range = LineRange(d.line_no, None)

                if d.is_a(MemoizationLookupDirective):
                    new_scope = MemoizationLookup(line_range)
                elif d.is_a(PropertyCallStart):
                    new_scope = PropertyCall(line_range, d.name)
                else:
                    assert d.is_a(ScopeStart)
                    new_scope = Scope(line_range)

                scope_stack.append(new_scope)

            elif d.is_a(End):
                if not scope_stack:
                    raise ParseError(line_no, 'no scope to end')
                ended_scope = scope_stack.pop()
                ended_scope.line_range.last_line = d.line_no
                if scope_stack:
                    scope_stack[-1].events.append(ended_scope)
                else:
                    assert isinstance(ended_scope, Property), (
                        'Top-level scopes must all be properties'
                    )
                    assert not expr_stack, (
                        'Some expressions are not done when leaving property'
                        ' {}: {}'.format(
                            ended_scope.name,
                            ', '.join(expr_stack)
                        )
                    )

            elif d.is_a(BindDirective):
                if not scope_stack:
                    raise ParseError(line_no, 'no scope for binding')
                scope_stack[-1].events.append(Bind(d.line_no, d.dsl_name,
                                                   d.gen_name))

            elif d.is_a(ExprStartDirective):
                if not scope_stack:
                    raise ParseError(line_no, 'no scope for expression')
                start_event = ExprStart(d.line_no, d.expr_id, d.expr_repr,
                                        d.result_var, d.dsl_sloc)
                scope_stack[-1].events.append(start_event)
                if expr_stack:
                    expr_stack[-1].sub_expr_start.append(start_event)
                expr_stack.append(start_event)

            elif d.is_a(ExprDoneDirective):
                if not scope_stack:
                    raise ParseError(line_no, 'no scope for expression')
                done_event = ExprDone(d.line_no, d.expr_id)
                start_event = expr_stack.pop()
                assert start_event.expr_id == done_event.expr_id, (
                    'Mismatching ExprStart/ExprStop events: {} and {}'.format(
                        start_event, done_event
                    )
                )
                start_event._done_event = done_event
                scope_stack[-1].events.append(done_event)

            elif d.is_a(MemoizationReturnDirective):
                if (not scope_stack or
                        not isinstance(scope_stack[-1], MemoizationLookup)):
                    raise ParseError(
                        line_no,
                        'memoization-result directive must appear inside a'
                        ' memoization-lookup scope'
                    )
                scope_stack[-1].events.append(d)

            elif d.is_a(PropertyBodyStart):
                if not scope_stack:
                    raise ParseError(
                        line_no,
                        'property-body-start directive must appear inside a'
                        ' property'
                    )
                scope_stack[0].body_start = d.line_no

            else:
                raise NotImplementedError('Unknown directive: {}'.format(d))

        if scope_stack:
            raise ParseError(line_no, 'end of scope expected before end of'
                                      ' file')

    def lookup_property(self, line_no):
        """
        Look for a property that covers the given source line number. Return
        None if there is no such property.

        :param int line_no: Line number to lookup.
        :rtype: None|Property
        """
        for p in self.properties:
            if line_no < p.line_range.first_line:
                break
            elif line_no in p.line_range:
                return p

    def get_property_by_name(self, name):
        """
        Fetch the property called `name`. Raise an error if not found.

        :param str name: Name of the property to fetch.
        :rtype: Property
        """
        return self.properties_dict[name]


class DSLLocation(object):
    """
    Source location in the DSL.
    """

    def __init__(self, filename, line_no):
        self.filename = filename
        self.line_no = line_no

    @classmethod
    def parse(cls, dsl_sloc):
        """
        If `dsl_sloc` is "None", return None. Otherwise, create a DSLLocation
        instance out of a string of the form "filename:line_no".

        :type dsl_sloc: str
        :rtype: DSLLocation
        """
        if dsl_sloc == 'None':
            return None

        try:
            filename, line_no = dsl_sloc.split(':', 1)
        except ValueError:
            raise ValueError('Invalid DSL location: {}'.format(dsl_sloc))

        try:
            line_no = int(line_no)
        except ValueError:
            raise ValueError('Invalid line number: {}'.format(line_no))

        return cls(filename, line_no)

    def matches(self, other):
        """
        Return whether `self` designates a file at least as much specifically
        as `other`. In practice, both must have the same line number and the
        filename from `other` must be a suffix for the one in `self`.

        :rtype: bool
        """
        return (self.line_no == other.line_no
                and self.filename.endswith(other.filename))

    def __str__(self):
        return '{}:{}'.format(self.filename, self.line_no)

    def __repr__(self):
        return '<DSLLocation {}>'.format(self)


class LineRange(object):
    """
    Range of lines in the $-analysis.adb source file.
    """

    def __init__(self, first_line, last_line):
        self.first_line = first_line
        self.last_line = last_line

    def __contains__(self, line_no):
        assert isinstance(line_no, int)
        return self.first_line <= line_no <= self.last_line

    def __repr__(self):
        return '<LineRange {}>'.format(str(self))

    def __str__(self):
        return '{}-{}'.format(self.first_line, self.last_line)


class BaseEvent(object):
    pass


class Scope(BaseEvent):
    def __init__(self, line_range, label=None):
        self.line_range = line_range
        self.label = label
        self.events = []

    @property
    def subscopes(self):
        return [e for e in self.events if isinstance(e, Scope)]

    def iter_events(self, recursive=True, filter=None):
        """
        Iterate through all events in this scope and, if `recursive`, all
        sub-scopes. Events are yielded in source order.

        :param filter: If left to None, don't filter the results. If it's a
            class, return only instances of this class. Otherwise, treat it as
            a predicate function and return only items for which the predicate
            returns true.

        :rtype: iter[BaseEvent]
        """

        def predicate(e):
            if filter is None:
                return True
            elif inspect.isclass(filter):
                return isinstance(e, filter)
            else:
                return filter(e)

        for e in self.events:
            if isinstance(e, Scope):
                if predicate(e):
                    yield e
                if recursive:
                    for sub_e in e.iter_events(filter=filter):
                        yield sub_e
            else:
                if predicate(e):
                    yield e

    def __repr__(self):
        return '<{}{} {}>'.format(
            type(self).__name__,
            ' {}'.format(self.label) if self.label else '',
            self.line_range
        )


class PropertyCall(object):
    def __init__(self, line_range, name):
        self.line_range = line_range
        self.name = name

        self.body_start = None
        """
        Line number where to put breakpoints for the beginning of this
        property, or None if this property has no code.

        :type: int|None
        """

    def property(self, context):
        """
        Look for the property that this property call targets.

        :rtype: Property
        """
        return context.debug_info.get_property_by_name(self.name)


class Property(Scope):
    def __init__(self, line_range, name, dsl_sloc, is_dispatcher):
        super(Property, self).__init__(line_range, name)
        self.name = name
        self.dsl_sloc = dsl_sloc
        self.is_dispatcher = is_dispatcher

    @property
    def memoization_lookup(self):
        """
        Return None if this property is not memoized. Otherwise, return the
        special scope that covers code that is about to return based on a
        memoization cached result. To be used when putting breakpoints at the
        beginning of properties.

        :rtype: MemoizationLookup|None
        """
        for e in self.events:
            if isinstance(e, MemoizationLookup):
                return e
        return None


class Event(BaseEvent):
    def __init__(self, line_no, entity=None):
        self.line_no = line_no
        self.entity = entity

    def apply_on_state(self, scope_state):
        """
        Modify the input state according to the effect this event has. All
        subclasses must override this.

        :type scope_state: langkit.gdb.state.ScopeState
        """
        raise NotImplementedError()

    def __repr__(self):
        return '<Event line {}>'.format(self.line_no)


class Bind(Event):
    def __init__(self, line_no, dsl_name, gen_name):
        super(Bind, self).__init__(line_no, dsl_name)
        self.dsl_name = dsl_name
        self.gen_name = gen_name

    def apply_on_state(self, scope_state):
        scope_state.bindings.append(
            Binding(self.dsl_name, self.gen_name)
        )

    def __repr__(self):
        return '<Bind {}, line {}>'.format(self.dsl_name, self.line_no)


class ExprStart(Event):
    def __init__(self, line_no, expr_id, expr_repr, result_var, dsl_sloc):
        super(ExprStart, self).__init__(line_no)
        self.expr_id = expr_id
        self.expr_repr = expr_repr
        self.result_var = result_var
        self.dsl_sloc = None if dsl_sloc == 'None' else dsl_sloc

        self.sub_expr_start = []
        """
        :type: list[ExprStart]

        List of ExprStart events that occur before the ExprDone corresponding
        to `self`.
        """

        self._done_event = None
        """
        :type: ExprDone

        Done event corresponding to this ExprStart event.
        """

    @property
    def done_event(self):
        """
        Return the ExprDone event that corresponds to `self`.

        :rtype: ExprDone
        """
        assert self._done_event
        return self._done_event

    @property
    def line_range(self):
        """
        Return the line range that spans from this ExprStart event to the
        corresponding ExprDone one.

        :rtype: LineRange
        """
        return LineRange(self.line_no, self.done_event.line_no)

    def apply_on_state(self, scope_state):
        assert self.expr_id not in scope_state.expressions
        expr = ExpressionEvaluation(self)
        scope_state.expressions[self.expr_id] = expr
        if scope_state.state.started_expressions:
            scope_state.state.started_expressions[-1].append_sub_expr(expr)
        scope_state.state.started_expressions.append(expr)

    def __repr__(self):
        return '<ExprStart {}, line {}>'.format(self.expr_id, self.line_no)


class ExprDone(Event):
    def __init__(self, line_no, expr_id):
        super(ExprDone, self).__init__(line_no)
        self.expr_id = expr_id

    def apply_on_state(self, scope_state):
        expr = scope_state.expressions[self.expr_id]

        pop = scope_state.state.started_expressions.pop()
        assert pop == expr, (
            'Expressions are not properly nested: {} is done before {}'
            ' is'.format(expr, pop)
        )
        expr.set_done(self.line_no)

    def __repr__(self):
        return '<ExprDone {}, line {}>'.format(self.expr_id, self.line_no)


class MemoizationLookup(Scope):
    pass


class MemoizationReturn(Event):
    def apply_on_state(self, scope_state):
        pass

    def __repr__(self):
        return '<MemoizationReturn, line {}>'.format(self.line_no)


class Directive(object):
    """
    Holder for GDB helper directives as parsed from source files.
    """

    name_to_cls = {}

    def __init__(self, line_no):
        self.line_no = line_no

    def is_a(self, *classes):
        return isinstance(self, classes)

    @property
    def directive_name(self):
        """
        Return the name of this directive.

        :rtype: str
        """
        for name, dir_type in self.name_to_cls.items():
            if isinstance(self, dir_type):
                return name
        raise KeyError('Unknown directive: {}'.format(self))

    @classmethod
    def parse(cls, line_no, name, args):
        """
        Try to parse a GDB helper directive. Raise a ParseError if anything
        goes wrong.

        :param int line_no: Line number on which this directive appears in the
            source file.
        :param str name: Name of the directive.
        :param list[str] args: Arguments for this directive.
        :rtype: Directive
        """
        try:
            subcls = cls.name_to_cls[name]
        except KeyError:
            raise ParseError(line_no, 'invalid directive: {}'.format(name))
        return subcls.parse(line_no, args)


class PropertyStart(Directive):
    def __init__(self, name, dsl_sloc, is_dispatcher, line_no):
        super(PropertyStart, self).__init__(line_no)
        self.name = name
        self.dsl_sloc = dsl_sloc
        self.is_dispatcher = is_dispatcher

    @classmethod
    def parse(cls, line_no, args):
        name, info = args
        if info == 'dispatcher':
            is_dispatcher = True
            dsl_sloc = None
        else:
            is_dispatcher = False
            dsl_sloc = DSLLocation.parse(info)
        return cls(name, dsl_sloc, is_dispatcher, line_no)


class PropertyBodyStart(Directive):
    @classmethod
    def parse(cls, line_no, args):
        assert len(args) == 0
        return cls(line_no)


class PropertyCallStart(Directive):
    def __init__(self, name, line_no):
        super(PropertyCallStart, self).__init__(line_no)
        self.name = name

    @classmethod
    def parse(cls, line_no, args):
        name, = args
        return cls(name, line_no)


class MemoizationLookupDirective(Directive):
    @classmethod
    def parse(cls, line_no, args):
        assert len(args) == 0
        return cls(line_no)


class MemoizationReturnDirective(Directive):
    @classmethod
    def parse(cls, line_no, args):
        assert len(args) == 0
        return cls(line_no)


class ScopeStart(Directive):
    @classmethod
    def parse(cls, line_no, args):
        return cls(line_no)


class BindDirective(Directive):
    def __init__(self, dsl_name, gen_name, line_no):
        super(BindDirective, self).__init__(line_no)
        self.dsl_name = dsl_name
        self.gen_name = gen_name

    @classmethod
    def parse(cls, line_no, args):
        dsl_name, gen_name = args
        return cls(dsl_name, gen_name, line_no)


class End(Directive):
    @classmethod
    def parse(cls, line_no, args):
        return cls(line_no)


class ExprStartDirective(Directive):
    def __init__(self, expr_id, expr_repr, result_var, dsl_sloc, line_no):
        super(ExprStartDirective, self).__init__(line_no)
        self.expr_id = expr_id
        self.expr_repr = expr_repr
        self.result_var = result_var
        self.dsl_sloc = dsl_sloc

    @classmethod
    def parse(cls, line_no, args):
        expr_id, expr_repr, result_var, dsl_sloc = args
        return cls(expr_id, expr_repr, result_var, DSLLocation.parse(dsl_sloc),
                   line_no)


class ExprDoneDirective(Directive):
    def __init__(self, expr_id, line_no):
        super(ExprDoneDirective, self).__init__(line_no)
        self.expr_id = expr_id

    @classmethod
    def parse(cls, line_no, args):
        expr_id, = args
        return cls(expr_id, line_no)


Directive.name_to_cls.update({
    'property-start': PropertyStart,
    'property-body-start': PropertyBodyStart,
    'property-call-start': PropertyCallStart,
    'memoization-lookup': MemoizationLookupDirective,
    'memoization-return': MemoizationReturnDirective,
    'scope-start': ScopeStart,
    'bind': BindDirective,
    'end': End,
    'expr-start': ExprStartDirective,
    'expr-done': ExprDoneDirective,
})
