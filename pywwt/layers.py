import sys
import uuid

if sys.version_info[0] == 2:  # noqa
    from io import BytesIO as StringIO
else:
    from io import StringIO

import warnings
from base64 import b64encode

from astropy import units as u

from traitlets import HasTraits, validate, observe
from .traits import Any, Unicode, Float, Color

__all__ = ['LayerManager', 'TableLayer']

VALID_LON_UNITS = {u.deg: 'degrees',
                   u.hour: 'hours',
                   u.hourangle: 'hours'}


# NOTE: for cartesian coordinates, we can also allow custom units
VALID_ALT_UNITS = {u.m: 'meters',
                   u.imperial.foot: 'feet',
                   u.imperial.inch: 'inches',
                   u.imperial.mile: 'miles',
                   u.km: 'kilometers',
                   u.au: 'astronomicalUnits',
                   u.lyr: 'lightYears',
                   u.pc: 'parsecs',
                   u.Mpc: 'megaParsecs'}

VALID_ALT_TYPES = ['depth', 'altitude', 'distance', 'seaLevel', 'terrain']


def guess_lon_lat_columns(colnames):
    """
    Given column names in a table, return the columns to use for lon/lat, or
    None/None if no high confidence possibilities.
    """

    # Do all the checks in lowercase
    colnames_lower = [colname.lower() for colname in colnames]

    for lon, lat in [('ra', 'dec'), ('lon', 'lat'), ('lng', 'lat')]:

        # Check first for exact matches
        if colnames_lower.count(lon) == 1 and colnames_lower.count(lat) == 1:
            return lon, lat

        # Next check for columns that start with specified names

        lon_match = [colname.startswith(lon) for colname in colnames_lower]
        lat_match = [colname.startswith(lat) for colname in colnames_lower]

        if sum(lon_match) == 1 and sum(lat_match) == 1:
            return colnames[lon_match.index(True)], colnames[lat_match.index(True)]

        # We don't check for cases where lon/lat are inside the name but not at
        # the start since that might be e.g. for proper motions (pm_ra) or
        # errors (dlat).

    return None, None


def pick_unit_if_available(unit, valid_units):
    # Check for equality rather than just identity
    for valid_unit in valid_units:
        if unit == valid_unit:
            return valid_unit
    return unit


class LayerManager(object):
    """
    A simple container for layers.
    """

    def __init__(self, parent=None):
        self._layers = []
        self._parent = parent

    def add_data_layer(self, table=None, frame='Sky', **kwargs):
        """
        Add a data layer to the current view

        Parameters
        ----------
        """
        if table is not None:
            layer = TableLayer(self._parent, table=table, frame=frame, **kwargs)
        else:
            # NOTE: in future we may allow different arguments such as e.g.
            # orbit=, hence why we haven't made this a positional argument.
            raise ValueError("The table argument is required")
        self._add_layer(layer)
        return layer

    def _add_layer(self, layer):
        if layer in self._layers:
            raise ValueError("layer already exists in layer manager")
        self._layers.append(layer)
        layer._manager = self

    def remove_layer(self, layer):
        if layer not in self._layers:
            raise ValueError("layer not in layer manager")
        layer.remove()
        # By this point, the call to remove() above may already have resulted
        # in the layer getting removed, so we check first if it's still present.
        if layer in self._layers:
            self._layers.remove(layer)

    def __len__(self):
        return len(self._layers)

    def __iter__(self):
        for layer in self._layers:
            yield layer

    def __getitem__(self, item):
        return self._layers[item]

    def __str__(self):
        if len(self) == 0:
            return 'Layer manager with no layers'
        else:
            s = 'Layer manager with {0} layers:\n\n'.format(len(self))
            for ilayer, layer in enumerate(self._layers):
                s += '  [{0}]: {1}\n'.format(ilayer, layer)
            return s

    __repr__ = __str__


class TableLayer(HasTraits):
    """
    A layer where the data is stored in an :class:`~astropy.table.Table`
    """

    lon_att = Unicode(help='The column to use for the longitude').tag(wwt='lngColumn')
    lon_unit = Any(help='The units to use for longitude').tag(wwt='raUnits')
    lat_att = Unicode(help='The column to use for the latitude').tag(wwt='latColumn')

    alt_att = Unicode(help='The column to use for the altitude').tag(wwt='altColumn')
    alt_unit = Any(help='The units to use for the altitude').tag(wwt='altUnit')
    alt_type = Unicode(help='The type of altitude').tag(wwt='altType')

    cmap_att = Unicode(help='The column to use for the colormap').tag(wwt='colorMapColumn')

    size_att = Unicode(help='The column to use for the size').tag(wwt='sizeColumn')
    size_scale = Float(10, help='The factor by which to scale the size of the points').tag(wwt='scaleFactor')

    color = Color('white', help='The color of the markers').tag(wwt='color')
    opacity = Float(1, help='The opacity of the markers').tag(wwt='opacity')

    # TODO: support:
    # xAxisColumn
    # yAxisColumn
    # zAxisColumn
    # xAxisReverse
    # yAxisReverse
    # zAxisReverse

    @validate('lon_unit')
    def _check_lon_unit(self, proposal):
        # Pass the proposal to Unit - this allows us to validate the unit,
        # and allows strings to be passed.
        unit = u.Unit(proposal['value'])
        unit = pick_unit_if_available(unit, VALID_LON_UNITS)
        if unit in VALID_LON_UNITS:
            return unit
        else:
            raise ValueError('lon_unit should be one of {0}'.format('/'.join(sorted(str(x) for x in VALID_LON_UNITS))))

    @validate('alt_unit')
    def _check_alt_unit(self, proposal):
        # Pass the proposal to Unit - this allows us to validate the unit,
        # and allows strings to be passed.
        with u.imperial.enable():
            unit = u.Unit(proposal['value'])
        unit = pick_unit_if_available(unit, VALID_ALT_UNITS)
        if unit in VALID_ALT_UNITS:
            return unit
        else:
            raise ValueError('alt_unit should be one of {0}'.format('/'.join(sorted(str(x) for x in VALID_ALT_UNITS))))

    @validate('alt_type')
    def _check_alt_type(self, proposal):
        if proposal['value'] in VALID_ALT_TYPES:
            return proposal['value']
        else:
            raise ValueError('alt_type should be one of {0}'.format('/'.join(str(x) for x in VALID_ALT_TYPES)))

    @observe('alt_att')
    def _on_alt_att_change(self, *value):
        # Check if we can set the unit of the altitude automatically
        if len(self.alt_att) == 0:
            return
        column = self.table[self.alt_att]
        unit = pick_unit_if_available(column.unit, VALID_ALT_UNITS)
        if unit in VALID_ALT_UNITS:
            self.alt_unit = unit
        elif unit is not None:
            warnings.warn('Column {0} has units of {1} but this is not a valid '
                          'unit of altitude - set the unit directly with '
                          'alt_unit'.format(self.alt_att, unit), UserWarning)

    @observe('lon_att')
    def _on_lon_att_change(self, *value):
        # Check if we can set the unit of the altitude automatically
        if len(self.lon_att) == 0:
            return
        column = self.table[self.lon_att]
        unit = pick_unit_if_available(column.unit, VALID_LON_UNITS)
        if unit in VALID_LON_UNITS:
            self.lon_unit = unit
        elif unit is not None:
            warnings.warn('Column {0} has units of {1} but this is not a valid '
                          'unit of longitude - set the unit directly with '
                          'lon_unit'.format(self.lon_att, unit), UserWarning)

    def __init__(self, parent=None, table=None, frame=None, **kwargs):

        # TODO: need to validate reference frame
        self.table = table
        self.frame = frame

        self.parent = parent
        self.id = str(uuid.uuid4())

        # Attribute to keep track of the manager, so that we can notify the
        # manager if a layer is removed.
        self._manager = None
        self._removed = False

        self._initialize_layer()

        # Force defaults
        self._on_trait_change({'name': 'size_scale', 'new': self.size_scale})
        self._on_trait_change({'name': 'color', 'new': self.color})
        self._on_trait_change({'name': 'opacity', 'new': self.opacity})

        self.observe(self._on_trait_change, type='change')

        if any(key not in self.trait_names() for key in kwargs):
            raise KeyError('a key doesn\'t match any layer trait name')

        super(TableLayer, self).__init__(**kwargs)

        lon_guess, lat_guess = guess_lon_lat_columns(self.table.colnames)

        if 'lon_att' not in kwargs:
            self.lon_att = lon_guess or self.table.colnames[0]

        if 'lat_att' not in kwargs:
            self.lat_att = lat_guess or self.table.colnames[1]

    @property
    def _table_b64(self):

        # TODO: We need to make sure that the table has ra/dec columns since
        # WWT absolutely needs that upon creation.

        s = StringIO()
        self.table.write(s, format='ascii.basic', delimiter=',', comment=False)
        s.seek(0)

        # Enforce Windows line endings
        # TODO: check if this needs to be different on Windows
        csv = s.read().replace('\n', '\r\n')

        return b64encode(csv.encode('ascii', errors='replace')).decode('ascii')

    def _initialize_layer(self):
        self.parent._send_msg(event='table_layer_create',
                              id=self.id, table=self._table_b64, frame=self.frame)

    def update_data(self, table=None):
        """
        Update the underlying data.
        """
        self.table = table
        self.parent._send_msg(event='table_layer_update', id=self.id, table=self._table_b64)

        if len(self.alt_att) > 0:
            if self.alt_att in self.table.colnames:
                self._on_alt_att_change()
            else:
                self.alt_att = ''

        lon_guess, lat_guess = guess_lon_lat_columns(self.table.colnames)

        if self.lon_att in self.table.colnames:
            self._on_lon_att_change()
        else:
            self.lon_att = lon_guess or self.table.colnames[0]

        if self.lat_att not in self.table.colnames:
            self.lat_att = lat_guess or self.table.colnames[1]

    def remove(self):
        """
        Remove the layer.
        """
        if self._removed:
            return
        self.parent._send_msg(event='table_layer_remove', id=self.id)
        self._removed = True
        if self._manager is not None:
            self._manager.remove_layer(self)

    def _on_trait_change(self, changed):
        # This method gets called anytime a trait gets changed. Since this class
        # gets inherited by the Jupyter widgets class which adds some traits of
        # its own, we only want to react to changes in traits that have the wwt
        # metadata attribute (which indicates the name of the corresponding WWT
        # setting).
        wwt_name = self.trait_metadata(changed['name'], 'wwt')
        if wwt_name is not None:
            value = changed['new']
            if changed['name'] == 'alt_unit':
                value = VALID_ALT_UNITS[self._check_alt_unit({'value': value})]
            elif changed['name'] == 'lon_unit':
                value = VALID_LON_UNITS[self._check_lon_unit({'value': value})]
            self.parent._send_msg(event='table_layer_set',
                                  id=self.id,
                                  setting=wwt_name,
                                  value=value)

    def __str__(self):
        return 'TableLayer with {0} markers'.format(len(self.table))

    def __repr__(self):
        return '<{0}>'.format(str(self))
