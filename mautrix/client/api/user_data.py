from typing import Optional

from ...errors import MatrixResponseError
from ...api import Method
from .types import UserSearchResults, Member, SerializerError, User
from .base import BaseClientAPI, quote


class UserDataMethods(BaseClientAPI):
    """
    Methods in section 10 User Data of the spec. These methods are used for setting and getting user
    metadata and searching for users.

    See also: `API reference <https://matrix.org/docs/spec/client_server/r0.4.0.html#user-data>`__
    """

    # region 10.1 User Directory
    # API reference: https://matrix.org/docs/spec/client_server/r0.4.0.html#user-directory

    async def search_users(self, search_query: str, limit: Optional[int] = 10
                           ) -> UserSearchResults:
        """
        Performs a search for users on the homeserver. The homeserver may determine which subset of
        users are searched, however the homeserver MUST at a minimum consider the users the
        requesting user shares a room with and those who reside in public rooms (known to the
        homeserver). The search MUST consider local users to the homeserver, and SHOULD query remote
        users as part of the search.

        The search is performed case-insensitively on user IDs and display names preferably using a
        collation determined based upon the Accept-Language header provided in the request, if
        present.

        See also: `API reference <https://matrix.org/docs/spec/client_server/r0.4.0.html#post-matrix-client-r0-user-directory-search>`__

        Args:
            search_query: The query to search for.
            limit: The maximum number of results to return.

        Returns:
            The results of the search and whether or not the results were limited.
        """
        content = await self.api.request(Method.POST, "/user_directory/search", {
            "search_term": search_query,
            "limit": limit,
        })
        try:
            return UserSearchResults([User.deserialize(user) for user in content["results"]],
                                     content["limited"])
        except SerializerError as e:
            raise MatrixResponseError("Invalid user in search results") from e
        except KeyError:
            if "results" not in content:
                raise MatrixResponseError("`results` not in content.")
            elif "limited" not in content:
                raise MatrixResponseError("`limited` not in content.")
            raise

    # endregion
    # region 10.2 Profiles
    # API reference: https://matrix.org/docs/spec/client_server/r0.4.0.html#profiles

    async def set_displayname(self, displayname: str) -> None:
        """
        Set the display name of the current user.

        See also: `API reference <https://matrix.org/docs/spec/client_server/r0.4.0.html#put-matrix-client-r0-profile-userid-displayname>`__

        Args:
            displayname: The new display name for the user.
        """
        await self.api.request(Method.PUT, f"/profile/{quote(self.mxid)}/displayname", {
            "displayname": displayname,
        })

    async def get_displayname(self, user_id: str) -> str:
        """
        Get the display name of a user.

        See also: `API reference <https://matrix.org/docs/spec/client_server/r0.4.0.html#get-matrix-client-r0-profile-userid-displayname>`__

        Args:
            user_id: The ID of the user whose display name to get.

        Returns:
            The display name of the given user.
        """
        content = await self.api.request(Method.GET, f"/profile/{quote(user_id)}/displayname")
        try:
            return content["displayname"]
        except KeyError:
            raise MatrixResponseError("`displayname` not in response.")

    async def set_avatar_url(self, avatar_url: str) -> None:
        """
        Set the avatar of the current user.

        See also: `API reference <https://matrix.org/docs/spec/client_server/r0.4.0.html#put-matrix-client-r0-profile-userid-avatar-url>`__

        Args:
            avatar_url: The ``mxc://`` URI to the new avatar.
        """
        await self.api.request(Method.PUT, f"/profile/{quote(self.mxid)}/avatar_url", {
            "avatar_url": avatar_url,
        })

    async def get_avatar_url(self, user_id: str) -> str:
        """
        Get the avatar URL of a user.

        See also: `API reference <https://matrix.org/docs/spec/client_server/r0.4.0.html#get-matrix-client-r0-profile-userid-avatar-url>`__

        Args:
            user_id: The ID of the user whose avatar to get.

        Returns:
            The ``mxc://`` URI to the user's avatar.
        """
        content = await self.api.request(Method.GET, f"/profile/{quote(user_id)}/avatar_url")
        try:
            return content["avatar_url"]
        except KeyError:
            raise MatrixResponseError("`avatar_url` not in response.")

    async def get_profile(self, user_id: str) -> Member:
        """
        Get the combined profile information for a user.

        See also: `API reference <https://matrix.org/docs/spec/client_server/r0.4.0.html#get-matrix-client-r0-profile-userid>`__

        Args:
            user_id: The ID of the user whose profile to get.

        Returns:
            The profile information of the given user.
        """
        content = await self.api.request(Method.GET, f"/profile/{quote(user_id)}")
        try:
            return Member.deserialize(content)
        except SerializerError as e:
            raise MatrixResponseError("Invalid member in response") from e

    # endregion