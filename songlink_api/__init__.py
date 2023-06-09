from typing import Optional, Union, List

from songlink_api.types.exceptions import APIException, TooManyRequests, EntityNotFound
from songlink_api.types import (
    PlatformName,
    EntityType,
    EntityUniqueId,
    APIResponse,
    APIProvider,
    Platform,
)

from aiohttp.typedefs import DEFAULT_JSON_DECODER
from aiohttp_client_cache import CachedSession, FileBackend, CacheBackend
from aiohttp_proxy import ProxyConnector

import orjson
import pkg_resources
import datetime
import random

try:
    __version__ = pkg_resources.get_distribution("songlink_api").version
except pkg_resources.DistributionNotFound:
    __version__ = ""


class SongLink:
    def __init__(
        self,
        api_key: str | None = None,
        api_url: str = "https://api.song.link/",
        api_version: str = "v1-alpha.1",
        api_timeout: int = 60,
        proxy: List[str] | str | None = None,
        always_use_proxy: bool = False,
        cache_backend: CacheBackend
        | None = FileBackend(
            expire_after=900,
            ignored_parameters=["key"],
            allowed_codes=(200,),
            cache_control=False,
        ),
        use_orjson: bool = True,
    ) -> None:
        """
        Initialize a new SongLink instance with the specified API configuration options.

        Args:
            api_key (Optional[str]): The API key to use for making SongLink API requests.
            api_url (str): The base URL for the SongLink API. Defaults to "https://api.song.link/".
            api_version (str): The version number of the SongLink API to use. Defaults to "v1-alpha.1".
            api_timeout (int): The number of seconds to wait for a response from the API before timing out. Defaults to 60.
            use_cache (bool): Whether to enable response caching for API requests. Defaults to True.
            cache_time (int): The number of seconds to cache API responses for. Ignored if use_cache is false. Defaults to 900.

        Returns:
            None
        """
        self.api_key: str | None = api_key
        self.api_url: str = api_url.rstrip("/")
        self.api_version: str = api_version
        self.api_timeout: int = api_timeout
        self.cache_backend: CacheBackend | None = cache_backend
        self.connections: Dict[str | None, datetime.datetime | None] = {}
        if proxy is not None:
            for _proxy in proxy if isinstance(proxy, list) else [proxy]:
                self.connections[_proxy] = None
        if not always_use_proxy:
            self.connections[None] = None
        if not self.connections:
            raise ValueError("No connections specified.")
        self.use_orjson: bool = use_orjson

    def __repr__(self) -> str:
        return f"<SongLink at {hex(id(self))}>"

    async def __make_request(
        self, method: str, params: Optional[dict] = None
    ) -> APIResponse:
        """
        Sends a HTTP request to the SongLink API and returns the response data as an APIResponse object

        Args:
            method (str): The API method to call
            params (Optional[dict]): Dictionary of query string parameters to send with the request. Defaults to None.

        Raises:
            TooManyRequests: If the API returns a 'too_many_requests' error code
            EntityNotFound: If the API returns a 'could_not_fetch_entity_data' error code
            APIException: If the response status code is not 200 or the response data is empty

        Returns:
            APIResponse: An object containing the API response data
        """
        if all(map(lambda e: self.connections[e] is not None, self.connections)):
            raise TooManyRequests()

        connection = random.choice(
            list(filter(lambda e: self.connections[e] is None, self.connections))
        )

        async with CachedSession(
            connector=None
            if connection is None
            else ProxyConnector.from_url(connection),
            cache=self.cache_backend,
            headers={
                "User-Agent": f"SongLinkAPI/v{__version__}",
            },
        ) as session:
            async with session.get(
                url=f"{self.api_url}/{self.api_version}/{method}",
                params={
                    **(
                        {k: v for k, v in params.items() if v is not None}
                        if params is not None
                        else {}
                    ),
                    **({"key": self.api_key} if self.api_key is not None else {}),
                },
                timeout=self.api_timeout,
            ) as response:
                try:
                    data = await response.json(
                        loads=orjson.loads if self.use_orjson else DEFAULT_JSON_DECODER
                    )
                except Exception:
                    data = {}

                if response.status != 200 or not data:
                    reason = data.get("code")
                    if reason == "too_many_requests":
                        self.connections[
                            connection
                        ] = datetime.datetime.now() + datetime.timedelta(
                            seconds=self.api_timeout
                        )
                        raise TooManyRequests()
                    if reason == "could_not_fetch_entity_data":
                        raise EntityNotFound()
                    else:
                        raise APIException(status_code=response.status, message=reason)

            return APIResponse(
                entity_unique_id=data.get("entityUniqueId"),
                user_country=data.get("userCountry", params.get("userCountry", "US")),
                page_url=data.get("pageUrl"),
                entities_by_unique_id=[
                    EntityUniqueId(
                        id=entity.get("id"),
                        type=entity.get("type"),
                        title=entity.get("title"),
                        artist_name=entity.get("artistName"),
                        thumbnail_url=entity.get("thumbnailUrl"),
                        thumbnail_width=entity.get("thumbnailWidth"),
                        thumbnail_height=entity.get("thumbnailHeight"),
                        api_provider=entity.get("apiProvider"),
                        platforms=[
                            PlatformName(platform_name)
                            for platform_name in entity.get("platforms")
                            if entity.get("platforms") in list(PlatformName)
                        ],
                    )
                    for entity in data.get("entitiesByUniqueId").values()
                    if entity.get("apiProvider") in list(APIProvider)
                ],
                links_by_platform=[
                    Platform(
                        name=platform_name,
                        country=platform.get("country"),
                        entity_unique_id=platform.get("entityUniqueId"),
                        url=platform.get("url"),
                        native_app_uri_mobile=platform.get("nativeAppUriMobile"),
                        native_app_uri_desktop=platform.get("nativeAppUriDesktop"),
                    )
                    for platform_name, platform in data.get("linksByPlatform").items()
                    if platform_name in list(PlatformName)
                ],
            )

    async def links_by_url(
        self,
        url: str,
        user_country: str = "US",
        song_if_single: bool = False,
    ) -> APIResponse:
        """
        Sends a HTTP request to the SongLink API with the provided URL and returns the response data as an APIResponse object.

        Args:
            url (str): The URL of the song or album to retrieve links for.
            user_country (str): The 2-letter country code to search links for. Defaults to "US".
            song_if_single (bool): Whether to return links to the full album or only to the song for single-track releases. Defaults to False.

        Raises:
            TooManyRequests: If the API returns a 'too_many_requests' error code
            EntityNotFound: If the API returns a 'could_not_fetch_entity_data' error code
            APIException: If the response status code is not 200 or the response data is empty

        Returns:
            APIResponse: An object containing the API response data
        """
        return await self.__make_request(
            method="links",
            params={
                "url": url,
                "userCountry": user_country.upper(),
                "songIfSingle": "true" if song_if_single else "false",
            },
        )

    async def links_by_id(
        self,
        id: Union[str, int],
        platform: PlatformName,
        type: EntityType,
        user_country: str = "US",
        song_if_single: bool = False,
    ) -> APIResponse:
        """
        Sends a HTTP request to the SongLink API with the provided song ID and returns the response data as an APIResponse object.

        Args:
            song_id (str): The SongLink ID of the song or album to retrieve links for.
            user_country (str): The 2-letter country code to search links for. Defaults to "US".
            song_if_single (bool): Whether to return links to the full album or only to the song for single-track releases. Defaults to False.

        Raises:
            TooManyRequests: If the API returns a 'too_many_requests' error code
            EntityNotFound: If the API returns a 'could_not_fetch_entity_data' error code
            APIException: If the response status code is not 200 or the response data is empty

        Returns:
            APIResponse: An object containing the API response data
        """
        return await self.__make_request(
            method="links",
            params={
                "id": id,
                "platform": platform.name,
                "type": type.name,
                "userCountry": user_country.upper(),
                "songIfSingle": "true" if song_if_single else "false",
            },
        )
