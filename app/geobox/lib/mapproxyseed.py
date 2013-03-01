# This file is part of the GBI project.
# Copyright (C) 2012 Omniscale GmbH & Co. KG <http://omniscale.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import

import time
import sys

import requests
from cStringIO import StringIO

from mapproxy.cache.couchdb import CouchDBCache, CouchDBMDTemplate
from mapproxy.cache.mbtiles import MBTilesCache
from mapproxy.cache.tile import TileManager
from mapproxy.client.http import HTTPClient, HTTPClientError, log_request
from mapproxy.client.tile import TileClient, TileURLTemplate
from mapproxy.grid import tile_grid
from mapproxy.image import ImageSource
from mapproxy.image.opts import ImageOptions
from mapproxy.layer import CacheMapLayer
from mapproxy.seed.seeder import SeedTask, SeedProgress as SeedProgress_
from mapproxy.source import DummySource
from mapproxy.source.tile import TiledSource
from mapproxy.tilefilter import watermark_filter
from mapproxy.util import reraise_exception
from mapproxy.util.coverage import BBOXCoverage

from .coverage import coverage_from_geojson, coverage_intersection

DEFAULT_GRID = tile_grid(3857, origin='nw', name='GoogleMapsCompatible')

class SeedProgress(SeedProgress_):
    def __init__(self, terminate_event, *args, **kw):
        SeedProgress_.__init__(self, *args, **kw)
        self.terminate_event = terminate_event

    def running(self):
        return not self.terminate_event.is_set()

class ProgressLog(object):
    def __init__(self, task, app_state, task_cls):
        self.app_state = app_state
        self.task_id = task.id
        self.previous_tiles = task.tiles or 0
        self.task_cls = task_cls

    def log_step(self, progress):
        pass

    def log_progress(self, progress, level, bbox, tiles):
        session = self.app_state.user_db_session()
        query = session.query(self.task_cls)
        task = query.filter(self.task_cls.id == self.task_id).one()

        task.seed_progress = progress_identifier(progress.current_progress_identifier())
        task.progress = progress.progress
        task.tiles = self.previous_tiles + tiles

        session.commit()

def progress_identifier(level_progresses):
    """
    >>> progress_identifier([(0, 1)])
    '0-1'
    >>> progress_identifier([(0, 1), (2, 4)])
    '0-1|2-4'
    """
    return '|'.join('%d-%d' % lvl for lvl in level_progresses)

def parse_progress_identifier(identifier):
    """
    >>> parse_progress_identifier('')
    None
    >>> parse_progress_identifier('0-1')
    [(0, 1)]
    >>> parse_progress_identifier('0-1|2-4')
    [(0, 1), (2, 4)]
    """
    if not identifier:
        return None
    levels = []
    for level in identifier.split('|'):
        level = level.split('-')
        levels.append((int(level[0]), int(level[1])))
    return levels

class RequestsHTTPClient(object):
    """
    MapProxy compatible HTTPClient implementation that uses the
    session function of the requests lib to get keep-alive connections.
    Only supports GET requests, so it might only be used for readonly CouchDB access.
    """
    def __init__(self, url=None, username=None, password=None, insecure=False,
                 ssl_ca_certs=None, timeout=None, headers=None):
        self.req_session = requests.Session(timeout=timeout)

    def open(self, url, data=None):

        code = None
        result = None
        try:
            start_time = time.time()
            result = self.req_session.get(url)
        except (requests.exceptions.RequestException, Exception), e:
            reraise_exception(HTTPClientError('Internal HTTP error "%s": %r'
                                              % (url, e)), sys.exc_info())
        else:
            code = result.status_code
            if not (200 <= code < 300):
                reraise_exception(HTTPClientError('HTTP Error "%s": %d'
                % (url, code), response_code=code), sys.exc_info())

            if code == 204:
                raise HTTPClientError('HTTP Error "204 No Content"', response_code=204)
            return result
        finally:
            log_request(url, code, result, duration=time.time()-start_time, method='GET')

    def open_image(self, url, data=None):
        resp = self.open(url, data=data)
        if 'content-type' in resp.headers:
            if not resp.headers['content-type'].lower().startswith('image'):
                raise HTTPClientError('response is not an image: (%s)' % (resp.content))
        return ImageSource(StringIO(resp.content))


def create_source(raster_source, app_state):
    url = raster_source.url
    username = raster_source.username
    password = raster_source.password

    http_client = HTTPClient(url, username, password)
                            # , insecure=insecure,
                             # ssl_ca_certs=ssl_ca_certs, timeout=timeout,
                             # headers=headers)

    grid = DEFAULT_GRID
    image_opts = None
    coverage = coverage_from_geojson(raster_source.download_coverage)
    format = raster_source.format

    url_template = TileURLTemplate(
        '%s/%s/%s-%%(z)s-%%(x)s-%%(y)s/tile' % (url.rstrip('/'), raster_source.layer, raster_source.matrix_set),
        format=format)
    client = TileClient(url_template, http_client=http_client, grid=grid)

    if app_state.tilebox.is_running():
        port = app_state.config.get('tilebox', 'port')
        url_template = TileURLTemplate(
            'http://127.0.0.1:%d/%s/%s-%%(z)s-%%(x)s-%%(y)s/tile' % (port, raster_source.layer, raster_source.matrix_set),
            format=format)
        tilebox_client =  TileClient(url_template, http_client=RequestsHTTPClient(), grid=grid)
        client = FallbackTileClient(tilebox_client, client)

    return TiledSource(grid, client, coverage=coverage, image_opts=image_opts)


def create_tile_manager(cache, sources, grid, format, tile_filter=None, image_opts=None):
    pre_store_filter = [tile_filter] if tile_filter else None
    mgr = TileManager(grid, cache, sources, format,
        pre_store_filter=pre_store_filter, image_opts=image_opts)
    return mgr

def create_couchdb_cache(wmts_source, app_state):
    cache_dir = app_state.user_temp_dir()

    db_name = wmts_source.name
    file_ext = wmts_source.format
    port = app_state.config.get('couchdb', 'port')
    url = 'http://127.0.0.1:%s' % (port, )

    md_template = CouchDBMDTemplate({})

    return CouchDBCache(url=url, db_name=db_name, md_template=md_template,
        lock_dir=cache_dir, file_ext=file_ext, tile_grid=DEFAULT_GRID)

def create_couchdb_source(layer, app_state, grid):
    cache = create_couchdb_cache(layer.wmts_source, app_state)
    source = DummySource()
    image_opts = image_options(layer.wmts_source)
    tile_mgr = create_tile_manager(format=layer.wmts_source.format, cache=cache, sources=[source],
        grid=grid, image_opts=image_opts)
    source = CacheMapLayer(tile_mgr)
    return source

def create_mbtiles_export_cache(export_filename, wmts_source, app_state):
    cache = MBTilesCache(export_filename)
    cache.locking_disabled = True
    cache.update_metadata(
        name=wmts_source.title,
        format=wmts_source.format,
        overlay=wmts_source.is_overlay,
    )
    return cache

def create_couchdb_export_cache(export_path, db_name, file_ext, couchdb_port, app_state):
    md_template = CouchDBMDTemplate({})
    cache_dir = app_state.user_temp_dir()
    url = 'http://%s:%s' % ('127.0.0.1', couchdb_port)

    return CouchDBCache(url=url, db_name=db_name, md_template=md_template,
        lock_dir=cache_dir, file_ext=file_ext, tile_grid=DEFAULT_GRID)

def image_options(source):
    return ImageOptions(transparent=source.is_overlay, format=source.format)

def create_import_seed_task(import_task, app_state):
    cache = create_couchdb_cache(import_task.source, app_state)
    source = create_source(import_task.source, app_state)
    grid = DEFAULT_GRID

    watermark_text = app_state.config.get('watermark', 'text')
    if app_state.config.has_option('user', 'name'):
        watermark_text += ' ' + app_state.config.get('user', 'name')

    tile_filter = watermark_filter(watermark_text, opacity=100, font_size=11,
                                spacing=None, font_color=(200, 200, 200))
    image_opts = image_options(import_task.source)
    tile_mgr = create_tile_manager(format=import_task.source.format,
        image_opts=image_opts, cache=cache, sources=[source],
        grid=grid, tile_filter=tile_filter)
    coverage = coverage_from_geojson(import_task.coverage)

    coverage = coverage_intersection(coverage, source.coverage)

    levels = range(import_task.zoom_level_start,
        import_task.zoom_level_end + 1)

    return create_seed_task(tile_mgr, coverage, levels,
        update_tiles=import_task.update_tiles)

def create_mbtiles_export_seed_task(export_task, app_state):
    grid = DEFAULT_GRID
    export_grid = tile_grid('EPSG:3857', origin='sw')
    source = create_couchdb_source(export_task.layer, app_state, grid)

    export_filename = app_state.user_data_path('export', export_task.project.title, export_task.layer.wmts_source.name + '.mbtiles', make_dirs=True)
    cache = create_mbtiles_export_cache(export_filename, export_task.layer.wmts_source, app_state)

    tile_mgr = create_tile_manager(format=export_task.layer.wmts_source.format,
        cache=cache, sources=[source], grid=export_grid)

    source_coverage = coverage_from_geojson(export_task.layer.wmts_source.download_coverage)
    export_coverage = coverage_from_geojson(export_task.coverage)
    coverage = coverage_intersection(source_coverage, export_coverage)

    levels = range(export_task.zoom_level_start,
        export_task.zoom_level_end + 1)

    return create_seed_task(tile_mgr, coverage, levels)


def create_couchdb_export_seed_task(export_task, app_state, couchdb_port):
    grid = DEFAULT_GRID
    source = create_couchdb_source(export_task.layer, app_state, grid)

    export_path = app_state.user_data_path('export', export_task.project.title, 'couchdb', make_dirs=True)
    cache = create_couchdb_export_cache(export_path=export_path, db_name=export_task.layer.wmts_source.name,
        file_ext=export_task.layer.wmts_source.format, couchdb_port=couchdb_port, app_state=app_state)

    tile_mgr = create_tile_manager(format=export_task.layer.wmts_source.format,
        cache=cache, sources=[source], grid=grid)

    source_coverage = coverage_from_geojson(export_task.layer.wmts_source.download_coverage)
    export_coverage = coverage_from_geojson(export_task.coverage)
    coverage = coverage_intersection(source_coverage, export_coverage)

    levels = range(export_task.zoom_level_start,
        export_task.zoom_level_end + 1)

    return create_seed_task(tile_mgr, coverage, levels)


def create_seed_task(tile_mgr, coverage, levels, update_tiles=None):
    if not coverage:
        coverage = BBOXCoverage(tile_mgr.grid.bbox, tile_mgr.grid.srs)

    if update_tiles:
        refresh_timestamp = time.time()
    else:
        refresh_timestamp = None
    return SeedTask(md={}, tile_manager=tile_mgr, levels=levels,
        refresh_timestamp=refresh_timestamp, coverage=coverage)


class FallbackTileClient(object):
    def __init__(self, client, fallback_client):
        self.client = client
        self.fallback_client = fallback_client

    def get_tile(self, tile_coord, format=None):
        try:
            return self.client.get_tile(tile_coord=tile_coord, format=format)
        except HTTPClientError:
            return self.fallback_client.get_tile(tile_coord=tile_coord, format=format)

