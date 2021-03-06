from __future__ import with_statement

import os
from os.path import join, exists
from time import time
import cPickle as pickle

from scrapy.xlib.pydispatch import dispatcher
from scrapy.core import signals
from scrapy.http import Headers
from scrapy.core.exceptions import NotConfigured, IgnoreRequest
from scrapy.core.downloader.responsetypes import responsetypes
from scrapy.utils.request import request_fingerprint
from scrapy.utils.http import headers_dict_to_raw, headers_raw_to_dict
from scrapy.utils.httpobj import urlparse_cached
from scrapy.utils.misc import load_object
from scrapy import conf


class HttpCacheMiddleware(object):

    def __init__(self, settings=conf.settings):
        self.storage = load_object(settings['HTTPCACHE_STORAGE'])(settings)
        self.ignore_missing = settings.getbool('HTTPCACHE_IGNORE_MISSING')
        dispatcher.connect(self.spider_opened, signal=signals.spider_opened)
        dispatcher.connect(self.spider_closed, signal=signals.spider_closed)

    def spider_opened(self, spider):
        self.storage.open_spider(spider)

    def spider_closed(self, spider):
        self.storage.close_spider(spider)

    def process_request(self, request, spider):
        if not self.is_cacheable(request):
            return
        response = self.storage.retrieve_response(spider, request)
        if response:
            response.flags.append('cached')
            return response
        elif self.ignore_missing:
            raise IgnoreRequest("Ignored request not in cache: %s" % request)

    def process_response(self, request, response, spider):
        if self.is_cacheable(request):
            self.storage.store_response(spider, request, response)
        return response

    def is_cacheable(self, request):
        return urlparse_cached(request).scheme in ['http', 'https']


class FilesystemCacheStorage(object):

    def __init__(self, settings=conf.settings):
        cachedir = settings['HTTPCACHE_DIR']
        if not cachedir:
            raise NotConfigured
        self.cachedir = cachedir
        self.expiration_secs = settings.getint('HTTPCACHE_EXPIRATION_SECS')

    def open_spider(self, spider):
        pass

    def close_spider(self, spider):
        pass

    def retrieve_response(self, spider, request):
        """Return response if present in cache, or None otherwise."""
        metadata = self._read_meta(spider, request)
        if metadata is None:
            return # not cached
        rpath = self._get_request_path(spider, request)
        with open(join(rpath, 'response_body'), 'rb') as f:
            body = f.read()
        with open(join(rpath, 'response_headers'), 'rb') as f:
            rawheaders = f.read()
        url = metadata['url']
        status = metadata['status']
        headers = Headers(headers_raw_to_dict(rawheaders))
        respcls = responsetypes.from_args(headers=headers, url=url)
        response = respcls(url=url, headers=headers, status=status, body=body)
        return response

    def store_response(self, spider, request, response):
        """Store the given response in the cache."""
        rpath = self._get_request_path(spider, request)
        if not exists(rpath):
            os.makedirs(rpath)
        metadata = {
            'url': request.url,
            'method': request.method,
            'status': response.status,
            'timestamp': time(),
        }
        with open(join(rpath, 'meta'), 'wb') as f:
            f.write(repr(metadata))
        with open(join(rpath, 'pickled_meta'), 'wb') as f:
            pickle.dump(metadata, f, protocol=2)
        with open(join(rpath, 'response_headers'), 'wb') as f:
            f.write(headers_dict_to_raw(response.headers))
        with open(join(rpath, 'response_body'), 'wb') as f:
            f.write(response.body)
        with open(join(rpath, 'request_headers'), 'wb') as f:
            f.write(headers_dict_to_raw(request.headers))
        with open(join(rpath, 'request_body'), 'wb') as f:
            f.write(request.body)

    def _get_request_path(self, spider, request):
        key = request_fingerprint(request)
        return join(self.cachedir, spider.name, key[0:2], key)

    def _read_meta(self, spider, request):
        rpath = self._get_request_path(spider, request)
        metapath = join(rpath, 'pickled_meta')
        if not exists(metapath):
            return # not found
        mtime = os.stat(rpath).st_mtime
        if 0 <= self.expiration_secs < time() - mtime:
            return # expired
        with open(metapath, 'rb') as f:
            return pickle.load(f)
