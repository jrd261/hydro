import webapp2
from google.appengine.ext import ndb
from threading import local
import uuid
import copy
import traceback
import json
import collections


class _Style(object):

    @property
    def name(self):
        return self.__class__.__name__.lower()

    def __init__(self, **kwargs):
        self.options = kwargs


class _ResourceStyle(_Style): pass


class Default(_ResourceStyle): pass


class _PropertyStyle(_Style): pass


class Hidden(_PropertyStyle): pass


class _MetaResource(ndb.MetaModel):
    """Meta-class for resources.

    This meta-class serves several purposes: The first is to record
    the public subclasses of the three major resource classes.  The
    second is to record the properties of each resource in a list by
    order of assignment.  The third is to record the name of the
    resource's attribute where each property is stored on the
    properties themselves.
    """

    def __new__(meta, name, baseclasses, class_dictionary):
        cls = ndb.MetaModel.__new__(meta, name, baseclasses, class_dictionary)

        if cls.public_class_name:
            cls._public_class_mapping[cls.public_class_name] = cls

        prop_names = [name_ for name_ in set(dir(cls)) if
                      isinstance(getattr(cls, name_), _Property)]
        [setattr(getattr(cls, name_), '_name', name_) for name_ in
         prop_names]
        cls._properties_ = [getattr(cls, name_) for name_ in prop_names]
        cls._properties_ = sorted(cls._properties_, key=lambda prop_:
                                  prop_._index)
        return cls


class HTTPException(Exception):
    """Exception called with an HTTP status code and message.

    Attributes:
        code: A 4XX/5XX HTTP status code indicating the nature of the
            error.  Should be an integer.
        message: A string containing a short message describing the
            nature of the error.
    """
    def __init__(self, code=500, message="An unknown error has occured."):
        self.code = code
        self.message = message


class _Property(object):
    """Base-class for all properties.

    Properties are data containers for resources.

    Attributes:
        _default_: The default value of the property.
        _display: The display object of the property.
        _index: An integer indicating the order in which the
            properties were initialized.
        _repeated: A boolean indicating whether of not the value of
            the property is a list of values. This value is assigned
            by a subclass.
        _name: The name of the resource's attribute where the property
            was assigned.
        _verbose_name: The name of the property as viewed by a client.

    Class Attributes:
        _counter: The number of properties that have been initialized.
    """

    _default_ = None
    _style = None
    _modifiable = False
    _repeated = False
    _verbose_name = None

    _counter = 0

    def __init__(self, default=None, style=None, modifiable=None,
                 repeated=None, verbose_name=None):

        if default is not None:
            self._default_ = default
        if isinstance(style, _PropertyStyle):
            self._style = style
        if modifiable is not None:
            self._modifiable = modifiable
        if repeated is not None:
            self._repeated = repeated
        if verbose_name is not None:
            self._verbose_name = verbose_name

        self._index = _Property._counter
        _Property._counter += 1

    def _client_get(self, resource):
        return getattr(resource, self._name)


class StaticProperty(_Property):

    def __init__(self, modifiable=None, value=None, **kwargs):
        kwargs['default'] = value
        super(StaticProperty, self).__init__(**kwargs)

    def __get__(self, resource, _=None):
        if resource is None:
            return self
        return copy.deepcopy(self._default_)

    def _client_get(self, resource):
        return self._default_


class LinkedProperty(_Property):

    def __init__(self, attr_name, modifiable=None, **kwargs):
        self._attr_name = attr_name
        super(LinkedProperty, self).__init__(**kwargs)

    def __get__(self, resource, _=None):
        if resource is None:
            return self
        return getattr(resource, self._attr_name)


class _TransientProperty(_Property):
    """Base class for transient properties.

    The value of a transient property only persists for a single request.

    """

    def __get__(self, resource, _=None):
        if resource is None:
            return self
        if not self._name in resource.__dict__:
            return copy.deepcopy(self._default_)
        return resource.__dict__[self._name]

    def _client_get(self, resource):
        return self._default_

    def _client_set(self, resource, value):
        if self._modifiable:
            if not isinstance(value, list):
                value = [value]
            value = [self._validate(v) for v in value]
            if not self._repeated:
                value = value[0]
            setattr(resource, self._name, value)

    def _validate(self, value):
        return value


class StringProperty(_TransientProperty):
    pass


class _PersistentProperty(_Property):
    """Baseclass for stored properties."""

    _attributes = ['_name', '_indexed', '_repeated', '_verbose_name',
                   '_default_', '_style', '_modifiable']

    def __init__(self, **kwargs):
        ndb.Property.__init__(self)
        _Property.__init__(self, **kwargs)

    def __get__(self, resource, _=None):
        if resource is None:
            return self
        value = self._get_value(resource)
        if value is None or (self._repeated and value == []):
            value = copy.deepcopy(self._default_)
        return value


class PersistentStringProperty(_PersistentProperty, ndb.TextProperty):
    pass


class PersistentFloatProperty(_PersistentProperty, ndb.FloatProperty):
    pass


class PersistentIntegerProperty(_PersistentProperty, ndb.IntegerProperty):
    pass


class PersistentBooleanProperty(_PersistentProperty, ndb.BooleanProperty):
    pass


class PersistentDateTimeProperty(_PersistentProperty, ndb.DateTimeProperty):

    _attributes = _PersistentProperty._attributes


class PersistentSerializedProperty(_PersistentProperty, ndb.JsonProperty):
    pass


class PersistentStructuredProperty(_PersistentProperty,
                                   ndb.StructuredProperty):

    _attributes = _PersistentProperty._attributes

    def __init__(self, resource_class, **kwargs):
        self._modelclass = resource_class
        _PersistentProperty.__init__(self, **kwargs)


class _Resource(object):

    __metaclass__ = _MetaResource

    @classmethod
    def _fix_up_properties(cls):
        pass

    def client_authorize_hook(self, user):
        pass

    def client_read_hook(self, user):
        pass

    def client_update_hook(self, user):
        pass

    def to_dictionary(self):
        d = collections.OrderedDict()
        d['name'] = self.name
        d['class'] = self.public_class_name
        d['uri'] = self.uri
        d['style'] = self.style.name
        d['options'] = self.style.options
        d['properties'] = collections.OrderedDict()
        for property_ in self._properties_:
            if property_._style:
                pdict = collections.OrderedDict()
                prop_name = property_._name
                if property_._verbose_name:
                    prop_name = property_._verbose_name
                d['properties'][prop_name] = pdict
                pdict['value'] = property_._client_get(self)
                pdict['style'] = property_._style.name
                pdict['options'] = property_._style.options

        return d

    @property
    def name(self):
        return self.key.string_id()

    style = Default()
    public_class_name = None
    perk = None


class TransientResource(_Resource):

    @classmethod
    def read(cls, *args, **kwargs):
        return cls()

    @property
    def key(self):
        return ndb.Key('%s:%s' % ('transient', self.public_class_name),
                       'transient')

    def _authorize(self, user):
        if not self.perk in user.other_perks:
            raise HTTPException(403, "Not authorized.")

    @property
    def uri(self):
        return self.public_class_name

    _public_class_mapping = {}


class PersistentResource(_Resource, ndb.Model):

    @property
    def uri(self):
        return '%s%s' % ('/', '/'.join([self.public_class_name,
                                        self.name]))

    @classmethod
    def create(cls, name=None, update=False, **kwargs):
        """Create a resource.

        Create a resource instance of the given class with the
        specified name.  If the name is not specified, it is generated
        with the create_name class-method.  The resource is placed
        immediately in the thread cache, and optionally into the
        instance cache, memcache, and datastore.  Additional
        functionality can be added with the on_create hook.

        Args:
            name: A string identifying the created resource. A name
                will be generated if not specified.
            update: A boolean indicating whether or not to save the
                resource to the memcache and datastore *after* calling
                the on_create hook.
            **kwargs: Pass through keyword arguments for the
                create_name, on_create, and update methods (when
                applicable).

        Returns:
            A shiny new resource.

        Note:
           This effectively replaces the class constructor. All
           resources should be created with this method.

        """
        if not name:
            name = cls.create_name(**kwargs)
        resource = cls(id=name)
        resource.create_hook(**kwargs)
        resource.update(externally=update, **kwargs)
        return resource

    @classmethod
    def read(cls, name=None, create_name=True, create=False, **kwargs):
        """Retrieve a resource

        Retrieve a resource of the given class with the specified name
        from the thread cache, instance cache, memcache, or datastore.
        If the name is not specified, it is generated with the
        create_name method.  If the resource is not found in any of
        those locations and create is true, a new resource will be
        created.  Additional functionality can be added with the
        on_read hook.  When a resource is retrieved it is
        automatically saved to the caches, i.e. if its found in the
        datastore it will be saved to the memcache, instance cache,
        and thread cache (when applicable).

        Args:
            name: A string identifying the resource to retrieve.
            create_name: A boolean indicating whether or not to create
                a name if the specified name is false.
            create: A boolean indicating whether or not to create a
                resource with the create method if the resource cannot
                be found.
            **kwargs : Pass through keyword arguments for the
                create_name, create, and on_read methods (when
                applicable).

        Returns:
            A resource, if the resource was found or create was
            true. None if the resource was not found and create was
            false.
        """

        if not name and create_name:
            name = cls.create_name(**kwargs)
        if not name:
            return None

        resource = cls._thread_cache.get((cls.__name__, name))
        if not resource and cls._use_instance_cache:
            resource = cls._instance_cache.get((cls.__name__, name))

        if not resource:
            resource = ndb.Key(cls.__name__, name).get()
            if resource:
                resource.read_hook(**kwargs)
                if cls._use_instance_cache:
                    cls._instance_cache[(cls.__name__, name)] = resource
                cls._thread_cache[(cls.__name__, name)] = resource

        if not resource and create:
            resource = cls.create(name, **kwargs)

        return resource

    def update(self, externally=True, modifications=None, **kwargs):
        """Save the resource.

        Modify the properties of the resource and save the resource to
        the thread cache.  If externally is true, also save the
        resource to the instance cache, memcache, and datastore (when
        applicable). Additional functionality can be added with the
        on_update hook.

        Args:
            externally: A boolean indicating whether or not to save
                the resource to the instance cache, memcache, and
                datastore (when applicable).
            modifications: A dictionary of property name/property
                values to set before updating.
            **kwargs: Pass-through keyword arguments for the on_update
                hook.

        """
        if modifications:
            for key, value in modifications.iteritems():
                setattr(self, key, value)
        self._thread_cache[
            (self.__class__.__name__, self.key.id())
        ] = self
        if externally:
            self.update_hook(**kwargs)
            if self._use_instance_cache:
                self._instance_cache[
                    (self.__class__.__name__, self.key.id())] = self
            self.put()

    def delete(self, **kwargs):
        """Purge the resource from existence.

        Remove the resource from the thread cache, instance cache,
        memcache, and datastore. Additional functionality can be added
        with the on_delete hook.

        Args:
            **kwargs: Pass-through keyword arguments for the on_delete
                hook.
        """
        self.delete_hook(**kwargs)
        self._thread_cache.pop((self.__class__.__name__,
                                self.key.id()), None)
        if self._use_instance_cache:
            self._instance_cache.pop((self.__class__.__name__,
                                      self.key.id()), None)
        self.delete()

    @classmethod
    def create_name(cls, **kwargs):
        """Create a name for a resource.

        Generate a unique identifier using a combination of uuid1 and
        uuid4.  There is virtually no chance of collision and the
        identifier should be securely random.  This method can be
        overriden to add context to the identifier.

        Args:
            **kwargs: Not used, but here for compatibility.

        Returns:
            A unique identifier for a resource (a string).

        """
        return (str(uuid.uuid1()) + str(uuid.uuid4())).replace('-', '')

    def create_hook(self, **kwargs):
        """Hook called when creating a resource.

        This method is called just after the resource is created and
        before it is saved.
        """

    def read_hook(self, **kwargs):
        """Hook called when reading a resource.

        This method is called just after the resource is loaded from
        the memcache or datastore.
        """

    def update_hook(self, **kwargs):
        """Hook called when updating a resource.

        This method is called just before a resource is saved to the
        instance cache, memcache, or datastore.
        """

    def delete_hook(self, **kwargs):
        """Hook called when deleting a resource.

        This method is called just before a resource is deleted.
        """

    @classmethod
    def _fix_up_properties(cls):
        cls._properties = {}
        for name in set(dir(cls)):
            prop_ = getattr(cls, name, None)
            if (isinstance(prop_, ndb.ModelAttribute) and not
               isinstance(prop_, ndb.ModelKey)):
                prop_._fix_up(cls, name)
                if (prop_._repeated or
                    (isinstance(prop_, PersistentStructuredProperty) and
                     prop_._modelclass._has_repeated)):
                    cls._has_repeated = True
                cls._properties[prop_._name] = prop_
        cls._kind_map[cls.__name__] = cls

    _use_instance_cache = False
    _use_memcache = True
    _use_datastore = True
    _instance_cache = {}
    _thread_cache = local().__dict__
    _public_class_mapping = {}


class Collection(_Resource):

    _public_class_mapping = {}


class _EncoderBase(PersistentResource):

    @classmethod
    def create_name(self, resource, **kwargs):
        return resource.key.urlsafe()

    value = PersistentStringProperty()
    _use_memcache = False
    _use_datastore = False


class _HTMLEncoder(_EncoderBase):

    def create_hook(self, resource, **kwargs):
        self.value = '<b>James needs to make an HTML encoder.</b>'


class _JSONEncoder(_EncoderBase):

    def create_hook(self, resource, **kwargs):
        self.value = json.dumps(resource.to_dictionary(), indent=4)


class User(PersistentResource):

    owner_perks = PersistentStringProperty(
        default=['owner'], repeated=True
    )
    group_perks = PersistentSerializedProperty(default={})
    other_perks = PersistentStringProperty(
        default=['basic', 'user'], repeated=True,
    )


class AnonymousUser(PersistentResource):

    owner_perks = PersistentStringProperty(
        default=[], repeated=True,
    )
    group_perks = PersistentSerializedProperty(default={})
    other_perks = PersistentStringProperty(
        default=['basic'], repeated=True,
    )


class Hydro(webapp2.WSGIApplication):
    """The application."""

    def __init__(self, **kwargs):

        super(Hydro, self).__init__(
            [
                webapp2.Route('/', _RootHandler),
                webapp2.Route('/<class_name>', _TransientHandler),
                webapp2.Route('/<class_name>/<name>', _PersistentHandler),
                webapp2.Route('/<class_name>/', _CollectionHandler),
                webapp2.Route('<:.*>', _GarbageHandler),
            ],
            config=dict({
                'front_page': None,
                'template_path': 'static/private/templates',
            }.items() + kwargs.pop('config', {}).items()),
            **kwargs)


class _RequestHandler(webapp2.RequestHandler):
    """Base-class for request handlers.

    The dispatch method of this handler serves every well formed
    request.

    Class Attributes:

        _resource_class: The base-class of resources this request
            handler serves.  One of the three resource types.
        _allowed_methods: A list of strings indicating the allowed
            HTTP methods for this request handler (i.e. ['GET']).
        _modification_methods: A list of strings indiciating the HTTP
            methods that when used, cause the request handler to
            modify the resource.
    """

    def dispatch(self):

        try:
            print 's'

            if self.request.method not in self._allowed_methods:
                raise HTTPException(405, "Method not allowed.")

            # Obtain the class of the requested resource.
            Resource = self._resource_class._public_class_mapping.get(
                self.request.route_kwargs.get('class_name'))
            if not Resource:
                raise HTTPException(400, "The requested resource could\
                not be found.")

            user = AnonymousUser.create()
            modifications = {}  # STUB

            resource = Resource.read(
                name=self.request.route_kwargs.get('name'),
                user=user,
            )
            resource.client_read_hook(user)

            resource.client_authorize_hook(user)
            resource._authorize(user)

            if self.request.method in self._modification_methods:
                for property_ in resource._properties_:
                    if property_._name in modifications:
                        property_._client_set(
                            resource,
                            modifications[property_._name]
                        )
                resource.client_update_hook(user)

            content_type = self.request.headers.get('Accept')
            if 'text/html' in content_type:
                Encoder = _HTMLEncoder
                self.response.headers['Content-Type'] = 'text/html'
            elif 'application/json' in content_type:
                self.response.headers['Content-Type'] = 'application/json'
                Encoder = _JSONEncoder
            else:
                raise HTTPException(406, "Unsupported content type.")

            self.response.write(
                Encoder.read(
                    create=True,
                    update=True,
                    resource=resource,
                ).value
            )

        except Exception as exception:
            self.handle_exception(exception)

    def handle_exception(self, exception):

        traceback.print_exc()
        if isinstance(exception, HTTPException):
            self.response.write(('<h2>' + str(exception.code) + ': '
                                 + exception.message + '</h2>'))
        else:
            raise exception

    _allowed_methods = ['GET']
    _modification_methods = []


class _TransientHandler(_RequestHandler):
    """Handler for requests for transient resources."""
    _resource_class = TransientResource
    _allowed_methods = ['GET', 'POST']
    _modification_methods = ['POST']


class _PersistentHandler(_RequestHandler):
    """Handler for requests for persistent resources."""
    _resource_class = PersistentResource


class _CollectionHandler(_RequestHandler):
    """Handler for requests for collections of resources."""
    _resource_class = Collection
    _modification_methods = ['GET']


class _RootHandler(_RequestHandler):
    """Handler for requests for the root resource."""
    def dispatch(self):
        if '/' in TransientResource._public_class_mapping:
            self.__class__ = _TransientHandler
        elif '/' in Collection._public_class_mapping:
            self.__class__ = _CollectionHandler
        else:
            self.__class__ = _GarbageHandler
        self.request.route_kwargs['class_name'] = '/'
        self.dispatch()


class _GarbageHandler(_RequestHandler):
    """Handler for requests at an unrecognized URN."""
    def dispatch(self):
        super(_GarbageHandler, self).handle_exception(
            HTTPException(404, "The requested resource could not be\
            found.")
        )


class Test3(TransientResource):

    public_class_name = '/'
    perk = 'basic'

    test_prop = StringProperty(
        default="It works!",
        style=Hidden(css_override=True),
    )
