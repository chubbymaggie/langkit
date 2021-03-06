## vim: filetype=makopython

<%def name="base_decl()">

class _BaseArray(object):
    """
    Base class for Ada arrays bindings.
    """

    def __init__(self, c_value, inc_ref=False):
        self._c_value = c_value
        self._length = c_value.contents.n

        items_addr = _field_address(c_value.contents, 'items')
        items = self._c_element_type.from_address(items_addr)
        self._items = ctypes.pointer(items)

        if inc_ref:
           self._inc_ref(self._c_value)

    def __repr__(self):
        return '<{} {}>'.format(type(self).__name__, list(self))

    def __del__(self):
        self._dec_ref(self._c_value)
        self._c_value = None
        self._length = None
        self._items = None

    def __len__(self):
        return self._length

    def __getitem__(self, key):
        if not isinstance(key, int):
            raise TypeError('array indices must be integers, not {}'.format(
                type(key)
            ))
        elif not (0 <= key < self._length):
            raise IndexError()

        # In ctypes, accessing an array element does not copy it, which means
        # the the array must live at least as long as the accessed element. We
        # cannot guarantee that, so we must copy the element so that it is
        # independent of the array it comes from.
        item = self._items[key]
        try:
            item = self._c_element_type.from_buffer_copy(item)
        except TypeError:
            pass
        return self._wrap_item(item)

    @classmethod
    def _unwrap(cls, value):
        if not isinstance(value, cls):
            _raise_type_error(cls.__name__, value)
        else:
            return value._c_value

    @staticmethod
    def _unwrap_item(item):
        raise NotImplementedError()

</%def>

<%def name="decl(cls)">

<%
   type_name = cls.array_type_name.camel
   element_type = cls.element_type
   c_element_type = pyapi.type_internal_name(element_type)
%>

class ${type_name}(_BaseArray):
    """
    Wrapper class for arrays of ${cls.element_type.name}.

    This class is not meant to be directly instantiated: it is only used to
    expose values that various methods return.
    """

    @staticmethod
    def _wrap_item(item):
        return ${(pyapi.wrap_value('item', element_type, from_field_access=True,
                                   inc_ref=True))}

    _c_element_type = ${c_element_type}

    class _c_struct(ctypes.Structure):
        _fields_ = [('n', ctypes.c_int),
                    ('ref_count', ctypes.c_int),
                    ('items', ${c_element_type} * 1)]

    _c_type = ctypes.POINTER(_c_struct)

    _inc_ref = staticmethod(_import_func('${cls.c_inc_ref(capi)}',
                            [_c_type], None))
    _dec_ref = staticmethod(_import_func('${cls.c_dec_ref(capi)}',
                            [_c_type], None))

</%def>
