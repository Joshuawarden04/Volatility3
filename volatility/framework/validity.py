"""A set of classes providing consistent type checking and error handling for type/class validity
"""


class ValidityRoutines(object):
    """Class to hold all validation routines, such as type checking

    Contains only private class methods, including `_check_type(cls, value, valid_type)` and
    `_check_class(cls, klass, valid_class)`.  These may eventually be made obsolete by PEP 484
    and appropriate static type verification by software such as mypy.

    These are currently implemented by assertions that will be optimized out of production code.
    """

    @classmethod
    def _check_type(cls, value, valid_type):
        """Checks that value is an instance of valid_type, and returns value if it is, or throws a TypeError otherwise

        :param value: The value of which to validate the type
        :type value: object
        :param valid_type: The type against which to validate
        :type valid_type: type
        """
        assert isinstance(value,
                          valid_type), cls.__name__ + " expected " + valid_type.__name__ + ", not " + type(
            value).__name__
        return value

    @classmethod
    def _check_class(cls, klass, valid_class):
        """Checks that class is an instance of valid_class, and returns klass if it is, or throws a TypeError otherwise

        :param klass: Class to validate
        :type klass: class
        :param valid_class: Valid class against which to check class validity
        :type valid_class: class
        """
        assert issubclass(klass,
                          valid_class), cls.__name__ + " expected " + valid_class.__name__ + ", not " + klass.__name__
        return klass
