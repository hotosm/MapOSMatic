# coding: utf-8

# maposmatic, the web front-end of the MapOSMatic city map generation system
# Copyright (C) 2009  David Decotigny
# Copyright (C) 2009  Frédéric Lehobey
# Copyright (C) 2009  David Mentré
# Copyright (C) 2009  Maxime Petazzoni
# Copyright (C) 2009  Thomas Petazzoni
# Copyright (C) 2009  Gaël Utard

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Nominatim parsing + json export
# Note: we query nominatim in XML format because otherwise we cannot
# access the osm_id tag. Then we format it as json back to the
# javascript routines


"""
Simple API to query http://nominatim.openstreetmap.org
"""

import www.settings
import psycopg2
from urllib import urlencode
import urllib2
from xml.etree.ElementTree import parse as XMLTree

NOMINATIM_BASE_URL = "http://nominatim.openstreetmap.org/search/"

def query(query_text, with_polygons = False):
    """
    Query the nominatim service for the given city query and return a
    (python) list of entries for the given squery (eg. "Paris"). Each
    entry is a dictionary key -> value (value is always a
    string). When possible, we also try to uncover the OSM database
    IDs associated with the entries; in that case, an
    "ocitysmap_params" key is provided, which maps to a dictionary
    containing:
      - key "table": when "line" -> refers to table "planet_osm_line";
        when "polygon" -> "planet_osm_polygon"
      - key "id": ID of the OSM database entry
      - key "admin_level": The value stored in the OSM table for admin_level
    """
    entries = _fetch_entries(query_text, with_polygons)
    return _retrieve_missing_data_from_GIS(entries)


def _fetch_entries(query_text, with_polygons):
    """
    Query the nominatim service for the given city query and return a
    (python) list of entries for the given squery (eg. "Paris"). Each
    entry is a dictionary key -> value (value is always a
    string).
    """
    # For some reason, the "xml" nominatim output is ALWAYS used, even
    # though we will later (in views.py) transform this into
    # json. This is because we know that this xml output is correct
    # and complete (at least the "osm_id" field is missing from the
    # json output)
    query_tags = dict(q=query_text, format='xml')
    if with_polygons:
        query_tags['polygon']=1

    qdata = urlencode(query_tags)
    f = urllib2.urlopen(url="%s?%s" % (NOMINATIM_BASE_URL, qdata))
    return [dict(place.items())
            for place in XMLTree(f).getroot().getchildren()]


def _retrieve_missing_data_from_GIS(entries):
    """
    Try to retrieve additional OSM information for the given nominatim
    entries. Among the information, we try to determine the real ID in
    an OSM table for each of these entries. All these additional data
    are stored in the "ocitysmap_params" key of the entry, which maps
    to a dictionary containing:
      - key "table": when "line" -> refers to table "planet_osm_line";
        when "polygon" -> "planet_osm_polygon"
      - key "id": ID of the OSM database entry
      - key "admin_level": The value stored in the OSM table for admin_level
    """
    if not www.settings.has_gis_database():
        return entries

    try:
        conn = psycopg2.connect("dbname='%s' user='%s' host='%s' password='%s'" %
                                (www.settings.GIS_DATABASE_NAME,
                                 www.settings.DATABASE_USER,
                                 www.settings.DATABASE_HOST,
                                 www.settings.DATABASE_PASSWORD))
    except psycopg2.OperationalError:
        return entries

    # Nominatim returns a field "osm_id" for each result
    # entry. Depending on the type of the entry, it can point to
    # various database entries. For admin boundaries, osm_id is
    # supposed to point to either the 'polygon' or the 'line'
    # table. Usually, the database entry ID in the table is derived by
    # the "relation" items by osm2pgsql, which assigns to that ID the
    # opposite of osm_id... But we still consider that it could be the
    # real osm_id (not its opposite). Let's have fun...
    try:
        cursor = conn.cursor()
        for entry in entries:
            # Just don't try to lookup any additional information from
            # OSM when the nominatim entry is not an administrative
            # boundary
            if ( (entry.get("class", None) != "boundary")
                 or (entry.get("type", None) != "administrative") ):
                continue

            for table_name in ("polygon", "line"):
                # Lookup the polygon/line table for both osm_id and
                # the opposite of osm_id
                cursor.execute("""select osm_id, admin_level
                                  from planet_osm_%s
                                  where osm_id in (%s,-%s)""" \
                                   % (table_name,
                                      entry["osm_id"],entry["osm_id"]))
                result = tuple(set(cursor.fetchall()))
                if len(result) == 1:
                    entry["ocitysmap_params"] = dict(table=table_name,
                                                     id=result[0][0],
                                                     admin_level=result[0][1])
                    break

        # Some cleanup
        cursor.close()
    finally:
        conn.close()

    return entries