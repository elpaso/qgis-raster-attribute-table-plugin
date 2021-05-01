# coding=utf-8
""""RAT data classes

.. note:: This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

"""

__author__ = 'elpaso@itopen.it'
__date__ = '2021-04-27'
__copyright__ = 'Copyright 2021, ItOpen'

from osgeo import gdal
import os
from qgis.PyQt.QtCore import QVariant, QCoreApplication, Qt
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    Qgis,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsVectorFileWriter,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsProject,
    QgsPalettedRasterRenderer,
)

try:
    from .rat_constants import RAT_COLOR_HEADER_NAME, RAT_UNIQUE_FIELDS
    from .rat_log import rat_log
except ImportError:
    from rat_constants import RAT_COLOR_HEADER_NAME, RAT_UNIQUE_FIELDS
    from rat_log import rat_log


class RATField:
    """RAT field"""

    def __init__(self, name, usage, type):
        """Create a RAT field

        :param name: name
        :type name: str
        :param usage: field usage type from gdal.GFU_*
        :type usage: enum GDALRATFieldUsage
        :param type: data type from gdal.GFT_* (Real, Int, String)
        :type type: enum GDALRATFieldType
        """
        self.name = name
        self.usage = usage
        self.type = type

    @property
    def qgis_type(self):
        """Returns the QVariant type of the field

        :raises Exception: in case of unhandled type
        :return: QVariant type of the field
        :rtype: QVariant
        """

        if self.type == gdal.GFT_Integer:
            return QVariant.Int
        elif self.type == gdal.GFT_Real:
            return QVariant.Double
        elif self.type == gdal.GFT_String:
            return QVariant.String
        else:
            raise Exception('Unhandled RAT field type:  %s' % self.type)

    @property
    def is_color(self) -> bool:
        """Returns TRUE if the usage is a color role

        :return: TRUE if the usage is a color role
        :rtype: bool
        """

        return self.usage in (
            gdal.GFU_Red,
            gdal.GFU_RedMax,
            gdal.GFU_RedMin,
            gdal.GFU_Green,
            gdal.GFU_GreenMax,
            gdal.GFU_GreenMin,
            gdal.GFU_Blue,
            gdal.GFU_BlueMax,
            gdal.GFU_BlueMin,
            gdal.GFU_Alpha,
            gdal.GFU_AlphaMax,
            gdal.GFU_AlphaMin,
        )

    def __repr__(self):

        return f'RATField(name={self.name}, usage={self.usage}, type={self.type})'


class RAT:
    """Encapsulate RAT table data"""

    # Ugly hack for GDAL: stores last version of RAT for a source
    _dirty_xml_rats = {}
    _dirty_xml_layer_ids = []

    def __init__(self, data, is_sidecar, fields, path):
        """Create a RAT

        :param data: dictionary with RAT data
        :type data: dict
        :param is_sidecar: TRUE if is a .VAT.DBF sidecar RAT
        :type is_sidecar: bool
        :param fields: dictionary of RAT fields, name is the key
        :type fields: dict
        :param path: path to the RAT file (vat.dbf or aux.xml)
        :type fields: str
        """

        self.__data = data
        self.is_sidecar = is_sidecar
        self.fields = fields
        self.path = path
        self.band = -1  # Unknown, for XML it will be set on save()

    def _restore_xml_rats(self):

        # Retrieve last version of itself
        rat = RAT._dirty_xml_rats["%s|%s" % (self.band, self.path)]
        rat.save(self.band)

    @property
    def values(self) -> list:

        return list(self.__data.values())

    @property
    def keys(self) -> list:

        return list(self.__data.keys())

    @property
    def data(self) -> dict:

        return self.__data

    @property
    def value_column(self) -> str:

        try:
            return [field.name for field in self.fields.values() if field.usage == gdal.GFU_MinMax][0]
        except:
            return None

    def isValid(self) -> bool:

        return len(self.keys) > 0 and self.values is not None

    @property
    def field_usages(self) -> set:
        """Returns all field usages in the RAT

        :return: field usages
        :rtype: set
        """

        usages = set()
        for field in self.fields.values():
            usages.add(field.usage)

        return usages

    @property
    def has_color(self) -> bool:
        """Returns TRUE if the RAT contains RGB fields, Alpha is optional.

        :return: checks if the RAT has color data
        :rtype: bool
        """

        return {gdal.GFU_Green, gdal.GFU_Red, gdal.GFU_Blue}.issubset(self.field_usages)

    def qgis_fields(self) -> QgsFields:

        fields = QgsFields()

        # collect fields
        for field in list(self.fields.values()):
            qgis_field = QgsField(
                field.name, field.qgis_type, comment='RAT usage: %s' % field.usage)
            fields.append(qgis_field)

        return fields

    def qgis_features(self) -> list:

        features = []
        fields = self.qgis_fields()
        for row_index in range(len(self.values[0])):
            feature = QgsFeature(fields)
            attributes = []
            for field_name in self.fields.keys():
                attributes.append(self.data[field_name][row_index])
            feature.setAttributes(attributes)
            features.append(feature)

        return features

    def save_as_dbf(self, raster_source) -> bool:
        """Save/export a copy of the RAT to path"""

        # Add .VAT: .DBF is added by the exporter
        if not raster_source.upper().endswith('.VAT'):
            raster_source = raster_source + '.vat'

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'ESRI Shapefile'
        options.layerOptions = ['SHPT=NULL']

        writer = QgsVectorFileWriter.create(
            raster_source, self.qgis_fields(), QgsWkbTypes.Unknown, QgsCoordinateReferenceSystem(), QgsCoordinateTransformContext(), options)

        self.path = raster_source + '.dbf'

        return writer.addFeatures(self.qgis_features())

    def save_as_xml(self, raster_source, band) -> bool:

        ds = gdal.OpenEx(raster_source)
        if ds:
            self.band = band
            gdal_band = ds.GetRasterBand(band)
            if gdal_band:
                rat = gdal.RasterAttributeTable()
                for field in list(self.fields.values()):
                    rat.CreateColumn(field.name, field.type, field.usage)

                type_map = {gdal.GFT_Integer: 'Int',
                            gdal.GFT_Real: 'Double', gdal.GFT_String: 'String'}

                column_index = 0

                for field_name, field in self.fields.items():
                    values = self.data[field_name]
                    func = getattr(rat, 'SetValueAs%s' % type_map[field.type])

                    for row_index in range(len(values)):
                        func(row_index, column_index, values[row_index])

                    column_index += 1

                assert rat.GetColumnCount() == len(self.fields)
                assert rat.GetRowCount() == len(self.values[0])

                gdal_band.SetDefaultRAT(rat)

                # Ugly hack because GDAL does not know about the newly created RAT
                for layer in [l for l in QgsProject.instance().mapLayers().values() if l.source() == raster_source]:
                    RAT._dirty_xml_rats["%s|%s" %
                                        (self.band, self.path)] = self
                    if layer.id() not in RAT._dirty_xml_layer_ids:
                        RAT._dirty_xml_layer_ids.append(layer.id())
                        layer.destroyed.connect(self._restore_xml_rats)

                return True

        return False

    def save(self, band) -> bool:
        """Saves the changes in the modified RAT"""

        raster_source = self.path[:-8]
        assert os.path.exists(raster_source)

        if self.is_sidecar:
            return self.save_as_dbf(raster_source)
        else:
            return self.save_as_xml(raster_source, band)

    def __insert_column(self, column, field) -> (bool, str):
        """Private insertion method: no validation"""

        column_data = ['' if field.qgis_type ==
                       QVariant.String else (255 if field.usage == gdal.GFU_Alpha else 0)] * len(self.values[0])
        self.data[field.name] = column_data

        # Fields: keep the ordering
        new_fields = {}
        i = 0
        field_index = column - 1 if self.has_color else column
        for field_name, field_data in self.fields.items():
            if field_index == i:
                new_fields[field.name] = field

            new_fields[field_name] = field_data
            i += 1

        self.fields = new_fields

        return True, None

    def insert_column(self, column, field) -> (bool, str):
        """Inserts a field into the RAT at position column

        :param column: insertion point
        :type column: int
        :param field: RAT field to insert
        :type field: RATField
        :return:  (TRUE, None) on success, (FALSE, error_message) on failure
        :rtype: tuple
        """

        if column < 0 or column >= len(self.keys):
            return False, QCoreApplication.translate('RAT', 'Insertion point is out of range.')

        if field.name in self.fields.keys():
            return False, QCoreApplication.translate('RAT', 'Column %s already exists.' % field.name)

        if field.is_color:
            return False, QCoreApplication.translate('RAT', 'Cannot add a single color data column: use insert_colors() instead.')

        if field.usage in RAT_UNIQUE_FIELDS and field.usage in self.field_usages:
            return False, QCoreApplication.translate('RAT', 'Column %s usage already exists and must be unique.' % field.name)

        if column < len(self.keys) - 1:
            next_key = self.keys[column]
            next_field = self.fields[next_key]
            if next_field.usage in (gdal.GFU_MinMax, gdal.GFU_PixelCount):
                return False, QCoreApplication.translate('RAT', 'Column %s cannot be inserted before a "Value" or "Count" column.' % field.name)

        # Validation ok: insert
        return self.__insert_column(column, field)

    def get_color(self, row_index) -> QColor:
        """Returns the color for a row index

        :param row_index: row index
        :type row_index: int
        :return: row color
        :rtype: QColor
        """

        if not self.has_color or row_index < 0 or row_index > len(self.values[0]) - 1:
            return QColor()
        else:
            return self.data[RAT_COLOR_HEADER_NAME][row_index]

    def set_color(self, row_index, color) -> bool:
        """Set the color for a row

        :param row_index: row index
        :type row_index: int
        :param color: color
        :type color: QColor
        :return: TRUE on success
        :rtype: bool
        """

        if not self.has_color:
            return False

        if row_index < 0 or row_index > len(self.values[0]) - 1:
            return False

        red = color.red()
        green = color.green()
        blue = color.alpha()
        alpha = color.alpha()

        self.data[RAT_COLOR_HEADER_NAME][row_index] = color

        for field in self.fields.values():
            if field.is_color:
                if field.usage == gdal.GFU_Red:
                    self.data[field.name][row_index] = red
                elif field.usage == gdal.GFU_Green:
                    self.data[field.name][row_index] = green
                elif field.usage == gdal.GFU_Blue:
                    self.data[field.name][row_index] = blue
                elif field.usage == gdal.GFU_AlphaMax:
                    self.data[field.name][row_index] = alpha

        return True

    def remove_column(self, column_name) -> (bool, str):
        """Removes the column named column_name

        :param column_name: name of the column to remove
        :type column_name: str
        :return: (TRUE, None) on success, (FALSE, error_message) on failure
        :rtype: tuple
        """

        if column_name not in self.keys:
            return False, QCoreApplication.translate('RAT', 'Column %s does not exist.' % column_name)

        # Delete virtual color column
        if column_name == RAT_COLOR_HEADER_NAME:
            del (self.data[column_name])
            return True, None

        field_usage = self.fields[column_name].usage
        if field_usage in (gdal.GFU_MinMax, gdal.GFU_Min, gdal.GFU_Max, gdal.GFU_PixelCount):
            return False, QCoreApplication.translate('RAT', 'Removal of a "Value" or "Count" column is not allowed.')

        if self.fields[column_name].is_color:
            return False, QCoreApplication.translate('RAT', 'Direct removal of color data is not allowed.')

        del(self.fields[column_name])
        del(self.data[column_name])

        return True, None

    def update_colors_from_raster(self, raster_layer) -> bool:
        """Updates colors from raster

        :param raster_layer: raster layer
        :type raster_layer: QgsRasterLayer
        :return: TRUE if at least one color could be set
        :rtype: bool
        """

        if self.has_color and raster_layer.isValid() and isinstance(raster_layer.renderer(), QgsPalettedRasterRenderer):
            result = False
            red_column = [field.name for field in self.fields.values() if field.usage == gdal.GFU_Red][0]
            green_column = [field.name for field in self.fields.values() if field.usage == gdal.GFU_Green][0]
            blue_column = [field.name for field in self.fields.values() if field.usage == gdal.GFU_Blue][0]
            try:
                alpha_column = [field.name for field in self.fields.values() if field.usage == gdal.GFU_Alpha][0]
            except:
                alpha_column = None

            color_map = {}
            for klass in raster_layer.renderer().classes():
                color_map[klass.value] = klass.color

            value_column = self.value_column

            for row_index in range(len(self.data[value_column])):
                value = self.data[value_column][row_index]
                try:
                    color = color_map[value]
                    self.__data[RAT_COLOR_HEADER_NAME][row_index] = color
                    self.__data[red_column][row_index] = color.red()
                    self.__data[green_column][row_index] = color.green()
                    self.__data[blue_column][row_index] = color.blue()
                    if alpha_column is not None:
                        self.__data[alpha_column][row_index] = color.alpha()
                    result = True
                except KeyError as ex:
                    rat_log(f'Error setting color for value {value}: {ex}', Qgis.Warning)

            return result

        else:
            return False


    def insert_color_fields(self, column) -> (bool, str):
        """Inserts all RGBA color fields at position column

        :param column: insertion point
        :type column: int
        :return: (TRUE, None) on success, (FALSE, error_message) on failure
        :rtype: tuple
        """

        fields = [
            RATField('A', gdal.GFU_Alpha, gdal.GFT_Integer),
            RATField('B', gdal.GFU_Blue, gdal.GFT_Integer),
            RATField('G', gdal.GFU_Green, gdal.GFT_Integer),
            RATField('R', gdal.GFU_Red, gdal.GFT_Integer),
        ]

        for field in fields:
            result, error_message = self.__insert_column(column, field)
            if not result:
                return result, error_message

        # Add color virtual field
        data = {RAT_COLOR_HEADER_NAME: [QColor(Qt.black)]*len(self.values[0])}

        data.update(self.data)
        self.__data = data

        return True, None

    def remove_color_fields(self) -> bool:
        """Remove all RGBA color fields (if any)

        :return: TRUE on success
        :rtype: bool
        """

        if not self.has_color:
            return False

        removed = False

        for field_name in [field.name for field in self.fields.values() if field.is_color]:
            removed = True
            del(self.fields[field_name])
            del(self.__data[field_name])

        del(self.__data[RAT_COLOR_HEADER_NAME])

        return removed

    def remove_row(self, row) -> (bool, str):
        """Removes the row

        :param row: row index 0-based
        :type row: int
        :return: (TRUE, None) on success, (FALSE, error_message) on failure
        :rtype: tuple
        """

        if row < 0  or row >= len(self.data[self.value_column]):
            return False, QCoreApplication.translate('RAT', f'Out of range error removing row {row}')
        else:
            for values in self.values:
                values.pop(row)
            return True, None


    def insert_row(self, row) -> (bool, str):
        """Insert a row before position row

        :param row: insertion point
        :type row: int
        :return: (TRUE, None) on success, (FALSE, error_message) on failure
        :rtype: tuple
        """

        if row < 0  or row > len(self.data[self.value_column]):
            return False, QCoreApplication.translate('RAT', f'Out of range error adding a new row {row}')
        else:
            for key in self.keys:
                if key == RAT_COLOR_HEADER_NAME:
                    data = QColor(Qt.white)
                else:
                    field = self.fields[key]
                    if field.is_color:
                        data = 255
                    elif field.type in {gdal.GFT_Integer, gdal.GFT_Real}:
                        data = 0
                    else:
                        data = ''
                self.__data[key].insert(row, data)
            return True, None



