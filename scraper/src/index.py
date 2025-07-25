"""
DocSearch scraper main entry point
"""
import os
import json
import requests
from requests_iap import IAPAuth
from keycloak.realm import KeycloakRealm

from scrapy.crawler import CrawlerProcess

from .typesense_helper import TypesenseHelper
from .config.config_loader import ConfigLoader
from .documentation_spider import DocumentationSpider
from .strategies.default_strategy import DefaultStrategy
from .custom_downloader_middleware import CustomDownloaderMiddleware
from .header_inspector_middleware import HeaderInspectionMiddleware
from .custom_dupefilter import CustomDupeFilter
from .config.browser_handler import BrowserHandler

try:
    # disable boto (S3 download)
    from scrapy import optional_features

    if 'boto' in optional_features:
        optional_features.remove('boto')
except ImportError:
    pass

EXIT_CODE_NO_RECORD = 3


def run_config(config):
    config = ConfigLoader(config)
    CustomDownloaderMiddleware.driver = config.driver
    DocumentationSpider.NB_INDEXED = 0

    strategy = DefaultStrategy(config)

    typesense_helper = TypesenseHelper(
        config.index_name,
        config.index_name_tmp,
        config.custom_settings
    )
    typesense_helper.create_tmp_collection()

    root_module = 'src.' if __name__ == '__main__' else 'scraper.src.'
    DOWNLOADER_MIDDLEWARES_PATH = root_module + 'custom_downloader_middleware.' + CustomDownloaderMiddleware.__name__
    HEADER_MIDDLEWARES_PATH = root_module + 'header_inspector_middleware.' + HeaderInspectionMiddleware.__name__
    DUPEFILTER_CLASS_PATH = root_module + 'custom_dupefilter.' + CustomDupeFilter.__name__

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en",
    }  # Defaults for scrapy https://docs.scrapy.org/en/latest/topics/settings.html#default-request-headers

    if config.headers is not None:
        headers.update(config.headers)

    # Cloudflare Zero Trust (CF)
    if (os.getenv("CF_ACCESS_CLIENT_ID") and
        os.getenv("CF_ACCESS_CLIENT_SECRET")):
        headers.update(
            {
                "CF-Access-Client-Id": os.getenv("CF_ACCESS_CLIENT_ID"),
                "CF-Access-Client-Secret": os.getenv("CF_ACCESS_CLIENT_SECRET"),
            }
        )

    # Google Identity-Aware Proxy (IAP)
    elif (os.getenv("IAP_AUTH_CLIENT_ID") and
        os.getenv("IAP_AUTH_SERVICE_ACCOUNT_JSON")):
        iap_token = IAPAuth(
            client_id=os.getenv("IAP_AUTH_CLIENT_ID"),
            service_account_secret_dict=json.loads(
                os.getenv("IAP_AUTH_SERVICE_ACCOUNT_JSON")
            ),
        )(requests.Request()).headers["Authorization"]
        headers.update({"Authorization": iap_token})

    # Keycloak (KC)
    elif (os.getenv("KC_URL") and
        os.getenv("KC_REALM") and
        os.getenv("KC_CLIENT_ID") and
        os.getenv("KC_CLIENT_SECRET")):
        realm = KeycloakRealm(
            server_url=os.getenv("KC_URL"),
            realm_name=os.getenv("KC_REALM"))
        oidc_client = realm.open_id_connect(
            client_id=os.getenv("KC_CLIENT_ID"),
            client_secret=os.getenv("KC_CLIENT_SECRET"))
        token_response = oidc_client.client_credentials()
        token = token_response["access_token"]
        headers.update({"Authorization": 'bearer ' + token})

    DEFAULT_REQUEST_HEADERS = headers

    crawler_settings = {
        'LOG_ENABLED': '1',
        'LOG_LEVEL': 'ERROR',
        'USER_AGENT': config.user_agent,
        'DOWNLOADER_MIDDLEWARES': {DOWNLOADER_MIDDLEWARES_PATH: 900, HEADER_MIDDLEWARES_PATH: 901},
        # Need to be > 600 to be after the redirectMiddleware
        'DUPEFILTER_USE_ANCHORS': config.use_anchors,
        # Use our custom dupefilter in order to be scheme agnostic regarding link provided
        'DUPEFILTER_CLASS': DUPEFILTER_CLASS_PATH,
        'DEFAULT_REQUEST_HEADERS': DEFAULT_REQUEST_HEADERS,
        'TELNETCONSOLE_ENABLED': False
    }

    if config.dns_resolver is not None:
        crawler_settings['DNS_RESOLVER'] = config.dns_resolver

    process = CrawlerProcess(crawler_settings)

    process.crawl(
        DocumentationSpider,
        config=config,
        typesense_helper=typesense_helper,
        strategy=strategy
    )

    process.start()
    process.stop()

    # Kill browser if needed
    BrowserHandler.destroy(config.driver)

    if len(config.extra_records) > 0:
        typesense_helper.add_records(config.extra_records, "Extra records", False)

    print("")

    if DocumentationSpider.NB_INDEXED > 0:
        typesense_helper.commit_tmp_collection()
        print('Nb hits: {}'.format(DocumentationSpider.NB_INDEXED))
        config.update_nb_hits_value(DocumentationSpider.NB_INDEXED)
    else:
        print('Crawling issue: nbHits 0 for ' + config.index_name)
        exit(EXIT_CODE_NO_RECORD)
    print("")


if __name__ == '__main__':
    from os import environ

    run_config(environ['CONFIG'])
